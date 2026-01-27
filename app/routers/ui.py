import os
import json
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import Dataset, Bar, Job, Window, Label, WindowImage, WindowFeature
from app.tasks import (
    import_csv_task, generate_windows_task, generate_labels_task,
    generate_window_images_task, generate_window_features_task
)

router = APIRouter(prefix="/ui", tags=["ui"])

# Feature display names (Japanese)
FEATURE_DISPLAY_NAMES = {
    "sma5_slope_5": "SMA5 の傾き(5本)",
    "sma20_slope_5": "SMA20 の傾き(5本)",
    "sma20_slope_20": "SMA20 の傾き(20本)",
    "sma60_slope_20": "SMA60 の傾き(20本)",
    "close_to_sma20": "終値 - SMA20 乖離",
    "spread_5_20": "SMA5 と SMA20 の差",
    "spread_20_60": "SMA20 と SMA60 の差",
    "atr14": "ATR(14)",
    "vol_mean": "平均出来高",
}

# Get the templates directory path
templates_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")
templates = Jinja2Templates(directory=templates_dir)


def get_dataset_response(dataset: Dataset, db: Session):
    """Helper to build dataset response with stats."""
    bar_count = db.query(func.count()).select_from(Bar).filter(Bar.dataset_id == dataset.id).scalar()
    min_ts = db.query(func.min(Bar.ts)).filter(Bar.dataset_id == dataset.id).scalar()
    max_ts = db.query(func.max(Bar.ts)).filter(Bar.dataset_id == dataset.id).scalar()
    return {
        "id": dataset.id,
        "name": dataset.name,
        "description": dataset.description,
        "timezone": dataset.timezone,
        "created_at": dataset.created_at,
        "updated_at": dataset.updated_at,
        "bar_count": bar_count,
        "start_ts": min_ts,
        "end_ts": max_ts,
    }


def get_windows_stats(dataset_id: int, db: Session):
    """Get windows statistics for a dataset."""
    total = db.query(func.count(Window.id)).filter(Window.dataset_id == dataset_id).scalar() or 0
    lookback_n = db.query(Window.lookback_n).filter(Window.dataset_id == dataset_id).first()
    min_start = db.query(func.min(Window.start_ts)).filter(Window.dataset_id == dataset_id).scalar()
    max_end = db.query(func.max(Window.end_ts)).filter(Window.dataset_id == dataset_id).scalar()

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

    return {
        "total_windows": total,
        "lookback_n": lookback_n[0] if lookback_n else None,
        "earliest_start": min_start,
        "latest_end": max_end,
        "generated_count": generated_count,
        "generation_start_ts": generation_start_ts,
        "generation_end_ts": generation_end_ts,
    }


def get_labels_stats(dataset_id: int, db: Session):
    """Get labels statistics for a dataset."""
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

    return {
        "total_labels": total,
        "tp_hit_count": tp_hit,
        "sl_hit_count": sl_hit,
        "neither_count": neither,
        "tp_hit_ratio": tp_hit / total if total > 0 else 0,
        "sl_hit_ratio": sl_hit / total if total > 0 else 0,
        "earliest_bar_ts": earliest_bar_ts,
        "latest_bar_ts": latest_bar_ts,
        "generated_count": generated_count,
        "generation_start_ts": generation_start_ts,
        "generation_end_ts": generation_end_ts,
    }


def get_images_stats(dataset_id: int, db: Session):
    """Get images statistics for a dataset."""
    total = db.query(func.count(WindowImage.id)).filter(WindowImage.dataset_id == dataset_id).scalar() or 0
    latest_created = db.query(func.max(WindowImage.created_at)).filter(WindowImage.dataset_id == dataset_id).scalar()

    latest_job = (
        db.query(Job)
        .filter(Job.dataset_id == dataset_id, Job.job_type == "images", Job.status == "completed")
        .order_by(Job.updated_at.desc())
        .first()
    )

    generated_count = None
    skipped_count = None
    if latest_job and latest_job.result:
        result = json.loads(latest_job.result)
        generated_count = result.get("generated_count")
        skipped_count = result.get("skipped_count")

    return {
        "total_images": total,
        "latest_created_at": latest_created,
        "generated_count": generated_count,
        "skipped_count": skipped_count,
    }


