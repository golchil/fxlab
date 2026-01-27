import os
import json
import tempfile
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import Response
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import Dataset, Bar, Job, Window, Label, WindowImage, WindowFeature, Timeframe
from app.schemas import (
    DatasetResponse, ImportRequest, WindowsRequest, LabelsTpSlRequest,
    WindowsStatsResponse, LabelsStatsResponse, JobCreatedResponse,
    ImagesRequest, ImagesStatsResponse, ImageListResponse, ImageListItem, ImageDetailResponse,
    FeaturesRequest, FeaturesStatsResponse, InsightsResponse, FeatureRankingItem, ThresholdSuggestion,
    ResampleRequest, TimeframeInfoItem
)
from app.tasks import (
    import_csv_task, generate_windows_task, generate_labels_task,
    generate_window_images_task, generate_window_features_task, resample_bars_task
)

router = APIRouter(prefix="/datasets", tags=["datasets"])


@router.get("", response_model=List[DatasetResponse])
def list_datasets(db: Session = Depends(get_db)):
    datasets = db.query(Dataset).all()
    result = []
    for ds in datasets:
        bar_count = db.query(func.count()).select_from(Bar).filter(Bar.dataset_id == ds.id).scalar()
        min_ts = db.query(func.min(Bar.ts)).filter(Bar.dataset_id == ds.id).scalar()
        max_ts = db.query(func.max(Bar.ts)).filter(Bar.dataset_id == ds.id).scalar()
        result.append(DatasetResponse(
            id=ds.id,
            name=ds.name,
            description=ds.description,
            timezone=ds.timezone,
            created_at=ds.created_at,
            updated_at=ds.updated_at,
            bar_count=bar_count,
            start_ts=min_ts,
            end_ts=max_ts,
        ))
    return result


@router.get("/{dataset_id}", response_model=DatasetResponse)
def get_dataset(dataset_id: int, db: Session = Depends(get_db)):
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    bar_count = db.query(func.count()).select_from(Bar).filter(Bar.dataset_id == dataset_id).scalar()
    min_ts = db.query(func.min(Bar.ts)).filter(Bar.dataset_id == dataset_id).scalar()
    max_ts = db.query(func.max(Bar.ts)).filter(Bar.dataset_id == dataset_id).scalar()

    return DatasetResponse(
        id=dataset.id,
        name=dataset.name,
        description=dataset.description,
        timezone=dataset.timezone,
        created_at=dataset.created_at,
        updated_at=dataset.updated_at,
        bar_count=bar_count,
        start_ts=min_ts,
        end_ts=max_ts,
    )


