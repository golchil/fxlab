# FXLab

ForexTester CSV import, window generation, and TP/SL labeling MVP.

## Tech Stack

- PostgreSQL 16 + TimescaleDB (bars as hypertable)
- Redis (Celery broker)
- MinIO (future use)
- FastAPI + Uvicorn
- SQLAlchemy 2 + Alembic
- Celery worker
- pandas (CSV chunked import)

## Port Mapping

| Service  | Host Port | Container Port |
|----------|-----------|----------------|
| API      | 18000     | 8000           |
| Postgres | 15432     | 5432           |
| Redis    | 16379     | 6379           |
| MinIO    | 19000     | 9000           |
| MinIO UI | 19001     | 9001           |

## Quick Start

```bash
# Build and start all services
make up

# Run migrations
make migrate

# Check logs
make logs

# Stop services
make down
```

## CSV Format

```
<TICKER>,<DTYYYYMMDD>,<TIME>,<OPEN>,<HIGH>,<LOW>,<CLOSE>,<VOL>
```

- `<DTYYYYMMDD>`: Date in YYYYMMDD format
- `<TIME>`: Time in HHMM format (e.g., 1 = 00:01, 930 = 09:30)
- Timezone is stored in `datasets.timezone` (default: Asia/Tokyo)

Example:
```
USDJPY,20240101,0,110.123,110.456,110.001,110.234,100
USDJPY,20240101,1,110.234,110.567,110.100,110.345,150
```

## API Endpoints

### Datasets

- `POST /datasets/import` - Import CSV file
  - Form fields: `file`, `name`, `description`, `timezone`, `timeframe`
  - Returns: `job_id`

- `GET /datasets` - List all datasets
- `GET /datasets/{id}` - Get dataset details with bar count and time range

### Windows

- `POST /datasets/{id}/windows` - Generate windows
  - Body: `{"lookback_n": 128, "step": 1}`
  - Returns: `job_id`

- `GET /datasets/{id}/windows/stats` - Get window statistics

### Labels

- `POST /datasets/{id}/labels/tp-sl` - Generate TP/SL labels
  - Body: `{"lookahead_m": 32, "tp_r": 1.0, "sl_r": 1.0}`
  - Returns: `job_id`

- `GET /datasets/{id}/labels/stats` - Get label statistics

### Jobs

- `GET /jobs/{job_id}` - Get job status and result

## Example Workflow

```bash
# 1. Import CSV
curl -X POST http://localhost:18000/datasets/import \
  -F "file=@sample.csv" \
  -F "name=USDJPY_2024" \
  -F "timezone=Asia/Tokyo" \
  -F "timeframe=M1"

# Response: {"job_id": 1, "message": "Import job started"}

# 2. Check job status
curl http://localhost:18000/jobs/1

# 3. Get dataset info
curl http://localhost:18000/datasets/1

# 4. Generate windows
curl -X POST http://localhost:18000/datasets/1/windows \
  -H "Content-Type: application/json" \
  -d '{"lookback_n": 128, "step": 1}'

# 5. Generate labels
curl -X POST http://localhost:18000/datasets/1/labels/tp-sl \
  -H "Content-Type: application/json" \
  -d '{"lookahead_m": 32, "tp_r": 1.0, "sl_r": 1.0}'

# 6. Get stats
curl http://localhost:18000/datasets/1/windows/stats
curl http://localhost:18000/datasets/1/labels/stats
```
