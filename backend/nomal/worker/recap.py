"""The game recap's pictures (PLAN §7.7): a title card and a score bug, drawn by ffmpeg.

The API chose the plays and wrote every word (nomal/core/recap.py); the worker draws them:
`drawtext` with the texts in files (no escaping of team names), `drawbox` for the panels,
DejaVu Sans (regular and bold) from the worker image. Brand colours (PLAN §13.2): navy panels,
yellow accents, light text. The card and the clips are encoded like every export piece, so the
export job joins them without re-encoding.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nomal.worker.ffmpeg import EXPORT_HEIGHT, EXPORT_WIDTH, filter_path

NAVY, LIGHT, YELLOW = "0x13203A", "0xEEF1F5", "0xFFC72C"
BUG_X, BUG_Y = 48, 44
FADE_S = 0.25
# Average advance of a glyph in DejaVu Sans, in em (digits are 0.64 regular, 0.70 bold): to
# size the bug's panel without measuring text.
EM_REGULAR, EM_BOLD = 0.62, 0.70


@dataclass(frozen=True)
class Fonts:
    regular: str
    bold: str


class Texts:
    """Each text in its own file under `folder` (drawtext's textfile=)."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.count = 0

    def __call__(self, text: str) -> str:
        path = self.folder / f"text_{self.count:03d}.txt"
        self.count += 1
        path.write_text(text[:300])
        return filter_path(path)


def drawtext(
    textfile: str,
    font: str,
    size: int,
    color: str,
    x: str | int,
    y: str | int,
    enable: str | None = None,
) -> str:
    out = (
        f"drawtext=fontfile={filter_path(font)}:textfile={textfile}:fontsize={size}:"
        f"fontcolor={color}:x={x}:y={y}"
    )
    return out + (f":enable='{enable}'" if enable else "")


def drawbox(x: int, y: int, w: int, h: int, color: str) -> str:
    return f"drawbox=x={x}:y={y}:w={w}:h={h}:color={color}:t=fill"


def text_width(text: str, size: int, bold: bool) -> int:
    return round(len(text) * size * (EM_BOLD if bold else EM_REGULAR))


def bug_filters(
    bug: dict[str, Any], switch_s: float | None, texts: Texts, fonts: Fonts
) -> list[str]:
    """The score bug, top left: the set, the score (the new one from `switch_s`, the moment
    of the point, in the piece's own time), what the play was."""
    head, before, after = (
        str(bug.get("set") or ""),
        str(bug.get("before") or ""),
        str(bug.get("after") or ""),
    )
    note = str(bug.get("label") or "")
    if not (head or before or after):
        return []
    width = (
        max(
            text_width(head, 28, True),
            text_width(before, 56, True),
            text_width(after, 56, True),
            text_width(note, 28, False),
            160,
        )
        + 64
    )
    height = 130 + (44 if note else 0)
    x, y = BUG_X, BUG_Y
    out = [drawbox(x, y, width, height, f"{NAVY}@0.88"), drawbox(x, y, 8, height, YELLOW)]
    if head:
        out.append(drawtext(texts(head), fonts.bold, 28, YELLOW, x + 32, y + 18))
    if before and after and before != after and switch_s is not None:
        at = f"{max(0.0, switch_s):.3f}"
        out.append(drawtext(texts(before), fonts.bold, 56, LIGHT, x + 32, y + 56, f"lt(t,{at})"))
        out.append(drawtext(texts(after), fonts.bold, 56, LIGHT, x + 32, y + 56, f"gte(t,{at})"))
    elif after or before:
        shown = after if switch_s is not None and switch_s <= 0 else (before or after)
        out.append(drawtext(texts(shown), fonts.bold, 56, LIGHT, x + 32, y + 56))
    if note:
        out.append(drawtext(texts(note), fonts.regular, 28, f"{LIGHT}@0.9", x + 32, y + 132))
    return out


def fades(duration_s: float, fade_in: bool, fade_out: bool) -> tuple[list[str], str | None]:
    """A short fade from and to black (and silence) at the ends of a clip."""
    video: list[str] = []
    audio: list[str] = []
    d = min(FADE_S, duration_s / 4)
    if fade_in:
        video.append(f"fade=t=in:st=0:d={d:.3f}")
        audio.append(f"afade=t=in:st=0:d={d:.3f}")
    if fade_out:
        start = max(0.0, duration_s - d)
        video.append(f"fade=t=out:st={start:.3f}:d={d:.3f}")
        audio.append(f"afade=t=out:st={start:.3f}:d={d:.3f}")
    return video, (",".join(audio) or None)


def _centred(texts: Texts, text: str, font: str, size: int, color: str, y: int) -> str:
    return drawtext(texts(text), font, size, color, "(w-tw)/2", y)


def fit_size(text: str, largest: int, smallest: int, width: int = EXPORT_WIDTH - 240) -> int:
    """The largest font size (bold) at which the text fits the card's width."""
    size = largest
    while size > smallest and text_width(text, size, True) > width:
        size -= 2
    return size


def card_filters(card: dict[str, Any], duration_s: float, texts: Texts, fonts: Fonts) -> list[str]:
    """The title card: what it is, the teams (on two lines when long), the result in sets, the
    set scores, when and where."""
    out: list[str] = []
    eyebrow = str(card.get("eyebrow") or "")
    matchup = str(card.get("matchup") or "")
    result = card.get("result")
    sets = card.get("sets")
    when = str(card.get("when") or "")
    footer = str(card.get("footer") or "")
    lines = [matchup]
    size = fit_size(matchup, 84, 52)
    if text_width(matchup, size, True) > EXPORT_WIDTH - 240 and " \N{EN DASH} " in matchup:
        home, away = matchup.split(" \N{EN DASH} ", 1)
        lines = [home, f"\N{EN DASH} {away}"]
        size = min(fit_size(home, 72, 30), fit_size(lines[1], 72, 30))
    y = 230 if result else 360
    if eyebrow:
        out.append(_centred(texts, eyebrow, fonts.bold, 40, YELLOW, y))
    out.append(drawbox(EXPORT_WIDTH // 2 - 60, y + 66, 120, 6, YELLOW))
    y += 110
    for line in lines:
        if line:
            out.append(_centred(texts, line, fonts.bold, size, LIGHT, y))
            y += round(size * 1.25)
    y += 20
    if result:
        out.append(_centred(texts, str(result), fonts.bold, 150, YELLOW, y))
        y += 190
    if sets:
        out.append(_centred(texts, str(sets), fonts.bold, fit_size(str(sets), 52, 30), LIGHT, y))
        y += 90
    if when:
        out.append(_centred(texts, when, fonts.regular, fit_size(when, 36, 24), f"{LIGHT}@0.8", y))
    if footer:
        out.append(_centred(texts, footer, fonts.regular, 28, f"{LIGHT}@0.6", EXPORT_HEIGHT - 110))
    video, _ = fades(duration_s, True, True)
    return out + video


def switch_at(bug: dict[str, Any] | None, part_start_ms: int) -> float | None:
    """When the point falls, in seconds into a part that starts at `part_start_ms` (video
    time)."""
    if not bug or bug.get("point_ms") is None:
        return None
    return (int(bug["point_ms"]) - part_start_ms) / 1000


def piece_overlays(
    piece: dict[str, Any],
    part_start_ms: int,
    duration_s: float,
    first: bool,
    last: bool,
    texts: Texts,
    fonts: Fonts,
) -> tuple[list[str], str | None]:
    """The filters of one part of a recap clip: the bug, the fades at the clip's ends."""
    bug = piece.get("bug")
    video = bug_filters(bug, switch_at(bug, part_start_ms), texts, fonts) if bug else []
    fade_video, fade_audio = fades(duration_s, first, last)
    return video + fade_video, fade_audio


def bold_of(regular: str, candidates: Sequence[str] = ()) -> str:
    """The bold face next to the regular one (DejaVuSans.ttf → DejaVuSans-Bold.ttf)."""
    path = Path(regular)
    for name in [*candidates, str(path.with_name(path.stem + "-Bold" + path.suffix))]:
        if Path(name).exists():
            return name
    return regular