@router.post("/import", response_model=JobCreatedResponse)
async def import_dataset(
    file: UploadFile = File(...),
    name: str = Form(...),
    description: str = Form(None),
    timezone: str = Form("Asia/Tokyo"),
    timeframe: str = Form("M1"),
    db: Session = Depends(get_db),
):
    # Save uploaded file temporarily
    data_dir = "/app/data"
    os.makedirs(data_dir, exist_ok=True)

    file_path = os.path.join(data_dir, f"upload_{name}_{os.urandom(4).hex()}.csv")
    with open(file_path, "wb") as f:
        content = await file.read()
        f.write(content)

    # Create job
    job = Job(
        job_type="import",
        status="pending",
        params=json.dumps({
            "name": name,
            "timezone": timezone,
            "timeframe": timeframe,
            "file_path": file_path,
        }),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Start Celery task
    import_csv_task.delay(
        job_id=job.id,
        file_path=file_path,
        dataset_name=name,
        timezone_str=timezone,
        timeframe_name=timeframe,
        description=description,
    )

    return JobCreatedResponse(job_id=job.id, message="Import job started")


@router.post("/{dataset_id}/windows", response_model=JobCreatedResponse)
def create_windows(
    dataset_id: int,
    request: WindowsRequest,
    db: Session = Depends(get_db),
):
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    params = {
        "lookback_n": request.lookback_n,
        "step": request.step,
        "limit": request.limit,
        "start_ts": request.start_ts.isoformat() if request.start_ts else None,
        "end_ts": request.end_ts.isoformat() if request.end_ts else None,
    }

    job = Job(
        dataset_id=dataset_id,
        job_type="windows",
        status="pending",
        params=json.dumps(params),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    generate_windows_task.delay(
        job_id=job.id,
        dataset_id=dataset_id,
        lookback_n=request.lookback_n,
        step=request.step,
        limit=request.limit,
        start_ts=request.start_ts.isoformat() if request.start_ts else None,
        end_ts=request.end_ts.isoformat() if request.end_ts else None,
    )

    return JobCreatedResponse(job_id=job.id, message="Windows generation job started")


@router.post("/{dataset_id}/labels/tp-sl", response_model=JobCreatedResponse)
def create_labels_tp_sl(
    dataset_id: int,
    request: LabelsTpSlRequest,
    db: Session = Depends(get_db),
):
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    params = {
        "lookahead_m": request.lookahead_m,
        "tp_r": request.tp_r,
        "sl_r": request.sl_r,
        "limit": request.limit,
        "start_ts": request.start_ts.isoformat() if request.start_ts else None,
        "end_ts": request.end_ts.isoformat() if request.end_ts else None,
    }

    job = Job(
        dataset_id=dataset_id,
        job_type="labels",
        status="pending",
        params=json.dumps(params),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    generate_labels_task.delay(
        job_id=job.id,
        dataset_id=dataset_id,
        lookahead_m=request.lookahead_m,
        tp_r=request.tp_r,
        sl_r=request.sl_r,
        limit=request.limit,
        start_ts=request.start_ts.isoformat() if request.start_ts else None,
        end_ts=request.end_ts.isoformat() if request.end_ts else None,
    )

    return JobCreatedResponse(job_id=job.id, message="Labels generation job started")


@router.get("/{dataset_id}/windows/stats", response_model=WindowsStatsResponse)
def get_windows_stats(dataset_id: int, db: Session = Depends(get_db)):
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    total = db.query(func.count(Window.id)).filter(Window.dataset_id == dataset_id).scalar()
    lookback_n = db.query(Window.lookback_n).filter(Window.dataset_id == dataset_id).first()
    min_start = db.query(func.min(Window.start_ts)).filter(Window.dataset_id == dataset_id).scalar()
    max_end = db.query(func.max(Window.end_ts)).filter(Window.dataset_id == dataset_id).scalar()

    # Get latest generation job info
    latest_job = (
        db.query(Job)
        .filter(Job.dataset_id == dataset_id, Job.job_type == "windows", Job.status == "completed")
        .order_by(Job.updated_at.desc())
        .first()
    )

    generated_count = None
    generation_start_ts = None
    generation_end_ts = None
    if latest_job and latest_job.result:
        result = json.loads(latest_job.result)
        generated_count = result.get("total_windows")
        generation_start_ts = result.get("generation_start_ts")
        generation_end_ts = result.get("generation_end_ts")

    return WindowsStatsResponse(
        total_windows=total or 0,
        lookback_n=lookback_n[0] if lookback_n else None,
        earliest_start=min_start,
        latest_end=max_end,
        generated_count=generated_count,
        generation_start_ts=generation_start_ts,
        generation_end_ts=generation_end_ts,
    )


@router.get("/{dataset_id}/labels/stats", response_model=LabelsStatsResponse)
def get_labels_stats(dataset_id: int, db: Session = Depends(get_db)):
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    total = db.query(func.count(Label.id)).filter(Label.dataset_id == dataset_id).scalar() or 0
    tp_hit = db.query(func.count(Label.id)).filter(
        Label.dataset_id == dataset_id, Label.result == "tp_hit"
    ).scalar() or 0
    sl_hit = db.query(func.count(Label.id)).filter(
        Label.dataset_id == dataset_id, Label.result == "sl_hit"
    ).scalar() or 0
    neither = db.query(func.count(Label.id)).filter(
        Label.dataset_id == dataset_id, Label.result == "neither"
    ).scalar() or 0

    earliest_bar_ts = db.query(func.min(Label.bar_ts)).filter(Label.dataset_id == dataset_id).scalar()
    latest_bar_ts = db.query(func.max(Label.bar_ts)).filter(Label.dataset_id == dataset_id).scalar()

    # Get latest generation job info
    latest_job = (
        db.query(Job)
        .filter(Job.dataset_id == dataset_id, Job.job_type == "labels", Job.status == "completed")
        .order_by(Job.updated_at.desc())
        .first()
    )

    generated_count = None
    generation_start_ts = None
    generation_end_ts = None
    if latest_job and latest_job.result:
        result = json.loads(latest_job.result)
        generated_count = result.get("total_labels")
        generation_start_ts = result.get("generation_start_ts")
        generation_end_ts = result.get("generation_end_ts")

    return LabelsStatsResponse(
        total_labels=total,
        tp_hit_count=tp_hit,
        sl_hit_count=sl_hit,
        neither_count=neither,
        tp_hit_ratio=tp_hit / total if total > 0 else 0,
        sl_hit_ratio=sl_hit / total if total > 0 else 0,
        earliest_bar_ts=earliest_bar_ts,
        latest_bar_ts=latest_bar_ts,
        generated_count=generated_count,
        generation_start_ts=generation_start_ts,
        generation_end_ts=generation_end_ts,
    )


# ============ Images API ============

@router.post("/{dataset_id}/images", response_model=JobCreatedResponse)
def create_images(
    dataset_id: int,
    request: ImagesRequest,
    db: Session = Depends(get_db),
):
    """Generate candlestick chart images for windows."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    params = {
        "lookback_n": request.lookback_n,
        "ma_periods": request.ma_periods,
        "limit": request.limit,
        "start_ts": request.start_ts.isoformat() if request.start_ts else None,
        "end_ts": request.end_ts.isoformat() if request.end_ts else None,
        "overwrite": request.overwrite,
    }

    job = Job(
        dataset_id=dataset_id,
        job_type="images",
        status="pending",
        params=json.dumps(params),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    generate_window_images_task.delay(
        job_id=job.id,
        dataset_id=dataset_id,
        lookback_n=request.lookback_n,
        ma_periods=request.ma_periods,
        limit=request.limit,
        start_ts=request.start_ts.isoformat() if request.start_ts else None,
        end_ts=request.end_ts.isoformat() if request.end_ts else None,
        overwrite=request.overwrite,
    )

    return JobCreatedResponse(job_id=job.id, message="Image generation job started")


@router.get("/{dataset_id}/images/stats", response_model=ImagesStatsResponse)
def get_images_stats(dataset_id: int, db: Session = Depends(get_db)):
    """Get image generation statistics."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    total = db.query(func.count(WindowImage.id)).filter(WindowImage.dataset_id == dataset_id).scalar() or 0
    latest_created = db.query(func.max(WindowImage.created_at)).filter(WindowImage.dataset_id == dataset_id).scalar()

    # Get latest generation job info
    latest_job = (
        db.query(Job)
        .filter(Job.dataset_id == dataset_id, Job.job_type == "images", Job.status == "completed")
        .order_by(Job.updated_at.desc())
        .first()
    )

    last_params = None
    generated_count = None
    generation_start_ts = None
    generation_end_ts = None
    if latest_job:
        last_params = latest_job.params
        if latest_job.result:
            result = json.loads(latest_job.result)
            generated_count = result.get("generated_count")
            generation_start_ts = result.get("generation_start_ts")
            generation_end_ts = result.get("generation_end_ts")

    return ImagesStatsResponse(
        total_images=total,
        latest_created_at=latest_created,
        last_generation_params=last_params,
        generated_count=generated_count,
        generation_start_ts=generation_start_ts,
        generation_end_ts=generation_end_ts,
    )


@router.get("/{dataset_id}/images/list", response_model=ImageListResponse)
def list_images(
    dataset_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    label: Optional[str] = Query(None, regex="^(tp_hit|sl_hit|neither|all)$"),
    db: Session = Depends(get_db),
):
    """List images with pagination and optional label filter."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    # Base query: join window_images with windows
    query = (
        db.query(WindowImage, Window)
        .join(Window, WindowImage.window_id == Window.id)
        .filter(WindowImage.dataset_id == dataset_id)
    )

    # Left join with labels to get label result
    # We need to find labels that match the window's end_ts (bar_ts)
    if label and label != "all":
        # Join with labels and filter by result
        query = (
            query.join(Label, (Label.dataset_id == dataset_id) & (Label.bar_ts == Window.end_ts))
            .filter(Label.result == label)
        )
    else:
        # Left join to get label if exists
        query = query.outerjoin(
            Label, (Label.dataset_id == dataset_id) & (Label.bar_ts == Window.end_ts)
        ).add_columns(Label.result.label("label_result"))

    # Count total
    if label and label != "all":
        count_query = (
            db.query(func.count(WindowImage.id))
            .join(Window, WindowImage.window_id == Window.id)
            .join(Label, (Label.dataset_id == dataset_id) & (Label.bar_ts == Window.end_ts))
            .filter(WindowImage.dataset_id == dataset_id, Label.result == label)
        )
    else:
        count_query = (
            db.query(func.count(WindowImage.id))
            .filter(WindowImage.dataset_id == dataset_id)
        )

    total = count_query.scalar() or 0

    # Order and paginate
    offset = (page - 1) * page_size

    if label and label != "all":
        rows = (
            query
            .add_columns(Label.result.label("label_result"))
            .order_by(Window.end_ts.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )
    else:
        rows = (
            query
            .order_by(Window.end_ts.desc())
            .offset(offset)
            .limit(page_size)
            .all()
        )

    items = []
    for row in rows:
        if label and label != "all":
            wi, window, label_result = row
        else:
            wi, window, label_result = row

        items.append(ImageListItem(
            id=wi.id,
            window_id=wi.window_id,
            end_ts=window.end_ts,
            image_url=f"/images/{wi.id}/raw",
            label_result=label_result,
        ))

    total_pages = (total + page_size - 1) // page_size

    return ImageListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


# ============ Features API ============

@router.post("/{dataset_id}/features", response_model=JobCreatedResponse)
def create_features(
    dataset_id: int,
    request: FeaturesRequest,
    db: Session = Depends(get_db),
):
    """Generate numerical features for windows."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    params = {
        "lookback_n": request.lookback_n,
        "limit": request.limit,
        "start_ts": request.start_ts.isoformat() if request.start_ts else None,
        "end_ts": request.end_ts.isoformat() if request.end_ts else None,
        "overwrite": request.overwrite,
    }

    job = Job(
        dataset_id=dataset_id,
        job_type="features",
        status="pending",
        params=json.dumps(params),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    generate_window_features_task.delay(
        job_id=job.id,
        dataset_id=dataset_id,
        lookback_n=request.lookback_n,
        limit=request.limit,
        start_ts=request.start_ts.isoformat() if request.start_ts else None,
        end_ts=request.end_ts.isoformat() if request.end_ts else None,
        overwrite=request.overwrite,
    )

    return JobCreatedResponse(job_id=job.id, message="Feature generation job started")


@router.get("/{dataset_id}/features/stats", response_model=FeaturesStatsResponse)
def get_features_stats(dataset_id: int, db: Session = Depends(get_db)):
    """Get feature generation statistics."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    total = db.query(func.count(WindowFeature.id)).filter(
        WindowFeature.dataset_id == dataset_id
    ).scalar() or 0
    latest_created = db.query(func.max(WindowFeature.created_at)).filter(
        WindowFeature.dataset_id == dataset_id
    ).scalar()

    latest_job = (
        db.query(Job)
        .filter(Job.dataset_id == dataset_id, Job.job_type == "features", Job.status == "completed")
        .order_by(Job.updated_at.desc())
        .first()
    )

    generated_count = None
    skipped_count = None
    if latest_job and latest_job.result:
        result = json.loads(latest_job.result)
        generated_count = result.get("generated_count")
        skipped_count = result.get("skipped_count")

    return FeaturesStatsResponse(
        total_features=total,
        latest_created_at=latest_created,
        generated_count=generated_count,
        skipped_count=skipped_count,
    )


@router.get("/{dataset_id}/insights", response_model=InsightsResponse)
def get_insights(dataset_id: int, db: Session = Depends(get_db)):
    """Get feature insights: ranking by tp_hit vs sl_hit and threshold suggestions."""
    import math

    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    # Join features with labels via window end_ts
    rows = (
        db.query(WindowFeature, Label.result)
        .join(Window, WindowFeature.window_id == Window.id)
        .join(Label, (Label.dataset_id == dataset_id) & (Label.bar_ts == Window.end_ts))
        .filter(WindowFeature.dataset_id == dataset_id)
        .all()
    )

    feature_names = [
        "sma5_slope_5", "sma20_slope_5", "sma20_slope_20", "sma60_slope_20",
        "close_to_sma20", "spread_5_20", "spread_20_60", "atr14", "vol_mean",
    ]

    # Separate by label
    tp_features = {name: [] for name in feature_names}
    sl_features = {name: [] for name in feature_names}
    neither_count = 0
    tp_count = 0
    sl_count = 0

    for wf, label_result in rows:
        if label_result == "tp_hit":
            tp_count += 1
            for name in feature_names:
                tp_features[name].append(getattr(wf, name))
        elif label_result == "sl_hit":
            sl_count += 1
            for name in feature_names:
                sl_features[name].append(getattr(wf, name))
        else:
            neither_count += 1

    total_features = db.query(func.count(WindowFeature.id)).filter(
        WindowFeature.dataset_id == dataset_id
    ).scalar() or 0

    # Compute ranking
    feature_ranking = []
    for name in feature_names:
        tp_vals = tp_features[name]
        sl_vals = sl_features[name]

        if not tp_vals or not sl_vals:
            continue

        tp_mean = sum(tp_vals) / len(tp_vals)
        sl_mean = sum(sl_vals) / len(sl_vals)
        diff = tp_mean - sl_mean

        # Pooled std for effect size (Cohen's d)
        tp_var = sum((v - tp_mean) ** 2 for v in tp_vals) / len(tp_vals) if len(tp_vals) > 1 else 0
        sl_var = sum((v - sl_mean) ** 2 for v in sl_vals) / len(sl_vals) if len(sl_vals) > 1 else 0
        pooled_std = math.sqrt((tp_var + sl_var) / 2) if (tp_var + sl_var) > 0 else 1e-10
        effect_size = abs(diff) / pooled_std

        direction = "higher_is_tp" if diff > 0 else "lower_is_tp"

        feature_ranking.append(FeatureRankingItem(
            feature_name=name,
            tp_mean=round(tp_mean, 6),
            sl_mean=round(sl_mean, 6),
            diff=round(diff, 6),
            effect_size=round(effect_size, 4),
            direction=direction,
        ))

    feature_ranking.sort(key=lambda x: x.effect_size, reverse=True)

    # Threshold suggestions for top features
    threshold_suggestions = []
    for item in feature_ranking[:5]:
        name = item.feature_name
        all_tp = tp_features[name]
        all_sl = sl_features[name]

        if item.direction == "higher_is_tp":
            # Try percentile thresholds on tp values
            all_vals = sorted(all_tp + all_sl)
            for pct in [0.5, 0.6, 0.7]:
                idx = int(len(all_vals) * pct)
                threshold = all_vals[min(idx, len(all_vals) - 1)]
                tp_above = sum(1 for v in all_tp if v > threshold)
                sl_above = sum(1 for v in all_sl if v > threshold)
                total_above = tp_above + sl_above
                if total_above > 0:
                    precision = tp_above / total_above
                    if precision > (tp_count / (tp_count + sl_count) if (tp_count + sl_count) > 0 else 0):
                        threshold_suggestions.append(ThresholdSuggestion(
                            feature_name=name,
                            operator=">",
                            threshold=round(threshold, 6),
                            tp_count=tp_above,
                            sl_count=sl_above,
                            precision=round(precision, 4),
                        ))
                        break
        else:
            all_vals = sorted(all_tp + all_sl)
            for pct in [0.5, 0.4, 0.3]:
                idx = int(len(all_vals) * pct)
                threshold = all_vals[min(idx, len(all_vals) - 1)]
                tp_below = sum(1 for v in all_tp if v < threshold)
                sl_below = sum(1 for v in all_sl if v < threshold)
                total_below = tp_below + sl_below
                if total_below > 0:
                    precision = tp_below / total_below
                    if precision > (tp_count / (tp_count + sl_count) if (tp_count + sl_count) > 0 else 0):
                        threshold_suggestions.append(ThresholdSuggestion(
                            feature_name=name,
                            operator="<",
                            threshold=round(threshold, 6),
                            tp_count=tp_below,
                            sl_count=sl_below,
                            precision=round(precision, 4),
                        ))
                        break

    return InsightsResponse(
        dataset_id=dataset_id,
        total_features=total_features,
        tp_count=tp_count,
        sl_count=sl_count,
        neither_count=neither_count,
        feature_ranking=feature_ranking,
        threshold_suggestions=threshold_suggestions,
    )


# ============ Resample API ============

@router.post("/{dataset_id}/resample", response_model=JobCreatedResponse)
def create_resample(
    dataset_id: int,
    request: ResampleRequest,
    db: Session = Depends(get_db),
):
    """Resample bars from one timeframe to another (e.g. M1 -> H1)."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    params = {
        "from_tf": request.from_tf,
        "to_tf": request.to_tf,
        "start_ts": request.start_ts.isoformat() if request.start_ts else None,
        "end_ts": request.end_ts.isoformat() if request.end_ts else None,
        "limit": request.limit,
    }

    job = Job(
        dataset_id=dataset_id,
        job_type="resample",
        status="pending",
        params=json.dumps(params),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    resample_bars_task.delay(
        job_id=job.id,
        dataset_id=dataset_id,
        from_tf=request.from_tf,
        to_tf=request.to_tf,
        start_ts=request.start_ts.isoformat() if request.start_ts else None,
        end_ts=request.end_ts.isoformat() if request.end_ts else None,
        limit=request.limit,
    )

    return JobCreatedResponse(job_id=job.id, message="Resample job started")


@router.get("/{dataset_id}/timeframes", response_model=List[TimeframeInfoItem])
def get_timeframes(dataset_id: int, db: Session = Depends(get_db)):
    """List distinct timeframes present in this dataset with bar counts."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    rows = (
        db.query(
            Timeframe.name,
            Timeframe.minutes,
            func.count().label("bar_count"),
            func.min(Bar.ts).label("start_ts"),
            func.max(Bar.ts).label("end_ts"),
        )
        .join(Bar, Bar.timeframe_id == Timeframe.id)
        .filter(Bar.dataset_id == dataset_id)
        .group_by(Timeframe.id, Timeframe.name, Timeframe.minutes)
        .order_by(Timeframe.minutes)
        .all()
    )

    return [
        TimeframeInfoItem(
            name=row.name,
            minutes=row.minutes,
            bar_count=row.bar_count,
            start_ts=row.start_ts,
            end_ts=row.end_ts,
        )
        for row in rows
    ]
