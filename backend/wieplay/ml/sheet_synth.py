"""Synthetic Matchblatt photos (PLAN §10.3a, SS4): for the tests, the evaluation and the reader's
training data. Nothing here comes from a real sheet: the form is drawn from its layout
(wieplay/ml/sheet_form.py) with OpenCV's own fonts, the handwriting is digits from a glyph source
(OpenCV's script font in the tests; handwritten digit datasets in scripts/ml/, never in git),
and the "photo" is that page warped, lit, blurred and compressed like a phone picture of a
sheet on a table, including faint carbon copies.

numpy and OpenCV (the analysis extra).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, ClassVar

import cv2
import numpy as np

from wieplay.ml import sheet as sh
from wieplay.ml import sheet_form as sf

F32 = np.ndarray  # float32 images, 0 = paper, 1 = ink, unless said otherwise
Rng = np.random.Generator


# --- handwriting ----------------------------------------------------------------------------------


class Glyphs:
    """A source of handwritten digits: ink in [0, 1], roughly tight, any size."""

    def digit(self, rng: Rng, d: int) -> F32:
        raise NotImplementedError


class HersheyGlyphs(Glyphs):
    """OpenCV's script and plain stroke fonts, slanted and thickened at random: handwriting
    enough for tests, no dataset needed."""

    FONTS = (
        cv2.FONT_HERSHEY_SCRIPT_SIMPLEX,
        cv2.FONT_HERSHEY_SCRIPT_COMPLEX,
        cv2.FONT_HERSHEY_SIMPLEX,
        cv2.FONT_HERSHEY_DUPLEX,
    )

    def digit(self, rng: Rng, d: int) -> F32:
        font = self.FONTS[int(rng.integers(len(self.FONTS)))]
        thick = int(rng.integers(2, 5))
        img = np.zeros((64, 52), np.uint8)
        cv2.putText(img, str(d), (8, 52), font, 1.6, 255, thick, cv2.LINE_AA)
        out = img.astype(np.float32) / 255
        return _slant(out, float(rng.uniform(-0.25, 0.25)))


class ArrayGlyphs(Glyphs):
    """Digits from arrays (N, h, w) with labels: a handwritten digit dataset, loaded by the
    caller (scripts/ml/sheet_cells.py)."""

    def __init__(self, images: np.ndarray, labels: np.ndarray) -> None:
        self.by_digit = [images[labels == d] for d in range(10)]

    def digit(self, rng: Rng, d: int) -> F32:
        pool = self.by_digit[d]
        img = pool[int(rng.integers(len(pool)))].astype(np.float32)
        if img.max() > 1.5:
            img /= 255.0
        # The datasets' strokes are a marker's; a ballpoint's are thinner: twice the size, then
        # thinned by up to two pixels.
        img = cv2.resize(img, (56, 56), interpolation=cv2.INTER_CUBIC)
        thin = int(rng.integers(0, 3))
        if thin:
            img = cv2.erode(img, np.ones((3, 3), np.uint8), iterations=thin)
        return np.asarray(np.clip(img, 0, 1), np.float32)


class StrokeGlyphs(Glyphs):
    """Continental-European forms a US dataset lacks: the 1 with a long up-stroke (often a
    "Λ"), the 7 with a bar, and plain stroke digits drawn from a skeleton."""

    # Polylines in a unit box (x right, y down) per digit; jittered on every draw.
    SKELETONS: ClassVar[dict[int, list[list[tuple[float, float]]]]] = {
        0: [
            [
                (0.5, 0.0),
                (0.15, 0.25),
                (0.12, 0.7),
                (0.5, 1.0),
                (0.85, 0.7),
                (0.85, 0.25),
                (0.5, 0.0),
            ]
        ],
        1: [[(0.1, 0.45), (0.6, 0.0), (0.6, 1.0)]],
        2: [[(0.12, 0.25), (0.45, 0.0), (0.85, 0.2), (0.75, 0.5), (0.1, 1.0), (0.95, 1.0)]],
        3: [
            [(0.1, 0.1), (0.5, 0.0), (0.85, 0.2), (0.45, 0.48), (0.9, 0.72), (0.5, 1.0), (0.1, 0.9)]
        ],
        4: [[(0.7, 1.0), (0.7, 0.0), (0.05, 0.7), (0.95, 0.7)]],
        5: [
            [(0.85, 0.0), (0.2, 0.0), (0.15, 0.45), (0.6, 0.4), (0.9, 0.7), (0.5, 1.0), (0.1, 0.9)]
        ],
        6: [
            [
                (0.8, 0.05),
                (0.3, 0.3),
                (0.15, 0.75),
                (0.5, 1.0),
                (0.85, 0.75),
                (0.5, 0.5),
                (0.18, 0.65),
            ]
        ],
        7: [[(0.08, 0.05), (0.92, 0.05), (0.35, 1.0)], [(0.3, 0.52), (0.85, 0.48)]],
        8: [
            [
                (0.5, 0.5),
                (0.15, 0.25),
                (0.5, 0.0),
                (0.85, 0.25),
                (0.5, 0.5),
                (0.1, 0.75),
                (0.5, 1.0),
                (0.9, 0.75),
                (0.5, 0.5),
            ]
        ],
        9: [
            [
                (0.85, 0.35),
                (0.5, 0.0),
                (0.15, 0.3),
                (0.5, 0.55),
                (0.85, 0.35),
                (0.8, 0.7),
                (0.4, 1.0),
            ]
        ],
    }

    def digit(self, rng: Rng, d: int) -> F32:
        h = 48
        w = 40
        img = np.zeros((h + 16, w + 16), np.float32)
        thick = int(rng.integers(2, 6))
        strokes = [list(s) for s in self.SKELETONS[d]]
        if d == 1 and rng.random() < 0.5:
            # The "Λ" one: a long flag, nearly as long as the stem.
            strokes = [[(0.0, 0.75), (0.55, 0.0), (0.75, 1.0)]]
        if d == 7 and rng.random() < 0.4:
            strokes = strokes[:1]  # no bar
        for stroke in strokes:
            pts = np.array(
                [
                    (8 + (x + rng.normal(0, 0.04)) * w, 8 + (y + rng.normal(0, 0.03)) * h)
                    for x, y in stroke
                ],
                np.float32,
            )
            cv2.polylines(img, [np.round(pts).astype(np.int32)], False, 1.0, thick, cv2.LINE_AA)
        return _slant(img, float(rng.uniform(-0.3, 0.2)))


class MixedGlyphs(Glyphs):
    def __init__(self, sources: Sequence[tuple[Glyphs, float]]) -> None:
        self.sources = list(sources)
        total = sum(w for _, w in sources)
        self.p = np.array([w / total for _, w in sources])

    def digit(self, rng: Rng, d: int) -> F32:
        k = int(rng.choice(len(self.sources), p=self.p))
        return self.sources[k][0].digit(rng, d)


def _slant(img: F32, shear: float) -> F32:
    h, w = img.shape
    m = np.array([[1, shear, -shear * h / 2], [0, 1, 0]], np.float32)
    return np.asarray(cv2.warpAffine(img, m, (w, h), flags=cv2.INTER_LINEAR), np.float32)


def _tight(img: F32) -> F32:
    ys, xs = np.nonzero(img > 0.15)
    if len(ys) == 0:
        return img
    return img[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]


def number_ink(rng: Rng, glyphs: Glyphs, text: str, height: int) -> F32:
    """Handwriting of a short text of digits (and ':'), `height` px tall."""
    parts: list[F32] = []
    for ch in text:
        if ch == ":":
            dot = max(2, height // 9)
            g = np.zeros((height, dot * 3), np.float32)
            cv2.circle(g, (dot + 1, int(height * 0.35)), dot // 2 + 1, 1.0, -1)
            cv2.circle(g, (dot + 1, int(height * 0.75)), dot // 2 + 1, 1.0, -1)
            parts.append(g)
            continue
        g = _tight(glyphs.digit(rng, int(ch)))
        gh = max(4, int(height * rng.uniform(0.85, 1.05)))
        gw = max(2, int(g.shape[1] * gh / max(g.shape[0], 1) * rng.uniform(0.8, 1.15)))
        sized = np.asarray(cv2.resize(g, (gw, gh), interpolation=cv2.INTER_AREA), np.float32)
        pad = np.zeros((height, gw), np.float32)
        top = int(rng.integers(0, max(1, height - gh + 1)))
        pad[top : top + gh] = sized[: height - top]
        parts.append(pad)
    gap = [max(0, int(height * rng.uniform(-0.08, 0.12))) for _ in parts]
    width = sum(p.shape[1] for p in parts) + sum(gap[1:]) + 2
    out = np.zeros((height, width), np.float32)
    x = 0
    for k, p in enumerate(parts):
        if k:
            x += gap[k]
        out[:, x : x + p.shape[1]] = np.maximum(out[:, x : x + p.shape[1]], p)
        x += p.shape[1]
    return np.clip(out, 0, 1)


# --- the printed form -----------------------------------------------------------------------------


def _pt(x: float, y: float, s: float) -> tuple[int, int]:
    return round(x * s), round(y * s)


def _text(
    img: np.ndarray, text: str, x: float, y: float, s: float, size_mm: float, thick: int = 1
) -> None:
    scale = size_mm * s / 22.0
    cv2.putText(img, text, _pt(x, y, s), cv2.FONT_HERSHEY_SIMPLEX, scale, 0, thick, cv2.LINE_AA)


def render_form(s: float) -> np.ndarray:
    """The empty form at `s` px per mm, uint8 grey (255 = paper)."""
    img = np.full((round(sf.PAGE_H * s), round(sf.PAGE_W * s)), 255, np.uint8)
    thin = max(1, round(0.18 * s))
    for x0, y0, x1, y1 in sf.lines():
        cv2.line(img, _pt(x0, y0, s), _pt(x1, y1, s), 60, thin, cv2.LINE_AA)
    for b in sf.BLOCKS.values():
        cv2.rectangle(
            img, _pt(b.x, b.y, s), _pt(b.x + b.w, b.y + b.h, s), 30, max(1, round(0.35 * s))
        )
        for k, ch in enumerate("SATZ"):
            _text(img, ch, b.x + 1.5, b.y + 6 + k * 5.5, s, 4.0)
        _text(img, str(b.set_no), b.x + 2, b.y + b.h - 2, s, 4.0)
        rows = b.service_rows
        for h in b.halves:
            y_r, hr = b.row(1)
            for p, roman in enumerate(("I", "II", "III", "IV", "V", "VI"), start=1):
                cx, cw = h.column(p)
                _text(img, roman, cx + cw / 2 - len(roman) * 0.9, y_r + hr - 1.2, s, 3.2)
                for row in (4, 5):
                    y, rh = b.row(row)
                    _text(img, ":", cx + cw / 2 - 0.5, y + rh - 1.2, s, 2.6)
                for box in range(1, 2 * rows + 1):
                    r = sf.service_rect(b, h, p, box)
                    _text(img, str(box), r.x1 - 1.6, r.y + 1.9, s, 1.7)
            y_h, hh = b.row(0)
            _text(img, "Mannschaft", h.x + 22.5, y_h + 2.2, s, 1.6)
            if h.half != "C":
                lo = sf.START_FIELD if h.half == "L" else sf.END_FIELD
                _text(
                    img, "Beginn" if h.half == "L" else "Ende", h.x + lo[0] - 8, y_h + 2.2, s, 1.6
                )
                sr = sf.SR_LEFT if h.half == "L" else sf.SR_RIGHT
                cxs = h.x + (sr[0] + sr[1]) / 2
                cv2.circle(img, _pt(cxs, y_h + hh * 0.27, s), round(1.4 * s), 40, thin)
                cv2.circle(img, _pt(cxs, y_h + hh * 0.73, s), round(1.4 * s), 40, thin)
                _text(img, "S", cxs - 0.6, y_h + hh * 0.27 + 0.7, s, 1.6)
                _text(img, "R", cxs - 0.6, y_h + hh * 0.73 + 0.7, s, 1.6)
                cv2.circle(img, _pt(h.x + sr[0] - 2.6, y_h + hh / 2, s), round(2.7 * s), 40, thin)
            # The running score's printed numbers, and the "T".
            n = len(b.rows)
            top = b.y + b.rows[1]
            bottom = b.y + b.rows[n - 4]
            per = 12 if h.punkte_cols == 4 else 10 if h.punkte_cols == 3 else 8
            step = (bottom - top) / per
            cw = h.punkte_w / h.punkte_cols
            for c in range(h.punkte_cols):
                for k in range(per):
                    num = c * per + k + 1
                    _text(
                        img, str(num), h.punkte_x + c * cw + 0.6, top + (k + 1) * step - 0.5, s, 1.6
                    )
            y_t, ht = b.row(n - 4)
            _text(img, '"T"', h.punkte_x + h.punkte_w / 2 - 2, y_t + ht - 1, s, 3.0, 2)
            for k in (n - 3, n - 2):
                y, rh = b.row(k)
                _text(img, ":", h.punkte_x + h.punkte_w / 2 - 0.5, y + rh - 1.2, s, 2.6)
    for r in sf.FRAMES:
        for k in range(1, int(r.h // 6)):
            cv2.line(img, _pt(r.x, r.y + k * 6, s), _pt(r.x1, r.y + k * 6, s), 120, thin)
    _text(img, "MATCHBLATT (synthetic)", 150, 20, s, 6.0, 2)
    return img


# --- what is written ------------------------------------------------------------------------------


@dataclass
class HalfContent:
    """What one team's half of a set holds (values by position and box)."""

    boxes: dict[tuple[int, int], int] = field(default_factory=dict)
    x_box: bool = False  # the X in position I, box 1 (this team received first)
    circled: tuple[int, int] | None = None
    struck: set[tuple[int, int]] = field(default_factory=set)
    lineup: list[int | None] = field(default_factory=lambda: [None] * 6)
    sub_in: dict[int, int] = field(default_factory=dict)
    sub_at: dict[int, tuple[int, int]] = field(default_factory=dict)
    sub_back: dict[int, tuple[int, int]] = field(default_factory=dict)
    timeouts: list[tuple[int, int]] = field(default_factory=list)
    time: str | None = None  # start (left half) or end (right half), hh:mm
    serves_first: bool = False  # the X in its S circle (else in R)
    points: int = 0  # the running score struck so far


