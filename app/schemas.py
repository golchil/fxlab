from datetime import datetime
from typing import Optional, List
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


# Image generation request
class ImagesRequest(BaseModel):
    lookback_n: int = 128
    ma_periods: List[int] = [5, 20, 60]
    limit: Optional[int] = None
    start_ts: Optional[datetime] = None
    end_ts: Optional[datetime] = None
    overwrite: bool = False


# Image stats response
class ImagesStatsResponse(BaseModel):
    total_images: int
    latest_created_at: Optional[datetime] = None
    last_generation_params: Optional[str] = None
    generated_count: Optional[int] = None
    generation_start_ts: Optional[datetime] = None
    generation_end_ts: Optional[datetime] = None


# Image list item
class ImageListItem(BaseModel):
    id: int
    window_id: int
    end_ts: datetime
    image_url: str
    label_result: Optional[str] = None  # tp_hit, sl_hit, neither

    class Config:
        from_attributes = True


# Image list response with pagination
class ImageListResponse(BaseModel):
    items: List[ImageListItem]
    total: int
    page: int
    page_size: int
    total_pages: int


# Image detail response
class ImageDetailResponse(BaseModel):
    id: int
    window_id: int
    dataset_id: int
    image_key: str
    width: int
    height: int
    ma_periods: str
    created_at: datetime
    window_start_ts: datetime
    window_end_ts: datetime
    lookback_n: int
    label_result: Optional[str] = None

    class Config:
        from_attributes = True


# Feature generation request
class FeaturesRequest(BaseModel):
    lookback_n: int = 128
    limit: Optional[int] = None
    start_ts: Optional[datetime] = None
    end_ts: Optional[datetime] = None
    overwrite: bool = False


# Features stats response
class FeaturesStatsResponse(BaseModel):
    total_features: int
    latest_created_at: Optional[datetime] = None
    generated_count: Optional[int] = None
    skipped_count: Optional[int] = None


# Insights response
class FeatureRankingItem(BaseModel):
    feature_name: str
    tp_mean: float
    sl_mean: float
    diff: float
    effect_size: float
    direction: str  # "higher_is_tp" or "lower_is_tp"


class ThresholdSuggestion(BaseModel):
    feature_name: str
    operator: str  # ">", "<"
    threshold: float
    tp_count: int
    sl_count: int
    precision: float  # tp / (tp + sl) for this filter


class InsightsResponse(BaseModel):
    dataset_id: int
    total_features: int
    tp_count: int
    sl_count: int
    neither_count: int
    feature_ranking: List[FeatureRankingItem]
    threshold_suggestions: List[ThresholdSuggestion]


# Resample request
class ResampleRequest(BaseModel):
    from_tf: str = "M1"
    to_tf: str = "H1"
    start_ts: Optional[datetime] = None
    end_ts: Optional[datetime] = None
    limit: Optional[int] = None


# Timeframe info response
class TimeframeInfoItem(BaseModel):
    name: str
    minutes: int
    bar_count: int
    start_ts: Optional[datetime] = None
    end_ts: Optional[datetime] = None


# Golden cross signal request
class GoldenCrossRequest(BaseModel):
    timeframe: str = "H1"
    fast_ma: int = 20
    slow_ma: int = 60
    start_ts: Optional[datetime] = None
    end_ts: Optional[datetime] = None
    limit: Optional[int] = None


# Strategy schemas
class StrategyCreate(BaseModel):
    name: str
    dataset_id: int
    instrument_id: int
    timeframe_id: int
    side: str = "long"
    session_start: str = "00:00"
    session_end: str = "23:59"
    weekdays: str = "0,1,2,3,4"
    entry_timing: str = "close"
    rule_json: str
    tp_type: str = "atr"
    tp_value: float = 1.5
    sl_type: str = "atr"
    sl_value: float = 1.0
    max_hold_bars: int = 100
    cooldown_bars: int = 0
    fee_pips: float = 0.0
    htf_timeframe_id: Optional[int] = None
    htf_signal_type: Optional[str] = None
    htf_lookback_hours: Optional[int] = 24
    htf_confirmed_only: Optional[bool] = True
    require_htf_signal: Optional[bool] = False


class StrategyResponse(BaseModel):
    id: int
    name: str
    dataset_id: int
    instrument_id: int
    timeframe_id: int
    side: str
    session_start: str
    session_end: str
    weekdays: str
    entry_timing: str
    rule_json: str
    tp_type: str
    tp_value: float
    sl_type: str
    sl_value: float
    max_hold_bars: int
    cooldown_bars: int
    fee_pips: float
    htf_timeframe_id: Optional[int] = None
    htf_signal_type: Optional[str] = None
    htf_lookback_hours: Optional[int] = None
    htf_confirmed_only: Optional[bool] = None
    require_htf_signal: Optional[bool] = None
    created_at: datetime

    class Config:
        from_attributes = True


class RunCreate(BaseModel):
    start_ts: Optional[datetime] = None
    end_ts: Optional[datetime] = None


class TradeResponse(BaseModel):
    id: int
    run_id: int
    entry_ts: datetime
    entry_price: float
    exit_ts: Optional[datetime] = None
    exit_price: Optional[float] = None
    side: str
    pnl_pips: Optional[float] = None
    r_multiple: Optional[float] = None
    exit_reason: Optional[str] = None
    signal_ts: Optional[datetime] = None

    class Config:
        from_attributes = True
