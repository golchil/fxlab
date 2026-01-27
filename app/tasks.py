import json
from datetime import datetime, timedelta
from celery import Celery
import pandas as pd
import pytz
from collections import defaultdict

from app.config import settings
from app.database import SessionLocal
from app.models import Dataset, Instrument, Timeframe, Bar, Job, Window, Label, WindowImage, WindowFeature, Strategy, BacktestRun, Trade, Signal
from app.config import settings as app_settings

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
                    timezone_str: str, timeframe_name: str, description: str = None,
                    dataset_id: int = None):
    db = SessionLocal()
    try:
        # Update job status
        job = db.query(Job).filter(Job.id == job_id).first()
        job.status = "running"
        job.celery_task_id = self.request.id
        db.commit()

        append_mode = dataset_id is not None

        if append_mode:
            # Append mode: use existing dataset
            dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
            if not dataset:
                raise ValueError(f"Dataset {dataset_id} not found")
            job.dataset_id = dataset.id
            db.commit()

            # Build set of existing bar timestamps for duplicate detection
            existing_bars = set(
                (r[0], r[1], r[2])
                for r in db.query(Bar.instrument_id, Bar.timeframe_id, Bar.ts)
                .filter(Bar.dataset_id == dataset_id)
                .all()
            )
        else:
            # New dataset mode
            dataset = Dataset(name=dataset_name, timezone=timezone_str, description=description)
            db.add(dataset)
            db.commit()
            db.refresh(dataset)
            job.dataset_id = dataset.id
            db.commit()
            existing_bars = set()

        tz = pytz.timezone(timezone_str)
        timeframe = get_or_create_timeframe(db, timeframe_name)

        total_rows = 0
        inserted_count = 0
        skipped_count = 0
        min_ts = None
        max_ts = None
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
                total_rows += 1
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
                except Exception:
                    continue  # Skip invalid rows

                # Duplicate check for append mode
                if append_mode:
                    bar_key = (instrument_id, timeframe.id, aware_dt)
                    if bar_key in existing_bars:
                        skipped_count += 1
                        continue
                    existing_bars.add(bar_key)

                # Track min/max ts
                if min_ts is None or aware_dt < min_ts:
                    min_ts = aware_dt
                if max_ts is None or aware_dt > max_ts:
                    max_ts = aware_dt

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
            inserted_count += len(bars_to_add)

        # Update job as completed
        job.status = "completed"
        job.result = json.dumps({
            "dataset_id": dataset.id,
            "total_bars": total_rows,
            "inserted_count": inserted_count,
            "skipped_count": skipped_count,
            "min_ts": min_ts.isoformat() if min_ts else None,
            "max_ts": max_ts.isoformat() if max_ts else None,
            "instruments": list(instruments_cache.keys()),
            "append_mode": append_mode,
        })
        db.commit()

        return {"dataset_id": dataset.id, "inserted_count": inserted_count, "skipped_count": skipped_count}

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