@dataclass
class SheetContent:
    """A synthetic sheet: per set and half (L, R) what is written; `truth` keeps the sets as
    the decoder takes them (us = the left team in sets 1 and 3, as team A)."""

    halves: dict[tuple[int, str], HalfContent]
    truth: dict[int, sh.SheetSet] = field(default_factory=dict)
    finals: dict[int, tuple[int, int]] = field(default_factory=dict)  # (us, them)
    us_letter: str = "A"


def half_of(set_no: int, side: sh.Side, us_letter: str) -> str:
    """Which half a team writes in: A on the left in sets 1 and 3, B in 2 and 4."""
    left = sf.left_letter(set_no) or "A"
    is_us_left = left == us_letter
    return "L" if (side == "us") == is_us_left else "R"


def match_content(
    rng: Rng, sets: int = 4, us_letter: str = "A", side_out: float = 0.6, extras: bool = True
) -> SheetContent:
    """A plausible match: consistent sets (wieplay/ml/sheet.py's simulator), lineups,
    substitutions and timeouts on the path, times."""
    py = __import__("random").Random(int(rng.integers(1 << 30)))
    halves: dict[tuple[int, str], HalfContent] = {}
    truth: dict[int, sh.SheetSet] = {}
    finals: dict[int, tuple[int, int]] = {}
    hour, minute = 18 + int(rng.integers(0, 3)), int(rng.integers(0, 4)) * 15
    for no in range(1, sets + 1):
        s, _ = sh.synthetic_set(py, no, side_out)
        decoded = sh.decode(s)
        truth[no] = s
        finals[no] = decoded.final
        for side in sh.SIDES:
            hc = HalfContent()
            first = s.first_serve == side
            for p, b in sh.cells(first):
                v = s.value(side, p, b)
                if v is not None:
                    hc.boxes[(p, b)] = v
                    hc.struck.add((p, b))
            hc.x_box = not first
            hc.serves_first = first
            hc.circled = s.circled.get(side)
            hc.lineup = list(s.lineup.get(side) or [None] * 6)
            hc.points = decoded.final[0 if side == "us" else 1]
            halves[(no, half_of(no, side, us_letter))] = hc
        scores = decoded.scores()
        if extras:
            for side in sh.SIDES:
                hc = halves[(no, half_of(no, side, us_letter))]
                for _ in range(int(rng.integers(0, 3))):
                    k = int(rng.integers(1, len(scores)))
                    u, t = scores[k]
                    hc.timeouts.append((u, t) if side == "us" else (t, u))
                hc.timeouts = hc.timeouts[:2]
                for p in rng.choice(np.arange(1, 7), int(rng.integers(0, 3)), replace=False):
                    k = int(rng.integers(1, len(scores) - 1))
                    u, t = scores[k]
                    hc.sub_in[int(p)] = int(rng.integers(1, 25))
                    hc.sub_at[int(p)] = (u, t) if side == "us" else (t, u)
                    if rng.random() < 0.5:
                        k2 = int(rng.integers(k, len(scores)))
                        u2, t2 = scores[k2]
                        hc.sub_back[int(p)] = (u2, t2) if side == "us" else (t2, u2)
        start = hour * 60 + minute
        end = start + int(rng.integers(18, 32))
        halves[(no, "L")].time = f"{start // 60:02d}:{start % 60:02d}"
        halves[(no, "R")].time = f"{end // 60:02d}:{end % 60:02d}"
        hour, minute = divmod(end + int(rng.integers(2, 6)), 60)
    return SheetContent(halves, truth, finals, us_letter)


