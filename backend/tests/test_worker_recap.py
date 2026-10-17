"""The game recap on the worker (PLAN §7.7, worker/recap.py): the score bug and the title card
as ffmpeg filters, and a real render of a tiny recap from generated clips through the export
handler: title card, two clips with the bug, the watermark, one H.264 MP4."""

from __future__ import annotations

import subprocess
import threading
from pathlib import Path
from typing import Any

import pytest

from nomal.worker import ffmpeg, handlers
from nomal.worker import recap as rp
from nomal.worker.client import ClaimedJob
from nomal.worker.config import WorkerSettings

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONTS = rp.Fonts(FONT, BOLD)
BUG = {
    "set": "Satz 2",
    "before": "21:20",
    "after": "22:20",
    "point_ms": 65_000,
    "label": "Angriffspunkt #7",
}
CARD = {
    "eyebrow": "SPIELZUSAMMENFASSUNG",
    "matchup": "KSC Wiedikon H3 \N{EN DASH} Testclub A",
    "result": "3:1",
    "sets": "25:20 · 23:25 · 25:18 · 25:22",
    "when": "Mittwoch, 17. September 2026 · Halle Test",
    "footer": "Die besten Szenen · nomal · KSC Wiedikon",
}


def test_the_bug_shows_the_new_score_from_the_point(tmp_path: Path) -> None:
    texts = rp.Texts(tmp_path)
    filters = rp.bug_filters(BUG, 4.5, texts, FONTS)
    joined = ",".join(filters)
    assert filters[0].startswith("drawbox=x=48:y=44:") and "0x13203A@0.88" in filters[0]
    assert "enable='lt(t,4.500)'" in joined and "enable='gte(t,4.500)'" in joined
    written = sorted(p.read_text() for p in tmp_path.glob("text_*.txt"))
    assert written == sorted(["Satz 2", "21:20", "22:20", "Angriffspunkt #7"])
    # The panel is wide enough for the longest line (the label, 16 characters at 28 px).
    width = int(filters[0].split(":w=")[1].split(":")[0])
    assert width >= rp.text_width("Angriffspunkt #7", 28, False) + 64


def test_a_bug_without_a_new_score_shows_one_and_nothing_shows_nothing(tmp_path: Path) -> None:
    texts = rp.Texts(tmp_path)
    one = rp.bug_filters({**BUG, "after": ""}, 4.5, texts, FONTS)
    assert sum("drawtext" in f for f in one) == 3 and not any("enable" in f for f in one)
    assert rp.bug_filters({"set": "", "before": "", "after": ""}, 1.0, texts, FONTS) == []


def test_fades_and_the_switch_follow_the_part(tmp_path: Path) -> None:
    piece = {"bug": BUG}
    # A clip cut in two (two camera files): the point falls in the second part.
    first, audio1 = rp.piece_overlays(piece, 60_000, 3.0, True, False, rp.Texts(tmp_path), FONTS)
    second, audio2 = rp.piece_overlays(piece, 63_000, 4.0, False, True, rp.Texts(tmp_path), FONTS)
    assert "fade=t=in:st=0:d=0.250" in first and not any("fade=t=out" in f for f in first)
    assert "fade=t=out:st=3.750:d=0.250" in second and not any("fade=t=in" in f for f in second)
    assert any("lt(t,5.000)" in f for f in first) and any("lt(t,2.000)" in f for f in second)
    assert audio1 == "afade=t=in:st=0:d=0.250" and audio2 == "afade=t=out:st=3.750:d=0.250"


