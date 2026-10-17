"""Reading a scoreboard (wieplay/ml/scoreboard.py): digits on synthetic flip-card and LED boards
(drawn with fonts the network never saw in training), the score split into two numbers, the
light reader and the OCR combined, and the worker's job end to end on a generated video."""

from __future__ import annotations

import hashlib
import subprocess
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from wieplay.ml import board as bd
from wieplay.ml import scoreboard as sb
from wieplay.ml import scoreboard_synth as synth
from wieplay.worker import scoreboard as job
from wieplay.worker.client import ClaimedJob
from wieplay.worker.config import WorkerSettings
from wieplay.worker.handlers import Context


def test_the_weights_are_the_ones_shipped() -> None:
    data = sb.WEIGHTS.read_bytes()
    assert sb.WEIGHTS_SHA256 is not None
    assert hashlib.sha256(data).hexdigest() == sb.WEIGHTS_SHA256
    net = sb.model()
    assert net.w1.shape == (sb.GLYPH_H * sb.GLYPH_W, net.w1.shape[1])
    assert net.w2.shape[1] == sb.CLASSES


@pytest.mark.parametrize(
    ("style", "right_at_least", "wrong_at_most"), [("flip", 0.85, 0.08), ("led", 0.7, 0.1)]
)
def test_synthetic_boards_read(style: Any, right_at_least: float, wrong_at_most: float) -> None:
    """Boards the network never saw (OpenCV's fonts and seven segments only, other seeds),
    degraded like a hall camera: right, wrong (sure but wrong: the dangerous kind), unread."""
    rng = np.random.default_rng(2026)
    right = wrong = 0
    n = 150
    for _ in range(n):
        board = synth.degrade(
            synth.surroundings(synth.random_board(rng, style=style), rng), rng, (18, 40)
        )
        crop, _, _ = synth.score_crop(board, rng)
        found = sb.read(crop, style)
        if found is None or found.confidence < bd.MIN_CONF:
            continue
        if found.numbers == board.numbers:
            right += 1
        else:
            wrong += 1
    assert right / n >= right_at_least, (right, wrong)
    assert wrong / n <= wrong_at_most, (right, wrong)


def test_auto_finds_the_polarity() -> None:
    rng = np.random.default_rng(7)
    hits = 0
    for style in ("flip", "led") * 15:
        board = synth.degrade(
            synth.surroundings(synth.random_board(rng, style=style), rng), rng, (24, 40)
        )
        crop, _, _ = synth.score_crop(board, rng)
        found = sb.read(crop, "auto")
        hits += int(found is not None and found.numbers == board.numbers)
    assert hits >= 24


def glyphs(*spec: tuple[int, int]) -> list[sb.Glyph]:
    """Digits of height 40 at x (width 20 each)."""
    return [sb.Glyph(x, 0, 20, 40, d, 0.9) for x, d in spec]


def test_the_score_splits_at_the_widest_gap() -> None:
    def values(line: list[sb.Glyph]) -> list[int | None]:
        return [sb._value(g) for g in sb.pair_of(line, 240)]

    # "15 8": flip cards close together; the gap between the numbers is still the widest.
    assert values(glyphs((0, 1), (22, 5), (52, 8))) == [15, 8]
    assert values(glyphs((0, 1), (30, 5), (52, 8))) == [1, 58]
    assert values(glyphs((0, 2), (22, 1), (60, 1), (82, 4))) == [21, 14]
    assert values(glyphs((0, 0), (40, 0))) == [0, 0]  # never "00"
    assert values(glyphs((0, 7))) == []
    # Set counts beside the score: the two numbers nearest the middle.
    line = glyphs((0, 1), (60, 2), (82, 3), (140, 1), (162, 9), (220, 0))
    assert values(line) == [23, 19]


