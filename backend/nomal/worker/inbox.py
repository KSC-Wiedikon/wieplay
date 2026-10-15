"""The NAS inbox (PLAN §7.2): copy camera files into the share, pick the game in Nomal.

A file counts once its size and modification time have not changed for two minutes. The
scanner probes it once and reports every stable file to the API on each pass.
"""

from __future__ import annotations

import logging
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nomal.worker import ffmpeg
from nomal.worker.client import ApiClient, ApiUnavailable
from nomal.worker.config import WorkerSettings

log = logging.getLogger(__name__)
VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".mts", ".m2ts", ".mkv", ".avi"}


class InboxScanner:
    def __init__(self, settings: WorkerSettings, api: ApiClient) -> None:
        assert settings.inbox_root is not None
        self.root = settings.inbox_root
        self.every = settings.inbox_scan_seconds
        self.stable_after = settings.inbox_stable_seconds
        self.api = api
        self._seen: dict[str, tuple[int, float]] = {}
        self._probed: dict[tuple[str, int, float], dict[str, Any]] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="inbox")

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self.scan_once()
            except ApiUnavailable as exc:
                log.warning("inbox report failed: %s", exc)
            except OSError as exc:
                log.warning("inbox not readable: %s", exc)
            self._stop.wait(self.every)

    def scan_once(self, now: float | None = None) -> list[dict[str, Any]]:
        now = now or datetime.now(UTC).timestamp()
        stable: list[dict[str, Any]] = []
        current: dict[str, tuple[int, float]] = {}
        for path in sorted(self.root.rglob("*")):
            rel = path.relative_to(self.root).as_posix()
            if (
                not path.is_file()
                or path.suffix.lower() not in VIDEO_SUFFIXES
                or any(part.startswith((".", "@", "#")) for part in rel.split("/"))
            ):
                continue
            st = path.stat()
            current[rel] = (st.st_size, st.st_mtime)
            unchanged = self._seen.get(rel) == current[rel]
            if not unchanged or now - st.st_mtime < self.stable_after:
                continue
            key = (rel, st.st_size, st.st_mtime)
            if key not in self._probed:
                self._probed[key] = self._probe(path)
            info = self._probed[key]
            stable.append(
                {
                    "path": rel,
                    "size": st.st_size,
                    "mtime": datetime.fromtimestamp(st.st_mtime, UTC).isoformat(),
                    "probe": info,
                    "recorded_at_guess": info.get("creation_time")
                    or datetime.fromtimestamp(st.st_mtime, UTC).isoformat(),
                }
            )
        self._seen = current
        self.api.report_inbox(stable)
        return stable

    def _probe(self, path: Path) -> dict[str, Any]:
        try:
            p = ffmpeg.probe(path)
        except ffmpeg.FfmpegError as exc:
            return {"error": str(exc)[:200]}
        return {
            "duration_ms": p.duration_ms,
            "width": p.width,
            "height": p.height,
            "fps": float(p.fps),
            **{k: v for k, v in p.capture.items() if k in {"creation_time", "make", "model"}},
        }
