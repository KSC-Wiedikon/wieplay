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

    # Object storage, S3 API (PLAN §7.5): Hetzner Object Storage on the VPS, SeaweedFS
    # locally. Empty endpoint = video features off (the app still runs).
    s3_endpoint: str = ""
    s3_region: str = "us-east-1"
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_path_style: bool = False  # True for SeaweedFS/MinIO; Hetzner takes virtual-host style
    # Signed segment URLs live this long (PLAN §5.2: 5 minutes, one segment each).
    media_url_ttl_seconds: int = 300
    # Browser uploads: S3 multipart, parts signed by the API (PLAN §7.2).
    upload_part_bytes: int = 64 * 1024 * 1024
    upload_part_url_ttl_seconds: int = 3600
    max_file_bytes: int = 200 * 1024**3
    max_files_per_video: int = 20

    # Workers: leases and the reaper (PLAN §8.1).
    job_lease_seconds: int = 300
    reaper_interval_seconds: int = 60

    # Automatic analysis (PLAN §10.1): analyse every volleyball video once it is ready, and
    # train the rally finder this long after a coach marked a game's rallies done (one run
    # after a burst of games).
    analysis_auto: bool = True
    analysis_train_delay_seconds: int = 600
    # Players in a video (PLAN §10.5, beta): ~2 h of the Lenovo's CPU a match and many cards
    # for the coach to merge, so off by default: nothing queues the players job by itself
    # (not after the court job, not in a court backfill); a coach finds them on demand
    # (POST /api/videos/{id}/players/find) or an admin with `nomal.cli players`. The
    # attribution of actions runs whenever a video has players.
    players_auto: bool = False
    # Suggested actions (PLAN §10.4a) are written only once the action finder's held-out score
    # on games whose actions a coach reviewed passes the bars (auto); "on" writes them anyway
    # (testing), "off" never. Below the bars the finder still runs and measures itself.
    action_suggestions: Literal["auto", "on", "off"] = "auto"
    action_suggest_min_precision: float = 0.70  # fully right (±0.3 s, skill, side) / suggested
    action_suggest_min_recall: float = 0.6  # coach's tags with a contact candidate ±0.3 s

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

    @property
    def video_enabled(self) -> bool:
        return bool(self.s3_endpoint and self.s3_bucket)

    @property
    def storage_origin(self) -> str | None:
        """Origin browsers fetch media and send upload parts to (for the CSP)."""
        if not self.video_enabled:
            return None
        parts = urlsplit(self.s3_endpoint)
        host = parts.netloc if self.s3_path_style else f"{self.s3_bucket}.{parts.netloc}"
        return f"{parts.scheme}://{host}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