def test_two_readers_combined() -> None:
    a = sb.Reading((3, 8), 0.9, 2)
    b = sb.Reading((13, 8), 0.6, 0)
    assert sb.combine(a, b) == b  # the OCR read the "1" the light reader lost
    assert sb.combine(b, a) == b
    same = sb.combine(sb.Reading((4, 2), 0.7, 2), sb.Reading((4, 2), 0.8, 0))
    assert same is not None and same.confidence == 0.8
    other = sb.combine(sb.Reading((4, 2), 0.9, 2), sb.Reading((7, 2), 0.8, 0))
    assert other is not None and other.numbers == (4, 2) and other.confidence < bd.MIN_CONF
    doubted = sb.combine(sb.Reading((1, 11), 0.5, 2), sb.Reading((18, 11), 0.76, 0))
    assert doubted is not None and doubted.numbers == (18, 11) and doubted.confidence == 0.76
    assert sb.combine(None, b) == b and sb.combine(a, None) == a and sb.combine(None, None) is None


def test_the_ocr_s_longest_run_of_digits() -> None:
    chars = ["0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "l", "|"]

    def frames(*ks: int) -> np.ndarray:
        out = np.zeros((len(ks), len(chars) + 1))
        for t, k in enumerate(ks):
            out[t, k] = 0.9
        return out

    # blank, "1", blank, "8", "|", "0" → the run "18"
    assert sb.longest_digits(frames(0, 2, 0, 9, 12, 1), chars) == ("18", 0.9)
    assert sb.longest_digits(frames(0, 11, 0), chars) == ("", 0.0)
    assert sb.LineReader.find(Path("/nonexistent")) is None


@pytest.fixture(scope="module")
def board_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """8 s of a hall with a flip board at the bottom left: 3:2 for 4 s, then 4:2."""
    folder = tmp_path_factory.mktemp("raw") / "video"
    folder.mkdir()
    path = folder / "01_C0001.MP4"
    font = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
    text = "drawtext=fontfile={f}:text='{t}':fontsize=44:fontcolor=black:x={x}:y=948:enable='{e}'"
    draw = ",".join(
        [
            "drawbox=x=20:y=930:w=170:h=90:color=0x202840:t=fill",
            "drawbox=x=30:y=938:w=70:h=74:color=white:t=fill",
            "drawbox=x=110:y=938:w=70:h=74:color=white:t=fill",
            text.format(f=font, t="3", x=52, e="lt(t,4)"),
            text.format(f=font, t="4", x=52, e="gte(t,4)"),
            text.format(f=font, t="2", x=132, e="1"),
        ]
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "color=c=0x8a8a80:size=1280x1080:rate=25", "-t", "8", "-vf", draw,
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         str(path)],
        check=True,
    )  # fmt: skip
    return path


def test_the_job_reads_a_video(board_video: Path, tmp_path: Path) -> None:
    raw = board_video.parent.parent
    analysis = tmp_path / "analysis"
    settings = WorkerSettings(
        api_url="http://unused", work_dir=tmp_path / "work", raw_root=raw, analysis_root=analysis,
        analysis_threads=2, players_weights=tmp_path / "none",
    )  # fmt: skip
    params = {
        "video_id": "v", "region_id": "r", "inputs": [str(board_video)], "duration_ms": 8000,
        "width": 1280, "height": 1080,
        "rect": {"x": 20 / 1280, "y": 930 / 1080, "w": 170 / 1280, "h": 90 / 1080},
        "sets": None, "kind": "flip", "fps": 2.0, "out_dir": "videos/x",
    }  # fmt: skip
    claimed = ClaimedJob(
        id="job-board-1", kind="scoreboard", video_id="v", params=params, attempt=1, checkpoint=None
    )
    ctx = Context(settings, None, claimed, threading.Event(), lambda p, m: None)  # type: ignore[arg-type]
    out = job.run(ctx)
    found = bd.unpack(out["track"])
    assert [(s.left, s.right) for s in found] == [(3, 2), (4, 2)]
    assert found[1].first_ms in (4000, 4500)
    assert out["summary"]["samples"] == 16 and out["model"]["params"]["ocr"] is None
    saved = analysis / out["artifact"]["nas_path"]
    assert saved.exists() and out["artifact"]["nas_path"].startswith("videos/x/")
    with np.load(saved) as z:
        assert list(z["left"][:2]) == [3, 3] and int(z["schema"][0]) == job.SCHEMA
