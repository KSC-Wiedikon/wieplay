"""ffprobe and ffmpeg: probing camera files, HLS renditions, thumbnails (PLAN §7.4)."""

from __future__ import annotations

import collections
import json
import math
import os
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

# Camera containers only, never playlists or network protocols: a crafted .m3u8 would make
# ffprobe read other files on the NAS. Applied to every input; the concat demuxer passes
# the lists on to the files it opens.
CAMERA_FORMATS = "mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,avi,mpegts"


def input_guard(*, concat: bool = False) -> list[str]:
    formats = f"concat,{CAMERA_FORMATS}" if concat else CAMERA_FORMATS
    return ["-protocol_whitelist", "file", "-format_whitelist", formats]


class Cancelled(Exception):
    pass


class FfmpegError(Exception):
    pass


@dataclass(frozen=True)
class Probe:
    duration_ms: int
    width: int
    height: int
    fps: Fraction
    has_audio: bool
    video_codec: str
    capture: dict[str, Any] = field(default_factory=dict)


def _fraction(value: str | None) -> Fraction | None:
    if not value or value in {"0/0", "0"}:
        return None
    try:
        frac = Fraction(value)
    except (ValueError, ZeroDivisionError):
        return None
    return frac if frac > 0 else None


def probe(path: Path, *, camera: bool = True) -> Probe:
    """Probe a file. `camera=True` (the default) accepts camera containers only."""
    guard = input_guard() if camera else []
    out = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            *guard,
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if out.returncode != 0:
        raise FfmpegError(f"ffprobe failed for {path.name}: {out.stderr.strip()[-300:]}")
    data = json.loads(out.stdout)
    streams = data.get("streams", [])
    video = next(
        (
            s
            for s in streams
            if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
        ),
        None,
    )
    if video is None:
        raise FfmpegError(f"{path.name} has no video stream")
    fmt = data.get("format", {})
    avg, real = _fraction(video.get("avg_frame_rate")), _fraction(video.get("r_frame_rate"))
    fps = avg or real
    if fps is None:
        raise FfmpegError(f"{path.name}: unknown frame rate")
    fps = fps.limit_denominator(1001)
    duration = float(fmt.get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise FfmpegError(f"{path.name}: unknown duration")
    tags = {**fmt.get("tags", {}), **video.get("tags", {})}
    capture = {
        "container": fmt.get("format_name"),
        "codec": video.get("codec_name"),
        "profile": video.get("profile"),
        "pix_fmt": video.get("pix_fmt"),
        "bit_rate": int(fmt["bit_rate"]) if str(fmt.get("bit_rate", "")).isdigit() else None,
        "vfr": bool(avg and real and abs(float(avg) - float(real)) > 0.01),
        "make": tags.get("com.apple.quicktime.make") or tags.get("make"),
        "model": tags.get("com.apple.quicktime.model") or tags.get("model"),
        "encoder": tags.get("encoder"),
        "creation_time": tags.get("creation_time"),
    }
    return Probe(
        duration_ms=round(duration * 1000),
        width=int(video["width"]),
        height=int(video["height"]),
        fps=fps,
        has_audio=any(s.get("codec_type") == "audio" for s in streams),
        video_codec=str(video.get("codec_name")),
        capture={k: v for k, v in capture.items() if v is not None},
    )


def concat_list(inputs: Sequence[str], target: Path) -> Path:
    """An ffconcat file joining split camera files without re-encoding (PLAN §6.4)."""
    lines = ["ffconcat version 1.0"]
    for item in inputs:
        if any(c in item for c in "\n\r\0"):
            raise FfmpegError("input path with a line break")
        escaped = str(Path(item).resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    target.write_text("\n".join(lines) + "\n")
    return target


class Parts:
    """A video's camera files in order: a moment of the video → (file, offset in it).

    Seeking with `-ss` before `-i` on one MP4 jumps to the nearest key frame; through the
    concat demuxer it decodes from the start of the file, minutes per seek on a 2 h match
    (seen on prod, 17.10.2026: the court job took 55 min for ~300 frames). File lengths come
    from ffprobe, once, and only when there is more than one file."""

    def __init__(self, inputs: Sequence[str]) -> None:
        self.files = [Path(item) for item in inputs]
        if not self.files:
            raise FfmpegError("no input files")
        self._starts: list[float] | None = [0.0] if len(self.files) == 1 else None

    def _ensure_starts(self) -> list[float]:
        if self._starts is None:
            starts, at = [], 0.0
            for path in self.files:
                starts.append(at)
                at += probe_duration_s(path)
            self._starts = starts
        return self._starts

    def locate(self, at_s: float) -> tuple[Path, float]:
        starts = self._ensure_starts()
        index = max(i for i, start in enumerate(starts) if start <= max(at_s, 0.0))
        return self.files[index], max(at_s - starts[index], 0.0)


def probe_duration_s(path: Path) -> float:
    """A file's length in seconds (ffprobe, container duration)."""
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        ],
        capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    try:
        return float(out.stdout.strip())
    except ValueError as exc:
        raise FfmpegError(f"could not read the length of {path.name}") from exc


def even(value: float) -> int:
    return max(2, round(value / 2) * 2)


def _lower_priority() -> None:
    os.nice(10)


def run(
    args: Sequence[str],
    *,
    duration_ms: int,
    on_progress: Callable[[float], None],
    cancel: threading.Event,
    cwd: Path | None = None,
    timeout_s: float | None = None,
) -> None:
    """Run ffmpeg with -progress on stdout; stop it when `cancel` is set or after
    `timeout_s` (default: 2 h or 4x the media duration, whichever is longer). Runs at
    lower CPU priority: the Lenovo server also serves DNS and other things."""
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-y", "-progress", "pipe:1", "-nostats", *args]
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        preexec_fn=_lower_priority,
    )
    timed_out = threading.Event()

    def kill() -> None:
        timed_out.set()
        proc.kill()

    limit = timeout_s if timeout_s is not None else max(7200.0, 4 * duration_ms / 1000)
    timer = threading.Timer(limit, kill)
    timer.daemon = True
    timer.start()
    tail: collections.deque[str] = collections.deque(maxlen=40)

    def drain() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            tail.append(line.rstrip())

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    assert proc.stdout is not None
    for line in proc.stdout:
        if cancel.is_set():
            proc.terminate()
            break
        key, _, value = line.strip().partition("=")
        if key == "out_time_us" and value.isdigit() and duration_ms > 0:
            on_progress(min(1.0, int(value) / 1000 / duration_ms))
    code = proc.wait()
    timer.cancel()
    reader.join(timeout=5)
    if cancel.is_set():
        raise Cancelled()
    if timed_out.is_set():
        raise FfmpegError(f"ffmpeg ran longer than {limit:.0f} s and was stopped")
    if code != 0:
        raise FfmpegError("ffmpeg failed: " + " | ".join(list(tail)[-6:]))