def random_content(rng: Rng, fill: float = 0.7) -> SheetContent:
    """Random numbers in every kind of cell (not a consistent match): the reader's training
    sheets. Scores 0-40 mostly, now and then up to 99."""

    def number() -> int:
        if rng.random() < 0.75:
            return int(rng.integers(0, 41))
        return int(rng.integers(0, 100))

    halves: dict[tuple[int, str], HalfContent] = {}
    for b in sf.BLOCKS.values():
        for h in b.halves:
            hc = HalfContent()
            boxes = 2 * b.service_rows
            # Like a set: the boxes filled in the sheet's order with rising scores, now and then
            # any number (every class needs examples), now and then a whole grid.
            n = (
                int(rng.integers(0, 25))
                if rng.random() < 0.85
                else int(rng.integers(0, 6 * boxes + 1))
            )
            order = [(p, k) for k in range(1, boxes + 1) for p in range(1, 7)]
            score = int(rng.integers(0, 3))
            for p, k in order[:n]:
                score += int(rng.integers(0, 4))
                if rng.random() < fill + 0.25:
                    hc.boxes[(p, k)] = number() if rng.random() < 0.3 else min(score, 99)
                hc.struck.add((p, k))
            hc.x_box = rng.random() < 0.5
            if hc.x_box:
                hc.boxes.pop((1, 1), None)
            if hc.boxes and rng.random() < 0.6:
                hc.circled = list(hc.boxes)[int(rng.integers(len(hc.boxes)))]
            hc.lineup = [
                int(rng.integers(1, 100)) if rng.random() < fill + 0.2 else None for _ in range(6)
            ]
            for p in range(1, 7):
                if rng.random() < 0.3:
                    hc.sub_in[p] = int(rng.integers(1, 100))
                if rng.random() < 0.3:
                    hc.sub_at[p] = (number(), number())
                if rng.random() < 0.2:
                    hc.sub_back[p] = (number(), number())
            for _ in range(int(rng.integers(0, 3))):
                hc.timeouts.append((number(), number()))
            if rng.random() < 0.7:
                hc.time = f"{int(rng.integers(8, 24)):02d}:{int(rng.integers(0, 60)):02d}"
            hc.serves_first = rng.random() < 0.5
            hc.points = int(rng.integers(0, 49))
            halves[(b.set_no, h.half)] = hc
    return SheetContent(halves)


