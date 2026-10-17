"""Reading a scoreboard in the picture (PLAN §10.3, step 5): the score in the box a coach drew.

Light first: the crop is thresholded locally, its connected components that look like glyphs
are classified by a tiny network (one hidden layer, numpy, ~270 KB of weights trained on
synthetic flip-card and seven-segment digits by scripts/ml/scoreboard_digits.py), the digits of
one row are grouped into numbers by the gaps between them. When that is not sure, PaddleOCR's
text recogniser (PP-OCRv5, the ONNX file the players job brings into the worker image) reads
each of the two numbers on its own, split where the glyphs' widest gap is: printed flip cards
seen from the side, italic and cut by the edge of the picture, are where it reads better. A
reading is the numbers left to right and how sure the least sure digit is; nomal/ml/board.py
turns the readings of a match (2 per second) into score changes.

    flip   dark digits on light cards (flip-card boards, printed numbers)
    led    light digits on a dark panel (LED and seven-segment boards)
    auto   both, the surer one wins

numpy and OpenCV: the worker image only.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path
from typing import Any, Literal

import cv2
import numpy as np
import numpy.typing as npt

Arr = npt.NDArray[Any]
Kind = Literal["flip", "led", "auto"]

GLYPH_H, GLYPH_W = 28, 20
INNER_H, INNER_W = 24, 18  # a glyph is scaled into this, keeping its aspect, then centred
NOT_DIGIT = 10
CLASSES = 11
WORK_H = 96  # the crop is scaled to this height before reading
MAX_WORK_W = 8 * WORK_H
MIN_DIGIT_P = 0.6  # a component below this is not taken for a digit
MAX_SCORE = 50
WEIGHTS = Path(__file__).parent / "weights" / "scoreboard-digits-v1.npz"
# Checked on load (the file comes with the code; a mismatch means a broken checkout).
WEIGHTS_SHA256: str | None = "2823e371d6cc6b30371fff3adf376dbbfeade8f4173090b1ed5316829c82c134"


@dataclass(frozen=True)
class Glyph:
    x: int  # box in the working image
    y: int
    w: int
    h: int
    digit: int
    p: float


@dataclass(frozen=True)
class Reading:
    numbers: tuple[int, ...]  # left to right
    confidence: float  # the least sure digit's probability
    digits: int  # how many digits were read


class DigitModel:
    """A one-hidden-layer network over a normalised glyph (GLYPH_H x GLYPH_W, 0..1)."""

    def __init__(self, w1: Arr, b1: Arr, w2: Arr, b2: Arr) -> None:
        self.w1 = w1.astype(np.float32)
        self.b1 = b1.astype(np.float32)
        self.w2 = w2.astype(np.float32)
        self.b2 = b2.astype(np.float32)

    @classmethod
    def load(cls, path: Path = WEIGHTS) -> DigitModel:
        data = path.read_bytes()
        if WEIGHTS_SHA256 is not None and hashlib.sha256(data).hexdigest() != WEIGHTS_SHA256:
            raise ValueError(f"{path.name}: SHA-256 does not match")
        with np.load(path) as z:
            return cls(z["w1"], z["b1"], z["w2"], z["b2"])

    def save(self, path: Path) -> None:
        with path.open("wb") as handle:
            np.savez_compressed(handle, w1=self.w1, b1=self.b1, w2=self.w2, b2=self.b2)

    def probs(self, x: Arr) -> Arr:
        """Class probabilities (n, 11) for glyphs (n, GLYPH_H * GLYPH_W)."""
        h = np.maximum(x.astype(np.float32) @ self.w1 + self.b1, 0.0)
        z = h @ self.w2 + self.b2
        z -= z.max(axis=1, keepdims=True)
        e = np.exp(z)
        out: Arr = e / e.sum(axis=1, keepdims=True)
        return out


_MODEL: DigitModel | None = None


def model() -> DigitModel:
    global _MODEL
    if _MODEL is None:
        _MODEL = DigitModel.load()
    return _MODEL


# --- preparing a crop ------------------------------------------------------------------------


def gray(image: Arr) -> Arr:
    if image.ndim == 3:
        out: Arr = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return out
    return image


def working(image: Arr) -> Arr:
    """The crop in gray at WORK_H pixels high (width in proportion, capped)."""
    g = gray(image)
    h, w = g.shape[:2]
    if h < 4 or w < 4:
        return np.zeros((WORK_H, WORK_H), np.uint8)
    scale = WORK_H / h
    width = max(4, min(MAX_WORK_W, round(w * scale)))
    interp = cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA
    out: Arr = cv2.resize(g, (width, WORK_H), interpolation=interp)
    return out


def bright_digits(work: Arr, dark: bool) -> Arr:
    """The working image with the digits bright: inverted for dark digits on light cards."""
    out: Arr = (255 - work) if dark else work.copy()
    return out


def foreground(img: Arr) -> Arr:
    """Pixels clearly brighter than their surroundings (a local threshold): the strokes."""
    block = (WORK_H * 3 // 4) | 1
    blurred = cv2.GaussianBlur(img, (3, 3), 0)
    mask: Arr = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, block, -12
    )
    # Seven-segment digits come in pieces: close the small gaps between a digit's segments.
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, max(3, WORK_H // 14)))
    closed: Arr = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return closed


Box = tuple[int, int, int, int]


@dataclass(frozen=True)
class Component:
    box: Box
    mask: Arr  # the component's pixels in its padded box, a little grown (bool)
    at: tuple[int, int]  # top left of that padded box in the image


def pad_of(h: int) -> int:
    return max(2, h // 12)


def components(mask: Arr, open_left: bool = False, open_right: bool = False) -> list[Component]:
    """Components shaped like a glyph: tall enough, not too wide, not touching the sides (a
    digit cut by the edge of the box is not read; `open_left`/`open_right`: that side of the
    box is the edge of the picture, a digit there is whole)."""
    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    height, width = mask.shape[:2]
    grow = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    out = []
    for i in range(1, n):
        x, y, w, h, area = (int(v) for v in stats[i])
        if h < 0.25 * height or h > 0.98 * height:
            continue
        if w > 1.1 * h or w < 0.06 * h or w > 0.6 * width:
            continue
        if (x <= 0 and not open_left) or (x + w >= width and not open_right):
            continue
        fill = area / max(1, w * h)
        if fill < 0.12 or fill > 0.92:
            continue
        pad = pad_of(h)
        y0, x0 = max(0, y - pad), max(0, x - pad)
        own = labels[y0 : min(height, y + h + pad), x0 : min(width, x + w + pad)] == i
        grown = cv2.dilate(own.astype(np.uint8), grow) > 0
        out.append(Component((x, y, w, h), grown, (x0, y0)))
    return out


def boxes(mask: Arr) -> list[Box]:
    return [c.box for c in components(mask)]


def glyph_input(img: Arr, part: Component | Box) -> Arr:
    """A glyph as the network sees it: its own pixels only (a neighbour leaning into its box,
    an italic digit, is blanked), scaled into INNER_H x INNER_W keeping its aspect, centred
    on GLYPH_H x GLYPH_W, stretched to 0..1 between its dark and bright parts."""
    H, W = img.shape[:2]
    if isinstance(part, Component):
        (x0, y0), own = part.at, part.mask
        crop = img[y0 : y0 + own.shape[0], x0 : x0 + own.shape[1]].astype(np.float32)
        floor = float(np.percentile(crop[~own], 50)) if (~own).any() else float(crop.min())
        crop = np.where(own, crop, floor)
    else:
        x, y, w, h = part
        pad = pad_of(h)
        crop = img[max(0, y - pad) : min(H, y + h + pad), max(0, x - pad) : min(W, x + w + pad)]
    ch, cw = crop.shape[:2]
    scale = min(INNER_H / ch, INNER_W / cw)
    nh, nw = max(1, round(ch * scale)), max(1, round(cw * scale))
    small = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_AREA).astype(np.float32)
    lo, hi = float(np.percentile(small, 5)), float(np.percentile(small, 95))
    small = np.clip((small - lo) / max(hi - lo, 1.0), 0.0, 1.0)
    canvas = np.zeros((GLYPH_H, GLYPH_W), np.float32)
    top, left = (GLYPH_H - nh) // 2, (GLYPH_W - nw) // 2
    canvas[top : top + nh, left : left + nw] = small
    return canvas.ravel()


def classified(
    img: Arr, net: DigitModel, open_left: bool = False, open_right: bool = False
) -> tuple[list[Glyph], list[Glyph]]:
    """The glyph-shaped components of a working image whose digits are bright: those read as
    digits, and the others (with their likeliest digit and its probability)."""
    found = components(foreground(img), open_left, open_right)
    if not found:
        return [], []
    probs = net.probs(np.stack([glyph_input(img, c) for c in found]))
    digits, others = [], []
    for c, p in zip(found, probs, strict=True):
        x, y, w, h = c.box
        best = int(np.argmax(p))
        if best != NOT_DIGIT and p[best] >= MIN_DIGIT_P:
            digits.append(Glyph(x, y, w, h, best, float(p[best])))
        else:
            likely = int(np.argmax(p[:NOT_DIGIT]))
            others.append(Glyph(x, y, w, h, likely, float(p[likely])))
    return digits, others


def glyphs(
    img: Arr, net: DigitModel, open_left: bool = False, open_right: bool = False
) -> list[Glyph]:
    """The digits in a working image whose digits are bright."""
    return classified(img, net, open_left, open_right)[0]


ONE_ASPECT = 0.5
ONE_P = 0.7


def _height(line: list[Glyph]) -> float:
    return float(np.median([g.h for g in line])) if line else 1.0


def unexplained(line: list[Glyph], others: list[Glyph]) -> list[Glyph]:
    """Components in the digits' row, of their height, that were not read as digits: a "1"
    the network missed, a digit half covered. A reading that leaves one out is not sure."""
    if not line:
        return []
    height = float(np.median([g.h for g in line]))
    cy = float(np.median([g.y + g.h / 2 for g in line]))
    x0, x1 = line[0].x - 1.5 * height, line[-1].x + line[-1].w + 1.5 * height
    return [
        g
        for g in others
        if abs(g.y + g.h / 2 - cy) <= 0.3 * height
        and 0.7 <= g.h / height <= 1.4
        and x0 <= g.x + g.w / 2 <= x1
    ]


# --- from digits to numbers -------------------------------------------------------------------


def row(found: list[Glyph]) -> list[Glyph]:
    """The largest set of digits of about one height on one line, left to right."""
    best: list[Glyph] = []
    for anchor in found:
        cy = anchor.y + anchor.h / 2
        same = [
            g
            for g in found
            if abs((g.y + g.h / 2) - cy) <= 0.3 * anchor.h and 0.7 <= g.h / anchor.h <= 1.4
        ]
        key = (len(same), sum(g.p for g in same))
        if key > (len(best), sum(g.p for g in best)):
            best = same
    return sorted(best, key=lambda g: g.x)


def _value(digits: list[Glyph]) -> int | None:
    """A number of one or two digits, no leading zero."""
    if not digits or len(digits) > 2 or (len(digits) == 2 and digits[0].digit == 0):
        return None
    value = 0
    for g in digits:
        value = value * 10 + g.digit
    return value


def numbers(line: list[Glyph]) -> list[list[Glyph]]:
    """Split a row of digits into numbers: a gap wider than ~half a digit's height separates
    two numbers; a run of more than two digits is split at its widest gaps (scores have at
    most two digits, never a leading zero)."""
    if not line:
        return []
    height = float(np.median([g.h for g in line]))
    groups: list[list[Glyph]] = [[line[0]]]
    for prev, g in pairwise(line):
        gap = g.x - (prev.x + prev.w)
        if gap > 0.45 * height:
            groups.append([g])
        else:
            groups[-1].append(g)
    out: list[list[Glyph]] = []
    for group in groups:
        out.extend(_split(group))
    return out


def _split(group: list[Glyph]) -> list[list[Glyph]]:
    if _value(group) is not None:
        return [group]
    if len(group) == 1:
        return []
    gaps = [b.x - (a.x + a.w) for a, b in pairwise(group)]
    cut = int(np.argmax(gaps)) + 1
    left, right = _split(group[:cut]), _split(group[cut:])
    return left + right if left and right else []


class LineReader:
    """PaddleOCR's PP-OCRv5 English recogniser (ONNX, as the players job has it): the digits
    in a crop of one number."""

    def __init__(self, path: Path, threads: int = 1) -> None:
        from nomal.ml import people  # ONNX Runtime: the worker image only

        self._session = people._session(path, threads, None)
        meta = self._session.get_modelmeta().custom_metadata_map
        self.characters = meta.get("character", "").splitlines()
        if not self.characters:
            raise ValueError("the recogniser carries no character list")
        self._height = people.OCR_REC_HEIGHT

    @classmethod
    def find(cls, folder: Path | None = None) -> LineReader | None:
        """The recogniser in the weights' folder, or None (the light reader alone)."""
        from nomal.ml import people

        path = (folder or people.WEIGHTS_DIR) / people.OCR_REC_FILE
        try:
            return cls(path) if path.exists() else None
        except (OSError, ValueError, ImportError):
            return None

    def digits(self, crop: Arr) -> tuple[str, float]:
        """The longest run of digits in a crop (gray or BGR, dark text on light; a card's edge
        or a cut neighbour read as a letter only separates runs) and its least sure digit's
        probability."""
        img = crop if crop.ndim == 3 else cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR)
        pad = max(2, img.shape[0] // 8)
        img = cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
        h, w = img.shape[:2]
        width = max(16, min(320, round(self._height * w / max(h, 1))))
        x = cv2.resize(img, (width, self._height), interpolation=cv2.INTER_LINEAR)
        x = x.astype(np.float32) / 127.5 - 1
        probs = self._session.run(
            None, {self._session.get_inputs()[0].name: x.transpose(2, 0, 1)[None]}
        )
        return longest_digits(probs[0][0], self.characters)


def longest_digits(probs: Arr, characters: list[str]) -> tuple[str, float]:
    """Greedy CTC decoding (T, classes; class 0 = blank), then the longest run of digits."""
    runs: list[list[tuple[str, float]]] = [[]]
    previous = 0
    for t, k in enumerate(probs.argmax(axis=1)):
        k = int(k)
        if k != 0 and k != previous:
            ch = characters[k - 1] if k - 1 < len(characters) else " "
            if ch.isdigit() and ch.isascii():
                runs[-1].append((ch, float(probs[t, k])))
            elif runs[-1]:
                runs.append([])
        previous = k
    best = max(runs, key=lambda r: (len(r), sum(c for _, c in r)))
    if not best:
        return "", 0.0
    return "".join(ch for ch, _ in best), min(c for _, c in best)


def _number(text: str) -> int | None:
    if not text or len(text) > 2 or (len(text) == 2 and text[0] == "0"):
        return None
    value = int(text)
    return value if value <= MAX_SCORE else None


def splits(work: Arr, dark: bool, open_left: bool, open_right: bool) -> list[int]:
    """Where the two numbers may part, in the working image, widest gap first: the middles of
    the gaps between the glyph-shaped components of the row, measured across their middle
    third (italic digits and a card's slanted edge overlap in their boxes, not there); the
    classifier's opinion aside. Taller components (a card's edge) are left out."""
    parts = components(foreground(bright_digits(work, dark)), open_left, open_right)
    if len(parts) < 2:
        return []
    height = float(np.median([c.box[3] for c in parts]))
    spans: list[tuple[int, int]] = []
    for c in parts:
        _x, y, _w, h = c.box
        if h > 1.25 * height:
            continue
        (x0, y0), own = c.at, c.mask
        top, bottom = y + h // 3 - y0, y + 2 * h // 3 - y0
        cols = np.nonzero(own[max(0, top) : max(top + 1, bottom)].any(axis=0))[0]
        if len(cols):
            spans.append((x0 + int(cols[0]), x0 + int(cols[-1]) + 1))
    spans.sort()
    gaps = sorted(((b[0] - a[1], (a[1] + b[0]) // 2) for a, b in pairwise(spans)), reverse=True)
    return [x for gap, x in gaps[:MAX_SPLITS] if gap > 0]


MAX_SPLITS = 3


def read_ocr(
    image: Arr, kind: Kind, ocr: LineReader, open_left: bool = False, open_right: bool = False
) -> Reading | None:
    """The score by the OCR: the crop cut where the numbers may part (the widest gaps), each
    half read, the surest cut that gives two numbers kept; else the whole line, four digits
    being two numbers of two (two digits, two of one)."""
    work = working(image)
    g = gray(image)
    scale = g.shape[1] / work.shape[1]
    best: Reading | None = None
    for dark in (True,) if kind == "flip" else (False,) if kind == "led" else (True, False):
        ink = g if dark else 255 - g  # dark digits on light, as the recogniser was trained
        for cut in splits(work, dark, open_left, open_right):
            x = round(cut * scale)
            if not 2 <= x <= g.shape[1] - 2:
                continue
            (left, c1), (right, c2) = ocr.digits(ink[:, :x]), ocr.digits(ink[:, x:])
            a, b = _number(left), _number(right)
            if a is not None and b is not None:
                found = Reading((a, b), round(min(c1, c2), 4), len(left) + len(right))
                if best is None or (found.digits, found.confidence) > (
                    best.digits,
                    best.confidence,
                ):
                    best = found
        if best is None:
            text, conf = ocr.digits(ink)
            half = len(text) // 2 if len(text) in (2, 4) else 0
            a = _number(text[:half]) if half else None
            b = _number(text[half:]) if half else None
            if a is not None and b is not None:
                best = Reading((a, b), round(conf * 0.9, 4), len(text))
    return best


def _extends(short: tuple[int, ...], long: tuple[int, ...]) -> bool:
    """`long` is `short` with a tens digit `short` missed (a "1" the edge or the network lost)."""
    return (
        len(short) == len(long)
        and short != long
        and all(a == b or (b >= 10 and b % 10 == a) for a, b in zip(short, long, strict=True))
    )


def combine(light: Reading | None, other: Reading | None) -> Reading | None:
    """Two readers' answers as one: agreeing, the surer one's confidence; one reading a tens
    digit the other missed, that one; disagreeing otherwise, the surer one, but doubted."""
    if light is None or other is None:
        return light or other
    if light.numbers == other.numbers:
        return Reading(light.numbers, max(light.confidence, other.confidence), light.digits)
    if _extends(light.numbers, other.numbers):
        return other
    if _extends(other.numbers, light.numbers):
        return light
    best = light if light.confidence >= other.confidence else other
    if min(light.confidence, other.confidence) <= DOUBTFUL:
        return best  # one of them was in doubt already: the other one
    return Reading(best.numbers, round(best.confidence * DOUBT, 4), best.digits)


DOUBT = 0.45  # the two readers disagree: not counted unless one of them was sure
DOUBTFUL = 0.5  # a reading this unsure (a glyph in its row it could not read) gives way


def read(
    image: Arr,
    kind: Kind = "auto",
    net: DigitModel | None = None,
    pair: bool = True,
    open_left: bool = False,
    open_right: bool = False,
    ocr: LineReader | None = None,
) -> Reading | None:
    """The numbers in a crop (BGR or gray), left to right; with `pair`, the two numbers
    nearest the middle of the crop (a score), else all of them. None when nothing reads.
    `open_left`/`open_right`: the crop reaches the edge of the picture there. With `ocr`, the
    score is read by both readers and their answers combined."""
    light = read_light(image, kind, net, pair, open_left, open_right)
    if ocr is None or not pair:
        return light
    return combine(light, read_ocr(image, kind, ocr, open_left, open_right))


def read_light(
    image: Arr,
    kind: Kind = "auto",
    net: DigitModel | None = None,
    pair: bool = True,
    open_left: bool = False,
    open_right: bool = False,
) -> Reading | None:
    """The light reader alone (the module's docstring)."""
    net = net or model()
    work = working(image)
    best: Reading | None = None
    darks = (True,) if kind == "flip" else (False,) if kind == "led" else (True, False)
    for dark in darks:
        found_digits, others = classified(bright_digits(work, dark), net, open_left, open_right)
        line = row(found_digits)
        # A thin upright stroke of the digits' height in their row is a "1" (the network
        # takes a slanted one for a card's edge, which is taller).
        ones = [
            g
            for g in unexplained(line, others)
            if g.w < ONE_ASPECT * g.h and 0.85 <= g.h / _height(line) <= 1.15
        ]
        if ones:
            line = sorted(
                line + [Glyph(g.x, g.y, g.w, g.h, 1, ONE_P) for g in ones], key=lambda g: g.x
            )
            others = [g for g in others if g not in ones]
        groups = pair_of(line, work.shape[1]) if pair else numbers(line)
        if pair and len(groups) != 2:
            continue
        values = [_value(g) for g in groups]
        if not values or any(v is None or v > MAX_SCORE for v in values):
            continue
        digits = [g for group in groups for g in group]
        doubt = [1.0 - g.p for g in unexplained(line, others)]
        found = Reading(
            tuple(int(v) for v in values if v is not None),
            round(min([g.p for g in digits] + [min(d, 0.5) for d in doubt]), 4),
            len(digits),
        )
        if best is None or found.confidence > best.confidence:
            best = found
    return best


def pair_of(line: list[Glyph], width: int) -> list[list[Glyph]]:
    """The score: two numbers. Up to four digits in a row are split where the widest gap is
    (cards of a flip board can stand close together: a gap of a fifth of a digit's height is
    enough when it is the widest); a longer row is cut into numbers first and the two
    nearest the middle are kept."""
    if len(line) > 4:
        return _middle_pair(numbers(line), width)
    if len(line) < 2:
        return []
    height = float(np.median([g.h for g in line]))
    gaps = [b.x - (a.x + a.w) for a, b in pairwise(line)]
    best: tuple[float, int] | None = None
    for k in range(1, len(line)):
        left, right = line[:k], line[k:]
        if _value(left) is None or _value(right) is None:
            continue
        inner = [g for i, g in enumerate(gaps) if i != k - 1]
        margin = gaps[k - 1] - max(inner, default=0.2 * height)
        if margin > 0 and (best is None or margin > best[0]):
            best = (margin, k)
    if best is None:
        return []
    return [line[: best[1]], line[best[1] :]]


def _middle_pair(groups: list[list[Glyph]], width: int) -> list[list[Glyph]]:
    """Two neighbouring numbers whose middle is nearest the middle of the box: the coach
    centred the box on the score; a set count or a team's number at its edge stays out."""
    if len(groups) <= 2:
        return groups
    centre = width / 2

    def middle(a: list[Glyph], b: list[Glyph]) -> float:
        return abs((a[0].x + b[-1].x + b[-1].w) / 2 - centre)

    k = min(range(len(groups) - 1), key=lambda i: middle(groups[i], groups[i + 1]))
    return [groups[k], groups[k + 1]]


# --- the box in a frame -----------------------------------------------------------------------


@dataclass(frozen=True)
class Rect:
    """A box in normalised frame coordinates (0..1)."""

    x: float
    y: float
    w: float
    h: float

    def pixels(self, width: int, height: int) -> tuple[int, int, int, int]:
        x0 = max(0, min(width - 2, round(self.x * width)))
        y0 = max(0, min(height - 2, round(self.y * height)))
        x1 = max(x0 + 2, min(width, round((self.x + self.w) * width)))
        y1 = max(y0 + 2, min(height, round((self.y + self.h) * height)))
        return x0, y0, x1 - x0, y1 - y0


def union(rects: list[Rect]) -> Rect:
    x0 = min(r.x for r in rects)
    y0 = min(r.y for r in rects)
    x1 = max(r.x + r.w for r in rects)
    y1 = max(r.y + r.h for r in rects)
    return Rect(x0, y0, x1 - x0, y1 - y0)


def sub_crop(frame: Arr, outer: Rect, inner: Rect) -> Arr:
    """The part of a crop of `outer` that `inner` covers (both normalised to the frame)."""
    h, w = frame.shape[:2]
    x = round((inner.x - outer.x) / outer.w * w)
    y = round((inner.y - outer.y) / outer.h * h)
    cw = round(inner.w / outer.w * w)
    ch = round(inner.h / outer.h * h)
    x, y = max(0, min(w - 2, x)), max(0, min(h - 2, y))
    out: Arr = frame[y : min(h, y + max(2, ch)), x : min(w, x + max(2, cw))]
    return out