def hls_args(
    *,
    concat: Path,
    out_width: int,
    out_height: int,
    fps: float,
    video_kbps: int,
    max_kbps: int,
    audio_kbps: int,
    has_audio: bool,
    segment_seconds: int,
    encoder: str,
    threads: int,
    preset: str,
    vaapi_device: str,
) -> list[str]:
    """One HLS rendition: H.264 + AAC, closed 2-second GOPs, fMP4 segments (PLAN §7.4)."""
    gop = max(1, round(fps * 2))
    fps_text = f"{fps:g}"
    level = "4.2" if out_height > 720 or fps > 30 else "3.1"
    args: list[str] = []
    if encoder == "vaapi":
        args += [
            "-hwaccel",
            "vaapi",
            "-hwaccel_device",
            vaapi_device,
            "-hwaccel_output_format",
            "vaapi",
        ]
    args += [*input_guard(concat=True), "-f", "concat", "-safe", "0", "-i", str(concat)]
    args += ["-map", "0:v:0"]
    if has_audio:
        args += ["-map", "0:a:0?"]
    if encoder == "vaapi":
        args += [
            "-vf",
            f"fps={fps_text},scale_vaapi=w={out_width}:h={out_height}:format=nv12",
            "-c:v",
            "h264_vaapi",
            "-profile:v",
            "high",
            "-rc_mode",
            "VBR",
        ]
    else:
        args += [
            "-vf",
            f"fps={fps_text},scale={out_width}:{out_height}:flags=bicubic,format=yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            preset,
            "-profile:v",
            "high",
            "-level:v",
            level,
            "-threads",
            str(threads),
            "-sc_threshold",
            "0",
        ]
    args += [
        "-b:v",
        f"{video_kbps}k",
        "-maxrate",
        f"{max_kbps}k",
        "-bufsize",
        f"{2 * max_kbps}k",
        "-g",
        str(gop),
        "-keyint_min",
        str(gop),
        "-flags",
        "+cgop",
    ]
    if has_audio:
        args += ["-c:a", "aac", "-b:a", f"{audio_kbps}k", "-ac", "2", "-ar", "48000"]
    args += [
        "-f",
        "hls",
        "-hls_time",
        str(segment_seconds),
        "-hls_playlist_type",
        "vod",
        "-hls_segment_type",
        "fmp4",
        "-hls_fmp4_init_filename",
        "init.mp4",
        "-hls_segment_filename",
        "seg_%05d.m4s",
        "-hls_flags",
        "independent_segments",
        "index.m3u8",
    ]
    return args


