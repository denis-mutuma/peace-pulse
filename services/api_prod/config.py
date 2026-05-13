from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="PEACEPULSE_", extra="ignore")

    app_name: str = "PeacePulse Hub"
    env: str = "development"
    database_url: str = f"sqlite:///{ROOT / 'data' / 'peacepulse-prod.db'}"
    jwt_secret: str = "change-this-secret-before-production"
    jwt_issuer: str = "peacepulse"
    bootstrap_token: str = ""
    access_token_minutes: int = 30
    refresh_token_days: int = 14
    s3_endpoint_url: str = ""
    s3_bucket: str = "peacepulse-evidence"
    remote_sync_url: str = ""
    remote_sync_hub_id: str = ""
    remote_sync_hub_secret: str = ""
    remote_sync_timeout_seconds: float = 10.0
    evidence_storage_dir: Path = ROOT / "data" / "storage" / "evidence-prod"
    allow_demo_seed: bool = os.environ.get("PEACEPULSE_ENV", "development") != "production"

    @property
    def sqlite_path(self) -> Path | None:
        if not self.database_url.startswith("sqlite:///"):
            return None
        return Path(self.database_url.removeprefix("sqlite:///"))


@lru_cache
def get_settings() -> Settings:
    return Settings()


def validate_production_settings(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    if settings.env != "production":
        return
    if settings.jwt_secret == "change-this-secret-before-production":
        raise RuntimeError("PEACEPULSE_JWT_SECRET must be set to a non-default value in production.")
    if not settings.bootstrap_token:
        raise RuntimeError("PEACEPULSE_BOOTSTRAP_TOKEN must be set in production.")
    remote_sync_fields = (settings.remote_sync_url, settings.remote_sync_hub_id, settings.remote_sync_hub_secret)
    if any(remote_sync_fields) and not all(remote_sync_fields):
        raise RuntimeError(
            "PEACEPULSE_REMOTE_SYNC_URL, PEACEPULSE_REMOTE_SYNC_HUB_ID, and PEACEPULSE_REMOTE_SYNC_HUB_SECRET must be set together."
        )
