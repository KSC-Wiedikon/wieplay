"""Fetch a YouTube or Twitch video with yt-dlp (PLAN §7.2.2).

Runs on the Lenovo worker: YouTube often refuses downloads from data-centre addresses, the
home connection works. Only the YouTube and Twitch video extractors are loaded, playlists
and live streams are refused, no cookies are used (public and unlisted videos only), and
the channel is checked against the allow-list before a single video byte is fetched.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from wieplay.core import imports

log = logging.getLogger(__name__)

EXTRACTORS = [r"youtube$", r"twitch:vod$"]
# 1080p or less, 50/60 fps and H.264 + AAC when offered (no re-encode needed to play it
# anywhere), else the best at most 1080p.
FORMAT = (
    "bv*[height<=1080][fps>=50][vcodec^=avc1]+ba[ext=m4a]"
    "/bv*[height<=1080][vcodec^=avc1]+ba[ext=m4a]"
    "/bv*[height<=1080]+ba/b[height<=1080]/b"
)
# What the API keeps from yt-dlp's metadata: where the video came from, nothing more.
INFO_KEYS = (
    "channel",
    "channel_id",
    "channel_url",
    "uploader",
    "uploader_id",
    "uploader_url",
    "title",
    "upload_date",
    "duration",
    "live_status",
)
# Messages after which retrying cannot help.
GONE = (
    "private video",
    "video unavailable",
    "has been removed",
    "members-only",
    "join this channel",
    "confirm your age",
    "does not exist",
    "not available",
)


# YouTube's bot check ("Sign in to confirm you're not a bot") after many downloads from one
# address: the import waits this long, and so do all other queued imports.
RATE_LIMIT_PAUSE_S = 3600
BOT_CHECK = ("not a bot", "sign in to confirm", "too many requests", "http error 429")
# Softer throttling seen on 16.10.2026 after ~18 full downloads in a few hours: "HTTP Error
# 403: Forbidden" on the video data, or the connection cut ("186 bytes read, 10208381 more
# expected"). A retry a few minutes later went through; a short pause for all imports keeps
# them from spending their attempts on it.
THROTTLE_PAUSE_S = 900
THROTTLED = ("http error 403", "more expected")


class RateLimited(Exception):
    """YouTube wants a pause of `seconds`."""

    def __init__(self, message: str, seconds: int = RATE_LIMIT_PAUSE_S) -> None:
        super().__init__(message)
        self.seconds = seconds


class Refused(Exception):
    """The video cannot be imported (channel, live, length, gone); retrying will not help."""


class Stopped(Exception):
    """The job was cancelled."""


def _downloader(options: dict[str, Any]) -> Any:
    """yt-dlp's YoutubeDL; tests replace this."""
    import yt_dlp  # type: ignore[import-untyped]  # imported late: only import workers need it

    return yt_dlp.YoutubeDL(options)


def _download_error() -> type[Exception]:
    try:
        from yt_dlp.utils import DownloadError  # type: ignore[import-untyped]
    except ImportError:  # pragma: no cover - yt-dlp is a dependency
        return Exception
    return DownloadError  # type: ignore[no-any-return]


class _Quiet:
    """yt-dlp logs go to ours, at debug level, warnings at info."""

    def debug(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def info(self, msg: str) -> None:
        log.debug("yt-dlp: %s", msg)

    def warning(self, msg: str) -> None:
        log.info("yt-dlp: %s", msg)

    def error(self, msg: str) -> None:
        log.info("yt-dlp: %s", msg)


def fetch(
    url: str,
    platform: str,
    channels: Iterable[str],
    work: Path,
    *,
    max_seconds: int,
    max_bytes: int,
    progress: Callable[[float], None],
    cancel: threading.Event,
) -> tuple[Path, dict[str, Any]]:
    """Download the video at `url` into `work`. Returns the file and the kept metadata."""
    allowed = set(channels)

    def hook(status: dict[str, Any]) -> None:
        if cancel.is_set():
            raise Stopped()
        if status.get("status") == "downloading":
            total = status.get("total_bytes") or status.get("total_bytes_estimate")
            done = status.get("downloaded_bytes") or 0
            if total:
                progress(min(1.0, done / total))

    options: dict[str, Any] = {
        "allowed_extractors": EXTRACTORS,
        "noplaylist": True,
        "format": FORMAT,
        "merge_output_format": "mp4",
        "outtmpl": {"default": "download.%(ext)s"},
        "paths": {"home": str(work), "temp": str(work)},
        "max_filesize": max_bytes,
        "socket_timeout": 30,
        "retries": 10,
        "fragment_retries": 10,
        "concurrent_fragment_downloads": 4,
        "overwrites": True,
        "progress_hooks": [hook],
        "js_runtimes": {"deno": {}},  # YouTube's JavaScript challenges (Deno in the image)
        "logger": _Quiet(),
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    download_error = _download_error()
    try:
        with _downloader(options) as ydl:
            info = ydl.extract_info(url, download=False)
            if not isinstance(info, dict) or info.get("_type", "video") != "video":
                raise Refused("The link is not a single video")
            name = info.get("channel") or info.get("uploader") or "?"
            if not imports.channel_allowed(platform, info, allowed):
                raise Refused(f"The channel {name} is not on the import allow-list")
            if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
                raise Refused("Live streams cannot be imported; wait until the stream has ended")
            duration = info.get("duration")
            if isinstance(duration, (int, float)) and duration > max_seconds:
                raise Refused(f"The video is longer than {max_seconds // 3600} hours")
            done = ydl.process_ie_result(info, download=True)
    except Stopped:
        raise
    except download_error as exc:
        if cancel.is_set():
            raise Stopped() from exc
        message = str(exc).removeprefix("ERROR: ")
        if any(marker in message.lower() for marker in BOT_CHECK):
            raise RateLimited(message[:300]) from exc
        if any(marker in message.lower() for marker in THROTTLED):
            raise RateLimited(message[:300], THROTTLE_PAUSE_S) from exc
        if any(marker in message.lower() for marker in GONE):
            raise Refused(message[:300]) from exc
        raise RuntimeError(message[:300]) from exc
    files = [
        Path(item["filepath"])
        for item in (done or {}).get("requested_downloads") or []
        if item.get("filepath")
    ]
    if len(files) != 1 or not files[0].is_file():
        found = sorted(p for p in work.glob("download.*") if p.suffix != ".part")
        files = found[:1]
    if not files:
        raise RuntimeError("yt-dlp finished without a file")
    path = files[0]
    if path.stat().st_size > max_bytes:
        raise Refused("The video is larger than the import limit")
    kept = {k: info[k] for k in INFO_KEYS if isinstance(info.get(k), (str, int, float))}
    return path, kept