@celery_app.task(bind=True)
def generate_window_images_task(
    self, job_id: int, dataset_id: int, lookback_n: int,
    ma_periods: list = None, limit: int = None,
    start_ts: str = None, end_ts: str = None, overwrite: bool = False
):
    """
    Generate candlestick chart images for windows.
    """
    from app.services.minio_client import upload_image, ensure_bucket_exists
    from app.services.image_generator import generate_candlestick_image

    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        job.status = "running"
        job.celery_task_id = self.request.id
        db.commit()

        if ma_periods is None:
            ma_periods = app_settings.default_ma_periods

        # Ensure MinIO bucket exists
        ensure_bucket_exists()

        # Build window query
        query = db.query(Window).filter(
            Window.dataset_id == dataset_id,
            Window.lookback_n == lookback_n
        )

        # Parse timestamps if provided
        filter_start_ts = None
        filter_end_ts = None
        if start_ts:
            filter_start_ts = datetime.fromisoformat(start_ts.replace('Z', '+00:00'))
            query = query.filter(Window.end_ts >= filter_start_ts)
        if end_ts:
            filter_end_ts = datetime.fromisoformat(end_ts.replace('Z', '+00:00'))
            query = query.filter(Window.end_ts <= filter_end_ts)

        query = query.order_by(Window.end_ts)

        if limit:
            query = query.limit(limit)

        windows = query.all()

        if not windows:
            job.status = "completed"
            job.result = json.dumps({
                "generated_count": 0,
                "skipped_count": 0,
                "message": "No windows found",
            })
            db.commit()
            return {"generated_count": 0}

        # Get all bars for this dataset for efficiency
        all_bars = db.query(Bar).filter(Bar.dataset_id == dataset_id).order_by(Bar.ts).all()

        # Create lookup by timestamp
        bars_by_ts = {b.ts: b for b in all_bars}
        sorted_bar_times = sorted(bars_by_ts.keys())

        generated_count = 0
        skipped_count = 0
        actual_start_ts = None
        actual_end_ts = None
        batch_size = 100
        image_size = app_settings.image_size
        ma_periods_str = ",".join(map(str, ma_periods))

        for i, window in enumerate(windows):
            # Check if image already exists
            existing = db.query(WindowImage).filter(WindowImage.window_id == window.id).first()
            if existing and not overwrite:
                skipped_count += 1
                continue

            # Track actual range
            if actual_start_ts is None or window.end_ts < actual_start_ts:
                actual_start_ts = window.end_ts
            if actual_end_ts is None or window.end_ts > actual_end_ts:
                actual_end_ts = window.end_ts

            # Get bars for this window
            # Find bars in range [start_ts, end_ts]
            window_bars = []
            for ts in sorted_bar_times:
                if window.start_ts <= ts <= window.end_ts:
                    bar = bars_by_ts[ts]
                    window_bars.append((bar.ts, bar.open, bar.high, bar.low, bar.close))

            if len(window_bars) < lookback_n:
                skipped_count += 1
                continue

            # Take exactly lookback_n bars
            window_bars = window_bars[-lookback_n:]

            # Generate image
            try:
                image_bytes = generate_candlestick_image(
                    bars=window_bars,
                    ma_periods=ma_periods,
                    image_size=image_size,
                )
            except Exception as e:
                # Skip on image generation error
                skipped_count += 1
                continue

            # Create image key
            image_key = f"dataset_{dataset_id}/windows/{window.id:08d}.png"

            # Upload to MinIO
            upload_image(image_key, image_bytes)

            # Upsert to database
            if existing:
                existing.image_key = image_key
                existing.width = image_size
                existing.height = image_size
                existing.ma_periods = ma_periods_str
                existing.created_at = datetime.utcnow()
            else:
                window_image = WindowImage(
                    dataset_id=dataset_id,
                    window_id=window.id,
                    image_key=image_key,
                    width=image_size,
                    height=image_size,
                    ma_periods=ma_periods_str,
                )
                db.add(window_image)

            generated_count += 1

            # Commit in batches
            if generated_count % batch_size == 0:
                db.commit()

        # Final commit
        db.commit()

        job.status = "completed"
        job.result = json.dumps({
            "generated_count": generated_count,
            "skipped_count": skipped_count,
            "generation_start_ts": actual_start_ts.isoformat() if actual_start_ts else None,
            "generation_end_ts": actual_end_ts.isoformat() if actual_end_ts else None,
        })
        db.commit()

        return {"generated_count": generated_count, "skipped_count": skipped_count}

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
def generate_window_features_task(
    self, job_id: int, dataset_id: int, lookback_n: int,
    limit: int = None, start_ts: str = None, end_ts: str = None,
    overwrite: bool = False
):
    """
    Generate numerical features for each window.
    """
    from app.services.feature_extractor import extract_features
    import bisect

    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        job.status = "running"
        job.celery_task_id = self.request.id
        db.commit()

        # Build window query
        query = db.query(Window).filter(
            Window.dataset_id == dataset_id,
            Window.lookback_n == lookback_n
        )

        if start_ts:
            filter_start = datetime.fromisoformat(start_ts.replace('Z', '+00:00'))
            query = query.filter(Window.end_ts >= filter_start)
        if end_ts:
            filter_end = datetime.fromisoformat(end_ts.replace('Z', '+00:00'))
            query = query.filter(Window.end_ts <= filter_end)

        query = query.order_by(Window.end_ts)

        if limit:
            query = query.limit(limit)

        windows = query.all()

        if not windows:
            job.status = "completed"
            job.result = json.dumps({
                "generated_count": 0,
                "skipped_count": 0,
                "message": "No windows found",
            })
            db.commit()
            return {"generated_count": 0}

        # Get all bars for this dataset
        all_bars = db.query(Bar).filter(Bar.dataset_id == dataset_id).order_by(Bar.ts).all()
        sorted_bar_times = [b.ts for b in all_bars]
        bars_data = [(b.open, b.high, b.low, b.close, b.volume) for b in all_bars]

        generated_count = 0
        skipped_count = 0
        batch_size = 500

        for window in windows:
            # Check if feature already exists
            existing = db.query(WindowFeature).filter(WindowFeature.window_id == window.id).first()
            if existing and not overwrite:
                skipped_count += 1
                continue

            # Find bars in window range using binary search
            start_idx = bisect.bisect_left(sorted_bar_times, window.start_ts)
            end_idx = bisect.bisect_right(sorted_bar_times, window.end_ts)

            window_bars_data = bars_data[start_idx:end_idx]

            if len(window_bars_data) < lookback_n:
                skipped_count += 1
                continue

            # Take exactly lookback_n bars from the end
            window_bars_data = window_bars_data[-lookback_n:]

            features = extract_features(window_bars_data)
            if features is None:
                skipped_count += 1
                continue

            if existing:
                for key, val in features.items():
                    setattr(existing, key, val)
                existing.created_at = datetime.utcnow()
            else:
                wf = WindowFeature(
                    dataset_id=dataset_id,
                    window_id=window.id,
                    created_at=datetime.utcnow(),
                    **features,
                )
                db.add(wf)

            generated_count += 1

            if generated_count % batch_size == 0:
                db.commit()

        db.commit()

        job.status = "completed"
        job.result = json.dumps({
            "generated_count": generated_count,
            "skipped_count": skipped_count,
        })
        db.commit()

        return {"generated_count": generated_count, "skipped_count": skipped_count}

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
def golden_cross_task(
    self, job_id: int, dataset_id: int, timeframe_name: str = "H1",
    fast_ma: int = 20, slow_ma: int = 60,
    start_ts: str = None, end_ts: str = None, limit: int = None
):
    """
    Detect SMA golden cross signals on higher timeframe bars.
    A golden cross: prev bar fast_sma <= slow_sma AND current bar fast_sma > slow_sma.
    Signal ts = H1 bar bucket start timestamp.
    """
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        job.status = "running"
        job.celery_task_id = self.request.id
        db.commit()

        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if not dataset:
            raise ValueError(f"Dataset {dataset_id} not found")

        timeframe = get_or_create_timeframe(db, timeframe_name)
        signal_type = f"golden_cross_{fast_ma}_{slow_ma}"

        # Load bars for this timeframe
        query = db.query(Bar).filter(
            Bar.dataset_id == dataset_id,
            Bar.timeframe_id == timeframe.id,
        )

        if start_ts:
            filter_start = datetime.fromisoformat(start_ts.replace('Z', '+00:00'))
            query = query.filter(Bar.ts >= filter_start)
        if end_ts:
            filter_end = datetime.fromisoformat(end_ts.replace('Z', '+00:00'))
            query = query.filter(Bar.ts <= filter_end)

        bars = query.order_by(Bar.instrument_id, Bar.ts).all()

        if not bars:
            job.status = "completed"
            job.result = json.dumps({"signal_count": 0, "message": "No bars found"})
            db.commit()
            return {"signal_count": 0}

        # Group by instrument
        grouped = defaultdict(list)
        for bar in bars:
            grouped[bar.instrument_id].append(bar)

        # Build existing signals set for dedup
        existing_signals = set(
            (r[0], r[1], r[2], r[3])
            for r in db.query(Signal.dataset_id, Signal.instrument_id, Signal.timeframe_id, Signal.ts)
            .filter(Signal.dataset_id == dataset_id, Signal.signal_type == signal_type)
            .all()
        )

        signal_count = 0
        skipped_count = 0
        signals_batch = []
        batch_size = 1000
        params_str = json.dumps({"fast_ma": fast_ma, "slow_ma": slow_ma})

        for instrument_id, bar_list in grouped.items():
            bar_list.sort(key=lambda b: b.ts)
            closes = [b.close for b in bar_list]
            n = len(closes)

            if n < slow_ma + 1:
                continue

            # Compute SMAs
            fast_sma = [None] * n
            slow_sma = [None] * n

            # Fast SMA
            running_sum = sum(closes[:fast_ma])
            fast_sma[fast_ma - 1] = running_sum / fast_ma
            for i in range(fast_ma, n):
                running_sum += closes[i] - closes[i - fast_ma]
                fast_sma[i] = running_sum / fast_ma

            # Slow SMA
            running_sum = sum(closes[:slow_ma])
            slow_sma[slow_ma - 1] = running_sum / slow_ma
            for i in range(slow_ma, n):
                running_sum += closes[i] - closes[i - slow_ma]
                slow_sma[i] = running_sum / slow_ma

            # Detect crossovers
            for i in range(slow_ma, n):
                if limit is not None and signal_count >= limit:
                    break

                prev_fast = fast_sma[i - 1]
                prev_slow = slow_sma[i - 1]
                curr_fast = fast_sma[i]
                curr_slow = slow_sma[i]

                if prev_fast is None or prev_slow is None:
                    continue

                # Golden cross: fast crosses above slow
                if prev_fast <= prev_slow and curr_fast > curr_slow:
                    bar_ts = bar_list[i].ts

                    sig_key = (dataset_id, instrument_id, timeframe.id, bar_ts)
                    if sig_key in existing_signals:
                        skipped_count += 1
                        continue
                    existing_signals.add(sig_key)

                    signals_batch.append(Signal(
                        dataset_id=dataset_id,
                        instrument_id=instrument_id,
                        timeframe_id=timeframe.id,
                        ts=bar_ts,
                        signal_type=signal_type,
                        params_json=params_str,
                        created_at=datetime.utcnow(),
                    ))
                    signal_count += 1

                    if len(signals_batch) >= batch_size:
                        db.bulk_save_objects(signals_batch)
                        db.commit()
                        signals_batch = []

            if limit is not None and signal_count >= limit:
                break

        if signals_batch:
            db.bulk_save_objects(signals_batch)
            db.commit()

        job.status = "completed"
        job.result = json.dumps({
            "signal_count": signal_count,
            "skipped_count": skipped_count,
            "signal_type": signal_type,
            "timeframe": timeframe_name,
        })
        db.commit()

        return {"signal_count": signal_count, "skipped_count": skipped_count}

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
def backtest_task(
    self, job_id: int, run_id: int, strategy_id: int,
    start_ts: str = None, end_ts: str = None
):
    """
    Run backtest for a strategy.
    Walks bars in time order, evaluates rule_json conditions on window_features,
    manages positions with TP/SL/max_hold/session_end exits.
    """
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        job.status = "running"
        job.celery_task_id = self.request.id
        db.commit()

        run = db.query(BacktestRun).filter(BacktestRun.id == run_id).first()
        run.status = "running"
        db.commit()

        strategy = db.query(Strategy).filter(Strategy.id == strategy_id).first()
        if not strategy:
            raise ValueError(f"Strategy {strategy_id} not found")

        rules = json.loads(strategy.rule_json)
        allowed_weekdays = set(int(d) for d in strategy.weekdays.split(",") if d.strip())

        # HTF signal setup
        htf_enabled = strategy.require_htf_signal and strategy.htf_signal_type and strategy.htf_timeframe_id
        htf_signals = []
        htf_lookback_td = None
        if htf_enabled:
            htf_lookback_td = timedelta(hours=strategy.htf_lookback_hours or 24)
            htf_signals_q = (
                db.query(Signal.ts)
                .filter(
                    Signal.dataset_id == strategy.dataset_id,
                    Signal.instrument_id == strategy.instrument_id,
                    Signal.timeframe_id == strategy.htf_timeframe_id,
                    Signal.signal_type == strategy.htf_signal_type,
                )
                .order_by(Signal.ts)
                .all()
            )
            htf_signals = [r[0] for r in htf_signals_q]

        def find_latest_htf_signal(bar_ts):
            """Find the most recent HTF signal before bar_ts within lookback window."""
            if not htf_signals:
                return None
            import bisect
            # bar_ts must be timezone-aware
            idx = bisect.bisect_right(htf_signals, bar_ts) - 1
            if idx < 0:
                return None
            sig_ts = htf_signals[idx]
            if bar_ts - sig_ts <= htf_lookback_td:
                return sig_ts
            return None

        # Parse session times
        sess_start_h, sess_start_m = map(int, strategy.session_start.split(":"))
        sess_end_h, sess_end_m = map(int, strategy.session_end.split(":"))
        sess_start_minutes = sess_start_h * 60 + sess_start_m
        sess_end_minutes = sess_end_h * 60 + sess_end_m
        # Day-crossing: session_start > session_end (e.g. 21:00-02:00)
        day_crossing = sess_start_minutes > sess_end_minutes

        tz = pytz.timezone("Asia/Tokyo")

        # Load bars for this dataset/instrument/timeframe
        query = db.query(Bar).filter(
            Bar.dataset_id == strategy.dataset_id,
            Bar.instrument_id == strategy.instrument_id,
            Bar.timeframe_id == strategy.timeframe_id,
        )

        if start_ts:
            filter_start = datetime.fromisoformat(start_ts.replace('Z', '+00:00'))
            query = query.filter(Bar.ts >= filter_start)
        if end_ts:
            filter_end = datetime.fromisoformat(end_ts.replace('Z', '+00:00'))
            query = query.filter(Bar.ts <= filter_end)

        bars = query.order_by(Bar.ts).all()

        if not bars:
            run.status = "completed"
            run.result_json = json.dumps({"total_trades": 0, "message": "No bars found"})
            job.status = "completed"
            job.result = json.dumps({"total_trades": 0})
            db.commit()
            return {"total_trades": 0}

        # Build feature lookup: window.end_ts -> WindowFeature dict
        windows_with_features = (
            db.query(Window, WindowFeature)
            .join(WindowFeature, WindowFeature.window_id == Window.id)
            .filter(
                Window.dataset_id == strategy.dataset_id,
                Window.instrument_id == strategy.instrument_id,
                Window.timeframe_id == strategy.timeframe_id,
            )
            .all()
        )

        feature_names = [
            "sma5_slope_5", "sma20_slope_5", "sma20_slope_20", "sma60_slope_20",
            "close_to_sma20", "spread_5_20", "spread_20_60", "atr14", "vol_mean",
        ]
        feature_map = {}
        feature_map_naive = {}
        for window, wf in windows_with_features:
            feat_dict = {name: getattr(wf, name) for name in feature_names}
            feature_map[window.end_ts] = feat_dict
            naive_utc = window.end_ts.replace(tzinfo=None) if window.end_ts.tzinfo else window.end_ts
            feature_map_naive[naive_utc] = feat_dict

        def get_features_for_bar(bar):
            if bar.ts in feature_map:
                return feature_map[bar.ts]
            ts_naive = bar.ts.replace(tzinfo=None) if bar.ts.tzinfo else bar.ts
            if ts_naive in feature_map_naive:
                return feature_map_naive[ts_naive]
            return None

        def is_in_session(bar_ts):
            if bar_ts.tzinfo is None:
                bar_ts = pytz.utc.localize(bar_ts)
            local_ts = bar_ts.astimezone(tz)
            if local_ts.weekday() not in allowed_weekdays:
                return False
            bar_minutes = local_ts.hour * 60 + local_ts.minute
            if day_crossing:
                return bar_minutes >= sess_start_minutes or bar_minutes < sess_end_minutes
            else:
                return sess_start_minutes <= bar_minutes < sess_end_minutes

        def evaluate_rules(features, rules_list):
            for rule in rules_list:
                feat_name = rule.get("feature")
                operator = rule.get("operator")
                threshold = rule.get("value")
                if feat_name not in features:
                    return False
                val = features[feat_name]
                if operator == ">" and not (val > threshold):
                    return False
                elif operator == "<" and not (val < threshold):
                    return False
                elif operator == ">=" and not (val >= threshold):
                    return False
                elif operator == "<=" and not (val <= threshold):
                    return False
            return True

        # Pip size heuristic
        sample_close = bars[0].close
        pip_size = 0.01 if sample_close > 50 else 0.0001

        # Walk bars
        trades_list = []
        position = None
        cooldown_remaining = 0

        for i, bar in enumerate(bars):
            if position is not None:
                position["hold_bars"] += 1
                exited = False
                exit_price = None
                exit_reason = None

                side = position["side"]
                tp_price = position["tp_price"]
                sl_price = position["sl_price"]

                if side == "long":
                    if bar.high >= tp_price:
                        exit_price = tp_price
                        exit_reason = "tp"
                        exited = True
                    elif bar.low <= sl_price:
                        exit_price = sl_price
                        exit_reason = "sl"
                        exited = True
                else:
                    if bar.low <= tp_price:
                        exit_price = tp_price
                        exit_reason = "tp"
                        exited = True
                    elif bar.high >= sl_price:
                        exit_price = sl_price
                        exit_reason = "sl"
                        exited = True

                if not exited and position["hold_bars"] >= strategy.max_hold_bars:
                    exit_price = bar.close
                    exit_reason = "time"
                    exited = True

                if not exited and not is_in_session(bar.ts):
                    exit_price = bar.close
                    exit_reason = "session_end"
                    exited = True

                if exited:
                    if side == "long":
                        pnl_pips = (exit_price - position["entry_price"]) / pip_size
                    else:
                        pnl_pips = (position["entry_price"] - exit_price) / pip_size
                    pnl_pips -= strategy.fee_pips

                    sl_dist = abs(position["entry_price"] - position["sl_raw"]) / pip_size
                    r_multiple = pnl_pips / sl_dist if sl_dist > 0 else 0

                    trades_list.append(Trade(
                        run_id=run_id,
                        entry_ts=position["entry_ts"],
                        entry_price=position["entry_price"],
                        exit_ts=bar.ts,
                        exit_price=exit_price,
                        side=side,
                        pnl_pips=round(pnl_pips, 2),
                        r_multiple=round(r_multiple, 4),
                        exit_reason=exit_reason,
                        signal_ts=position.get("signal_ts"),
                    ))
                    position = None
                    cooldown_remaining = strategy.cooldown_bars
            else:
                if cooldown_remaining > 0:
                    cooldown_remaining -= 1
                    continue

                if not is_in_session(bar.ts):
                    continue

                features = get_features_for_bar(bar)
                if features is None:
                    continue

                if not evaluate_rules(features, rules):
                    continue

                # HTF signal check
                current_signal_ts = None
                if htf_enabled:
                    current_signal_ts = find_latest_htf_signal(bar.ts)
                    if current_signal_ts is None:
                        continue

                atr14 = features.get("atr14", 0)
                side = strategy.side

                if strategy.entry_timing == "next_open" and i + 1 < len(bars):
                    entry_price = bars[i + 1].open
                    entry_ts = bars[i + 1].ts
                else:
                    entry_price = bar.close
                    entry_ts = bar.ts

                if strategy.tp_type == "atr":
                    tp_dist = atr14 * strategy.tp_value
                else:
                    tp_dist = strategy.tp_value * pip_size

                if strategy.sl_type == "atr":
                    sl_dist = atr14 * strategy.sl_value
                else:
                    sl_dist = strategy.sl_value * pip_size

                if side == "long":
                    tp_price = entry_price + tp_dist
                    sl_price = entry_price - sl_dist
                    sl_raw = entry_price - sl_dist
                else:
                    tp_price = entry_price - tp_dist
                    sl_price = entry_price + sl_dist
                    sl_raw = entry_price + sl_dist

                position = {
                    "entry_ts": entry_ts,
                    "entry_price": entry_price,
                    "side": side,
                    "tp_price": tp_price,
                    "sl_price": sl_price,
                    "sl_raw": sl_raw,
                    "hold_bars": 0,
                    "atr14": atr14,
                    "signal_ts": current_signal_ts,
                }

        # Close open position at end of data
        if position is not None:
            last_bar = bars[-1]
            side = position["side"]
            exit_price = last_bar.close
            if side == "long":
                pnl_pips = (exit_price - position["entry_price"]) / pip_size
            else:
                pnl_pips = (position["entry_price"] - exit_price) / pip_size
            pnl_pips -= strategy.fee_pips
            sl_dist = abs(position["entry_price"] - position["sl_raw"]) / pip_size
            r_multiple = pnl_pips / sl_dist if sl_dist > 0 else 0
            trades_list.append(Trade(
                run_id=run_id,
                entry_ts=position["entry_ts"],
                entry_price=position["entry_price"],
                exit_ts=last_bar.ts,
                exit_price=exit_price,
                side=side,
                pnl_pips=round(pnl_pips, 2),
                r_multiple=round(r_multiple, 4),
                exit_reason="end_of_data",
                signal_ts=position.get("signal_ts"),
            ))

        if trades_list:
            db.bulk_save_objects(trades_list)
            db.commit()

        # Calculate summary
        total_trades = len(trades_list)
        if total_trades > 0:
            pnls = [t.pnl_pips for t in trades_list]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            win_count = len(wins)
            loss_count = len(losses)
            win_rate = win_count / total_trades

            gross_profit = sum(wins) if wins else 0
            gross_loss = abs(sum(losses)) if losses else 0
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else 999.99

            r_multiples = [t.r_multiple for t in trades_list]
            avg_r = sum(r_multiples) / len(r_multiples)
            total_pnl_pips = sum(pnls)
            avg_pnl_pips = total_pnl_pips / total_trades

            cumulative = 0
            peak = 0
            max_dd = 0
            for p in pnls:
                cumulative += p
                if cumulative > peak:
                    peak = cumulative
                dd = peak - cumulative
                if dd > max_dd:
                    max_dd = dd

            max_consec_wins = 0
            max_consec_losses = 0
            current_consec = 0
            current_is_win = None
            for p in pnls:
                is_win = p > 0
                if is_win == current_is_win:
                    current_consec += 1
                else:
                    current_consec = 1
                    current_is_win = is_win
                if is_win and current_consec > max_consec_wins:
                    max_consec_wins = current_consec
                if not is_win and current_consec > max_consec_losses:
                    max_consec_losses = current_consec

            result_summary = {
                "total_trades": total_trades,
                "wins": win_count, "losses": loss_count,
                "win_rate": round(win_rate, 4),
                "profit_factor": round(min(profit_factor, 999.99), 2),
                "avg_r": round(avg_r, 4),
                "max_dd": round(max_dd, 2),
                "total_pnl_pips": round(total_pnl_pips, 2),
                "avg_pnl_pips": round(avg_pnl_pips, 2),
                "max_consecutive_wins": max_consec_wins,
                "max_consecutive_losses": max_consec_losses,
            }
        else:
            result_summary = {
                "total_trades": 0, "wins": 0, "losses": 0,
                "win_rate": 0, "profit_factor": 0, "avg_r": 0, "max_dd": 0,
                "total_pnl_pips": 0, "avg_pnl_pips": 0,
                "max_consecutive_wins": 0, "max_consecutive_losses": 0,
            }

        run.status = "completed"
        run.result_json = json.dumps(result_summary)
        job.status = "completed"
        job.result = json.dumps(result_summary)
        db.commit()

        return result_summary

    except Exception as e:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(e)
        run_obj = db.query(BacktestRun).filter(BacktestRun.id == run_id).first()
        if run_obj:
            run_obj.status = "failed"
        db.commit()
        raise
    finally:
        db.close()