def codecs_of(init_segment: Path, has_audio: bool) -> str:
    """RFC 6381 codecs string for the master playlist (avc1.PPCCLL), read from the avcC box
    of the init segment. ffprobe cannot report the level of an init segment without frames."""
    data = init_segment.read_bytes()
    at = data.find(b"avcC")
    if at < 0 or len(data) < at + 8:
        raise FfmpegError("init segment has no avcC box")
    profile, compat, level = data[at + 5], data[at + 6], data[at + 7]
    video = f"avc1.{profile:02x}{compat:02x}{level:02x}"
    return f"{video},mp4a.40.2" if has_audio else video


def peak_bandwidth(directory: Path, playlist: str) -> int:
    """Peak bits per second over the segments, for BANDWIDTH in the master playlist."""
    peak, duration = 0.0, None
    for line in playlist.splitlines():
        if line.startswith("#EXTINF:"):
            duration = float(line[8:].split(",")[0])
        elif line and not line.startswith("#") and duration:
            size = (directory / line).stat().st_size
            # At least a second: the last segment of a recording can be a few frames long.
            peak = max(peak, size * 8 / max(duration, 1.0))
            duration = None
    return max(1, math.ceil(peak * 1.1))


def thumbs_args(
    *, concat: Path, interval_s: int, tile_w: int, tile_h: int, cols: int, rows: int
) -> list[str]:
    """Scrub-preview sprite sheets from keyframes only (fast on long matches)."""
    return [
        "-skip_frame",
        "nokey",
        *input_guard(concat=True),
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat),
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        f"fps=1/{interval_s},scale={tile_w}:{tile_h},tile={cols}x{rows}",
        "-fps_mode",
        "vfr",
        "-q:v",
        "5",
        "-start_number",
        "1",
        "sprite_%03d.jpg",
    ]


def poster_args(*, source: Path, at_s: float) -> list[str]:
    """A poster frame from one camera file. Seeking inside the concat demuxer is
    unreliable ("could not seek"), and a failed poster would fail the thumbnails job."""
    return [
        "-ss", f"{at_s:.2f}", *input_guard(), "-i", str(source),
        "-map", "0:v:0", "-frames:v", "1", "-vf", "scale=1280:-2", "-q:v", "3", "poster.jpg",
    ]  # fmt: skip


