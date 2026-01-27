from datetime import datetime
from typing import Optional
from pydantic import BaseModel


# Dataset schemas
class DatasetBase(BaseModel):
    name: str
    description: Optional[str] = None
    timezone: str = "Asia/Tokyo"


class DatasetCreate(DatasetBase):
    pass


class DatasetResponse(DatasetBase):
    id: int
    created_at: datetime
    updated_at: datetime
    bar_count: Optional[int] = None
    start_ts: Optional[datetime] = None
    end_ts: Optional[datetime] = None

    class Config:
        from_attributes = True


# Job schemas
class JobResponse(BaseModel):
    id: int
    dataset_id: Optional[int] = None
    job_type: str
    status: str
    params: Optional[str] = None
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# Import request
class ImportRequest(BaseModel):
    name: str
    description: Optional[str] = None
    timezone: str = "Asia/Tokyo"
    timeframe: str = "M1"


# Windows request
class WindowsRequest(BaseModel):
    lookback_n: int = 128
    step: int = 1
    limit: Optional[int] = None  # None = unlimited
    start_ts: Optional[datetime] = None  # Filter bars from this timestamp
    end_ts: Optional[datetime] = None  # Filter bars until this timestamp


# Labels TP-SL request
class LabelsTpSlRequest(BaseModel):
    lookahead_m: int = 32
    tp_r: float = 1.0
    sl_r: float = 1.0
    limit: Optional[int] = None  # None = unlimited
    start_ts: Optional[datetime] = None  # Filter bars from this timestamp
    end_ts: Optional[datetime] = None  # Filter bars until this timestamp


# Stats responses
class WindowsStatsResponse(BaseModel):
    total_windows: int
    lookback_n: Optional[int] = None
    earliest_start: Optional[datetime] = None
    latest_end: Optional[datetime] = None
    # Additional generation info (returned when generation completes)
    generated_count: Optional[int] = None
    generation_start_ts: Optional[datetime] = None
    generation_end_ts: Optional[datetime] = None


class LabelsStatsResponse(BaseModel):
    total_labels: int
    tp_hit_count: int
    sl_hit_count: int
    neither_count: int
    tp_hit_ratio: float
    sl_hit_ratio: float
    # Additional generation info
    earliest_bar_ts: Optional[datetime] = None
    latest_bar_ts: Optional[datetime] = None
    generated_count: Optional[int] = None
    generation_start_ts: Optional[datetime] = None
    generation_end_ts: Optional[datetime] = None


# Job created response
class JobCreatedResponse(BaseModel):
    job_id: int
    message: str
