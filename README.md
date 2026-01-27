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

## Web UI

FXLab includes a web-based management interface accessible at `http://localhost:18000/ui`.

### UI Features

| Page | URL | Description |
|------|-----|-------------|
| Home | `/ui` | Dashboard with links to main features |
| Datasets | `/ui/datasets` | List all imported datasets |
| Dataset Detail | `/ui/datasets/{id}` | View dataset info, stats, and generate windows/labels |
| Import CSV | `/ui/import` | Upload ForexTester CSV files |
| Job Status | `/ui/jobs/{id}` | Monitor job progress (auto-refreshes every 3 seconds) |

### UI Workflow

1. **Import CSV**: Navigate to `/ui/import`, fill in the form and upload a CSV file
2. **Monitor Import**: You'll be redirected to the job status page to track progress
3. **View Dataset**: Once complete, go to `/ui/datasets` and click "Details"
4. **Generate Windows**: On the dataset detail page, set parameters and click "Generate Windows"
5. **Generate Labels**: Similarly, configure TP/SL parameters and click "Generate Labels"
6. **View Stats**: Stats are displayed on the dataset detail page after generation completes

### Parameter Notes

- **Lookback N**: Number of bars to include in each window (default: 128)
- **Step**: Step size between consecutive windows (default: 1)
- **Lookahead M**: Number of bars to look ahead for TP/SL calculation (default: 32)
- **TP/SL Ratio**: Take profit and stop loss ratios (default: 1.0)
- **Limit**: Maximum number of windows/labels to generate (optional)
- **Start/End TS**: ISO8601 datetime to filter bars (e.g., `2003-06-01T00:00:00Z`)
