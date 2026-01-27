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
from app.models import Dataset, Bar, Job, Window, Label, WindowImage
from app.tasks import import_csv_task, generate_windows_task, generate_labels_task, generate_window_images_task

router = APIRouter(prefix="/ui", tags=["ui"])

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

    return templates.TemplateResponse("ui_dataset_detail.html", {
        "request": request,
        "dataset": dataset_data,
        "windows_stats": windows_stats,
        "labels_stats": labels_stats,
        "images_stats": images_stats,
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


@router.get("/datasets/{dataset_id}/images", response_class=HTMLResponse)
def ui_image_gallery(
    request: Request,
    dataset_id: int,
    page: int = 1,
    label: str = "all",
    db: Session = Depends(get_db),
):
    """Image gallery page."""
    dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
    if not dataset:
        return RedirectResponse(url="/ui/datasets", status_code=303)

    page_size = 50

    # Build query
    query = (
        db.query(WindowImage, Window, Label.result)
        .join(Window, WindowImage.window_id == Window.id)
        .outerjoin(Label, (Label.dataset_id == dataset_id) & (Label.bar_ts == Window.end_ts))
        .filter(WindowImage.dataset_id == dataset_id)
    )

    if label and label != "all":
        query = query.filter(Label.result == label)

    # Count
    count_q = (
        db.query(func.count(WindowImage.id))
        .join(Window, WindowImage.window_id == Window.id)
        .filter(WindowImage.dataset_id == dataset_id)
    )
    if label and label != "all":
        count_q = count_q.join(Label, (Label.dataset_id == dataset_id) & (Label.bar_ts == Window.end_ts)).filter(Label.result == label)
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

    return templates.TemplateResponse("ui_image_gallery.html", {
        "request": request,
        "dataset": {"id": dataset_id, "name": dataset.name},
        "images": images,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "label": label,
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
