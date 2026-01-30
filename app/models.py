from datetime import datetime
from typing import Optional
from sqlalchemy import (
    String, Integer, Float, DateTime, Boolean, ForeignKey, Text, UniqueConstraint, Index, PrimaryKeyConstraint
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class Dataset(Base):
    __tablename__ = "datasets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Tokyo")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    bars: Mapped[list["Bar"]] = relationship(back_populates="dataset", cascade="all, delete-orphan")
    jobs: Mapped[list["Job"]] = relationship(back_populates="dataset", cascade="all, delete-orphan")
    windows: Mapped[list["Window"]] = relationship(back_populates="dataset", cascade="all, delete-orphan")
    labels: Mapped[list["Label"]] = relationship(back_populates="dataset", cascade="all, delete-orphan")
    window_images: Mapped[list["WindowImage"]] = relationship(back_populates="dataset", cascade="all, delete-orphan")
    window_features: Mapped[list["WindowFeature"]] = relationship(back_populates="dataset", cascade="all, delete-orphan")
    strategies: Mapped[list["Strategy"]] = relationship(back_populates="dataset", cascade="all, delete-orphan")
    signals: Mapped[list["Signal"]] = relationship(back_populates="dataset", cascade="all, delete-orphan")
    entry_points: Mapped[list["EntryPoint"]] = relationship(back_populates="dataset", cascade="all, delete-orphan")
    pattern_instances: Mapped[list["PatternInstance"]] = relationship(cascade="all, delete-orphan")


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    bars: Mapped[list["Bar"]] = relationship(back_populates="instrument")


class Timeframe(Base):
    __tablename__ = "timeframes"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(32), unique=True)  # e.g., "M1", "H1", "D1"
    minutes: Mapped[int] = mapped_column(Integer)  # Duration in minutes

    bars: Mapped[list["Bar"]] = relationship(back_populates="timeframe")


class Bar(Base):
    __tablename__ = "bars"

    # Composite primary key for TimescaleDB hypertable compatibility
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    timeframe_id: Mapped[int] = mapped_column(ForeignKey("timeframes.id"), primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0)

    dataset: Mapped["Dataset"] = relationship(back_populates="bars")
    instrument: Mapped["Instrument"] = relationship(back_populates="bars")
    timeframe: Mapped["Timeframe"] = relationship(back_populates="bars")

    __table_args__ = (
        Index("ix_bars_dataset_ts", "dataset_id", "ts"),
    )


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[Optional[int]] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"), nullable=True)
    job_type: Mapped[str] = mapped_column(String(64))  # import, windows, labels
    status: Mapped[str] = mapped_column(String(32), default="pending")  # pending, running, completed, failed
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    params: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON params
    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON result
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    dataset: Mapped[Optional["Dataset"]] = relationship(back_populates="jobs")


class Window(Base):
    __tablename__ = "windows"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    timeframe_id: Mapped[int] = mapped_column(ForeignKey("timeframes.id"))
    start_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    lookback_n: Mapped[int] = mapped_column(Integer)
    bar_count: Mapped[int] = mapped_column(Integer)

    dataset: Mapped["Dataset"] = relationship(back_populates="windows")
    image: Mapped[Optional["WindowImage"]] = relationship(back_populates="window", uselist=False, cascade="all, delete-orphan")
    feature: Mapped[Optional["WindowFeature"]] = relationship(back_populates="window", uselist=False, cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_windows_dataset", "dataset_id"),
    )


class Label(Base):
    __tablename__ = "labels"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    timeframe_id: Mapped[int] = mapped_column(ForeignKey("timeframes.id"))
    bar_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # Reference bar timestamp
    label_type: Mapped[str] = mapped_column(String(32))  # tp-sl
    lookahead_m: Mapped[int] = mapped_column(Integer)
    tp_r: Mapped[float] = mapped_column(Float)
    sl_r: Mapped[float] = mapped_column(Float)
    result: Mapped[str] = mapped_column(String(16))  # tp_hit, sl_hit, neither

    dataset: Mapped["Dataset"] = relationship(back_populates="labels")

    __table_args__ = (
        Index("ix_labels_dataset", "dataset_id"),
    )