def get_features_stats(dataset_id: int, db: Session):
    """Get features statistics for a dataset."""
    total = db.query(func.count(WindowFeature.id)).filter(WindowFeature.dataset_id == dataset_id).scalar() or 0
    latest_created = db.query(func.max(WindowFeature.created_at)).filter(WindowFeature.dataset_id == dataset_id).scalar()

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

    return {
        "total_features": total,
        "latest_created_at": latest_created,
        "generated_count": generated_count,
        "skipped_count": skipped_count,
    }


def parse_optional_datetime(value: str) -> Optional[datetime]:
    """Parse optional ISO8601 datetime string."""
    if not value or value.strip() == "":
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# === Routes ===

@router.get("", response_class=HTMLResponse)
def ui_index(request: Request):
    """UI Home page."""
    return templates.TemplateResponse("ui_index.html", {"request": request})


@router.get("/datasets", response_class=HTMLResponse)
def ui_datasets(request: Request, db: Session = Depends(get_db)):
    """List all datasets."""
    datasets = db.query(Dataset).all()
    dataset_list = [get_dataset_response(ds, db) for ds in datasets]
    return templates.TemplateResponse("ui_datasets.html", {
        "request": request,
        "datasets": dataset_list,
    })


@router.get("/datasets/{dataset_id}", response_class=HTMLResponse)
def ui_dataset_detail(request: Request, dataset_id: int, db: Session = Depends(get_db)):
    """Dataset detail page with stats and generation forms."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        return templates.TemplateResponse("ui_datasets.html", {
            "request": request,
            "datasets": [],
            "error": f"Dataset {dataset_id} not found",
        })

    dataset_data = get_dataset_response(dataset, db)
    windows_stats = get_windows_stats(dataset_id, db)
    labels_stats = get_labels_stats(dataset_id, db)
    images_stats = get_images_stats(dataset_id, db)
    features_stats = get_features_stats(dataset_id, db)

    return templates.TemplateResponse("ui_dataset_detail.html", {
        "request": request,
        "dataset": dataset_data,
        "windows_stats": windows_stats,
        "labels_stats": labels_stats,
        "images_stats": images_stats,
        "features_stats": features_stats,
    })


@router.get("/import", response_class=HTMLResponse)
def ui_import_form(request: Request):
    """CSV import form."""
    return templates.TemplateResponse("ui_import.html", {"request": request})


@router.post("/import")
async def ui_import_submit(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(...),
    timezone: str = Form("Asia/Tokyo"),
    timeframe: str = Form("M1"),
    db: Session = Depends(get_db),
):
    """Handle CSV import submission."""
    try:
        # Save uploaded file temporarily
        data_dir = "/app/data"
        os.makedirs(data_dir, exist_ok=True)

        file_path = os.path.join(data_dir, f"upload_{name}_{os.urandom(4).hex()}.csv")
        content = await file.read()
        with open(file_path, "wb") as f:
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
            description=None,
        )

        return RedirectResponse(url=f"/ui/jobs/{job.id}", status_code=303)

    except Exception as e:
        return templates.TemplateResponse("ui_import.html", {
            "request": request,
            "error": str(e),
        })


@router.post("/datasets/{dataset_id}/windows")
def ui_create_windows(
    request: Request,
    dataset_id: int,
    lookback_n: int = Form(128),
    step: int = Form(1),
    limit: Optional[str] = Form(None),
    start_ts: Optional[str] = Form(None),
    end_ts: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Create windows generation job."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        return RedirectResponse(url="/ui/datasets", status_code=303)

    try:
        limit_val = int(limit) if limit and limit.strip() else None
        start_ts_val = parse_optional_datetime(start_ts) if start_ts else None
        end_ts_val = parse_optional_datetime(end_ts) if end_ts else None

        params = {
            "lookback_n": lookback_n,
            "step": step,
            "limit": limit_val,
            "start_ts": start_ts_val.isoformat() if start_ts_val else None,
            "end_ts": end_ts_val.isoformat() if end_ts_val else None,
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
            lookback_n=lookback_n,
            step=step,
            limit=limit_val,
            start_ts=start_ts_val.isoformat() if start_ts_val else None,
            end_ts=end_ts_val.isoformat() if end_ts_val else None,
        )

        return RedirectResponse(url=f"/ui/jobs/{job.id}", status_code=303)

    except Exception as e:
        dataset_data = get_dataset_response(dataset, db)
        windows_stats = get_windows_stats(dataset_id, db)
        labels_stats = get_labels_stats(dataset_id, db)
        return templates.TemplateResponse("ui_dataset_detail.html", {
            "request": request,
            "dataset": dataset_data,
            "windows_stats": windows_stats,
            "labels_stats": labels_stats,
            "error": str(e),
        })


@router.post("/datasets/{dataset_id}/labels")
def ui_create_labels(
    request: Request,
    dataset_id: int,
    lookahead_m: int = Form(32),
    tp_r: float = Form(1.0),
    sl_r: float = Form(1.0),
    limit: Optional[str] = Form(None),
    start_ts: Optional[str] = Form(None),
    end_ts: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Create labels generation job."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        return RedirectResponse(url="/ui/datasets", status_code=303)

    try:
        limit_val = int(limit) if limit and limit.strip() else None
        start_ts_val = parse_optional_datetime(start_ts) if start_ts else None
        end_ts_val = parse_optional_datetime(end_ts) if end_ts else None

        params = {
            "lookahead_m": lookahead_m,
            "tp_r": tp_r,
            "sl_r": sl_r,
            "limit": limit_val,
            "start_ts": start_ts_val.isoformat() if start_ts_val else None,
            "end_ts": end_ts_val.isoformat() if end_ts_val else None,
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
            lookahead_m=lookahead_m,
            tp_r=tp_r,
            sl_r=sl_r,
            limit=limit_val,
            start_ts=start_ts_val.isoformat() if start_ts_val else None,
            end_ts=end_ts_val.isoformat() if end_ts_val else None,
        )

        return RedirectResponse(url=f"/ui/jobs/{job.id}", status_code=303)

    except Exception as e:
        dataset_data = get_dataset_response(dataset, db)
        windows_stats = get_windows_stats(dataset_id, db)
        labels_stats = get_labels_stats(dataset_id, db)
        return templates.TemplateResponse("ui_dataset_detail.html", {
            "request": request,
            "dataset": dataset_data,
            "windows_stats": windows_stats,
            "labels_stats": labels_stats,
            "error": str(e),
        })


@router.post("/datasets/{dataset_id}/images")
def ui_create_images(
    request: Request,
    dataset_id: int,
    lookback_n: int = Form(128),
    ma_periods: str = Form("5,20,60"),
    limit: Optional[str] = Form(None),
    start_ts: Optional[str] = Form(None),
    end_ts: Optional[str] = Form(None),
    overwrite: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Create image generation job."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        return RedirectResponse(url="/ui/datasets", status_code=303)

    try:
        limit_val = int(limit) if limit and limit.strip() else None
        start_ts_val = parse_optional_datetime(start_ts) if start_ts else None
        end_ts_val = parse_optional_datetime(end_ts) if end_ts else None
        ma_list = [int(p.strip()) for p in ma_periods.split(",") if p.strip()]
        overwrite_flag = overwrite == "on"

        params = {
            "lookback_n": lookback_n,
            "ma_periods": ma_list,
            "limit": limit_val,
            "start_ts": start_ts_val.isoformat() if start_ts_val else None,
            "end_ts": end_ts_val.isoformat() if end_ts_val else None,
            "overwrite": overwrite_flag,
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
            lookback_n=lookback_n,
            ma_periods=ma_list,
            limit=limit_val,
            start_ts=start_ts_val.isoformat() if start_ts_val else None,
            end_ts=end_ts_val.isoformat() if end_ts_val else None,
            overwrite=overwrite_flag,
        )

        return RedirectResponse(url=f"/ui/jobs/{job.id}", status_code=303)

    except Exception as e:
        dataset_data = get_dataset_response(dataset, db)
        windows_stats_data = get_windows_stats(dataset_id, db)
        labels_stats_data = get_labels_stats(dataset_id, db)
        images_stats_data = get_images_stats(dataset_id, db)
        return templates.TemplateResponse("ui_dataset_detail.html", {
            "request": request,
            "dataset": dataset_data,
            "windows_stats": windows_stats_data,
            "labels_stats": labels_stats_data,
            "images_stats": images_stats_data,
            "error": str(e),
        })


@router.post("/datasets/{dataset_id}/features")
def ui_create_features(
    request: Request,
    dataset_id: int,
    lookback_n: int = Form(128),
    limit: Optional[str] = Form(None),
    start_ts: Optional[str] = Form(None),
    end_ts: Optional[str] = Form(None),
    overwrite: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Create feature generation job."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        return RedirectResponse(url="/ui/datasets", status_code=303)

    try:
        limit_val = int(limit) if limit and limit.strip() else None
        start_ts_val = parse_optional_datetime(start_ts) if start_ts else None
        end_ts_val = parse_optional_datetime(end_ts) if end_ts else None
        overwrite_flag = overwrite == "on"

        params = {
            "lookback_n": lookback_n,
            "limit": limit_val,
            "start_ts": start_ts_val.isoformat() if start_ts_val else None,
            "end_ts": end_ts_val.isoformat() if end_ts_val else None,
            "overwrite": overwrite_flag,
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
            lookback_n=lookback_n,
            limit=limit_val,
            start_ts=start_ts_val.isoformat() if start_ts_val else None,
            end_ts=end_ts_val.isoformat() if end_ts_val else None,
            overwrite=overwrite_flag,
        )

        return RedirectResponse(url=f"/ui/jobs/{job.id}", status_code=303)

    except Exception as e:
        dataset_data = get_dataset_response(dataset, db)
        windows_stats_data = get_windows_stats(dataset_id, db)
        labels_stats_data = get_labels_stats(dataset_id, db)
        images_stats_data = get_images_stats(dataset_id, db)
        features_stats_data = get_features_stats(dataset_id, db)
        return templates.TemplateResponse("ui_dataset_detail.html", {
            "request": request,
            "dataset": dataset_data,
            "windows_stats": windows_stats_data,
            "labels_stats": labels_stats_data,
            "images_stats": images_stats_data,
            "features_stats": features_stats_data,
            "error": str(e),
        })


@router.get("/datasets/{dataset_id}/insights", response_class=HTMLResponse)
def ui_insights(
    request: Request,
    dataset_id: int,
    db: Session = Depends(get_db),
):
    """Insights page showing feature ranking and threshold suggestions."""
    import math

    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        return RedirectResponse(url="/ui/datasets", status_code=303)

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

    feature_ranking = []
    for name in feature_names:
        tp_vals = tp_features[name]
        sl_vals = sl_features[name]

        if not tp_vals or not sl_vals:
            continue

        tp_mean = sum(tp_vals) / len(tp_vals)
        sl_mean = sum(sl_vals) / len(sl_vals)
        diff = tp_mean - sl_mean

        tp_var = sum((v - tp_mean) ** 2 for v in tp_vals) / len(tp_vals) if len(tp_vals) > 1 else 0
        sl_var = sum((v - sl_mean) ** 2 for v in sl_vals) / len(sl_vals) if len(sl_vals) > 1 else 0
        pooled_std = math.sqrt((tp_var + sl_var) / 2) if (tp_var + sl_var) > 0 else 1e-10
        effect_size = abs(diff) / pooled_std

        direction = "higher_is_tp" if diff > 0 else "lower_is_tp"

        feature_ranking.append({
            "feature_name": name,
            "tp_mean": round(tp_mean, 6),
            "sl_mean": round(sl_mean, 6),
            "diff": round(diff, 6),
            "effect_size": round(effect_size, 4),
            "direction": direction,
        })

    feature_ranking.sort(key=lambda x: x["effect_size"], reverse=True)

    # Threshold suggestions
    threshold_suggestions = []
    base_precision = tp_count / (tp_count + sl_count) if (tp_count + sl_count) > 0 else 0

    for item in feature_ranking[:5]:
        name = item["feature_name"]
        all_tp = tp_features[name]
        all_sl = sl_features[name]

        if item["direction"] == "higher_is_tp":
            all_vals = sorted(all_tp + all_sl)
            for pct in [0.5, 0.6, 0.7]:
                idx = int(len(all_vals) * pct)
                threshold = all_vals[min(idx, len(all_vals) - 1)]
                tp_above = sum(1 for v in all_tp if v > threshold)
                sl_above = sum(1 for v in all_sl if v > threshold)
                total_above = tp_above + sl_above
                if total_above > 0:
                    precision = tp_above / total_above
                    if precision > base_precision:
                        threshold_suggestions.append({
                            "feature_name": name,
                            "operator": ">",
                            "threshold": round(threshold, 6),
                            "tp_count": tp_above,
                            "sl_count": sl_above,
                            "precision": round(precision, 4),
                        })
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
                    if precision > base_precision:
                        threshold_suggestions.append({
                            "feature_name": name,
                            "operator": "<",
                            "threshold": round(threshold, 6),
                            "tp_count": tp_below,
                            "sl_count": sl_below,
                            "precision": round(precision, 4),
                        })
                        break

    return templates.TemplateResponse("ui_insights.html", {
        "request": request,
        "dataset": {"id": dataset_id, "name": dataset.name},
        "total_features": total_features,
        "tp_count": tp_count,
        "sl_count": sl_count,
        "neither_count": neither_count,
        "base_precision": round(base_precision, 4),
        "feature_ranking": feature_ranking,
        "threshold_suggestions": threshold_suggestions,
        "feat_names": FEATURE_DISPLAY_NAMES,
    })


FEATURE_COLUMNS = {
    "sma5_slope_5": WindowFeature.sma5_slope_5,
    "sma20_slope_5": WindowFeature.sma20_slope_5,
    "sma20_slope_20": WindowFeature.sma20_slope_20,
    "sma60_slope_20": WindowFeature.sma60_slope_20,
    "close_to_sma20": WindowFeature.close_to_sma20,
    "spread_5_20": WindowFeature.spread_5_20,
    "spread_20_60": WindowFeature.spread_20_60,
    "atr14": WindowFeature.atr14,
    "vol_mean": WindowFeature.vol_mean,
}


@router.get("/datasets/{dataset_id}/images", response_class=HTMLResponse)
def ui_image_gallery(
    request: Request,
    dataset_id: int,
    page: int = 1,
    label: str = "all",
    feat: Optional[str] = None,
    op: Optional[str] = None,
    thr: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Image gallery page with optional feature filter."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        return RedirectResponse(url="/ui/datasets", status_code=303)

    page_size = 50

    # Parse feature filter
    feat_filter = None
    if feat and op and thr and feat in FEATURE_COLUMNS:
        try:
            thr_val = float(thr)
            col = FEATURE_COLUMNS[feat]
            if op == "gt":
                feat_filter = col > thr_val
            elif op == "lt":
                feat_filter = col < thr_val
        except ValueError:
            pass

    # Build query
    query = (
        db.query(WindowImage, Window, Label.result)
        .join(Window, WindowImage.window_id == Window.id)
        .outerjoin(Label, (Label.dataset_id == dataset_id) & (Label.bar_ts == Window.end_ts))
        .filter(WindowImage.dataset_id == dataset_id)
    )

    if label and label != "all":
        query = query.filter(Label.result == label)

    if feat_filter is not None:
        query = query.join(WindowFeature, WindowFeature.window_id == Window.id).filter(feat_filter)

    # Count
    count_q = (
        db.query(func.count(WindowImage.id))
        .join(Window, WindowImage.window_id == Window.id)
        .filter(WindowImage.dataset_id == dataset_id)
    )
    if label and label != "all":
        count_q = count_q.join(Label, (Label.dataset_id == dataset_id) & (Label.bar_ts == Window.end_ts)).filter(Label.result == label)
    if feat_filter is not None:
        count_q = count_q.join(WindowFeature, WindowFeature.window_id == Window.id).filter(feat_filter)
    total = count_q.scalar() or 0

    # Paginate
    offset = (page - 1) * page_size
    rows = (
        query
        .order_by(Window.end_ts.desc())
        .offset(offset)
        .limit(page_size)
        .all()
    )

    images = []
    for wi, window, label_result in rows:
        images.append({
            "id": wi.id,
            "window_id": wi.window_id,
            "end_ts": window.end_ts,
            "image_url": f"/images/{wi.id}/raw",
            "label_result": label_result,
        })

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1

    # Build extra query string for feature filter
    feat_qs = ""
    if feat and op and thr:
        feat_qs = f"&feat={feat}&op={op}&thr={thr}"

    # Resolve display name for active feature filter
    feat_display = FEATURE_DISPLAY_NAMES.get(feat, feat) if feat else ""

    return templates.TemplateResponse("ui_image_gallery.html", {
        "request": request,
        "dataset": {"id": dataset_id, "name": dataset.name},
        "images": images,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "label": label,
        "feat": feat or "",
        "feat_display": feat_display,
        "op": op or "",
        "thr": thr or "",
        "feat_qs": feat_qs,
    })


@router.get("/datasets/{dataset_id}/images/{image_id}", response_class=HTMLResponse)
def ui_image_detail(
    request: Request,
    dataset_id: int,
    image_id: int,
    label: str = "all",
    db: Session = Depends(get_db),
):
    """Image detail page."""
    window_image = db.query(WindowImage).filter(WindowImage.id == image_id).first()
    if not window_image:
        return RedirectResponse(url=f"/ui/datasets/{dataset_id}/images", status_code=303)

    window = db.query(Window).filter(Window.id == window_image.window_id).first()

    # Get label
    label_obj = db.query(Label).filter(
        Label.dataset_id == dataset_id,
        Label.bar_ts == window.end_ts
    ).first()

    # Get prev/next image ids with same filter
    nav_query = (
        db.query(WindowImage.id, Window.end_ts)
        .join(Window, WindowImage.window_id == Window.id)
        .filter(WindowImage.dataset_id == dataset_id)
    )
    if label and label != "all":
        nav_query = nav_query.join(Label, (Label.dataset_id == dataset_id) & (Label.bar_ts == Window.end_ts)).filter(Label.result == label)

    # Previous (earlier end_ts)
    prev_img = (
        nav_query.filter(Window.end_ts > window.end_ts)
        .order_by(Window.end_ts.asc())
        .first()
    )
    # Next (later end_ts)
    next_img = (
        nav_query.filter(Window.end_ts < window.end_ts)
        .order_by(Window.end_ts.desc())
        .first()
    )

    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()

    return templates.TemplateResponse("ui_image_detail.html", {
        "request": request,
        "dataset": {"id": dataset_id, "name": dataset.name if dataset else ""},
        "image": {
            "id": window_image.id,
            "window_id": window_image.window_id,
            "image_key": window_image.image_key,
            "width": window_image.width,
            "height": window_image.height,
            "ma_periods": window_image.ma_periods,
            "created_at": window_image.created_at,
            "image_url": f"/images/{window_image.id}/raw",
        },
        "window": {
            "start_ts": window.start_ts,
            "end_ts": window.end_ts,
            "lookback_n": window.lookback_n,
        },
        "label_result": label_obj.result if label_obj else None,
        "prev_id": prev_img[0] if prev_img else None,
        "next_id": next_img[0] if next_img else None,
        "current_label": label,
    })


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def ui_job_detail(request: Request, job_id: int, db: Session = Depends(get_db)):
    """Job detail page with auto-refresh."""
    job = db.query(Job).filter(Job.id == job_id).first()
    if not job:
        return templates.TemplateResponse("ui_index.html", {
            "request": request,
            "error": f"Job {job_id} not found",
        })

    return templates.TemplateResponse("ui_job_detail.html", {
        "request": request,
        "job": job,
    })