@dataclass
class Ink:
    colour: tuple[int, int, int]  # BGR
    strength: float  # 1 = a fresh pen, 0.25 = a faint carbon copy
    thickness: float  # stroke thickening in px at 8 px/mm (can be negative)


def random_ink(rng: Rng, carbon: bool | None = None) -> Ink:
    carbon = bool(rng.random() < 0.35) if carbon is None else carbon
    if rng.random() < 0.75:
        colour = (int(rng.integers(120, 200)), int(rng.integers(40, 90)), int(rng.integers(10, 60)))
    else:
        colour = (int(rng.integers(20, 60)),) * 3
    strength = float(rng.uniform(0.25, 0.6)) if carbon else float(rng.uniform(0.75, 1.0))
    return Ink(colour, strength, float(rng.uniform(-0.5, 1.5)))


Writer = Callable[[F32, float, float, float, float], None]


def write(content: SheetContent, glyphs: Glyphs, rng: Rng, s: float) -> F32:
    """The handwriting of a sheet as an ink layer at `s` px per mm (0..1)."""
    ink = np.zeros((round(sf.PAGE_H * s), round(sf.PAGE_W * s)), np.float32)
    # One scorer per sheet, with habits: how big they write, how they strike a box's printed
    # number once it is used (a short tick in its corner, or a long slash after the score).
    scale = float(rng.uniform(0.7, 1.15))
    tick = float(rng.uniform(1.4, 2.4)) if rng.random() < 0.7 else float(rng.uniform(2.4, 3.8))
    tick_dx = float(rng.uniform(0.2, 1.4))
    tick_p = float(rng.uniform(0.6, 1.0))

    def put(text: str, x: float, y: float, w: float, h: float, *, size: float = 1.0) -> None:
        """Write `text` centred in the rect (mm), its height about the rect's, with jitter."""
        hp = max(6, int(h * s * size * scale * rng.uniform(0.8, 1.15)))
        g = number_ink(rng, glyphs, text, hp)
        cx = (x + w / 2 + rng.normal(0, 0.12 * w)) * s
        cy = (y + h / 2 + rng.normal(0, 0.1 * h)) * s
        _blit(ink, g, int(cx - g.shape[1] / 2), int(cy - g.shape[0] / 2))

    def stroke(points: list[tuple[float, float]], width: float = 0.3) -> None:
        pts = np.array([_pt(x, y, s) for x, y in points], np.int32)
        cv2.polylines(ink, [pts], False, 1.0, max(1, round(width * s)), cv2.LINE_AA)

    for (no, half), hc in content.halves.items():
        b = sf.BLOCKS[no]
        h = b.half(half)  # type: ignore[arg-type]
        for (p, k), v in hc.boxes.items():
            r = sf.service_rect(b, h, p, k)
            right = rng.random() < 0.15  # written over towards the next box
            put(str(v), r.x + (0.25 * r.w if right else 0), r.y, r.w, r.h, size=1.05)
            if hc.circled == (p, k):
                centre = _pt(r.x + r.w * 0.55, r.y + r.h / 2, s)
                axes = (
                    round(r.w * s * rng.uniform(0.75, 1.0)),
                    round(r.h * s * rng.uniform(0.65, 0.9)),
                )
                cv2.ellipse(
                    ink,
                    centre,
                    axes,
                    float(rng.uniform(-20, 20)),
                    0,
                    360,
                    1.0,
                    max(1, round(0.3 * s)),
                    cv2.LINE_AA,
                )
        for p, k in hc.struck:
            r = sf.service_rect(b, h, p, k)
            if rng.random() < tick_p:
                x1 = r.x1 - tick_dx + float(rng.normal(0, 0.2))
                y0 = r.y + float(rng.uniform(0.0, 0.5))
                stroke(
                    [(x1 - 0.6 * tick, y0 + 0.9 * tick), (x1, y0)], float(rng.uniform(0.2, 0.32))
                )
        if hc.x_box:
            r = sf.service_rect(b, h, 1, 1)
            m = 0.15 * r.w
            stroke([(r.x + m, r.y + m), (r.x1 - m, r.y1 - m)], 0.35)
            stroke([(r.x1 - m, r.y + m), (r.x + m, r.y1 - m)], 0.35)
        for p, number in enumerate(hc.lineup, start=1):
            if number is not None:
                cx, cw = h.column(p)
                y, rh = b.row(2)
                put(str(number), cx, y, cw, rh, size=1.1)
        for p, number in hc.sub_in.items():
            cx, cw = h.column(p)
            y, rh = b.row(3)
            put(str(number), cx, y, cw, rh)
        for row, scores in ((4, hc.sub_at), (5, hc.sub_back)):
            for p, (a, c) in scores.items():
                cx, cw = h.column(p)
                y, rh = b.row(row)
                put(str(a), cx, y, cw / 2, rh)
                put(str(c), cx + cw / 2, y, cw / 2, rh)
        n = len(b.rows) - 1
        for k, (a, c) in enumerate(hc.timeouts[:2]):
            y, rh = b.row(n - 2 + k)
            put(str(a), h.punkte_x, y, h.punkte_w / 2, rh)
            put(str(c), h.punkte_x + h.punkte_w / 2, y, h.punkte_w / 2, rh)
        if hc.time and half != "C":
            lo, hi = sf.START_FIELD if half == "L" else sf.END_FIELD
            y, rh = b.row(0)
            put(hc.time, h.x + lo, y + 0.6, hi - lo, rh - 1.2, size=0.95)
        if half != "C":
            sr = sf.SR_LEFT if half == "L" else sf.SR_RIGHT
            y, rh = b.row(0)
            cy = y + rh * (0.27 if hc.serves_first else 0.73)
            cx = h.x + (sr[0] + sr[1]) / 2
            stroke([(cx - 1.3, cy - 1.3), (cx + 1.3, cy + 1.3)], 0.3)
            stroke([(cx + 1.3, cy - 1.3), (cx - 1.3, cy + 1.3)], 0.3)
        # The running score: each point slashed.
        top, bottom = b.y + b.rows[1], b.y + b.rows[len(b.rows) - 4]
        per = 12 if h.punkte_cols == 4 else 10 if h.punkte_cols == 3 else 8
        step = (bottom - top) / per
        cw = h.punkte_w / h.punkte_cols
        for k in range(min(hc.points, per * h.punkte_cols)):
            c, row = divmod(k, per)
            x0 = h.punkte_x + c * cw
            y0 = top + row * step
            stroke([(x0 + 0.5, y0 + step - 0.3), (x0 + cw - 0.6, y0 + 0.3)], 0.2)
    return ink