class WindowFeature(Base):
    __tablename__ = "window_features"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    window_id: Mapped[int] = mapped_column(ForeignKey("windows.id", ondelete="CASCADE"), unique=True)
    sma5_slope_5: Mapped[float] = mapped_column(Float)
    sma20_slope_5: Mapped[float] = mapped_column(Float)
    sma20_slope_20: Mapped[float] = mapped_column(Float)
    sma60_slope_20: Mapped[float] = mapped_column(Float)
    close_to_sma20: Mapped[float] = mapped_column(Float)
    spread_5_20: Mapped[float] = mapped_column(Float)
    spread_20_60: Mapped[float] = mapped_column(Float)
    atr14: Mapped[float] = mapped_column(Float)
    vol_mean: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    dataset: Mapped["Dataset"] = relationship(back_populates="window_features")
    window: Mapped["Window"] = relationship(back_populates="feature")

    __table_args__ = (
        Index("ix_window_features_dataset", "dataset_id"),
        Index("ix_window_features_dataset_window", "dataset_id", "window_id"),
    )


class WindowImage(Base):
    __tablename__ = "window_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    window_id: Mapped[int] = mapped_column(ForeignKey("windows.id", ondelete="CASCADE"), unique=True)
    image_key: Mapped[str] = mapped_column(String(512))  # MinIO object key
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    ma_periods: Mapped[str] = mapped_column(String(128))  # e.g., "5,20,60"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    dataset: Mapped["Dataset"] = relationship(back_populates="window_images")
    window: Mapped["Window"] = relationship(back_populates="image")

    __table_args__ = (
        Index("ix_window_images_dataset_created", "dataset_id", "created_at"),
        Index("ix_window_images_dataset_window", "dataset_id", "window_id"),
    )