def test_the_card_fits_long_names(tmp_path: Path) -> None:
    filters = rp.card_filters(CARD, 4.0, rp.Texts(tmp_path), FONTS)
    assert sum("drawtext" in f for f in filters) == 6
    assert any(f.startswith("fade=t=out") for f in filters)
    assert rp.fit_size(CARD["matchup"], 84, 40) == 84
    long_name = "Volleyball Club Sehr Langer Vereinsname Zürich-Nord \N{EN DASH} KSC Wiedikon H3"
    texts = rp.Texts(tmp_path)
    two = [f for f in rp.card_filters({**CARD, "matchup": long_name}, 4.0, texts, FONTS)]
    assert sum("drawtext" in f for f in two) == 7  # the teams on two lines
    lines = sorted(p.read_text() for p in tmp_path.glob("text_*.txt"))
    assert "Volleyball Club Sehr Langer Vereinsname Zürich-Nord" in lines
    assert "\N{EN DASH} KSC Wiedikon H3" in lines
    assert rp.bold_of(FONT, ()) == BOLD


@pytest.fixture(scope="module")
def clip(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("raw") / "video"
    folder.mkdir()
    path = folder / "01_C0001.MP4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000", "-t", "8",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
         str(path)],
        check=True,
    )  # fmt: skip
    return path


class FakeApi:
    def sign(self, job_id: str, op: str, keys: list[str]) -> dict[str, str]:
        return {k: f"http://upload/{k}" for k in keys}


def test_a_tiny_recap_renders(clip: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    uploaded: dict[str, bytes] = {}

    class Response:
        status_code = 200

    def put(url: str, content: Any, **_: Any) -> Response:
        uploaded[url] = content.read()
        return Response()

    monkeypatch.setattr(handlers.httpx, "put", put)
    raw = clip.parent.parent
    settings = WorkerSettings(
        api_url="http://unused", work_dir=tmp_path / "work", raw_root=raw, font_file=FONT
    )
    part = {"path": str(clip), "offset_ms": 0, "duration_ms": 8000}
    pieces = [
        {"video_id": "v", "start_ms": 1000, "end_ms": 3000, "parts": [part], "has_audio": True,
         "bug": {**BUG, "point_ms": 2500}},
        {"video_id": "v", "start_ms": 5000, "end_ms": 7500, "parts": [part], "has_audio": True,
         "bug": {"set": "Satz 4", "before": "24:22", "after": "25:22", "point_ms": 7000,
                 "label": "Matchball"}},
    ]  # fmt: skip
    job = ClaimedJob(
        id="job-recap", kind="export", video_id=None, attempt=1, checkpoint=None,
        params={"export_id": "e", "key": "exports/e.mp4", "pieces": pieces,
                "watermark": "KSCW intern · Jo Trainer · 17.10.2026", "duration_ms": 6000,
                "recap": {"card": CARD, "card_ms": 1500, "source": "facts", "lang": "de"}},
    )  # fmt: skip
    ctx = handlers.Context(settings, FakeApi(), job, threading.Event(), lambda p, m: None)  # type: ignore[arg-type]
    result = handlers.export(ctx)
    assert result["duration_ms"] == 6000
    out = tmp_path / "recap.mp4"
    out.write_bytes(uploaded["http://upload/exports/e.mp4"])
    probe = ffmpeg.probe(out, camera=False)
    assert (probe.width, probe.height, round(float(probe.fps))) == (1920, 1080, 50)
    assert probe.has_audio and 5700 <= probe.duration_ms <= 6300

    def pixel(at_s: float, x: int, y: int) -> tuple[int, int, int]:
        data = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{at_s}", "-i", str(out), "-frames:v", "1",
             "-vf", f"format=rgb24,crop=1:1:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            capture_output=True, check=True,
        ).stdout  # fmt: skip
        return data[0], data[1], data[2]

    # The title card: navy, the yellow rule under the eyebrow.
    r, g, b = pixel(0.75, 40, 40)
    assert abs(r - 0x13) < 12 and abs(g - 0x20) < 12 and abs(b - 0x3A) < 12
    r, g, b = pixel(0.75, 960, 230 + 66 + 3)
    assert r > 200 and g > 160 and b < 90
    # A clip: the bug's yellow bar at the top left.
    r, g, b = pixel(2.5, 51, 80)
    assert r > 200 and g > 160 and b < 90