def _blit(dst: F32, g: F32, x: int, y: int) -> None:
    h, w = g.shape
    x0, y0 = max(0, x), max(0, y)
    x1, y1 = min(dst.shape[1], x + w), min(dst.shape[0], y + h)
    if x0 >= x1 or y0 >= y1:
        return
    sub = g[y0 - y : y1 - y, x0 - x : x1 - x]
    dst[y0:y1, x0:x1] = np.maximum(dst[y0:y1, x0:x1], sub)


def page(
    content: SheetContent, glyphs: Glyphs, rng: Rng, s: float = 8.0, ink: Ink | None = None
) -> np.ndarray:
    """The filled form, BGR uint8, at `s` px per mm."""
    ink = ink or random_ink(rng)
    form = render_form(s).astype(np.float32) / 255
    layer = write(content, glyphs, rng, s)
    if ink.thickness > 0.3:
        k = max(1, round(ink.thickness * s / 8))
        layer = cv2.dilate(layer, np.ones((k, k), np.uint8))
    elif ink.thickness < -0.3:
        layer = cv2.erode(layer, np.ones((2, 2), np.uint8))
    layer = cv2.GaussianBlur(layer, (0, 0), 0.5) * ink.strength
    if ink.strength < 0.65:  # carbon: grainy, patchy
        grain = rng.uniform(0.55, 1.0, layer.shape).astype(np.float32)
        layer *= cv2.GaussianBlur(grain, (0, 0), 1.0)
    paper = np.array(_paper_tint(rng), np.float32) / 255
    out = np.empty((*form.shape, 3), np.float32)
    colour = np.array(ink.colour, np.float32) / 255
    for c in range(3):
        printed = paper[c] * form
        out[..., c] = printed * (1 - layer) + colour[c] * layer
    return np.asarray(np.clip(out * 255, 0, 255), np.uint8)


