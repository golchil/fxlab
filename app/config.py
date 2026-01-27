from typing import List
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://fxlab:fxlab@localhost:15432/fxlab"
    redis_url: str = "redis://localhost:16379/0"
    celery_broker_url: str = "redis://localhost:16379/0"
    celery_result_backend: str = "redis://localhost:16379/0"
    default_timezone: str = "Asia/Tokyo"

    # MinIO settings
    minio_endpoint: str = "localhost:19000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "fxlab"
    minio_secure: bool = False

    # Image generation settings
    image_size: int = 512
    default_ma_periods: List[int] = [5, 20, 60]

    class Config:
        env_file = ".env"


settings = Settings()
