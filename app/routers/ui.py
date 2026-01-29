import os
import json
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Request, Depends, Form, UploadFile, File, Body
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import Dataset, Bar, Job, Window, Label, WindowImage, WindowFeature, Timeframe, Strategy, BacktestRun, Trade, Instrument, Signal, EntryPoint, MLModel, MLScore, TradeImage
from app.services.image_generator import generate_candlestick_image
from app.services.minio_client import upload_image, get_image, image_exists
from app.tasks import (
    import_csv_task, generate_windows_task, generate_labels_task,
    generate_window_images_task, generate_window_features_task, resample_bars_task, backtest_task,
    golden_cross_task
)
from app.tasks_ml import ml_train_task, ml_infer_task

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
templates.env.filters["from_json"] = lambda s: json.loads(s) if s else {}


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


def get_timeframe_info(dataset_id: int, db: Session):
    """Get timeframe breakdown for a dataset."""
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
        {
            "name": row.name,
            "minutes": row.minutes,
            "bar_count": row.bar_count,
            "start_ts": row.start_ts,
            "end_ts": row.end_ts,
        }
        for row in rows
    ]


def get_signals_stats(dataset_id: int, db: Session):
    """Get signals statistics for a dataset."""
    total = db.query(func.count(Signal.id)).filter(Signal.dataset_id == dataset_id).scalar() or 0
    signal_types = (
        db.query(Signal.signal_type, func.count(Signal.id))
        .filter(Signal.dataset_id == dataset_id)
        .group_by(Signal.signal_type)
        .all()
    )
    latest_job = (
        db.query(Job)
        .filter(Job.dataset_id == dataset_id, Job.job_type == "signals", Job.status == "completed")
        .order_by(Job.updated_at.desc())
        .first()
    )
    generated_count = None
    if latest_job and latest_job.result:
        result = json.loads(latest_job.result)
        generated_count = result.get("signal_count")

    return {
        "total_signals": total,
        "signal_types": [{"type": t, "count": c} for t, c in signal_types],
        "generated_count": generated_count,
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
    signals_stats = get_signals_stats(dataset_id, db)
    timeframes = get_timeframe_info(dataset_id, db)

    return templates.TemplateResponse("ui_dataset_detail.html", {
        "request": request,
        "dataset": dataset_data,
        "windows_stats": windows_stats,
        "labels_stats": labels_stats,
        "images_stats": images_stats,
        "features_stats": features_stats,
        "signals_stats": signals_stats,
        "timeframes": timeframes,
    })


@router.get("/import", response_class=HTMLResponse)
def ui_import_form(request: Request, db: Session = Depends(get_db)):
    """CSV import form."""
    datasets = db.query(Dataset).all()
    dataset_list = [{"id": ds.id, "name": ds.name, "timezone": ds.timezone} for ds in datasets]
    return templates.TemplateResponse("ui_import.html", {
        "request": request,
        "datasets": dataset_list,
    })


@router.post("/import")
async def ui_import_submit(
    request: Request,
    file: UploadFile = File(...),
    name: Optional[str] = Form(None),
    timezone: str = Form("Asia/Tokyo"),
    timeframe: str = Form("M1"),
    append_mode: Optional[str] = Form(None),
    dataset_id: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Handle CSV import submission."""
    try:
        is_append = append_mode == "on" and dataset_id and dataset_id.strip()
        target_dataset_id = int(dataset_id) if is_append else None

        if is_append:
            dataset = db.query(Dataset).filter(Dataset.id == target_dataset_id).first()
            if not dataset:
                raise ValueError(f"Dataset {target_dataset_id} not found")
            if dataset.timezone != timezone:
                raise ValueError(
                    f"タイムゾーン不一致: データセット={dataset.timezone}, 指定={timezone}"
                )
            label = f"append_{target_dataset_id}"
        else:
            if not name or not name.strip():
                raise ValueError("新規作成の場合はデータセット名が必要です")
            label = name

        # Save uploaded file temporarily
        data_dir = "/app/data"
        os.makedirs(data_dir, exist_ok=True)

        file_path = os.path.join(data_dir, f"upload_{label}_{os.urandom(4).hex()}.csv")
        content = await file.read()
        with open(file_path, "wb") as f:
            f.write(content)

        # Create job
        job = Job(
            dataset_id=target_dataset_id,
            job_type="import",
            status="pending",
            params=json.dumps({
                "name": name,
                "timezone": timezone,
                "timeframe": timeframe,
                "file_path": file_path,
                "dataset_id": target_dataset_id,
                "append_mode": is_append,
            }),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        # Start Celery task
        import_csv_task.delay(
            job_id=job.id,
            file_path=file_path,
            dataset_name=name or "",
            timezone_str=timezone,
            timeframe_name=timeframe,
            description=None,
            dataset_id=target_dataset_id,
        )

        return RedirectResponse(url=f"/ui/jobs/{job.id}", status_code=303)

    except Exception as e:
        datasets = db.query(Dataset).all()
        dataset_list = [{"id": ds.id, "name": ds.name, "timezone": ds.timezone} for ds in datasets]
        return templates.TemplateResponse("ui_import.html", {
            "request": request,
            "datasets": dataset_list,
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


@router.post("/datasets/{dataset_id}/resample")
def ui_create_resample(
    request: Request,
    dataset_id: int,
    from_tf: str = Form("M1"),
    to_tf: str = Form("H1"),
    limit: Optional[str] = Form(None),
    start_ts: Optional[str] = Form(None),
    end_ts: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Create resample job."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        return RedirectResponse(url="/ui/datasets", status_code=303)

    try:
        limit_val = int(limit) if limit and limit.strip() else None
        start_ts_val = parse_optional_datetime(start_ts) if start_ts else None
        end_ts_val = parse_optional_datetime(end_ts) if end_ts else None

        params = {
            "from_tf": from_tf,
            "to_tf": to_tf,
            "limit": limit_val,
            "start_ts": start_ts_val.isoformat() if start_ts_val else None,
            "end_ts": end_ts_val.isoformat() if end_ts_val else None,
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
            from_tf=from_tf,
            to_tf=to_tf,
            start_ts=start_ts_val.isoformat() if start_ts_val else None,
            end_ts=end_ts_val.isoformat() if end_ts_val else None,
            limit=limit_val,
        )

        return RedirectResponse(url=f"/ui/jobs/{job.id}", status_code=303)

    except Exception as e:
        dataset_data = get_dataset_response(dataset, db)
        return templates.TemplateResponse("ui_dataset_detail.html", {
            "request": request,
            "dataset": dataset_data,
            "windows_stats": get_windows_stats(dataset_id, db),
            "labels_stats": get_labels_stats(dataset_id, db),
            "images_stats": get_images_stats(dataset_id, db),
            "features_stats": get_features_stats(dataset_id, db),
            "timeframes": get_timeframe_info(dataset_id, db),
            "error": str(e),
        })


@router.post("/datasets/{dataset_id}/signals/golden-cross")
def ui_create_golden_cross(
    request: Request,
    dataset_id: int,
    timeframe: str = Form("H1"),
    fast_ma: int = Form(20),
    slow_ma: int = Form(60),
    limit: Optional[str] = Form(None),
    start_ts: Optional[str] = Form(None),
    end_ts: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Create golden cross signal generation job."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        return RedirectResponse(url="/ui/datasets", status_code=303)

    try:
        limit_val = int(limit) if limit and limit.strip() else None
        start_ts_val = parse_optional_datetime(start_ts) if start_ts else None
        end_ts_val = parse_optional_datetime(end_ts) if end_ts else None

        params = {
            "timeframe": timeframe,
            "fast_ma": fast_ma,
            "slow_ma": slow_ma,
            "limit": limit_val,
            "start_ts": start_ts_val.isoformat() if start_ts_val else None,
            "end_ts": end_ts_val.isoformat() if end_ts_val else None,
        }

        job = Job(
            dataset_id=dataset_id,
            job_type="signals",
            status="pending",
            params=json.dumps(params),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        golden_cross_task.delay(
            job_id=job.id,
            dataset_id=dataset_id,
            timeframe_name=timeframe,
            fast_ma=fast_ma,
            slow_ma=slow_ma,
            start_ts=start_ts_val.isoformat() if start_ts_val else None,
            end_ts=end_ts_val.isoformat() if end_ts_val else None,
            limit=limit_val,
        )

        return RedirectResponse(url=f"/ui/jobs/{job.id}", status_code=303)

    except Exception as e:
        dataset_data = get_dataset_response(dataset, db)
        return templates.TemplateResponse("ui_dataset_detail.html", {
            "request": request,
            "dataset": dataset_data,
            "windows_stats": get_windows_stats(dataset_id, db),
            "labels_stats": get_labels_stats(dataset_id, db),
            "images_stats": get_images_stats(dataset_id, db),
            "features_stats": get_features_stats(dataset_id, db),
            "signals_stats": get_signals_stats(dataset_id, db),
            "timeframes": get_timeframe_info(dataset_id, db),
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
    sort: Optional[str] = None,  # "score_desc", "score_asc", or None (time desc)
    model_id: Optional[int] = None,  # Required when sort by score
    db: Session = Depends(get_db),
):
    """Image gallery page with optional feature filter and score sorting."""
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

    # Check if sorting by score
    sort_by_score = sort in ("score_desc", "score_asc") and model_id is not None

    # Build query
    if sort_by_score:
        # Join with MLScore for score-based sorting
        query = (
            db.query(WindowImage, Window, Label.result, MLScore.score)
            .join(Window, WindowImage.window_id == Window.id)
            .join(MLScore, MLScore.window_id == Window.id)
            .outerjoin(Label, (Label.dataset_id == dataset_id) & (Label.bar_ts == Window.end_ts))
            .filter(WindowImage.dataset_id == dataset_id)
            .filter(MLScore.model_id == model_id)
        )
    else:
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
    if sort_by_score:
        count_q = (
            db.query(func.count(WindowImage.id))
            .join(Window, WindowImage.window_id == Window.id)
            .join(MLScore, MLScore.window_id == Window.id)
            .filter(WindowImage.dataset_id == dataset_id)
            .filter(MLScore.model_id == model_id)
        )
    else:
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

    # Apply sorting
    if sort_by_score:
        if sort == "score_desc":
            query = query.order_by(MLScore.score.desc())
        else:
            query = query.order_by(MLScore.score.asc())
    else:
        query = query.order_by(Window.end_ts.desc())

    # Paginate
    offset = (page - 1) * page_size
    rows = query.offset(offset).limit(page_size).all()

    images = []
    if sort_by_score:
        for wi, window, label_result, score in rows:
            images.append({
                "id": wi.id,
                "window_id": wi.window_id,
                "end_ts": window.end_ts,
                "image_url": f"/images/{wi.id}/raw",
                "label_result": label_result,
                "score": round(score, 4) if score is not None else None,
            })
    else:
        for wi, window, label_result in rows:
            images.append({
                "id": wi.id,
                "window_id": wi.window_id,
                "end_ts": window.end_ts,
                "image_url": f"/images/{wi.id}/raw",
                "label_result": label_result,
                "score": None,
            })

    total_pages = (total + page_size - 1) // page_size if total > 0 else 1

    # Build extra query string for feature filter
    feat_qs = ""
    if feat and op and thr:
        feat_qs = f"&feat={feat}&op={op}&thr={thr}"

    # Resolve display name for active feature filter
    feat_display = FEATURE_DISPLAY_NAMES.get(feat, feat) if feat else ""

    # Get available models for sorting dropdown
    ml_models = db.query(MLModel).filter(MLModel.dataset_id == dataset_id).all()
    ml_model_list = [{"id": m.id, "name": m.name, "model_type": m.model_type} for m in ml_models]

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
        "sort": sort or "",
        "model_id": model_id,
        "ml_models": ml_model_list,
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


@router.get("/strategies", response_class=HTMLResponse)
def ui_strategies(request: Request, db: Session = Depends(get_db)):
    """Strategy list page with create form."""
    strategies = db.query(Strategy).order_by(Strategy.created_at.desc()).all()
    datasets = db.query(Dataset).all()
    dataset_list = [{"id": ds.id, "name": ds.name} for ds in datasets]
    timeframes = db.query(Timeframe).order_by(Timeframe.minutes).all()
    instruments = db.query(Instrument).all()
    return templates.TemplateResponse("ui_strategies.html", {
        "request": request,
        "strategies": strategies,
        "datasets": dataset_list,
        "timeframes": timeframes,
        "instruments": instruments,
    })


@router.post("/strategies")
def ui_create_strategy(
    request: Request,
    name: str = Form(...),
    dataset_id: int = Form(...),
    instrument_id: int = Form(...),
    timeframe_id: int = Form(...),
    side: str = Form("long"),
    session_start: str = Form("00:00"),
    session_end: str = Form("23:59"),
    weekdays: Optional[str] = Form(None),
    entry_timing: str = Form("close"),
    rule_json: str = Form("[]"),
    tp_type: str = Form("atr"),
    tp_value: float = Form(1.5),
    sl_type: str = Form("atr"),
    sl_value: float = Form(1.0),
    max_hold_bars: int = Form(100),
    cooldown_bars: int = Form(0),
    fee_pips: float = Form(0.0),
    spread_pips: float = Form(0.0),
    slippage_pips: float = Form(0.0),
    intrabar_fill_mode: str = Form("conservative"),
    htf_timeframe_id: Optional[str] = Form(None),
    htf_signal_type: Optional[str] = Form(None),
    htf_lookback_hours: Optional[str] = Form(None),
    require_htf_signal: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Create a strategy from form submission."""
    # weekdays comes from checkboxes: "0,1,2,3,4" or None
    if not weekdays or weekdays.strip() == "":
        weekdays = "0,1,2,3,4"

    # Parse HTF fields
    htf_tf_id = int(htf_timeframe_id) if htf_timeframe_id and htf_timeframe_id.strip() else None
    htf_sig = htf_signal_type.strip() if htf_signal_type and htf_signal_type.strip() else None
    htf_hours = int(htf_lookback_hours) if htf_lookback_hours and htf_lookback_hours.strip() else 24
    htf_required = require_htf_signal == "on"

    strategy = Strategy(
        name=name,
        dataset_id=dataset_id,
        instrument_id=instrument_id,
        timeframe_id=timeframe_id,
        side=side,
        session_start=session_start,
        session_end=session_end,
        weekdays=weekdays,
        entry_timing=entry_timing,
        rule_json=rule_json,
        tp_type=tp_type,
        tp_value=tp_value,
        sl_type=sl_type,
        sl_value=sl_value,
        max_hold_bars=max_hold_bars,
        cooldown_bars=cooldown_bars,
        fee_pips=fee_pips,
        spread_pips=spread_pips,
        slippage_pips=slippage_pips,
        intrabar_fill_mode=intrabar_fill_mode,
        htf_timeframe_id=htf_tf_id,
        htf_signal_type=htf_sig,
        htf_lookback_hours=htf_hours,
        htf_confirmed_only=True,
        require_htf_signal=htf_required,
        created_at=datetime.utcnow(),
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    return RedirectResponse(url=f"/ui/strategies/{strategy.id}", status_code=303)


@router.get("/strategies/{strategy_id}", response_class=HTMLResponse)
def ui_strategy_detail(request: Request, strategy_id: int, db: Session = Depends(get_db)):
    """Strategy detail page with run form and past runs."""
    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        return RedirectResponse(url="/ui/strategies", status_code=303)

    dataset = db.query(Dataset).filter(Dataset.id == strategy.dataset_id).first()
    instrument = db.query(Instrument).filter(Instrument.id == strategy.instrument_id).first()
    timeframe = db.query(Timeframe).filter(Timeframe.id == strategy.timeframe_id).first()
    htf_timeframe = db.query(Timeframe).filter(Timeframe.id == strategy.htf_timeframe_id).first() if strategy.htf_timeframe_id else None

    runs = (
        db.query(BacktestRun)
        .filter(BacktestRun.strategy_id == strategy_id)
        .order_by(BacktestRun.created_at.desc())
        .all()
    )

    # Parse rule_json for display
    try:
        rules = json.loads(strategy.rule_json)
    except Exception:
        rules = []

    return templates.TemplateResponse("ui_strategy_detail.html", {
        "request": request,
        "strategy": strategy,
        "dataset": dataset,
        "instrument": instrument,
        "timeframe": timeframe,
        "htf_timeframe": htf_timeframe,
        "runs": runs,
        "rules": rules,
        "feat_names": FEATURE_DISPLAY_NAMES,
    })


@router.post("/strategies/{strategy_id}/run")
def ui_run_backtest(
    request: Request,
    strategy_id: int,
    start_ts: Optional[str] = Form(None),
    end_ts: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Start a backtest run."""
    strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
    if not strategy:
        return RedirectResponse(url="/ui/strategies", status_code=303)

    start_ts_val = parse_optional_datetime(start_ts) if start_ts else None
    end_ts_val = parse_optional_datetime(end_ts) if end_ts else None

    job = Job(
        dataset_id=strategy.dataset_id,
        job_type="backtest",
        status="pending",
        params=json.dumps({
            "strategy_id": strategy_id,
            "start_ts": start_ts_val.isoformat() if start_ts_val else None,
            "end_ts": end_ts_val.isoformat() if end_ts_val else None,
        }),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    run = BacktestRun(
        strategy_id=strategy_id,
        job_id=job.id,
        start_ts=start_ts_val,
        end_ts=end_ts_val,
        status="pending",
        params_json=json.dumps({
            "start_ts": start_ts_val.isoformat() if start_ts_val else None,
            "end_ts": end_ts_val.isoformat() if end_ts_val else None,
        }),
        created_at=datetime.utcnow(),
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    backtest_task.delay(
        job_id=job.id,
        run_id=run.id,
        strategy_id=strategy_id,
        start_ts=start_ts_val.isoformat() if start_ts_val else None,
        end_ts=end_ts_val.isoformat() if end_ts_val else None,
    )

    return RedirectResponse(url=f"/ui/jobs/{job.id}", status_code=303)


@router.get("/runs/{run_id}", response_class=HTMLResponse)
def ui_run_detail(request: Request, run_id: int, db: Session = Depends(get_db)):
    """Run detail page with summary and trade list."""
    run = db.query(BacktestRun).filter(BacktestRun.id == run_id).first()
    if not run:
        return RedirectResponse(url="/ui/strategies", status_code=303)

    strategy = db.query(Strategy).filter(Strategy.id == run.strategy_id).first()
    dataset = db.query(Dataset).filter(Dataset.id == strategy.dataset_id).first() if strategy else None
    instrument = db.query(Instrument).filter(Instrument.id == strategy.instrument_id).first() if strategy else None
    timeframe = db.query(Timeframe).filter(Timeframe.id == strategy.timeframe_id).first() if strategy else None

    trades = db.query(Trade).filter(Trade.run_id == run_id).order_by(Trade.entry_ts).all()

    # Parse result summary
    result_summary = None
    if run.result_json:
        try:
            result_summary = json.loads(run.result_json)
        except Exception:
            pass

    return templates.TemplateResponse("ui_run_detail.html", {
        "request": request,
        "run": run,
        "strategy": strategy,
        "dataset": dataset,
        "instrument": instrument,
        "timeframe": timeframe,
        "trades": trades,
        "result": result_summary,
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


# === Lab Routes ===

@router.get("/lab", response_class=HTMLResponse)
def ui_lab(
    request: Request,
    dataset_id: Optional[int] = None,
    timeframe: str = "M1",
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Lab main page with interactive chart."""
    datasets = db.query(Dataset).all()
    dataset_list = [{"id": ds.id, "name": ds.name} for ds in datasets]
    timeframes = db.query(Timeframe).order_by(Timeframe.minutes).all()

    # Resolve instrument_id from dataset's bars
    instrument_id = None
    entry_points = []
    if dataset_id:
        first_bar = db.query(Bar.instrument_id).filter(Bar.dataset_id == dataset_id).first()
        if first_bar:
            instrument_id = first_bar[0]

        tf = db.query(Timeframe).filter(Timeframe.name == timeframe).first()
        if tf and instrument_id:
            ep_query = db.query(EntryPoint).filter(
                EntryPoint.dataset_id == dataset_id,
                EntryPoint.instrument_id == instrument_id,
                EntryPoint.timeframe_id == tf.id,
            ).order_by(EntryPoint.ts)
            entry_points = ep_query.all()

    return templates.TemplateResponse("ui_lab.html", {
        "request": request,
        "datasets": dataset_list,
        "timeframes": timeframes,
        "selected_dataset_id": dataset_id,
        "selected_timeframe": timeframe,
        "start_ts": start_ts or "",
        "end_ts": end_ts or "",
        "instrument_id": instrument_id,
        "entry_points": entry_points,
    })


@router.get("/lab/bars")
def ui_lab_bars(
    dataset_id: int,
    timeframe: str = "M1",
    start_ts: Optional[str] = None,
    end_ts: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """JSON API: bar data + MAs for chart."""
    tf = db.query(Timeframe).filter(Timeframe.name == timeframe).first()
    if not tf:
        return JSONResponse({"error": f"Timeframe {timeframe} not found"}, status_code=404)

    first_bar = db.query(Bar.instrument_id).filter(Bar.dataset_id == dataset_id).first()
    if not first_bar:
        return JSONResponse({"error": "No bars in dataset"}, status_code=404)
    instrument_id = first_bar[0]

    query = db.query(Bar).filter(
        Bar.dataset_id == dataset_id,
        Bar.instrument_id == instrument_id,
        Bar.timeframe_id == tf.id,
    )
    if start_ts:
        query = query.filter(Bar.ts >= parse_optional_datetime(start_ts))
    if end_ts:
        query = query.filter(Bar.ts <= parse_optional_datetime(end_ts))

    bars = query.order_by(Bar.ts).limit(3000).all()

    if not bars:
        return JSONResponse({"bars": [], "ma5": [], "ma20": [], "ma60": []})

    closes = [b.close for b in bars]

    def compute_ma(values, period):
        result = []
        for i in range(len(values)):
            if i < period - 1:
                result.append(None)
            else:
                result.append(sum(values[i - period + 1:i + 1]) / period)
        return result

    ma5 = compute_ma(closes, 5)
    ma20 = compute_ma(closes, 20)
    ma60 = compute_ma(closes, 60)

    bar_data = []
    ma5_data = []
    ma20_data = []
    ma60_data = []

    for i, b in enumerate(bars):
        # Convert to UNIX timestamp (UTC seconds) for lightweight-charts
        ts_unix = int(b.ts.replace(tzinfo=timezone.utc).timestamp())
        bar_data.append({
            "time": ts_unix,
            "open": b.open,
            "high": b.high,
            "low": b.low,
            "close": b.close,
        })
        if ma5[i] is not None:
            ma5_data.append({"time": ts_unix, "value": ma5[i]})
        if ma20[i] is not None:
            ma20_data.append({"time": ts_unix, "value": ma20[i]})
        if ma60[i] is not None:
            ma60_data.append({"time": ts_unix, "value": ma60[i]})

    return JSONResponse({
        "bars": bar_data,
        "ma5": ma5_data,
        "ma20": ma20_data,
        "ma60": ma60_data,
        "instrument_id": instrument_id,
        "timeframe_id": tf.id,
    })


@router.post("/lab/entries")
def ui_lab_create_entry(
    request: Request,
    data: dict = Body(...),
    db: Session = Depends(get_db),
):
    """AJAX: Create or update entry point."""
    dataset_id = data["dataset_id"]
    instrument_id = data["instrument_id"]
    timeframe_id = data["timeframe_id"]
    ts_unix = data["ts_unix"]  # UNIX seconds from lightweight-charts
    side = data["side"]
    label = data.get("label", "unknown")
    note = data.get("note")

    # Get timeframe for snapping
    tf = db.query(Timeframe).filter(Timeframe.id == timeframe_id).first()
    if not tf:
        return JSONResponse({"error": "Timeframe not found"}, status_code=400)

    # Snap to bar start time based on timeframe
    bucket_seconds = tf.minutes * 60
    snapped_unix = (ts_unix // bucket_seconds) * bucket_seconds

    # Convert to UTC datetime
    ts = datetime.fromtimestamp(snapped_unix, tz=timezone.utc)

    # Upsert: check existing
    existing = db.query(EntryPoint).filter(
        EntryPoint.dataset_id == dataset_id,
        EntryPoint.instrument_id == instrument_id,
        EntryPoint.timeframe_id == timeframe_id,
        EntryPoint.ts == ts,
        EntryPoint.side == side,
    ).first()

    if existing:
        existing.label = label
        if note is not None:
            existing.note = note
        db.commit()
        db.refresh(existing)
        return JSONResponse({"id": existing.id, "action": "updated", "ts_unix": snapped_unix})

    ep = EntryPoint(
        dataset_id=dataset_id,
        instrument_id=instrument_id,
        timeframe_id=timeframe_id,
        ts=ts,
        side=side,
        label=label,
        note=note,
    )
    db.add(ep)
    db.commit()
    db.refresh(ep)
    return JSONResponse({"id": ep.id, "action": "created", "ts_unix": snapped_unix})


@router.delete("/lab/entries/{entry_id}")
def ui_lab_delete_entry(entry_id: int, db: Session = Depends(get_db)):
    """AJAX: Delete entry point."""
    ep = db.query(EntryPoint).filter(EntryPoint.id == entry_id).first()
    if not ep:
        return JSONResponse({"error": "Not found"}, status_code=404)
    db.delete(ep)
    db.commit()
    return JSONResponse({"ok": True})


@router.get("/lab/entries", response_class=HTMLResponse)
def ui_lab_entries(
    request: Request,
    dataset_id: Optional[int] = None,
    side: Optional[str] = None,
    label: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Entry points list page."""
    datasets = db.query(Dataset).all()
    dataset_list = [{"id": ds.id, "name": ds.name} for ds in datasets]

    entries = []
    if dataset_id:
        query = db.query(EntryPoint).filter(EntryPoint.dataset_id == dataset_id)
        if side:
            query = query.filter(EntryPoint.side == side)
        if label:
            query = query.filter(EntryPoint.label == label)
        entries = query.order_by(EntryPoint.ts).all()

    return templates.TemplateResponse("ui_lab_entries.html", {
        "request": request,
        "datasets": dataset_list,
        "entries": entries,
        "selected_dataset_id": dataset_id,
        "selected_side": side or "",
        "selected_label": label or "",
    })


@router.get("/lab/suggest-rules", response_class=HTMLResponse)
def ui_lab_suggest_rules(
    request: Request,
    dataset_id: int,
    side: str = "long",
    db: Session = Depends(get_db),
):
    """Rule suggestion based on good vs bad entry points."""
    import math

    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        return RedirectResponse(url="/ui/lab", status_code=303)

    # Get good/bad entries
    good_entries = db.query(EntryPoint).filter(
        EntryPoint.dataset_id == dataset_id,
        EntryPoint.side == side,
        EntryPoint.label == "good",
    ).all()

    bad_entries = db.query(EntryPoint).filter(
        EntryPoint.dataset_id == dataset_id,
        EntryPoint.side == side,
        EntryPoint.label == "bad",
    ).all()

    feature_names = [
        "sma5_slope_5", "sma20_slope_5", "sma20_slope_20", "sma60_slope_20",
        "close_to_sma20", "spread_5_20", "spread_20_60", "atr14", "vol_mean",
    ]

    # Compute features for each entry via on-the-fly
    first_bar = db.query(Bar.instrument_id).filter(Bar.dataset_id == dataset_id).first()
    if not first_bar:
        return templates.TemplateResponse("ui_lab_suggest.html", {
            "request": request,
            "dataset": {"id": dataset_id, "name": dataset.name},
            "side": side,
            "good_count": 0,
            "bad_count": 0,
            "feature_ranking": [],
            "threshold_suggestions": [],
            "feat_names": FEATURE_DISPLAY_NAMES,
            "error": "No bars in dataset",
        })

    instrument_id = first_bar[0]

    def get_features_for_entries(entries):
        """Compute on-the-fly features for entry timestamps."""
        results = []
        for ep in entries:
            tf = db.query(Timeframe).filter(Timeframe.id == ep.timeframe_id).first()
            if not tf:
                continue

            # Load bars around this timestamp for feature computation
            LOOKBACK = 128
            bars = (
                db.query(Bar)
                .filter(
                    Bar.dataset_id == dataset_id,
                    Bar.instrument_id == instrument_id,
                    Bar.timeframe_id == tf.id,
                    Bar.ts <= ep.ts,
                )
                .order_by(Bar.ts.desc())
                .limit(LOOKBACK)
                .all()
            )
            bars.reverse()

            if len(bars) < LOOKBACK:
                continue

            closes = [b.close for b in bars]
            highs = [b.high for b in bars]
            lows = [b.low for b in bars]

            # Compute features (same as tasks.py compute_features_onthefly)
            idx = len(closes) - 1

            def sma(arr, period, at):
                return sum(arr[at - period + 1:at + 1]) / period

            sma5 = sma(closes, 5, idx)
            sma20 = sma(closes, 20, idx)
            sma60 = sma(closes, 60, idx)
            sma5_prev5 = sma(closes, 5, idx - 5)
            sma20_prev5 = sma(closes, 20, idx - 5)
            sma20_prev20 = sma(closes, 20, idx - 20)
            sma60_prev20 = sma(closes, 60, idx - 20)

            # ATR14
            trs = []
            for j in range(idx - 13, idx + 1):
                tr = max(
                    highs[j] - lows[j],
                    abs(highs[j] - closes[j - 1]),
                    abs(lows[j] - closes[j - 1]),
                )
                trs.append(tr)
            atr14 = sum(trs) / 14
            if atr14 < 1e-10:
                continue

            feats = {
                "sma5_slope_5": (sma5 - sma5_prev5) / atr14,
                "sma20_slope_5": (sma20 - sma20_prev5) / atr14,
                "sma20_slope_20": (sma20 - sma20_prev20) / atr14,
                "sma60_slope_20": (sma60 - sma60_prev20) / atr14,
                "close_to_sma20": (closes[idx] - sma20) / atr14,
                "spread_5_20": (sma5 - sma20) / atr14,
                "spread_20_60": (sma20 - sma60) / atr14,
                "atr14": atr14,
                "vol_mean": sum(b.volume for b in bars[-20:]) / 20,
            }
            results.append(feats)
        return results

    good_features = get_features_for_entries(good_entries)
    bad_features = get_features_for_entries(bad_entries)

    good_count = len(good_features)
    bad_count = len(bad_features)

    # Cohen's d ranking
    feature_ranking = []
    good_by_feat = {n: [f[n] for f in good_features] for n in feature_names}
    bad_by_feat = {n: [f[n] for f in bad_features] for n in feature_names}

    for name in feature_names:
        gv = good_by_feat[name]
        bv = bad_by_feat[name]
        if not gv or not bv:
            continue

        g_mean = sum(gv) / len(gv)
        b_mean = sum(bv) / len(bv)
        diff = g_mean - b_mean

        g_var = sum((v - g_mean) ** 2 for v in gv) / len(gv) if len(gv) > 1 else 0
        b_var = sum((v - b_mean) ** 2 for v in bv) / len(bv) if len(bv) > 1 else 0
        pooled_std = math.sqrt((g_var + b_var) / 2) if (g_var + b_var) > 0 else 1e-10
        effect_size = abs(diff) / pooled_std

        direction = "higher_is_good" if diff > 0 else "lower_is_good"

        feature_ranking.append({
            "feature_name": name,
            "good_mean": round(g_mean, 6),
            "bad_mean": round(b_mean, 6),
            "diff": round(diff, 6),
            "effect_size": round(effect_size, 4),
            "direction": direction,
        })

    feature_ranking.sort(key=lambda x: x["effect_size"], reverse=True)

    # Threshold suggestions
    threshold_suggestions = []
    base_precision = good_count / (good_count + bad_count) if (good_count + bad_count) > 0 else 0

    for item in feature_ranking[:5]:
        name = item["feature_name"]
        all_good = good_by_feat[name]
        all_bad = bad_by_feat[name]

        if item["direction"] == "higher_is_good":
            all_vals = sorted(all_good + all_bad)
            for pct in [0.5, 0.6, 0.7]:
                idx = int(len(all_vals) * pct)
                threshold = all_vals[min(idx, len(all_vals) - 1)]
                g_above = sum(1 for v in all_good if v > threshold)
                b_above = sum(1 for v in all_bad if v > threshold)
                total_above = g_above + b_above
                if total_above > 0:
                    precision = g_above / total_above
                    if precision > base_precision:
                        threshold_suggestions.append({
                            "feature_name": name,
                            "operator": ">",
                            "threshold": round(threshold, 6),
                            "good_count": g_above,
                            "bad_count": b_above,
                            "precision": round(precision, 4),
                        })
                        break
        else:
            all_vals = sorted(all_good + all_bad)
            for pct in [0.5, 0.4, 0.3]:
                idx = int(len(all_vals) * pct)
                threshold = all_vals[min(idx, len(all_vals) - 1)]
                g_below = sum(1 for v in all_good if v < threshold)
                b_below = sum(1 for v in all_bad if v < threshold)
                total_below = g_below + b_below
                if total_below > 0:
                    precision = g_below / total_below
                    if precision > base_precision:
                        threshold_suggestions.append({
                            "feature_name": name,
                            "operator": "<",
                            "threshold": round(threshold, 6),
                            "good_count": g_below,
                            "bad_count": b_below,
                            "precision": round(precision, 4),
                        })
                        break

    return templates.TemplateResponse("ui_lab_suggest.html", {
        "request": request,
        "dataset": {"id": dataset_id, "name": dataset.name},
        "side": side,
        "good_count": good_count,
        "bad_count": bad_count,
        "base_precision": round(base_precision, 4),
        "feature_ranking": feature_ranking,
        "threshold_suggestions": threshold_suggestions,
        "feat_names": FEATURE_DISPLAY_NAMES,
    })


@router.post("/lab/create-strategy")
def ui_lab_create_strategy(
    request: Request,
    dataset_id: int = Form(...),
    side: str = Form("long"),
    rule_json: str = Form("[]"),
    session_start: str = Form("00:00"),
    session_end: str = Form("23:59"),
    tp_type: str = Form("atr"),
    tp_value: float = Form(1.5),
    sl_type: str = Form("atr"),
    sl_value: float = Form(1.0),
    db: Session = Depends(get_db),
):
    """Create strategy from Lab suggestion."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        return RedirectResponse(url="/ui/lab", status_code=303)

    first_bar = db.query(Bar.instrument_id).filter(Bar.dataset_id == dataset_id).first()
    if not first_bar:
        return RedirectResponse(url="/ui/lab", status_code=303)
    instrument_id = first_bar[0]

    # Use M1 timeframe for the strategy
    tf = db.query(Timeframe).filter(Timeframe.name == "M1").first()
    if not tf:
        return RedirectResponse(url="/ui/lab", status_code=303)

    # Generate name
    rules = json.loads(rule_json)
    rule_desc = ", ".join(f"{r['feature']} {r['operator']} {r['value']}" for r in rules[:2])
    name = f"Lab-{side}-{rule_desc}" if rule_desc else f"Lab-{side}"

    strategy = Strategy(
        name=name[:255],
        dataset_id=dataset_id,
        instrument_id=instrument_id,
        timeframe_id=tf.id,
        side=side,
        session_start=session_start,
        session_end=session_end,
        weekdays="0,1,2,3,4",
        entry_timing="close",
        rule_json=rule_json,
        tp_type=tp_type,
        tp_value=tp_value,
        sl_type=sl_type,
        sl_value=sl_value,
        max_hold_bars=100,
        cooldown_bars=0,
        fee_pips=0.0,
        created_at=datetime.utcnow(),
    )
    db.add(strategy)
    db.commit()
    db.refresh(strategy)
    return RedirectResponse(url=f"/ui/strategies/{strategy.id}", status_code=303)


# ============ ML Routes ============

@router.get("/ml", response_class=HTMLResponse)
def ui_ml(request: Request, db: Session = Depends(get_db)):
    """ML models list page with train/infer forms."""
    models = db.query(MLModel).order_by(MLModel.created_at.desc()).all()
    datasets = db.query(Dataset).all()
    dataset_list = [{"id": ds.id, "name": ds.name} for ds in datasets]
    timeframes = db.query(Timeframe).order_by(Timeframe.minutes).all()

    # Add metrics to model data
    model_list = []
    for m in models:
        metrics = json.loads(m.metrics_json) if m.metrics_json else {}
        model_list.append({
            "id": m.id,
            "name": m.name,
            "model_type": m.model_type,
            "dataset_id": m.dataset_id,
            "timeframe_id": m.timeframe_id,
            "label_source": m.label_source,
            "created_at": m.created_at,
            "accuracy": metrics.get("best_val_accuracy", 0),
            "auc": metrics.get("auc", 0),
            "train_samples": metrics.get("train_samples", 0),
            "val_samples": metrics.get("val_samples", 0),
        })

    return templates.TemplateResponse("ui_ml.html", {
        "request": request,
        "models": model_list,
        "datasets": dataset_list,
        "timeframes": timeframes,
    })


@router.post("/ml/train")
def ui_ml_train(
    request: Request,
    model_name: str = Form(...),
    model_type: str = Form(...),
    dataset_id: int = Form(...),
    timeframe_name: str = Form(...),
    epochs: int = Form(10),
    batch_size: int = Form(32),
    learning_rate: float = Form(0.001),
    limit: Optional[str] = Form(None),
    start_ts: Optional[str] = Form(None),
    end_ts: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Start ML training job."""
    try:
        limit_val = int(limit) if limit and limit.strip() else None
        start_ts_val = parse_optional_datetime(start_ts) if start_ts else None
        end_ts_val = parse_optional_datetime(end_ts) if end_ts else None

        params = {
            "model_name": model_name,
            "model_type": model_type,
            "dataset_id": dataset_id,
            "timeframe_name": timeframe_name,
            "epochs": epochs,
            "batch_size": batch_size,
            "learning_rate": learning_rate,
            "limit": limit_val,
            "start_ts": start_ts_val.isoformat() if start_ts_val else None,
            "end_ts": end_ts_val.isoformat() if end_ts_val else None,
        }

        job = Job(
            dataset_id=dataset_id,
            job_type="train",
            status="pending",
            params=json.dumps(params),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        ml_train_task.delay(
            job_id=job.id,
            dataset_id=dataset_id,
            timeframe_name=timeframe_name,
            model_type=model_type,
            model_name=model_name,
            epochs=epochs,
            batch_size=batch_size,
            learning_rate=learning_rate,
            limit=limit_val,
            start_ts=start_ts_val.isoformat() if start_ts_val else None,
            end_ts=end_ts_val.isoformat() if end_ts_val else None,
        )

        return RedirectResponse(url=f"/ui/jobs/{job.id}", status_code=303)

    except Exception as e:
        return templates.TemplateResponse("ui_ml.html", {
            "request": request,
            "models": [],
            "datasets": [],
            "timeframes": [],
            "error": str(e),
        })


@router.post("/ml/infer")
def ui_ml_infer(
    request: Request,
    model_id: int = Form(...),
    dataset_id: Optional[int] = Form(None),
    limit: Optional[str] = Form(None),
    start_ts: Optional[str] = Form(None),
    end_ts: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Start ML inference job."""
    try:
        ml_model = db.query(MLModel).filter(MLModel.id == model_id).first()
        if not ml_model:
            raise ValueError(f"Model {model_id} not found")

        limit_val = int(limit) if limit and limit.strip() else None
        start_ts_val = parse_optional_datetime(start_ts) if start_ts else None
        end_ts_val = parse_optional_datetime(end_ts) if end_ts else None

        # Use model's dataset if not specified
        target_dataset_id = dataset_id if dataset_id else ml_model.dataset_id

        params = {
            "model_id": model_id,
            "dataset_id": target_dataset_id,
            "limit": limit_val,
            "start_ts": start_ts_val.isoformat() if start_ts_val else None,
            "end_ts": end_ts_val.isoformat() if end_ts_val else None,
        }

        job = Job(
            dataset_id=target_dataset_id,
            job_type="infer",
            status="pending",
            params=json.dumps(params),
        )
        db.add(job)
        db.commit()
        db.refresh(job)

        ml_infer_task.delay(
            job_id=job.id,
            model_id=model_id,
            dataset_id=target_dataset_id,
            limit=limit_val,
            start_ts=start_ts_val.isoformat() if start_ts_val else None,
            end_ts=end_ts_val.isoformat() if end_ts_val else None,
        )

        return RedirectResponse(url=f"/ui/jobs/{job.id}", status_code=303)

    except Exception as e:
        return templates.TemplateResponse("ui_ml.html", {
            "request": request,
            "models": [],
            "datasets": [],
            "timeframes": [],
            "error": str(e),
        })


@router.get("/ml/{model_id}", response_class=HTMLResponse)
def ui_ml_detail(request: Request, model_id: int, db: Session = Depends(get_db)):
    """ML model detail page."""
    ml_model = db.query(MLModel).filter(MLModel.id == model_id).first()
    if not ml_model:
        return RedirectResponse(url="/ui/ml", status_code=303)

    metrics = json.loads(ml_model.metrics_json) if ml_model.metrics_json else {}
    config = json.loads(ml_model.config_json) if ml_model.config_json else {}

    # Count scores
    score_count = db.query(func.count(MLScore.id)).filter(MLScore.model_id == model_id).scalar()

    dataset = db.query(Dataset).filter(Dataset.id == ml_model.dataset_id).first()
    timeframe = db.query(Timeframe).filter(Timeframe.id == ml_model.timeframe_id).first()

    return templates.TemplateResponse("ui_ml_detail.html", {
        "request": request,
        "model": ml_model,
        "metrics": metrics,
        "config": config,
        "score_count": score_count,
        "dataset": dataset,
        "timeframe": timeframe,
    })


# ============ Trade Image Routes ============

def get_default_lookback(tf_minutes: int) -> int:
    """Get default lookback_n based on timeframe."""
    if tf_minutes <= 1:
        return 128
    elif tf_minutes <= 15:
        return 128
    elif tf_minutes <= 60:
        return 72
    elif tf_minutes <= 240:
        return 48
    else:
        return 32


def snap_to_bar_start(ts: datetime, tf_minutes: int) -> datetime:
    """Snap timestamp to bar start time."""
    ts_unix = int(ts.replace(tzinfo=timezone.utc).timestamp())
    bucket_seconds = tf_minutes * 60
    snapped_unix = (ts_unix // bucket_seconds) * bucket_seconds
    return datetime.fromtimestamp(snapped_unix, tz=timezone.utc)


def generate_trade_image(
    db: Session,
    trade: Trade,
    strategy: Strategy,
    target_ts: datetime,
    lookback_n: int,
    ma_periods: list,
    marker_label: str = None,
    marker_color: str = None,
) -> bytes:
    """Generate chart image for a trade at given timestamp with optional marker."""
    from app.services.image_generator import COLOR_ENTRY, COLOR_EXIT

    # Get the timeframe
    tf = db.query(Timeframe).filter(Timeframe.id == strategy.timeframe_id).first()
    if not tf:
        raise ValueError("Timeframe not found")

    # Snap to bar start
    snapped_ts = snap_to_bar_start(target_ts, tf.minutes)

    # Get bars ending at snapped_ts
    bars = (
        db.query(Bar)
        .filter(
            Bar.dataset_id == strategy.dataset_id,
            Bar.instrument_id == strategy.instrument_id,
            Bar.timeframe_id == tf.id,
            Bar.ts <= snapped_ts,
        )
        .order_by(Bar.ts.desc())
        .limit(lookback_n)
        .all()
    )

    if not bars:
        raise ValueError(f"No bars found before {snapped_ts}")

    # Reverse to chronological order
    bars = list(reversed(bars))

    # Prepare data for image generator
    bar_data = [(b.ts, b.open, b.high, b.low, b.close) for b in bars]

    # Prepare marker if specified (marker is at the last bar = snapped_ts)
    markers = None
    if marker_label and marker_color:
        marker_bar_index = len(bars) - 1  # Last bar is the target timestamp
        markers = [(marker_bar_index, marker_label, marker_color)]

    # Generate image
    return generate_candlestick_image(bar_data, ma_periods=ma_periods, markers=markers)


def generate_trade_range_image(
    db: Session,
    trade: Trade,
    strategy: Strategy,
    ma_periods: list,
    max_bars: int = 256,
) -> bytes | None:
    """Generate chart image showing the full trade range from entry to exit."""
    from app.services.image_generator import COLOR_ENTRY, COLOR_EXIT

    if not trade.entry_ts or not trade.exit_ts:
        return None

    # Get the timeframe
    tf = db.query(Timeframe).filter(Timeframe.id == strategy.timeframe_id).first()
    if not tf:
        return None

    # Snap both timestamps to bar start
    entry_snapped = snap_to_bar_start(trade.entry_ts, tf.minutes)
    exit_snapped = snap_to_bar_start(trade.exit_ts, tf.minutes)

    # Calculate bars between entry and exit
    entry_unix = int(entry_snapped.replace(tzinfo=timezone.utc).timestamp())
    exit_unix = int(exit_snapped.replace(tzinfo=timezone.utc).timestamp())
    tf_seconds = tf.minutes * 60
    bars_between = (exit_unix - entry_unix) // tf_seconds + 1

    # If trade spans more than max_bars, skip range image
    if bars_between > max_bars:
        return None

    # Add padding before entry and after exit for context
    padding = max(20, bars_between // 4)  # At least 20 bars padding, or 25% of trade duration
    total_bars = bars_between + padding * 2

    # Get bars: padding before entry through padding after exit
    start_ts = datetime.fromtimestamp(entry_unix - padding * tf_seconds, tz=timezone.utc)
    end_ts = datetime.fromtimestamp(exit_unix + padding * tf_seconds, tz=timezone.utc)

    bars = (
        db.query(Bar)
        .filter(
            Bar.dataset_id == strategy.dataset_id,
            Bar.instrument_id == strategy.instrument_id,
            Bar.timeframe_id == tf.id,
            Bar.ts >= start_ts,
            Bar.ts <= end_ts,
        )
        .order_by(Bar.ts.asc())
        .all()
    )

    if len(bars) < 3:
        return None

    # Prepare data for image generator
    bar_data = [(b.ts, b.open, b.high, b.low, b.close) for b in bars]

    # Find entry and exit bar indices
    entry_idx = None
    exit_idx = None
    for i, b in enumerate(bars):
        if b.ts == entry_snapped:
            entry_idx = i
        if b.ts == exit_snapped:
            exit_idx = i

    if entry_idx is None or exit_idx is None:
        return None

    # Prepare markers and highlight
    markers = [
        (entry_idx, "ENTRY", COLOR_ENTRY),
        (exit_idx, "EXIT", COLOR_EXIT),
    ]
    highlight_range = (entry_idx, exit_idx)

    # Generate image
    return generate_candlestick_image(
        bar_data,
        ma_periods=ma_periods,
        markers=markers,
        highlight_range=highlight_range
    )


def get_or_create_trade_images(
    db: Session,
    trade: Trade,
    strategy: Strategy,
    lookback_n: int = None,
    ma_periods: list = None,
) -> TradeImage:
    """Get or create trade images (cached in MinIO)."""
    if ma_periods is None:
        ma_periods = [5, 20, 60]

    tf = db.query(Timeframe).filter(Timeframe.id == strategy.timeframe_id).first()
    if lookback_n is None:
        lookback_n = get_default_lookback(tf.minutes if tf else 15)

    ma_periods_str = ",".join(map(str, ma_periods))

    # Check if already exists
    existing = db.query(TradeImage).filter(TradeImage.trade_id == trade.id).first()
    if existing:
        return existing

    from app.services.image_generator import COLOR_ENTRY, COLOR_EXIT

    # Generate entry image with ENTRY marker
    entry_image_key = None
    if trade.entry_ts:
        try:
            entry_image_bytes = generate_trade_image(
                db, trade, strategy, trade.entry_ts, lookback_n, ma_periods,
                marker_label="ENTRY", marker_color=COLOR_ENTRY
            )
            entry_image_key = f"trade_images/{trade.id}/entry.png"
            upload_image(entry_image_key, entry_image_bytes)
        except Exception as e:
            print(f"Error generating entry image for trade {trade.id}: {e}")

    # Generate exit image with EXIT marker
    exit_image_key = None
    if trade.exit_ts:
        try:
            exit_image_bytes = generate_trade_image(
                db, trade, strategy, trade.exit_ts, lookback_n, ma_periods,
                marker_label="EXIT", marker_color=COLOR_EXIT
            )
            exit_image_key = f"trade_images/{trade.id}/exit.png"
            upload_image(exit_image_key, exit_image_bytes)
        except Exception as e:
            print(f"Error generating exit image for trade {trade.id}: {e}")

    # Generate range image (entry to exit with both markers) - max 256 bars
    range_image_key = None
    if trade.entry_ts and trade.exit_ts:
        try:
            range_image_bytes = generate_trade_range_image(
                db, trade, strategy, ma_periods, max_bars=256
            )
            if range_image_bytes:
                range_image_key = f"trade_images/{trade.id}/range.png"
                upload_image(range_image_key, range_image_bytes)
        except Exception as e:
            print(f"Error generating range image for trade {trade.id}: {e}")

    # Save to DB
    trade_image = TradeImage(
        trade_id=trade.id,
        entry_image_key=entry_image_key,
        exit_image_key=exit_image_key,
        range_image_key=range_image_key,
        lookback_n=lookback_n,
        ma_periods=ma_periods_str,
    )
    db.add(trade_image)
    db.commit()
    db.refresh(trade_image)

    return trade_image


@router.get("/trades/{trade_id}", response_class=HTMLResponse)
def ui_trade_detail(
    request: Request,
    trade_id: int,
    db: Session = Depends(get_db),
):
    """Trade detail page with entry/exit images."""
    trade = db.query(Trade).filter(Trade.id == trade_id).first()
    if not trade:
        return RedirectResponse(url="/ui", status_code=303)

    run = db.query(BacktestRun).filter(BacktestRun.id == trade.run_id).first()
    if not run:
        return RedirectResponse(url="/ui", status_code=303)

    strategy = db.query(Strategy).filter(Strategy.id == run.strategy_id).first()
    dataset = db.query(Dataset).filter(Dataset.id == strategy.dataset_id).first() if strategy else None
    instrument = db.query(Instrument).filter(Instrument.id == strategy.instrument_id).first() if strategy else None
    timeframe = db.query(Timeframe).filter(Timeframe.id == strategy.timeframe_id).first() if strategy else None

    # Get or create trade images
    trade_image = None
    if strategy:
        try:
            trade_image = get_or_create_trade_images(db, trade, strategy)
        except Exception as e:
            print(f"Error getting trade images: {e}")

    # Get prev/next trades in same run
    prev_trade = (
        db.query(Trade)
        .filter(Trade.run_id == trade.run_id, Trade.id < trade.id)
        .order_by(Trade.id.desc())
        .first()
    )
    next_trade = (
        db.query(Trade)
        .filter(Trade.run_id == trade.run_id, Trade.id > trade.id)
        .order_by(Trade.id.asc())
        .first()
    )

    return templates.TemplateResponse("ui_trade_detail.html", {
        "request": request,
        "trade": trade,
        "run": run,
        "strategy": strategy,
        "dataset": dataset,
        "instrument": instrument,
        "timeframe": timeframe,
        "trade_image": trade_image,
        "prev_trade_id": prev_trade.id if prev_trade else None,
        "next_trade_id": next_trade.id if next_trade else None,
    })


@router.get("/trades/{trade_id}/entry.png")
def ui_trade_entry_image(trade_id: int, db: Session = Depends(get_db)):
    """Serve trade entry image."""
    from fastapi.responses import Response

    trade_image = db.query(TradeImage).filter(TradeImage.trade_id == trade_id).first()
    if not trade_image or not trade_image.entry_image_key:
        # Try to generate on the fly
        trade = db.query(Trade).filter(Trade.id == trade_id).first()
        if not trade:
            return Response(content=b"", status_code=404)

        run = db.query(BacktestRun).filter(BacktestRun.id == trade.run_id).first()
        strategy = db.query(Strategy).filter(Strategy.id == run.strategy_id).first() if run else None

        if strategy:
            trade_image = get_or_create_trade_images(db, trade, strategy)

        if not trade_image or not trade_image.entry_image_key:
            return Response(content=b"", status_code=404)

    try:
        image_bytes = get_image(trade_image.entry_image_key)
        return Response(content=image_bytes, media_type="image/png")
    except Exception:
        return Response(content=b"", status_code=404)


@router.get("/trades/{trade_id}/exit.png")
def ui_trade_exit_image(trade_id: int, db: Session = Depends(get_db)):
    """Serve trade exit image."""
    from fastapi.responses import Response

    trade_image = db.query(TradeImage).filter(TradeImage.trade_id == trade_id).first()
    if not trade_image or not trade_image.exit_image_key:
        # Try to generate on the fly
        trade = db.query(Trade).filter(Trade.id == trade_id).first()
        if not trade:
            return Response(content=b"", status_code=404)

        run = db.query(BacktestRun).filter(BacktestRun.id == trade.run_id).first()
        strategy = db.query(Strategy).filter(Strategy.id == run.strategy_id).first() if run else None

        if strategy:
            trade_image = get_or_create_trade_images(db, trade, strategy)

        if not trade_image or not trade_image.exit_image_key:
            return Response(content=b"", status_code=404)

    try:
        image_bytes = get_image(trade_image.exit_image_key)
        return Response(content=image_bytes, media_type="image/png")
    except Exception:
        return Response(content=b"", status_code=404)


@router.get("/trades/{trade_id}/range.png")
def ui_trade_range_image(trade_id: int, db: Session = Depends(get_db)):
    """Serve trade range image (entry to exit with both markers)."""
    from fastapi.responses import Response

    trade_image = db.query(TradeImage).filter(TradeImage.trade_id == trade_id).first()
    if not trade_image or not trade_image.range_image_key:
        # Try to generate on the fly
        trade = db.query(Trade).filter(Trade.id == trade_id).first()
        if not trade:
            return Response(content=b"", status_code=404)

        run = db.query(BacktestRun).filter(BacktestRun.id == trade.run_id).first()
        strategy = db.query(Strategy).filter(Strategy.id == run.strategy_id).first() if run else None

        if strategy:
            trade_image = get_or_create_trade_images(db, trade, strategy)

        if not trade_image or not trade_image.range_image_key:
            return Response(content=b"", status_code=404)

    try:
        image_bytes = get_image(trade_image.range_image_key)
        return Response(content=image_bytes, media_type="image/png")
    except Exception:
        return Response(content=b"", status_code=404)
