"""Machine Learning tasks for FXLab."""
import json
import io
import random
from datetime import datetime
from typing import Optional, List, Tuple

from app.tasks import celery_app
from app.database import SessionLocal
from app.models import (
    Job, Dataset, Timeframe, Window, WindowImage, Label, EntryPoint, MLModel, MLScore
)
from app.config import settings


@celery_app.task(bind=True)
def ml_train_task(
    self, job_id: int, dataset_id: int, timeframe_name: str,
    model_type: str,  # "entry" or "profit"
    model_name: str,
    epochs: int = 10,
    batch_size: int = 32,
    learning_rate: float = 0.001,
    limit: int = None,
    start_ts: str = None,
    end_ts: str = None,
):
    """
    Train a CNN model for entry detection or profit prediction.

    model_type:
      - "entry": Classify good entry vs random (using entry_points)
      - "profit": Classify tp_hit vs sl_hit (using labels)
    """
    import torch
    import torch.nn as nn
    import torch.optim as optim
    from torchvision import models, transforms
    from torch.utils.data import Dataset as TorchDataset, DataLoader
    from PIL import Image
    from app.services.minio_client import get_image, upload_image, ensure_bucket_exists

    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        job.status = "running"
        job.celery_task_id = self.request.id
        db.commit()

        tf = db.query(Timeframe).filter(Timeframe.name == timeframe_name).first()
        if not tf:
            raise ValueError(f"Timeframe {timeframe_name} not found")

        ensure_bucket_exists()

        # Build training data based on model_type
        if model_type == "entry":
            train_data, val_data, label_source = _build_entry_dataset(
                db, dataset_id, tf.id, limit, start_ts, end_ts
            )
        elif model_type == "profit":
            train_data, val_data, label_source = _build_profit_dataset(
                db, dataset_id, tf.id, limit, start_ts, end_ts
            )
        else:
            raise ValueError(f"Unknown model_type: {model_type}")

        if len(train_data) < 10:
            raise ValueError(f"Not enough training data: {len(train_data)} samples")

        # Define transforms
        transform = transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

        # Custom dataset class
        class ImageDataset(TorchDataset):
            def __init__(self, data_list, transform):
                self.data = data_list
                self.transform = transform

            def __len__(self):
                return len(self.data)

            def __getitem__(self, idx):
                image_key, label = self.data[idx]
                try:
                    img_bytes = get_image(image_key)
                    img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
                    img = self.transform(img)
                    return img, label
                except Exception:
                    # Return a black image on error
                    return torch.zeros(3, 224, 224), label

        train_dataset = ImageDataset(train_data, transform)
        val_dataset = ImageDataset(val_data, transform)

        train_loader = DataLoader(train_dataset, batch_size=batch_size, shuffle=True)
        val_loader = DataLoader(val_dataset, batch_size=batch_size, shuffle=False)

        # Create model (ResNet18 pretrained, modify final layer for binary classification)
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model = models.resnet18(weights=models.ResNet18_Weights.DEFAULT)
        model.fc = nn.Linear(model.fc.in_features, 2)
        model = model.to(device)

        criterion = nn.CrossEntropyLoss()
        optimizer = optim.Adam(model.parameters(), lr=learning_rate)

        # Training loop
        best_val_acc = 0.0
        best_model_state = None
        train_losses = []
        val_accs = []

        for epoch in range(epochs):
            model.train()
            running_loss = 0.0
            for images, labels in train_loader:
                images, labels = images.to(device), labels.to(device)
                optimizer.zero_grad()
                outputs = model(images)
                loss = criterion(outputs, labels)
                loss.backward()
                optimizer.step()
                running_loss += loss.item()

            avg_loss = running_loss / len(train_loader) if train_loader else 0
            train_losses.append(avg_loss)

            # Validation
            model.eval()
            correct = 0
            total = 0
            with torch.no_grad():
                for images, labels in val_loader:
                    images, labels = images.to(device), labels.to(device)
                    outputs = model(images)
                    _, predicted = torch.max(outputs.data, 1)
                    total += labels.size(0)
                    correct += (predicted == labels).sum().item()

            val_acc = correct / total if total > 0 else 0
            val_accs.append(val_acc)

            if val_acc > best_val_acc:
                best_val_acc = val_acc
                best_model_state = model.state_dict().copy()

        # Save best model to MinIO
        if best_model_state is None:
            best_model_state = model.state_dict()

        model_buffer = io.BytesIO()
        torch.save(best_model_state, model_buffer)
        model_bytes = model_buffer.getvalue()

        artifact_key = f"ml_models/dataset_{dataset_id}/{model_type}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.pt"
        upload_image(artifact_key, model_bytes, content_type="application/octet-stream")

        # Calculate final metrics
        model.load_state_dict(best_model_state)
        model.eval()

        # Full validation metrics
        all_preds = []
        all_labels = []
        all_probs = []
        with torch.no_grad():
            for images, labels in val_loader:
                images, labels = images.to(device), labels.to(device)
                outputs = model(images)
                probs = torch.softmax(outputs, dim=1)
                _, predicted = torch.max(outputs.data, 1)
                all_preds.extend(predicted.cpu().tolist())
                all_labels.extend(labels.cpu().tolist())
                all_probs.extend(probs[:, 1].cpu().tolist())

        # Calculate AUC
        try:
            auc = _calculate_auc(all_labels, all_probs)
        except Exception:
            auc = 0.0

        metrics = {
            "train_samples": len(train_data),
            "val_samples": len(val_data),
            "epochs": epochs,
            "best_val_accuracy": round(best_val_acc, 4),
            "final_train_loss": round(train_losses[-1], 4) if train_losses else 0,
            "auc": round(auc, 4),
        }

        config = {
            "model_type": model_type,
            "epochs": epochs,
            "batch_size": batch_size,
            "learning_rate": learning_rate,
            "architecture": "resnet18",
        }

        # Save model to database
        ml_model = MLModel(
            name=model_name,
            model_type=model_type,
            dataset_id=dataset_id,
            timeframe_id=tf.id,
            label_source=label_source,
            config_json=json.dumps(config),
            metrics_json=json.dumps(metrics),
            artifact_key=artifact_key,
            created_at=datetime.utcnow(),
        )
        db.add(ml_model)
        db.commit()
        db.refresh(ml_model)

        job.status = "completed"
        job.result = json.dumps({
            "model_id": ml_model.id,
            "metrics": metrics,
        })
        db.commit()

        return {"model_id": ml_model.id, "metrics": metrics}

    except Exception as e:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(e)
            db.commit()
        raise
    finally:
        db.close()