class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    timeframe_id: Mapped[int] = mapped_column(ForeignKey("timeframes.id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    signal_type: Mapped[str] = mapped_column(String(64))
    params_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    dataset: Mapped["Dataset"] = relationship(back_populates="signals")

    __table_args__ = (
        Index("ix_signals_dataset_tf_ts", "dataset_id", "instrument_id", "timeframe_id", "ts"),
        Index("ix_signals_type", "signal_type"),
    )


class SwingPoint(Base):
    """ダウ理論のスイングハイ/ロー（山谷）"""
    __tablename__ = "swing_points"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    timeframe_id: Mapped[int] = mapped_column(ForeignKey("timeframes.id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # 山/谷の価格発生時刻
    confirmed_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # 確定時刻（閾値反転確認）
    kind: Mapped[str] = mapped_column(String(16))  # "high" or "low"
    price: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(32))  # "zigzag"
    threshold_type: Mapped[str] = mapped_column(String(16))  # "atr" or "pips"
    threshold_value: Mapped[float] = mapped_column(Float)
    min_bars: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    dataset: Mapped["Dataset"] = relationship()
    instrument: Mapped["Instrument"] = relationship()
    timeframe: Mapped["Timeframe"] = relationship()

    __table_args__ = (
        Index("ix_swing_points_lookup", "dataset_id", "instrument_id", "timeframe_id", "ts"),
        Index("ix_swing_points_confirmed_lookup", "dataset_id", "instrument_id", "timeframe_id", "kind", "confirmed_ts"),
    )


class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    timeframe_id: Mapped[int] = mapped_column(ForeignKey("timeframes.id"))
    side: Mapped[str] = mapped_column(String(16))  # long, short
    session_start: Mapped[str] = mapped_column(String(8))  # HH:MM (JST)
    session_end: Mapped[str] = mapped_column(String(8))  # HH:MM (JST)
    weekdays: Mapped[str] = mapped_column(Text, default="0,1,2,3,4")  # 0=Mon..4=Fri
    entry_timing: Mapped[str] = mapped_column(String(16), default="close")  # close, next_open
    rule_json: Mapped[str] = mapped_column(Text)  # JSON rule
    tp_type: Mapped[str] = mapped_column(String(16), default="atr")  # atr, pips
    tp_value: Mapped[float] = mapped_column(Float)
    sl_type: Mapped[str] = mapped_column(String(16), default="atr")  # atr, pips
    sl_value: Mapped[float] = mapped_column(Float)
    max_hold_bars: Mapped[int] = mapped_column(Integer, default=100)
    cooldown_bars: Mapped[int] = mapped_column(Integer, default=0)
    fee_pips: Mapped[float] = mapped_column(Float, default=0.0)  # 追加手数料（往復、pips）
    spread_pips: Mapped[float] = mapped_column(Float, default=0.0)  # スプレッド幅（pips）
    slippage_pips: Mapped[float] = mapped_column(Float, default=0.0)  # スリッページ（片道、pips）
    intrabar_fill_mode: Mapped[str] = mapped_column(String(32), default="conservative")  # conservative/optimistic/ignore
    htf_timeframe_id: Mapped[Optional[int]] = mapped_column(ForeignKey("timeframes.id"), nullable=True)
    htf_signal_type: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    htf_lookback_hours: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, default=24)
    htf_confirmed_only: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True, default=True)
    require_htf_signal: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True, default=False)
    dow_timeframe_id: Mapped[Optional[int]] = mapped_column(ForeignKey("timeframes.id"), nullable=True)
    # MTFパターン: HTF (Higher TimeFrame) パターン設定
    htf_pattern_timeframe_id: Mapped[Optional[int]] = mapped_column(ForeignKey("timeframes.id"), nullable=True)
    htf_pattern_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)  # "double_bottom", "double_top"
    htf_pattern_lookback_hours: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # MTFパターン: LTF (Lower TimeFrame) パターン設定
    ltf_pattern_timeframe_id: Mapped[Optional[int]] = mapped_column(ForeignKey("timeframes.id"), nullable=True)
    ltf_pattern_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    ltf_pattern_lookback_minutes: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    require_ltf_breakout: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    dataset: Mapped["Dataset"] = relationship(back_populates="strategies")
    instrument: Mapped["Instrument"] = relationship()
    timeframe: Mapped["Timeframe"] = relationship(foreign_keys=[timeframe_id])
    htf_timeframe: Mapped[Optional["Timeframe"]] = relationship(foreign_keys=[htf_timeframe_id])
    dow_timeframe: Mapped[Optional["Timeframe"]] = relationship(foreign_keys=[dow_timeframe_id])
    htf_pattern_timeframe: Mapped[Optional["Timeframe"]] = relationship(foreign_keys=[htf_pattern_timeframe_id])
    ltf_pattern_timeframe: Mapped[Optional["Timeframe"]] = relationship(foreign_keys=[ltf_pattern_timeframe_id])
    runs: Mapped[list["BacktestRun"]] = relationship(back_populates="strategy", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_strategies_dataset", "dataset_id"),
    )


class BacktestRun(Base):
    __tablename__ = "backtest_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    strategy_id: Mapped[int] = mapped_column(ForeignKey("strategies.id", ondelete="CASCADE"))
    job_id: Mapped[Optional[int]] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    start_ts: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    end_ts: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    params_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    result_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    strategy: Mapped["Strategy"] = relationship(back_populates="runs")
    job: Mapped[Optional["Job"]] = relationship()
    trades: Mapped[list["Trade"]] = relationship(back_populates="run", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_backtest_runs_strategy", "strategy_id"),
    )


class Trade(Base):
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("backtest_runs.id", ondelete="CASCADE"))
    entry_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    entry_price: Mapped[float] = mapped_column(Float)
    exit_ts: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    side: Mapped[str] = mapped_column(String(16))
    pnl_pips: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    r_multiple: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_reason: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    signal_ts: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    meta_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    run: Mapped["BacktestRun"] = relationship(back_populates="trades")

    __table_args__ = (
        Index("ix_trades_run", "run_id"),
    )


class TradeImage(Base):
    __tablename__ = "trade_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    trade_id: Mapped[int] = mapped_column(ForeignKey("trades.id", ondelete="CASCADE"), unique=True)
    entry_image_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    exit_image_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    range_image_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    lookback_n: Mapped[int] = mapped_column(Integer)
    ma_periods: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)  # e.g., "5,20,60"
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    trade: Mapped["Trade"] = relationship()

    __table_args__ = (
        Index("ix_trade_images_trade", "trade_id", unique=True),
    )


