import json
from datetime import datetime
from celery import Celery
import pandas as pd
import pytz

from app.config import settings
from app.database import SessionLocal
from app.models import Dataset, Instrument, Timeframe, Bar, Job, Window, Label

celery_app = Celery(
    "fxlab",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)


def get_or_create_instrument(db, symbol: str) -> Instrument:
    instrument = db.query(Instrument).filter(Instrument.symbol == symbol).first()
    if not instrument:
        instrument = Instrument(symbol=symbol)
        db.add(instrument)
        db.commit()
        db.refresh(instrument)
    return instrument


def get_or_create_timeframe(db, name: str) -> Timeframe:
    timeframe = db.query(Timeframe).filter(Timeframe.name == name).first()
    if not timeframe:
        # Parse minutes from timeframe name
        minutes_map = {
            "M1": 1, "M5": 5, "M15": 15, "M30": 30,
            "H1": 60, "H4": 240, "D1": 1440, "W1": 10080,
        }
        minutes = minutes_map.get(name, 1)
        timeframe = Timeframe(name=name, minutes=minutes)
        db.add(timeframe)
        db.commit()
        db.refresh(timeframe)
    return timeframe


@celery_app.task(bind=True)
def import_csv_task(self, job_id: int, file_path: str, dataset_name: str,
                    timezone_str: str, timeframe_name: str, description: str = None):
    db = SessionLocal()
    try:
        # Update job status
        job = db.query(Job).filter(Job.id == job_id).first()
        job.status = "running"
        job.celery_task_id = self.request.id
        db.commit()

        # Create dataset
        dataset = Dataset(name=dataset_name, timezone=timezone_str, description=description)
        db.add(dataset)
        db.commit()
        db.refresh(dataset)

        # Update job with dataset_id
        job.dataset_id = dataset.id
        db.commit()

        tz = pytz.timezone(timezone_str)
        timeframe = get_or_create_timeframe(db, timeframe_name)

        total_rows = 0
        instruments_cache = {}  # ticker -> instrument_id

        # Read CSV in chunks
        for chunk in pd.read_csv(
            file_path,
            chunksize=10000,
            header=None,
            names=["ticker", "date", "time", "open", "high", "low", "close", "volume"],
            dtype={"ticker": str, "date": str, "time": str},
        ):
            bars_to_add = []

            for _, row in chunk.iterrows():
                ticker = row["ticker"].strip()

                # Get or create instrument
                if ticker not in instruments_cache:
                    instrument = get_or_create_instrument(db, ticker)
                    instruments_cache[ticker] = instrument.id
                instrument_id = instruments_cache[ticker]

                # Parse date and time
                date_str = str(row["date"]).strip()
                time_str = str(row["time"]).strip().zfill(4)

                try:
                    year = int(date_str[:4])
                    month = int(date_str[4:6])
                    day = int(date_str[6:8])
                    hour = int(time_str[:2]) if len(time_str) >= 2 else 0
                    minute = int(time_str[2:4]) if len(time_str) >= 4 else int(time_str)

                    local_dt = datetime(year, month, day, hour, minute)
                    aware_dt = tz.localize(local_dt)
                except Exception as e:
                    continue  # Skip invalid rows

                bar = Bar(
                    dataset_id=dataset.id,
                    instrument_id=instrument_id,
                    timeframe_id=timeframe.id,
                    ts=aware_dt,
                    open=float(row["open"]),
                    high=float(row["high"]),
                    low=float(row["low"]),
                    close=float(row["close"]),
                    volume=float(row["volume"]) if pd.notna(row["volume"]) else 0,
                )
                bars_to_add.append(bar)

            db.bulk_save_objects(bars_to_add)
            db.commit()
            total_rows += len(bars_to_add)

        # Update job as completed
        job.status = "completed"
        job.result = json.dumps({
            "dataset_id": dataset.id,
            "total_bars": total_rows,
            "instruments": list(instruments_cache.keys()),
        })
        db.commit()

        return {"dataset_id": dataset.id, "total_bars": total_rows}

    except Exception as e:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(e)
            db.commit()
        raise
    finally:
        db.close()