def _build_entry_dataset(
    db, dataset_id: int, timeframe_id: int, limit: int = None,
    start_ts: str = None, end_ts: str = None
) -> Tuple[List, List, str]:
    """
    Build training data for entry model.
    Positive: entry_points with label='good'
    Negative: random windows (not entry points)
    """
    from datetime import datetime as dt

    # Get good entry points
    query = db.query(EntryPoint).filter(
        EntryPoint.dataset_id == dataset_id,
        EntryPoint.timeframe_id == timeframe_id,
        EntryPoint.label == "good",
    )
    if start_ts:
        query = query.filter(EntryPoint.ts >= dt.fromisoformat(start_ts.replace('Z', '+00:00')))
    if end_ts:
        query = query.filter(EntryPoint.ts <= dt.fromisoformat(end_ts.replace('Z', '+00:00')))

    good_entries = query.all()

    if not good_entries:
        raise ValueError("No good entry points found")

    # Get window images for good entries (match by end_ts close to entry ts)
    positive_samples = []
    good_entry_window_ids = set()

    for entry in good_entries:
        # Find window whose end_ts matches entry.ts
        window = db.query(Window).filter(
            Window.dataset_id == dataset_id,
            Window.timeframe_id == timeframe_id,
            Window.end_ts == entry.ts,
        ).first()

        if window:
            wi = db.query(WindowImage).filter(WindowImage.window_id == window.id).first()
            if wi:
                positive_samples.append((wi.image_key, 1))
                good_entry_window_ids.add(window.id)

    if not positive_samples:
        raise ValueError("No window images found for good entry points")

    # Get negative samples (random windows not in positive set)
    neg_count = len(positive_samples) * 2  # 2x negatives

    all_window_images = db.query(WindowImage).join(Window).filter(
        Window.dataset_id == dataset_id,
        Window.timeframe_id == timeframe_id,
    ).all()

    negative_candidates = [
        wi for wi in all_window_images if wi.window_id not in good_entry_window_ids
    ]

    if len(negative_candidates) < neg_count:
        neg_count = len(negative_candidates)

    random.shuffle(negative_candidates)
    negative_samples = [(wi.image_key, 0) for wi in negative_candidates[:neg_count]]

    # Combine and split (80/20 time-based would be ideal, but for MVP random split)
    all_samples = positive_samples + negative_samples
    random.shuffle(all_samples)

    if limit:
        all_samples = all_samples[:limit]

    split_idx = int(len(all_samples) * 0.8)
    train_data = all_samples[:split_idx]
    val_data = all_samples[split_idx:]

    return train_data, val_data, "entry_points"