@celery_app.task(bind=True)
def resample_bars_task(
    self, job_id: int, dataset_id: int,
    from_tf: str = "M1", to_tf: str = "H1",
    start_ts: str = None, end_ts: str = None, limit: int = None
):
    """
    Resample bars from one timeframe to another (e.g. M1 -> H1).
    Bucketing is done in Asia/Tokyo timezone.
    """
    db = SessionLocal()
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        job.status = "running"
        job.celery_task_id = self.request.id
        db.commit()

        dataset = db.query(Dataset).filter(Dataset.id == dataset_id).first()
        if not dataset:
            raise ValueError(f"Dataset {dataset_id} not found")

        from_timeframe = get_or_create_timeframe(db, from_tf)
        to_timeframe = get_or_create_timeframe(db, to_tf)

        bucket_minutes = to_timeframe.minutes

        tz = pytz.timezone(dataset.timezone or "Asia/Tokyo")

        query = db.query(Bar).filter(
            Bar.dataset_id == dataset_id,
            Bar.timeframe_id == from_timeframe.id,
        )

        if start_ts:
            filter_start = datetime.fromisoformat(start_ts.replace('Z', '+00:00'))
            query = query.filter(Bar.ts >= filter_start)
        if end_ts:
            filter_end = datetime.fromisoformat(end_ts.replace('Z', '+00:00'))
            query = query.filter(Bar.ts <= filter_end)

        bars = query.order_by(Bar.instrument_id, Bar.ts).all()

        if not bars:
            job.status = "completed"
            job.result = json.dumps({
                "generated_count": 0,
                "skipped_count": 0,
                "message": "No source bars found",
            })
            db.commit()
            return {"generated_count": 0}

        grouped = defaultdict(list)
        for bar in bars:
            grouped[bar.instrument_id].append(bar)

        generated_count = 0
        skipped_count = 0
        batch = []
        batch_size = 5000

        for instrument_id, bar_list in grouped.items():
            buckets = defaultdict(list)
            for bar in bar_list:
                bar_ts = bar.ts
                if bar_ts.tzinfo is None:
                    bar_ts = pytz.utc.localize(bar_ts)
                local_ts = bar_ts.astimezone(tz)
                # Floor to bucket boundary based on bucket_minutes
                if bucket_minutes < 60:
                    # Sub-hour: floor minute to nearest multiple (M5, M15, etc.)
                    floored_min = (local_ts.minute // bucket_minutes) * bucket_minutes
                    bucket_dt = local_ts.replace(minute=floored_min, second=0, microsecond=0)
                elif bucket_minutes == 60:
                    # Exactly 1 hour
                    bucket_dt = local_ts.replace(minute=0, second=0, microsecond=0)
                else:
                    # Multi-hour (H4, etc.): floor hour to nearest multiple
                    hours = bucket_minutes // 60
                    floored_hour = (local_ts.hour // hours) * hours
                    bucket_dt = local_ts.replace(hour=floored_hour, minute=0, second=0, microsecond=0)
                buckets[bucket_dt].append(bar)

            sorted_buckets = sorted(buckets.items())

            for bucket_ts, bucket_bars in sorted_buckets:
                if limit is not None and generated_count >= limit:
                    break

                bucket_bars.sort(key=lambda b: b.ts)

                open_val = bucket_bars[0].open
                high_val = max(b.high for b in bucket_bars)
                low_val = min(b.low for b in bucket_bars)
                close_val = bucket_bars[-1].close
                volume_val = sum(b.volume for b in bucket_bars)

                bucket_ts_utc = bucket_ts.astimezone(pytz.utc)

                existing = db.query(Bar).filter(
                    Bar.dataset_id == dataset_id,
                    Bar.instrument_id == instrument_id,
                    Bar.timeframe_id == to_timeframe.id,
                    Bar.ts == bucket_ts_utc,
                ).first()

                if existing:
                    skipped_count += 1
                    continue

                batch.append(Bar(
                    dataset_id=dataset_id,
                    instrument_id=instrument_id,
                    timeframe_id=to_timeframe.id,
                    ts=bucket_ts_utc,
                    open=open_val,
                    high=high_val,
                    low=low_val,
                    close=close_val,
                    volume=volume_val,
                ))
                generated_count += 1

                if len(batch) >= batch_size:
                    db.bulk_save_objects(batch)
                    db.commit()
                    batch = []

            if limit is not None and generated_count >= limit:
                break

        if batch:
            db.bulk_save_objects(batch)
            db.commit()

        job.status = "completed"
        job.result = json.dumps({
            "generated_count": generated_count,
            "skipped_count": skipped_count,
            "from_tf": from_tf,
            "to_tf": to_tf,
        })
        db.commit()

        return {"generated_count": generated_count, "skipped_count": skipped_count}

    except Exception as e:
        job = db.query(Job).filter(Job.id == job_id).first()
        if job:
            job.status = "failed"
            job.error = str(e)
            db.commit()
        raise
    finally:
        db.close()
