from datetime import datetime
from typing import Optional
from sqlalchemy import (
    String, Integer, Float, DateTime, ForeignKey, Text, UniqueConstraint, Index, PrimaryKeyConstraint
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
