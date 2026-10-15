"""Renditions, the capture standard and HLS/WebVTT text (PLAN §7.1, §7.4, §7.6)."""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from nomal.core.models import Video, VideoRendition


@dataclass(frozen=True)
class RenditionSpec:
    name: str
    height: int
    max_fps: int
    video_kbps: int
    max_kbps: int
    audio_kbps: int

    def fps_for(self, source_fps: float) -> float:
        """Constant output rate: the cap, or the source rate when it is lower (no upsampling)."""
        if source_fps >= self.max_fps - 1:
            return float(self.max_fps)
        return round(source_fps, 3)

    def as_params(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "height": self.height,
            "max_fps": self.max_fps,
            "video_kbps": self.video_kbps,
            "max_kbps": self.max_kbps,
            "audio_kbps": self.audio_kbps,
        }


# 1080p for tagging and desktop, 540p for phones and weak connections (PLAN §7.4).
RENDITIONS = (
    RenditionSpec("1080p", 1080, 50, 5000, 7000, 128),
    RenditionSpec("540p", 540, 25, 1200, 1600, 96),
)
SEGMENT_SECONDS = 4
THUMB_INTERVAL_SECONDS = 10
THUMB_WIDTH = 160
THUMB_COLUMNS = 10
THUMB_ROWS = 10

STANDARD_MIN_HEIGHT = 1080
STANDARD_MIN_FPS = 49.0

SEGMENT_NAME = re.compile(r"^(init\.mp4|seg_\d{5}\.m4s)$")
THUMB_NAME = re.compile(r"^(sprite_\d{3}\.jpg|poster\.jpg)$")


def renditions_for(source_height: int) -> list[RenditionSpec]:
    """Renditions at or below the source height; always at least the smallest one."""
    fitting = [r for r in RENDITIONS if r.height <= source_height]
    return fitting or [RENDITIONS[-1]]


def standard_issues(width: int, height: int, fps: float) -> list[str]:
    """Why a video falls short of the capture standard, e.g. ['720p', '25fps']."""
    issues = []
    if height < STANDARD_MIN_HEIGHT:
        issues.append(f"{height}p")
    if fps < STANDARD_MIN_FPS:
        issues.append(f"{round(fps)}fps")
    return issues


def master_playlist(renditions: Sequence[VideoRendition]) -> str:
    lines = ["#EXTM3U", "#EXT-X-VERSION:7", "#EXT-X-INDEPENDENT-SEGMENTS"]
    for r in sorted(renditions, key=lambda r: -r.height):
        lines.append(
            f"#EXT-X-STREAM-INF:BANDWIDTH={r.bandwidth},RESOLUTION={r.width}x{r.height},"
            f'FRAME-RATE={r.fps:.3f},CODECS="{r.codecs}"'
        )
        lines.append(f"{r.name}/index.m3u8")
    return "\n".join(lines) + "\n"


def _vtt_time(seconds: float) -> str:
    ms = round(seconds * 1000)
    h, rest = divmod(ms, 3_600_000)
    m, rest = divmod(rest, 60_000)
    s, ms = divmod(rest, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def thumbnails_vtt(video: Video, base: str) -> str:
    """Scrub previews: one cue per thumbnail, pointing into sprite sheets via #xywh."""
    thumbs = video.thumbs or {}
    interval = float(thumbs.get("interval_s", THUMB_INTERVAL_SECONDS))
    cols, rows = int(thumbs.get("cols", THUMB_COLUMNS)), int(thumbs.get("rows", THUMB_ROWS))
    tw, th = int(thumbs.get("tile_w", THUMB_WIDTH)), int(thumbs.get("tile_h", 90))
    duration = (video.duration_ms or 0) / 1000
    count = min(int(thumbs.get("count", 0)), math.ceil(duration / interval) if duration else 0)
    lines = ["WEBVTT", ""]
    per_sheet = cols * rows
    for i in range(count):
        sheet, pos = divmod(i, per_sheet)
        x, y = (pos % cols) * tw, (pos // cols) * th
        start, end = i * interval, min((i + 1) * interval, duration)
        lines += [
            f"{_vtt_time(start)} --> {_vtt_time(end)}",
            f"{base}/thumbs/sprite_{sheet + 1:03d}.jpg#xywh={x},{y},{tw},{th}",
            "",
        ]
    return "\n".join(lines)
