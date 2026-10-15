"""Settings, read from NOMAL_* environment variables (and backend/.env when present)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOMAL_", env_file=".env", extra="ignore")

    env: Literal["local", "dev", "prod"] = "local"
    database_url: str = "postgresql+psycopg://nomal:nomal@127.0.0.1:55488/nomal"

    # Where browsers reach Nomal (web app and API share this origin).
    public_url: str = "http://localhost:5173"

    # Wiedisync bridge (PLAN §4). The browser is sent to `bridge_authorize_url`; the
    # API calls `bridge_api_url` server-side with the service token.
    bridge_authorize_url: str = "http://localhost:5173/api/dev-bridge/authorize"
    bridge_api_url: str = ""
    bridge_service_token: str = ""
    bridge_issuer: str = "wiedisync-local"
    # kid -> Ed25519 public key (PEM). JSON object in the environment.
    bridge_public_keys: dict[str, str] = Field(default_factory=dict)

    session_hours: int = 8
    cookie_secure: bool = True

    # Local development only: a fake bridge with fixture data and test personas.
    dev_bridge: bool = False

    # Background sync with Wiedisync; 0 disables the loop (tests, one-off scripts).
    sync_interval_minutes: int = 60

    # Built web app (web/dist) to serve at "/"; None when Vite serves it in development.
    web_dist: str | None = None

    @model_validator(mode="after")
    def _check(self) -> Settings:
        if self.dev_bridge and self.env != "local":
            raise ValueError("NOMAL_DEV_BRIDGE is only allowed with NOMAL_ENV=local")
        return self

    @property
    def public_origin(self) -> str:
        parts = urlsplit(self.public_url)
        return f"{parts.scheme}://{parts.netloc}"

    @property
    def callback_url(self) -> str:
        return self.public_url.rstrip("/") + "/api/auth/callback"


@lru_cache
def get_settings() -> Settings:
    return Settings()