@celery_app.task(bind=True)
def generate_windows_task(
    self, job_id: int, dataset_id: int, lookback_n: int, step: int,
    limit: int = None, start_ts: str = None, end_ts: str = None
):
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        job.status = "running"
        job.celery_task_id = self.request.id
        db.commit()

        # Delete existing windows for this dataset
        db.query(Window).filter(Window.dataset_id == dataset_id).delete()
        db.commit()

        # Build query with optional time filters
        query = db.query(Bar).filter(Bar.dataset_id == dataset_id)

        # Parse timestamps if provided
        filter_start_ts = None
        filter_end_ts = None
        if start_ts:
            filter_start_ts = datetime.fromisoformat(start_ts.replace('Z', '+00:00'))
            query = query.filter(Bar.ts >= filter_start_ts)
        if end_ts:
            filter_end_ts = datetime.fromisoformat(end_ts.replace('Z', '+00:00'))
            query = query.filter(Bar.ts <= filter_end_ts)

        # Order and fetch all bars (optimized: single query, no iteration)
        bars = query.order_by(Bar.instrument_id, Bar.timeframe_id, Bar.ts).all()

        # Track actual generation range
        actual_start_ts = None
        actual_end_ts = None

        if len(bars) < lookback_n:
            job.status = "completed"
            job.result = json.dumps({
                "total_windows": 0,
                "message": "Not enough bars",
                "generation_start_ts": None,
                "generation_end_ts": None,
            })
            db.commit()
            return {"total_windows": 0}

        # Group by instrument and timeframe
        grouped = {}
        for bar in bars:
            key = (bar.instrument_id, bar.timeframe_id)
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(bar)

        windows_created = 0
        windows_batch = []
        batch_size = 5000  # Larger batch for bulk insert

        for (instrument_id, timeframe_id), bar_list in grouped.items():
            bar_list.sort(key=lambda x: x.ts)

            for i in range(lookback_n - 1, len(bar_list), step):
                # Check limit
                if limit is not None and windows_created >= limit:
                    break

                start_idx = i - lookback_n + 1
                end_idx = i

                window_start = bar_list[start_idx].ts
                window_end = bar_list[end_idx].ts

                # Track actual range
                if actual_start_ts is None or window_start < actual_start_ts:
                    actual_start_ts = window_start
                if actual_end_ts is None or window_end > actual_end_ts:
                    actual_end_ts = window_end

                windows_batch.append(Window(
                    dataset_id=dataset_id,
                    instrument_id=instrument_id,
                    timeframe_id=timeframe_id,
                    start_ts=window_start,
                    end_ts=window_end,
                    lookback_n=lookback_n,
                    bar_count=lookback_n,
                ))
                windows_created += 1

                # Bulk insert in batches
                if len(windows_batch) >= batch_size:
                    db.bulk_save_objects(windows_batch)
                    db.commit()
                    windows_batch = []

            # Check limit at group level too
            if limit is not None and windows_created >= limit:
                break

        # Insert remaining windows
        if windows_batch:
            db.bulk_save_objects(windows_batch)
            db.commit()

        job.status = "completed"
        job.result = json.dumps({
            "total_windows": windows_created,
            "generation_start_ts": actual_start_ts.isoformat() if actual_start_ts else None,
            "generation_end_ts": actual_end_ts.isoformat() if actual_end_ts else None,
        })
        db.commit()

        return {"total_windows": windows_created}

    except Exception as e:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(e)
            db.commit()
        raise
    finally:
        db.close()