def _build_profit_dataset(
    db, dataset_id: int, timeframe_id: int, limit: int = None,
    start_ts: str = None, end_ts: str = None
) -> Tuple[List, List, str]:
    """
    Build training data for profit model.
    Class 0: sl_hit
    Class 1: tp_hit
    (neither is excluded for MVP 2-class)
    """
    from datetime import datetime as dt

    # Get labels with tp_hit or sl_hit
    query = db.query(Label).filter(
        Label.dataset_id == dataset_id,
        Label.timeframe_id == timeframe_id,
        Label.result.in_(["tp_hit", "sl_hit"]),
    )
    if start_ts:
        query = query.filter(Label.bar_ts >= dt.fromisoformat(start_ts.replace('Z', '+00:00')))
    if end_ts:
        query = query.filter(Label.bar_ts <= dt.fromisoformat(end_ts.replace('Z', '+00:00')))

    labels = query.all()

    if not labels:
        raise ValueError("No tp/sl labels found")

    # Match labels to window images by bar_ts == window.end_ts
    samples = []
    for label in labels:
        window = db.query(Window).filter(
            Window.dataset_id == dataset_id,
            Window.timeframe_id == timeframe_id,
            Window.end_ts == label.bar_ts,
        ).first()

        if window:
            wi = db.query(WindowImage).filter(WindowImage.window_id == window.id).first()
            if wi:
                label_val = 1 if label.result == "tp_hit" else 0
                samples.append((wi.image_key, label_val))

    if not samples:
        raise ValueError("No window images found for labels")

    random.shuffle(samples)

    if limit:
        samples = samples[:limit]

    split_idx = int(len(samples) * 0.8)
    train_data = samples[:split_idx]
    val_data = samples[split_idx:]

    return train_data, val_data, "tp_sl"


def _calculate_auc(labels: List[int], probs: List[float]) -> float:
    """Calculate AUC-ROC score."""
    if len(set(labels)) < 2:
        return 0.5

    # Sort by probability
    pairs = list(zip(probs, labels))
    pairs.sort(reverse=True)

    # Calculate AUC using trapezoidal rule
    tp = 0
    fp = 0
    total_pos = sum(labels)
    total_neg = len(labels) - total_pos

    if total_pos == 0 or total_neg == 0:
        return 0.5

    auc = 0.0
    prev_fpr = 0.0
    prev_tpr = 0.0

    for prob, label in pairs:
        if label == 1:
            tp += 1
        else:
            fp += 1

        tpr = tp / total_pos
        fpr = fp / total_neg

        auc += (fpr - prev_fpr) * (tpr + prev_tpr) / 2
        prev_fpr = fpr
        prev_tpr = tpr

    return auc


