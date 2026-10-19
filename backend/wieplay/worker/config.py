"""Worker settings, from WIEPLAY_WORKER_* environment variables."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class WorkerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WIEPLAY_WORKER_", extra="ignore")

    # The API through the SSH tunnel on the worker host, e.g. http://172.17.0.1:18090
    # (deploy/lenovo/wieplay-api-tunnel.service; worker routes refuse requests that come
    # through Cloudflare).
    api_url: str
    token: str = ""
    token_file: Path | None = None
    caps: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["ingest", "transcode", "thumbs", "nas"]
    )

    work_dir: Path = Path("/var/lib/wieplay-worker")
    # NAS: originals live under raw_root; the inbox share is scanned when "inbox" is a cap.
    raw_root: Path = Path("/nas/wieplay/raw")
    inbox_root: Path | None = None
    # Automatic analysis (cap "analyse"): signals and rally models are written here, on the
    # NAS next to raw/ (analysis/, analysis-dev/).
    analysis_root: Path | None = None
    # Decoding for the analysis. The CPU by default: in the worker image VAAPI was not
    # faster (the model is the bottleneck) and its scaler found ~1 % fewer balls than the
    # CPU's area filter (16.10.2026); "vaapi" falls back to the CPU by itself.
    analysis_hwaccel: Literal["vaapi", "none"] = "none"
    analysis_threads: int = 6
    # The players job (PLAN §10.5): its weights' folder (the image has them in
    # /opt/wieplay-weights) and ONNX Runtime providers (empty: the CPU; a GPU worker sets
    # e.g. CUDAExecutionProvider,CPUExecutionProvider).
    players_weights: Path | None = None
    players_providers: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Where the players job reads the video: the originals on the NAS (the Lenovo), or the
    # 1080p rendition from the bucket through signed URLs, writing its files there too (a
    # GPU worker without the NAS share, PLAN §8.2).
    players_source: Literal["nas", "bucket"] = "nas"
    # Rally windows closer than this are one stretch the tracker follows the people through
    # (the dead time between them decoded too): a GPU worker follows a whole set (players
    # v2, PLAN §10.5); the CPU looks at the rallies only.
    players_join_gap_s: float = 2.0
    players_detect_fps: float = 10.0
    # The official scoresheet's labels (who served, who was on court; PLAN §10.5): used when
    # the API sends them (a sheet lined up with the rallies).
    players_sheet: bool = True

    # The busy guard of a GPU worker (gpu_guard.py, PLAN §8.2): before every claim and
    # between frames, nobody else may need the card.
    gpu_guard: bool = False
    gpu_max_other_mib: int = 1024  # other processes' GPU memory (the meeting scribe: ~4 GB)
    gpu_max_util: int = 40  # percent, while this worker runs nothing
    gpu_need_mains: bool = True  # never on battery
    gpu_pause_file: Path | None = None  # exists: wait (the local pause switch)
    gpu_lock_file: Path | None = None  # one job at a time for the GPU workers of a machine
    gpu_max_pause_s: int = 1200  # a job paused this long goes back to the queue
    gpu_retry_after_s: int = 1800
    gpu_mem_limit_mib: int = 2048  # ONNX Runtime's arena on the card, per session
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

    @field_validator("caps", "players_providers", mode="before")
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
        raise ValueError("set WIEPLAY_WORKER_TOKEN or WIEPLAY_WORKER_TOKEN_FILE")
