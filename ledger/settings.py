from __future__ import annotations

from functools import lru_cache
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # App
    app_name: str = Field(default="DoubleX Ledger", env="APP_NAME")
    app_env: Literal["dev", "prod"] = Field(default="dev", env="APP_ENV")
    app_version: str = Field(default="0.1.0", env="APP_VERSION")

    # Database
    postgres_dsn: str = Field(
        default="postgresql+psycopg://ledger:ledger@localhost:5432/ledger",
        env="POSTGRES_DSN",
    )
    db_pool_size: int = Field(default=20, ge=1, env="DB_POOL_SIZE")

    # Observability
    prometheus_port: int = Field(default=9000, ge=1, le=65535, env="PROMETHEUS_PORT")
    otel_exporter_otlp_endpoint: Optional[str] = Field(
        default=None, env="OTEL_EXPORTER_OTLP_ENDPOINT"
    )

    # Domain/config
    default_base_ccy: str = Field(default="EUR", min_length=3, max_length=3, env="DEFAULT_BASE_CCY")
    max_tx_per_req: int = Field(default=1000, ge=1, env="MAX_TX_PER_REQ")
    idempotency_ttl_seconds: int = Field(default=86400, ge=0, env="IDEMPOTENCY_TTL_SECONDS")
    cut_off_hour: int = Field(default=23, ge=0, le=23, env="CUT_OFF_HOUR")
    cut_off_minute: int = Field(default=59, ge=0, le=59, env="CUT_OFF_MINUTE")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