EXPORT_WIDTH, EXPORT_HEIGHT, EXPORT_FPS = 1920, 1080, 50


def filter_path(path: Path | str) -> str:
    """A file name inside a filter argument (ffmpeg's option escaping)."""
    return str(path).replace("\\", "/").replace(":", "\\:")


def watermark_filter(watermark_file: Path, font_file: str) -> str:
    return (
        f"drawtext=fontfile={filter_path(font_file)}:textfile={filter_path(watermark_file)}:"
        "expansion=none:fontsize=30:fontcolor=white@0.85:box=1:boxcolor=black@0.45:boxborderw=8:"
        "x=w-tw-28:y=h-th-28"
    )


# Every export piece and the recap's title card are encoded alike, so they join without
# re-encoding.
EXPORT_ENCODE = [
    "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-maxrate", "8M",
    "-bufsize", "16M", "-g", str(2 * EXPORT_FPS), "-profile:v", "high",
    "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
]  # fmt: skip


def export_piece_args(
    *,
    source: Path,
    start_s: float,
    duration_s: float,
    has_audio: bool,
    watermark_file: Path,
    font_file: str,
    output: str,
    overlays: Sequence[str] = (),
    audio_filter: str | None = None,
) -> list[str]:
    """One piece of an export: cut frame-accurately from the originals, 1080p50, the
    watermark burned in (PLAN §5.2). Pieces share every parameter, so they join without
    re-encoding. Silent audio when the source has none, so every piece has a track.
    `source` is one camera file: seeking inside the concat demuxer is unreliable, so a
    piece that spans two files is cut as two pieces. `overlays`: more filters on the 1080p
    picture before the watermark (the recap's score bug, fades)."""
    video_filter = ",".join(
        [
            f"scale={EXPORT_WIDTH}:{EXPORT_HEIGHT}:force_original_aspect_ratio=decrease",
            f"pad={EXPORT_WIDTH}:{EXPORT_HEIGHT}:(ow-iw)/2:(oh-ih)/2",
            f"fps={EXPORT_FPS}",
            "format=yuv420p",
            *overlays,
            watermark_filter(watermark_file, font_file),
        ]
    )
    args = [
        "-ss", f"{start_s:.3f}", *input_guard(), "-i", str(source),
    ]  # fmt: skip
    if not has_audio:
        args += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000"]
    args += [
        "-t", f"{duration_s:.3f}",
        "-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0",
        "-vf", video_filter,
    ]  # fmt: skip
    if audio_filter:
        args += ["-af", audio_filter]
    args += [*EXPORT_ENCODE, output]
    return args


def card_args(
    *,
    duration_s: float,
    background: str,
    overlays: Sequence[str],
    watermark_file: Path,
    font_file: str,
    output: str,
) -> list[str]:
    """A still card (the recap's title): a plain colour with text drawn on it, silent audio,
    encoded like the export pieces."""
    video_filter = ",".join(
        ["format=yuv420p", *overlays, watermark_filter(watermark_file, font_file)]
    )
    return [
        "-f", "lavfi", "-i",
        f"color=c={background}:s={EXPORT_WIDTH}x{EXPORT_HEIGHT}:r={EXPORT_FPS}:d={duration_s:.3f}",
        "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-t", f"{duration_s:.3f}",
        "-map", "0:v:0", "-map", "1:a:0",
        "-vf", video_filter,
        *EXPORT_ENCODE,
        output,
    ]  # fmt: skip


def join_args(*, parts_list: Path, output: str) -> list[str]:
    """Join export pieces without re-encoding; moov at the front for streaming."""
    return [
        "-f", "concat", "-safe", "0", "-i", str(parts_list),
        "-c", "copy", "-movflags", "+faststart", output,
    ]  # fmt: skip
