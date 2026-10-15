"""Worker settings, from NOMAL_WORKER_* environment variables."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOMAL_WORKER_", extra="ignore")

    # The API through the SSH tunnel on the worker host, e.g. http://172.17.0.1:18090
    # (deploy/lenovo/nomal-api-tunnel.service; worker routes refuse requests that come
    # through Cloudflare).
    api_url: str
    token: str = ""
    token_file: Path | None = None
    caps: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["ingest", "transcode", "thumbs", "nas"]
    )

    work_dir: Path = Path("/var/lib/nomal-worker")
    # NAS: originals live under raw_root; the inbox share is scanned when "inbox" is a cap.
    raw_root: Path = Path("/nas/nomal/raw")
    inbox_root: Path | None = None
    inbox_scan_seconds: int = 60
    inbox_stable_seconds: int = 120

    encoder: Literal["x264", "vaapi"] = "x264"
    vaapi_device: str = "/dev/dri/renderD128"
    threads: int = 6  # x264 threads; keeps the box responsive for its other services
    x264_preset: str = "veryfast"

    # The watermark's font (Debian package fonts-dejavu-core in the worker image).
    font_file: str = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

    heartbeat_seconds: int = 30
    upload_concurrency: int = 6

    @field_validator("caps", mode="before")
    @classmethod
    def _split(cls, value: object) -> object:
        if isinstance(value, str):
            return [c.strip() for c in value.split(",") if c.strip()]
        return value

    def resolved_token(self) -> str:
        if self.token:
            return self.token
        if self.token_file is not None:
            return self.token_file.read_text().strip()
        raise ValueError("set NOMAL_WORKER_TOKEN or NOMAL_WORKER_TOKEN_FILE")
