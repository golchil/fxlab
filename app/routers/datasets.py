import os
import json
import tempfile
from typing import List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models import Dataset, Bar, Job, Window, Label
from app.schemas import (
    DatasetResponse, ImportRequest, WindowsRequest, LabelsTpSlRequest,
    WindowsStatsResponse, LabelsStatsResponse, JobCreatedResponse
)
from app.tasks import import_csv_task, generate_windows_task, generate_labels_task

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