class EntryPoint(Base):
    __tablename__ = "entry_points"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    timeframe_id: Mapped[int] = mapped_column(ForeignKey("timeframes.id"))
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    side: Mapped[str] = mapped_column(Text)  # "long" / "short"
    label: Mapped[str] = mapped_column(Text, default="unknown")  # "good" / "bad" / "unknown"
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    dataset: Mapped["Dataset"] = relationship(back_populates="entry_points")

    __table_args__ = (
        Index("ix_entry_points_dataset", "dataset_id", "instrument_id", "timeframe_id"),
        UniqueConstraint("dataset_id", "instrument_id", "timeframe_id", "ts", "side", name="uq_entry_points_ts"),
    )


class MLModel(Base):
    __tablename__ = "ml_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    model_type: Mapped[str] = mapped_column(String(32))  # "entry" or "profit"
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    timeframe_id: Mapped[int] = mapped_column(ForeignKey("timeframes.id"))
    label_source: Mapped[str] = mapped_column(String(32))  # "entry_points" or "tp_sl"
    config_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    artifact_key: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    dataset: Mapped["Dataset"] = relationship()
    timeframe: Mapped["Timeframe"] = relationship()
    scores: Mapped[list["MLScore"]] = relationship(back_populates="model", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_ml_models_dataset", "dataset_id"),
        Index("ix_ml_models_type", "model_type"),
    )


class MLScore(Base):
    __tablename__ = "ml_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    timeframe_id: Mapped[int] = mapped_column(ForeignKey("timeframes.id"))
    window_id: Mapped[int] = mapped_column(ForeignKey("windows.id", ondelete="CASCADE"))
    model_id: Mapped[int] = mapped_column(ForeignKey("ml_models.id", ondelete="CASCADE"))
    score: Mapped[float] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    dataset: Mapped["Dataset"] = relationship()
    timeframe: Mapped["Timeframe"] = relationship()
    window: Mapped["Window"] = relationship()
    model: Mapped["MLModel"] = relationship(back_populates="scores")

    __table_args__ = (
        Index("ix_ml_scores_lookup", "dataset_id", "timeframe_id", "model_id", "score"),
        Index("ix_ml_scores_window", "window_id"),
    )


class PatternInstance(Base):
    """ダブルボトム/ダブルトップなどのチャートパターン"""
    __tablename__ = "pattern_instances"

    id: Mapped[int] = mapped_column(primary_key=True)
    dataset_id: Mapped[int] = mapped_column(ForeignKey("datasets.id", ondelete="CASCADE"))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    timeframe_id: Mapped[int] = mapped_column(ForeignKey("timeframes.id"))
    pattern_type: Mapped[str] = mapped_column(String(32))  # "double_bottom", "double_top"
    left_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # 左側の山/谷時刻
    right_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # 右側の山/谷時刻
    neckline_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # ネックライン時刻
    left_price: Mapped[float] = mapped_column(Float)
    right_price: Mapped[float] = mapped_column(Float)
    neckline_price: Mapped[float] = mapped_column(Float)
    confirmed_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # 右側確定時刻（未来参照排除）
    params_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # tol, atr_mult, min_bars, method等
    rule_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # ルールベーススコア
    ml_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)  # ML推論スコア
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    dataset: Mapped["Dataset"] = relationship()
    instrument: Mapped["Instrument"] = relationship()
    timeframe: Mapped["Timeframe"] = relationship()
    labels: Mapped[list["PatternLabel"]] = relationship(back_populates="pattern", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_pattern_instances_lookup", "dataset_id", "instrument_id", "timeframe_id", "pattern_type", "confirmed_ts"),
    )


class PatternLabel(Base):
    """ユーザーによるパターン評価"""
    __tablename__ = "pattern_labels"

    id: Mapped[int] = mapped_column(primary_key=True)
    pattern_id: Mapped[int] = mapped_column(ForeignKey("pattern_instances.id", ondelete="CASCADE"))
    label: Mapped[str] = mapped_column(String(16))  # "good", "bad"
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    pattern: Mapped["PatternInstance"] = relationship(back_populates="labels")