@celery_app.task(bind=True)
def ml_infer_task(
    self, job_id: int, model_id: int,
    dataset_id: int = None,
    limit: int = None,
    start_ts: str = None,
    end_ts: str = None,
):
    """
    Run inference on window images and save scores to ml_scores.
    """
    import torch
    import torch.nn as nn
    from torchvision import models, transforms
    from PIL import Image
    from app.services.minio_client import get_image

    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        job.status = "running"
        job.celery_task_id = self.request.id
        db.commit()

        ml_model = db.query(MLModel).filter(MLModel.id == model_id).first()
        if not ml_model:
            raise ValueError(f"Model {model_id} not found")

        if not ml_model.artifact_key:
            raise ValueError(f"Model {model_id} has no artifact")

        # Use model's dataset if not specified
        if dataset_id is None:
            dataset_id = ml_model.dataset_id

        # Load model
        device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        model = models.resnet18(weights=None)
        model.fc = nn.Linear(model.fc.in_features, 2)

        model_bytes = get_image(ml_model.artifact_key)
        model_buffer = io.BytesIO(model_bytes)
        state_dict = torch.load(model_buffer, map_location=device, weights_only=True)
        model.load_state_dict(state_dict)
        model = model.to(device)
        model.eval()

        transform = transforms.Compose([
            transforms.Resize((224, 224)),
            transforms.ToTensor(),
            transforms.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225]),
        ])

        # Get window images to score
        query = db.query(WindowImage).join(Window).filter(
            Window.dataset_id == dataset_id,
            Window.timeframe_id == ml_model.timeframe_id,
        )

        if start_ts:
            filter_start = datetime.fromisoformat(start_ts.replace('Z', '+00:00'))
            query = query.filter(Window.end_ts >= filter_start)
        if end_ts:
            filter_end = datetime.fromisoformat(end_ts.replace('Z', '+00:00'))
            query = query.filter(Window.end_ts <= filter_end)

        if limit:
            query = query.limit(limit)

        window_images = query.all()

        if not window_images:
            job.status = "completed"
            job.result = json.dumps({"scored_count": 0, "message": "No window images found"})
            db.commit()
            return {"scored_count": 0}

        # Delete existing scores for this model/dataset combination
        db.query(MLScore).filter(
            MLScore.model_id == model_id,
            MLScore.dataset_id == dataset_id,
        ).delete()
        db.commit()

        # Run inference
        scored_count = 0
        batch_size = 100
        scores_batch = []

        with torch.no_grad():
            for wi in window_images:
                try:
                    img_bytes = get_image(wi.image_key)
                    img = Image.open(io.BytesIO(img_bytes)).convert('RGB')
                    img_tensor = transform(img).unsqueeze(0).to(device)

                    output = model(img_tensor)
                    probs = torch.softmax(output, dim=1)
                    score = probs[0, 1].item()  # Probability of class 1

                    window = db.query(Window).filter(Window.id == wi.window_id).first()

                    scores_batch.append(MLScore(
                        dataset_id=dataset_id,
                        timeframe_id=window.timeframe_id,
                        window_id=wi.window_id,
                        model_id=model_id,
                        score=score,
                        created_at=datetime.utcnow(),
                    ))
                    scored_count += 1

                    if len(scores_batch) >= batch_size:
                        db.bulk_save_objects(scores_batch)
                        db.commit()
                        scores_batch = []

                except Exception:
                    continue

        if scores_batch:
            db.bulk_save_objects(scores_batch)
            db.commit()

        job.status = "completed"
        job.result = json.dumps({
            "scored_count": scored_count,
            "model_id": model_id,
            "dataset_id": dataset_id,
        })
        db.commit()

        return {"scored_count": scored_count}

    except Exception as e:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(e)
            db.commit()
        raise
    finally:
        db.close()
