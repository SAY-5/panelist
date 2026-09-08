from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://panelist:panelist@localhost:5439/panelist"
    log_level: str = "INFO"

    # Queue routing
    lease_seconds: int = 900

    # Attention checks
    attention_fraction: float = 0.1
    attention_window: int = 10
    attention_min_checks: int = 3
    attention_threshold: float = 0.7
    attention_tolerance: float = 1.0

    # Data delivery
    delivery_dir: str = "./deliveries"
    delivery_s3_bucket: str = ""
    aws_endpoint_url: str = ""
    aws_region: str = "us-east-1"

    # Bootstrap admin key, only used by `panelist bootstrap`
    bootstrap_admin_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
