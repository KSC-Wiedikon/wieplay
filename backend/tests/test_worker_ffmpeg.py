"""The worker's ffmpeg steps on a tiny generated clip (skipped without ffmpeg)."""

from __future__ import annotations

import re
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from nomal.worker import ffmpeg

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def make_clip(path: Path, seconds: int, fps: int = 50) -> None:
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size=640x360:rate={fps}",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
            "-t", str(seconds), "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", str(path),
        ],
        check=True,
    )  # fmt: skip


@pytest.fixture(scope="module")
def parts(tmp_path_factory: pytest.TempPathFactory) -> list[Path]:
    root = tmp_path_factory.mktemp("clips")
    clips = [root / "C0001.MP4", root / "C0002.MP4"]
    make_clip(clips[0], 5)
    make_clip(clips[1], 3)
    return clips


def test_probe_reads_what_the_standard_needs(parts: list[Path]) -> None:
    probe = ffmpeg.probe(parts[0])
    assert (probe.width, probe.height, float(probe.fps)) == (640, 360, 50.0)
    assert probe.has_audio and probe.video_codec == "h264"
    assert 4900 <= probe.duration_ms <= 5100
    assert probe.capture["vfr"] is False


def test_probe_refuses_a_file_without_video(tmp_path: Path) -> None:
    text = tmp_path / "notes.mp4"
    text.write_text("not a video")
    with pytest.raises(ffmpeg.FfmpegError):
        ffmpeg.probe(text)


def test_split_files_become_one_hls_rendition(parts: list[Path], tmp_path: Path) -> None:
    concat = ffmpeg.concat_list([str(p) for p in parts], tmp_path / "in.ffconcat")
    out = tmp_path / "540p"
    out.mkdir()
    seen: list[float] = []
    ffmpeg.run(
        ffmpeg.hls_args(
            concat=concat, out_width=320, out_height=180, fps=25.0, video_kbps=400, max_kbps=600,
            audio_kbps=64, has_audio=True, segment_seconds=4, encoder="x264", threads=2,
            preset="ultrafast", vaapi_device="",
        ),
        duration_ms=8000, on_progress=seen.append, cancel=threading.Event(), cwd=out,
    )  # fmt: skip
    playlist = (out / "index.m3u8").read_text()
    segments = [line for line in playlist.splitlines() if line.startswith("seg_")]
    # 8 s in 4-second segments, plus at most a sliver where the audio runs a little longer.
    assert segments[:2] == ["seg_00000.m4s", "seg_00001.m4s"] and len(segments) <= 3
    assert playlist.count("#EXTINF:4.000000,") == 2
    assert '#EXT-X-MAP:URI="init.mp4"' in playlist
    # Read from the avcC box; "ultrafast" makes x264 fall back to Constrained Baseline (42c0).
    assert re.fullmatch(r"avc1\.[0-9a-f]{6},mp4a\.40\.2", ffmpeg.codecs_of(out / "init.mp4", True))
    assert ffmpeg.peak_bandwidth(out, playlist) > 0
    assert seen and seen[-1] > 0.5
    joined = ffmpeg.probe(out / "index.m3u8", camera=False)
    assert (joined.width, joined.height, float(joined.fps)) == (320, 180, 25.0)
    assert 7800 <= joined.duration_ms <= 8200


def test_thumbnails_and_poster(parts: list[Path], tmp_path: Path) -> None:
    concat = ffmpeg.concat_list([str(p) for p in parts], tmp_path / "in.ffconcat")
    out = tmp_path / "thumbs"
    out.mkdir()
    cancel = threading.Event()
    ffmpeg.run(
        ffmpeg.thumbs_args(concat=concat, interval_s=2, tile_w=160, tile_h=90, cols=2, rows=2),
        duration_ms=8000, on_progress=lambda _: None, cancel=cancel, cwd=out,
    )  # fmt: skip
    ffmpeg.run(
        ffmpeg.poster_args(concat=concat, at_s=1.0),
        duration_ms=1, on_progress=lambda _: None, cancel=cancel, cwd=out,
    )  # fmt: skip
    sheets = sorted(p.name for p in out.glob("sprite_*.jpg"))
    assert sheets[0] == "sprite_001.jpg"
    assert ffmpeg.probe(out / "sprite_001.jpg", camera=False).width == 320  # 2 tiles of 160 px
    assert (out / "poster.jpg").stat().st_size > 0


def test_cancel_stops_ffmpeg(parts: list[Path], tmp_path: Path) -> None:
    concat = ffmpeg.concat_list([str(p) for p in parts], tmp_path / "in.ffconcat")
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ffmpeg.Cancelled):
        ffmpeg.run(
            ["-f", "concat", "-safe", "0", "-i", str(concat), "-f", "null", "-"],
            duration_ms=8000, on_progress=lambda _: None, cancel=cancel,
        )  # fmt: skip