def _paper_tint(rng: Rng) -> tuple[int, int, int]:
    kind = rng.random()
    if kind < 0.6:
        return (int(rng.integers(235, 256)),) * 3
    if kind < 0.8:  # yellow copy
        return (
            int(rng.integers(170, 215)),
            int(rng.integers(225, 250)),
            int(rng.integers(235, 256)),
        )
    return (int(rng.integers(235, 256)), int(rng.integers(215, 240)), int(rng.integers(200, 230)))


# --- the photo ------------------------------------------------------------------------------------


@dataclass
class Photo:
    image: np.ndarray  # BGR uint8
    h_page: np.ndarray  # 3 x 3: page mm → photo px
    params: dict[str, Any]


def photograph(
    pg: np.ndarray,
    rng: Rng,
    s: float = 8.0,
    *,
    width: int | None = None,
    quarter_turns: int = 0,
    tilt: float = 0.06,
    angle: float = 6.0,
    crop: float = 0.0,
    underlay: bool | None = None,
    curl: float = 0.6,
    jpeg: int | None = None,
) -> Photo:
    """A phone photo of the page (`pg` at `s` px per mm): on a table, the page tilted in
    perspective and turned, lit unevenly, curled a little, blurred, noisy, JPEG; `crop` cuts up
    to that fraction off one edge."""
    width = width or int(rng.integers(1400, 3600))
    hp, wp = pg.shape[:2]
    out_w = width
    out_h = int(width * rng.uniform(0.7, 0.78))
    # Where the page's corners land.
    fill = rng.uniform(0.84, 0.98)
    pw, ph = out_w * fill, out_w * fill * sf.PAGE_H / sf.PAGE_W
    if ph > out_h * 0.98:
        ph = out_h * 0.98
        pw = ph * sf.PAGE_W / sf.PAGE_H
    cx = out_w / 2 + rng.uniform(-0.03, 0.03) * out_w
    cy = out_h / 2 + rng.uniform(-0.03, 0.03) * out_h
    a = math.radians(rng.uniform(-angle, angle))
    corners = []
    for dx, dy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        x, y = dx * pw / 2, dy * ph / 2
        x, y = x * math.cos(a) - y * math.sin(a), x * math.sin(a) + y * math.cos(a)
        x += rng.uniform(-tilt, tilt) * pw / 2
        y += rng.uniform(-tilt, tilt) * ph / 2
        corners.append((cx + x, cy + y))
    # A camera's optics blur before the sensor samples: shrink the page with area averaging
    # first (a plain warp would alias thin lines away).
    k = min(1.0, 1.15 * pw / wp)
    if k < 0.95:
        pg = cv2.resize(pg, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
        s *= k
        hp, wp = pg.shape[:2]
    src = np.array([(0, 0), (wp, 0), (wp, hp), (0, hp)], np.float32)
    h_px = np.asarray(cv2.getPerspectiveTransform(src, np.array(corners, np.float32)), np.float64)
    background = _table(rng, out_h, out_w)
    if underlay if underlay is not None else rng.random() < 0.4:
        shift = np.array(
            [
                [1, 0, rng.uniform(-0.1, 0.1) * out_w],
                [0, 1, rng.uniform(0.04, 0.12) * out_h],
                [0, 0, 1],
            ],
            np.float64,
        )
        under = cv2.warpPerspective(
            pg,
            (shift @ h_px).astype(np.float64),
            (out_w, out_h),
            borderMode=cv2.BORDER_TRANSPARENT,
            dst=background.copy(),
        )
        background = under
    warped = background.copy()
    page_in = pg
    if curl > 0:
        page_in = _curl(pg, rng, curl * s)
    cv2.warpPerspective(
        page_in, h_px, (out_w, out_h), dst=warped, borderMode=cv2.BORDER_TRANSPARENT
    )
    img = warped.astype(np.float32)
    # Light: a gradient and a soft shadow.
    yy, xx = np.mgrid[0:out_h, 0:out_w].astype(np.float32)
    gx, gy = rng.uniform(-1, 1, 2)
    light = 1 - 0.25 * rng.random() * ((gx * xx / out_w + gy * yy / out_h) % 1.0)
    if rng.random() < 0.5:
        sx, sy, sr = rng.uniform(0, out_w), rng.uniform(0, out_h), rng.uniform(0.2, 0.5) * out_w
        light *= 1 - 0.3 * np.exp(-((xx - sx) ** 2 + (yy - sy) ** 2) / (2 * sr**2))
    img *= light[..., None] * rng.uniform(0.85, 1.05)
    sigma = rng.uniform(0.3, 1.2) * width / 2400
    blurred = np.asarray(cv2.GaussianBlur(img, (0, 0), max(0.3, sigma)), np.float32)
    blurred += rng.normal(0, rng.uniform(1, 6), blurred.shape).astype(np.float32)
    shot = np.clip(blurred, 0, 255).astype(np.uint8)
    scale = np.diag([s, s, 1.0])
    h_page = h_px @ scale  # mm → page px → photo px
    if crop > 0:
        shot, h_page = _crop(shot, h_page, rng, crop)
    for _ in range(quarter_turns % 4):
        hh = shot.shape[0]
        shot = np.asarray(cv2.rotate(shot, cv2.ROTATE_90_CLOCKWISE))
        h_page = np.array([[0, -1, hh - 1], [1, 0, 0], [0, 0, 1]], np.float64) @ h_page
    q = jpeg or int(rng.integers(60, 93))
    ok, enc = cv2.imencode(".jpg", shot, [cv2.IMWRITE_JPEG_QUALITY, q])
    assert ok
    out = np.asarray(cv2.imdecode(enc, cv2.IMREAD_COLOR))
    return Photo(out, h_page / h_page[2, 2], {"width": width, "jpeg": q, "turns": quarter_turns})


def _table(rng: Rng, h: int, w: int) -> np.ndarray:
    base = np.array([rng.integers(40, 200) for _ in range(3)], np.float32)
    noise = cv2.resize(
        rng.normal(0, 1, (max(2, h // 40), max(2, w // 8))).astype(np.float32),
        (w, h),
        interpolation=cv2.INTER_CUBIC,
    )
    img = base[None, None, :] + noise[..., None] * rng.uniform(5, 25)
    return np.clip(img, 0, 255).astype(np.uint8)


def _curl(pg: np.ndarray, rng: Rng, amp: float) -> np.ndarray:
    """A gentle bend: rows shift by a smooth function of x (the paper lifts at one side)."""
    h, w = pg.shape[:2]
    xs = np.arange(w, dtype=np.float32)
    phase = rng.uniform(0, math.pi)
    dy = amp * np.sin(xs / w * math.pi + phase).astype(np.float32)
    map_x = np.tile(xs, (h, 1))
    map_y = np.arange(h, dtype=np.float32)[:, None] + dy[None, :]
    return cv2.remap(
        pg, map_x, map_y.astype(np.float32), cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE
    )


def _crop(
    img: np.ndarray, h_page: np.ndarray, rng: Rng, frac: float
) -> tuple[np.ndarray, np.ndarray]:
    h, w = img.shape[:2]
    cut = rng.uniform(0.3, 1.0) * frac
    edge = int(rng.integers(4))
    x0, y0, x1, y1 = 0, 0, w, h
    if edge == 0:
        x0 = int(w * cut)
    elif edge == 1:
        x1 = int(w * (1 - cut))
    elif edge == 2:
        y0 = int(h * cut)
    else:
        y1 = int(h * (1 - cut))
    shift = np.array([[1, 0, -x0], [0, 1, -y0], [0, 0, 1]], np.float64)
    return img[y0:y1, x0:x1].copy(), shift @ h_page


def truth_cells(content: SheetContent) -> dict[str, int | str | None]:
    """What each readable cell holds (sheet_form keys): a number, "X", or None (blank)."""
    out: dict[str, int | str | None] = {}
    for c in sf.CELLS:
        hc = content.halves.get((c.set_no, c.half))
        if c.kind == "mark":
            continue
        value: int | str | None = None
        if hc is not None:
            p = c.position or 0
            if c.kind == "box":
                key = (p, c.box or 0)
                value = hc.boxes.get(key)
                if hc.x_box and key == (1, 1):
                    value = "X"
            elif c.kind == "lineup":
                value = hc.lineup[p - 1]
            elif c.kind == "sub_in":
                value = hc.sub_in.get(p)
            elif c.kind == "score":
                what = c.key.split(".")[2]
                part = c.part or 0
                if what == "sub_at" and p in hc.sub_at:
                    value = hc.sub_at[p][part]
                elif what == "sub_back" and p in hc.sub_back:
                    value = hc.sub_back[p][part]
                elif what == "to" and c.box is not None and c.box <= len(hc.timeouts):
                    value = hc.timeouts[c.box - 1][part]
            elif c.kind == "time" and hc.time:
                hh, mm = hc.time.split(":")
                value = int(hh) if c.part == 0 else int(mm)
        out[c.key] = value
    return out
