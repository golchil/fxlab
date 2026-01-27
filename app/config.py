from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+psycopg://fxlab:fxlab@localhost:15432/fxlab"
    redis_url: str = "redis://localhost:16379/0"
    celery_broker_url: str = "redis://localhost:16379/0"
    celery_result_backend: str = "redis://localhost:16379/0"
    default_timezone: str = "Asia/Tokyo"

    class Config:
        env_file = ".env"


settings = Settings()