@celery_app.task(bind=True)
def generate_labels_task(
    self, job_id: int, dataset_id: int, lookahead_m: int, tp_r: float, sl_r: float,
    limit: int = None, start_ts: str = None, end_ts: str = None
):
    """
    Generate labels from bars directly (no window dependency).
    Optimized with numpy-like array processing for performance.
    """
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        job.status = "running"
        job.celery_task_id = self.request.id
        db.commit()

        # Delete existing labels for this dataset with same params
        db.query(Label).filter(
            Label.dataset_id == dataset_id,
            Label.lookahead_m == lookahead_m,
            Label.tp_r == tp_r,
            Label.sl_r == sl_r,
        ).delete()
        db.commit()

        # Build query with optional time filters
        query = db.query(Bar).filter(Bar.dataset_id == dataset_id)

        # Parse timestamps if provided
        filter_start_ts = None
        filter_end_ts = None
        if start_ts:
            filter_start_ts = datetime.fromisoformat(start_ts.replace('Z', '+00:00'))
            query = query.filter(Bar.ts >= filter_start_ts)
        if end_ts:
            filter_end_ts = datetime.fromisoformat(end_ts.replace('Z', '+00:00'))
            query = query.filter(Bar.ts <= filter_end_ts)

        # Get all bars efficiently
        bars = query.order_by(Bar.instrument_id, Bar.timeframe_id, Bar.ts).all()

        if not bars:
            job.status = "completed"
            job.result = json.dumps({
                "total_labels": 0,
                "message": "No bars found",
                "generation_start_ts": None,
                "generation_end_ts": None,
            })
            db.commit()
            return {"total_labels": 0}

        # Group by instrument and timeframe
        grouped = {}
        for bar in bars:
            key = (bar.instrument_id, bar.timeframe_id)
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(bar)

        labels_created = 0
        labels_batch = []
        batch_size = 5000  # Larger batch for bulk insert
        actual_start_ts = None
        actual_end_ts = None

        for (instrument_id, timeframe_id), bar_list in grouped.items():
            bar_list.sort(key=lambda x: x.ts)
            n_bars = len(bar_list)

            # Pre-extract arrays for faster processing
            timestamps = [b.ts for b in bar_list]
            highs = [b.high for b in bar_list]
            lows = [b.low for b in bar_list]
            closes = [b.close for b in bar_list]

            # Calculate ranges
            ranges = [max(highs[i] - lows[i], 0.0001) for i in range(n_bars)]

            for i in range(n_bars - lookahead_m):
                # Check limit
                if limit is not None and labels_created >= limit:
                    break

                current_ts = timestamps[i]

                # Track actual range
                if actual_start_ts is None or current_ts < actual_start_ts:
                    actual_start_ts = current_ts
                if actual_end_ts is None or current_ts > actual_end_ts:
                    actual_end_ts = current_ts

                bar_range = ranges[i]
                tp_distance = bar_range * tp_r
                sl_distance = bar_range * sl_r

                entry_price = closes[i]
                tp_price = entry_price + tp_distance
                sl_price = entry_price - sl_distance

                # Check future bars
                result = "neither"
                for j in range(i + 1, min(i + 1 + lookahead_m, n_bars)):
                    if highs[j] >= tp_price:
                        result = "tp_hit"
                        break
                    if lows[j] <= sl_price:
                        result = "sl_hit"
                        break

                labels_batch.append(Label(
                    dataset_id=dataset_id,
                    instrument_id=instrument_id,
                    timeframe_id=timeframe_id,
                    bar_ts=current_ts,
                    label_type="tp-sl",
                    lookahead_m=lookahead_m,
                    tp_r=tp_r,
                    sl_r=sl_r,
                    result=result,
                ))
                labels_created += 1

                # Bulk insert in batches
                if len(labels_batch) >= batch_size:
                    db.bulk_save_objects(labels_batch)
                    db.commit()
                    labels_batch = []

            # Check limit at group level too
            if limit is not None and labels_created >= limit:
                break

        # Insert remaining labels
        if labels_batch:
            db.bulk_save_objects(labels_batch)
            db.commit()

        job.status = "completed"
        job.result = json.dumps({
            "total_labels": labels_created,
            "generation_start_ts": actual_start_ts.isoformat() if actual_start_ts else None,
            "generation_end_ts": actual_end_ts.isoformat() if actual_end_ts else None,
        })
        db.commit()

        return {"total_labels": labels_created}

    except Exception as e:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(e)
            db.commit()
        raise
    finally:
        db.close()
