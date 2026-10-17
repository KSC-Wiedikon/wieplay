"""Synthetic scoreboards (PLAN §10.3, step 5): flip cards and seven-segment panels with known
numbers, for training the digit network (scripts/ml/scoreboard_digits.py) and for testing the
reader. A board is drawn large and clean, then made as bad as a hall camera makes it: tilted
and sheared (the board seen from the side), blurred, shrunk to a few pixels per digit, noisy,
JPEG-compressed, partly covered.

Glyph shapes: seven segments drawn here, OpenCV's Hershey fonts, and (training only) TrueType
fonts that the script renders with Pillow and passes in as masks.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

import cv2
import numpy as np

from wieplay.ml.scoreboard import (
    NOT_DIGIT,
    Arr,
    bright_digits,
    components,
    foreground,
    glyph_input,
    working,
)

Style = Literal["flip", "led"]
# A digit's mask at a height (uint8, 255 = ink), from a font the caller has.
MaskFont = Callable[[int, int], Arr]

SEGMENTS = {  # a top, b top right, c bottom right, d bottom, e bottom left, f top left, g middle
    0: "abcdef", 1: "bc", 2: "abged", 3: "abgcd", 4: "fgbc",
    5: "afgcd", 6: "afgedc", 7: "abc", 8: "abcdefg", 9: "abcdfg",
}  # fmt: skip
HERSHEY = (
    cv2.FONT_HERSHEY_SIMPLEX,
    cv2.FONT_HERSHEY_DUPLEX,
    cv2.FONT_HERSHEY_TRIPLEX,
    cv2.FONT_HERSHEY_COMPLEX,
)


def seven_segment(digit: int, height: int, rng: np.random.Generator) -> Arr:
    """A seven-segment digit as a mask: bars with bevelled ends, a small gap between them."""
    width = max(6, round(height * rng.uniform(0.48, 0.62)))
    t = max(2.0, height * rng.uniform(0.09, 0.15))  # bar thickness
    gap = t * rng.uniform(0.08, 0.3)
    mask = np.zeros((height, width), np.uint8)
    top, mid, bottom = t / 2, height / 2, height - t / 2
    left, right = t / 2, width - t / 2
    bars = {
        "a": ((left, top), (right, top)),
        "g": ((left, mid), (right, mid)),
        "d": ((left, bottom), (right, bottom)),
        "f": ((left, top), (left, mid)),
        "b": ((right, top), (right, mid)),
        "e": ((left, mid), (left, bottom)),
        "c": ((right, mid), (right, bottom)),
    }
    variants = SEGMENTS[digit]
    if digit == 6 and rng.random() < 0.2:
        variants = variants.replace("a", "")
    if digit == 9 and rng.random() < 0.2:
        variants = variants.replace("d", "")
    if digit == 7 and rng.random() < 0.2:
        variants += "f"
    for name in variants:
        (x0, y0), (x1, y1) = bars[name]
        h = t / 2
        if y0 == y1:  # horizontal
            x0, x1 = x0 + gap + h * 0.6, x1 - gap - h * 0.6
            pts = [
                (x0 - h, y0),
                (x0, y0 - h),
                (x1, y0 - h),
                (x1 + h, y0),
                (x1, y0 + h),
                (x0, y0 + h),
            ]
        else:
            y0, y1 = y0 + gap + h * 0.6, y1 - gap - h * 0.6
            pts = [
                (x0, y0 - h),
                (x0 + h, y0),
                (x0 + h, y1),
                (x0, y1 + h),
                (x0 - h, y1),
                (x0 - h, y0),
            ]
        cv2.fillPoly(mask, [np.round(np.array(pts) * 16).astype(np.int32)], 255, cv2.LINE_AA, 4)
    return mask


def hershey(digit: int, height: int, rng: np.random.Generator) -> Arr:
    """A digit in one of OpenCV's stroke fonts, bold."""
    font = HERSHEY[int(rng.integers(len(HERSHEY)))]
    base = 200
    scale = cv2.getFontScaleFromHeight(font, base)
    thick = max(2, round(base * rng.uniform(0.09, 0.17)))
    canvas = np.zeros((base * 2, base * 2), np.uint8)
    cv2.putText(
        canvas, str(digit), (base // 3, base + base // 3), font, scale, 255, thick, cv2.LINE_AA
    )
    return _fit(canvas, height)


def _fit(canvas: Arr, height: int) -> Arr:
    """Cut a mask to its ink and scale it to `height`."""
    ys, xs = np.nonzero(canvas > 40)
    if len(ys) == 0:
        return np.zeros((height, max(2, height // 2)), np.uint8)
    cut = canvas[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    scale = height / cut.shape[0]
    width = max(2, round(cut.shape[1] * scale))
    out: Arr = cv2.resize(cut, (width, height), interpolation=cv2.INTER_AREA)
    return out


@dataclass
class Board:
    image: Arr  # gray
    digits: list[tuple[int, int, int, int, int]]  # x, y, w, h, digit (where the ink is)
    score_box: tuple[int, int, int, int]  # x, y, w, h around the two score numbers
    numbers: tuple[int, int]
    style: Style
    extra: dict[str, Any] = field(default_factory=dict)


def _paste(dst: Arr, mask: Arr, x: int, y: int, ink: float) -> tuple[int, int, int, int]:
    h, w = mask.shape
    region = dst[y : y + h, x : x + w].astype(np.float32)
    alpha = mask[: region.shape[0], : region.shape[1]].astype(np.float32) / 255.0
    dst[y : y + h, x : x + w] = np.clip(region * (1 - alpha) + ink * alpha, 0, 255).astype(np.uint8)
    return x, y, w, h


def draw_board(
    numbers: Sequence[int],
    style: Style,
    rng: np.random.Generator,
    fonts: Sequence[MaskFont] = (),
    score_at: int = 0,
) -> Board:
    """A clean board, large (digits ~90 px high): `numbers` left to right, the score being
    numbers[score_at] and numbers[score_at + 1]."""
    digit_h = 90
    if style == "led":
        shape: MaskFont = lambda d, h: seven_segment(d, h, rng)  # noqa: E731
    else:
        options: list[MaskFont] = [
            lambda d, h: hershey(d, h, rng),
            lambda d, h: seven_segment(d, h, rng),
            *fonts,
        ]
        weights = np.array([1.0, 0.25] + [2.0] * len(fonts))
        shape = options[int(rng.choice(len(options), p=weights / weights.sum()))]
    pad_y = int(rng.integers(18, 40))
    space = int(rng.integers(12, 36))  # between digits of one number (flip: between cards)
    between = int(rng.integers(40, 110))  # between numbers
    per_digit_cards = style == "flip" and rng.random() < 0.35
    masks = [[shape(int(c), digit_h) for c in str(n)] for n in numbers]
    width = sum(sum(m.shape[1] for m in ms) + space * (len(ms) - 1) + 2 * space for ms in masks)
    width += between * (len(numbers) + 1)
    height = digit_h + 2 * pad_y + 40
    if style == "flip":
        bg = float(rng.uniform(10, 110))
        card, ink = float(rng.uniform(175, 255)), float(rng.uniform(0, 70))
    else:
        bg = float(rng.uniform(0, 45))
        card, ink = float(rng.uniform(0, 60)), float(rng.uniform(140, 255))
    image = np.full((height, width), bg, np.float32)
    image += rng.normal(0, 4, image.shape)
    image = np.clip(image, 0, 255).astype(np.uint8)
    digits: list[tuple[int, int, int, int, int]] = []
    spans: list[tuple[int, int]] = []
    x = between
    y = 20 + pad_y
    for n, ms in zip(numbers, masks, strict=True):
        w = sum(m.shape[1] for m in ms) + space * (len(ms) - 1) + 2 * space
        if style == "flip" and not per_digit_cards:
            cv2.rectangle(image, (x, y - pad_y), (x + w, y + digit_h + pad_y), card, -1)
        elif style == "led":
            cv2.rectangle(image, (x, y - pad_y // 2), (x + w, y + digit_h + pad_y // 2), card, -1)
        dx = x + space
        for c, m in zip(str(n), ms, strict=True):
            if per_digit_cards:
                cv2.rectangle(
                    image,
                    (dx - space // 2, y - pad_y),
                    (dx + m.shape[1] + space // 2, y + digit_h + pad_y),
                    card,
                    -1,
                )
            bx, by, bw, bh = _paste(image, m, dx, y, ink)
            digits.append((bx, by, bw, bh, int(c)))
            dx += m.shape[1] + space
        spans.append((x, x + w))
        x += w + between
    if style == "led":
        glow = cv2.GaussianBlur(image, (0, 0), rng.uniform(1.5, 4.0))
        image = cv2.addWeighted(image, 1.0, glow, rng.uniform(0.0, 0.5), 0)
    s0, s1 = spans[score_at][0], spans[score_at + 1][1]
    score_box = (s0 - space, y - pad_y - 4, s1 - s0 + 2 * space, digit_h + 2 * pad_y + 8)
    a, b = numbers[score_at], numbers[score_at + 1]
    return Board(image, digits, score_box, (a, b), style)


def surroundings(board: Board, rng: np.random.Generator, margin: float = 0.8) -> Board:
    """The board in a hall: floor, table, lines and blobs around it (things that are no digit)."""
    h, w = board.image.shape
    mh, mw = round(h * margin), round(w * margin * 0.5)
    tone = float(rng.uniform(60, 220))
    out = np.full((h + 2 * mh, w + 2 * mw), tone, np.uint8)
    for _ in range(int(rng.integers(2, 8))):
        p0 = (int(rng.integers(0, out.shape[1])), int(rng.integers(0, out.shape[0])))
        p1 = (int(rng.integers(0, out.shape[1])), int(rng.integers(0, out.shape[0])))
        cv2.line(out, p0, p1, float(rng.uniform(0, 255)), int(rng.integers(2, 12)))
    for _ in range(int(rng.integers(0, 4))):
        centre = (int(rng.integers(0, out.shape[1])), int(rng.integers(0, out.shape[0])))
        axes = (int(rng.integers(10, 80)), int(rng.integers(10, 120)))
        cv2.ellipse(
            out, centre, axes, float(rng.uniform(0, 180)), 0, 360, float(rng.uniform(0, 255)), -1
        )
    out[mh : mh + h, mw : mw + w] = board.image
    x, y, bw, bh = board.score_box
    shifted = [(dx + mw, dy + mh, dw, dh, d) for dx, dy, dw, dh, d in board.digits]
    return Board(out, shifted, (x + mw, y + mh, bw, bh), board.numbers, board.style, board.extra)


def degrade(board: Board, rng: np.random.Generator, digit_px: tuple[int, int] = (14, 40)) -> Board:
    """Seen by a hall camera: sheared and tilted, a few pixels per digit, blurred, noisy,
    compressed. The digit and score boxes follow."""
    img = board.image
    h, w = img.shape
    shear = rng.uniform(-0.35, 0.35)
    angle = np.deg2rad(rng.uniform(-7, 7))
    stretch = rng.uniform(0.75, 1.15)  # seen from the side: narrower
    a = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]]) @ np.array(
        [[stretch, shear], [0.0, 1.0]]
    )
    target_h = rng.uniform(*digit_px)
    scale = target_h / 90.0
    a = a * scale
    corners = np.array([[0, 0], [w, 0], [0, h], [w, h]], np.float64) @ a.T
    offset = -corners.min(axis=0) + 2
    out_w = int(np.ceil(corners[:, 0].max() - corners[:, 0].min())) + 4
    out_h = int(np.ceil(corners[:, 1].max() - corners[:, 1].min())) + 4
    m = np.hstack([a, offset[:, None]]).astype(np.float32)
    small = cv2.warpAffine(
        img, m, (out_w, out_h), flags=cv2.INTER_AREA, borderMode=cv2.BORDER_REPLICATE
    )
    sigma = rng.uniform(0.0, 1.0)
    if sigma > 0.2:
        small = cv2.GaussianBlur(small, (0, 0), sigma)
    noisy = small.astype(np.float32) + rng.normal(0, rng.uniform(0, 9), small.shape)
    small = np.clip(noisy, 0, 255).astype(np.uint8)
    quality = int(rng.integers(35, 95))
    ok, data = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, quality])
    decoded = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE) if ok else None
    if decoded is not None:
        small = decoded

    def move(box: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        x, y, bw, bh = box
        pts = (
            np.array([[x, y], [x + bw, y], [x, y + bh], [x + bw, y + bh]], np.float64) @ a.T
            + offset
        )
        x0, y0 = pts.min(axis=0)
        x1, y1 = pts.max(axis=0)
        return round(x0), round(y0), round(x1 - x0), round(y1 - y0)

    digits = [(*move((x, y, bw, bh)), d) for x, y, bw, bh, d in board.digits]
    return Board(small, digits, move(board.score_box), board.numbers, board.style, {"scale": scale})


def occlude(board: Board, rng: np.random.Generator, share: float = 0.2) -> Board:
    """Something in front of part of the board (a hand, a head)."""
    img = board.image.copy()
    h, w = img.shape
    rw, rh = round(w * rng.uniform(0.05, share)), round(h * rng.uniform(0.2, 1.0))
    x, y = int(rng.integers(0, max(1, w - rw))), int(rng.integers(0, max(1, h - rh)))
    cv2.rectangle(img, (x, y), (x + rw, y + rh), float(rng.uniform(0, 255)), -1)
    return Board(img, board.digits, board.score_box, board.numbers, board.style, board.extra)


def score_crop(
    board: Board, rng: np.random.Generator, loose: float = 0.25, cut: float = 0.0
) -> tuple[Arr, tuple[int, int], tuple[bool, bool]]:
    """The box a coach would draw around the score, a little loose; its top-left corner; and
    whether a side is the edge of the picture. With `cut`, that often the picture's edge cuts
    into the first or last digit (a board at the edge of the frame)."""
    x, y, w, h = board.score_box
    H, W = board.image.shape
    mx, my = round(w * rng.uniform(0, loose / 2)), round(h * rng.uniform(0, loose))
    x0, y0 = max(0, x - mx), max(0, y - my)
    x1, y1 = min(W, x + w + mx), min(H, y + h + my)
    inside = sorted((d for d in board.digits if x <= d[0] + d[2] / 2 <= x + w), key=lambda d: d[0])
    open_left = open_right = False
    if inside and rng.random() < cut:
        first = inside[0]
        x0, open_left = first[0] + round(first[2] * rng.uniform(0.0, 0.35)), True
    elif inside and rng.random() < cut / 2:
        last = inside[-1]
        x1, open_right = last[0] + last[2] - round(last[2] * rng.uniform(0.0, 0.35)), True
    return board.image[y0:y1, x0:x1], (x0, y0), (open_left, open_right)


def random_board(
    rng: np.random.Generator, fonts: Sequence[MaskFont] = (), style: Style | None = None
) -> Board:
    """A board of a random style: the score (0..30 a side), maybe the set counts beside it."""
    style = style or ("flip" if rng.random() < 0.6 else "led")
    a, b = int(rng.integers(0, 31)), int(rng.integers(0, 31))
    if rng.random() < 0.4:
        numbers: list[int] = [int(rng.integers(0, 3)), a, b, int(rng.integers(0, 3))]
        score_at = 1
    else:
        numbers, score_at = [a, b], 0
    return draw_board(numbers, style, rng, fonts, score_at)


def training_samples(
    rng: np.random.Generator, count: int, fonts: Sequence[MaskFont] = ()
) -> tuple[Arr, Arr]:
    """Glyphs as the reader cuts them out of degraded boards: (n, GLYPH_H * GLYPH_W) inputs,
    labels 0-9, NOT_DIGIT (10) for components that are no digit (card edges, clutter, both
    polarities)."""
    xs: list[Arr] = []
    ys: list[int] = []
    while len(ys) < count:
        board = surroundings(random_board(rng, fonts), rng)
        board = degrade(board, rng)
        if rng.random() < 0.25:
            board = occlude(board, rng)
        crop, (cx, cy), (open_left, open_right) = score_crop(board, rng, loose=0.6, cut=0.25)
        work = working(crop)
        fy = work.shape[0] / max(1, crop.shape[0])
        fx = work.shape[1] / max(1, crop.shape[1])
        truth = [
            ((x - cx) * fx, (y - cy) * fy, w * fx, h * fy, d) for x, y, w, h, d in board.digits
        ]
        for dark in (True, False):
            img = bright_digits(work, dark)
            right = dark == (board.style == "flip")
            for part in components(foreground(img), open_left, open_right):
                label = NOT_DIGIT
                if right:
                    label = _match(part.box, truth)
                elif rng.random() > 0.3:
                    continue  # fewer negatives from the wrong polarity
                xs.append(glyph_input(img, part))
                ys.append(label)
    return np.stack(xs[:count]), np.array(ys[:count], np.int64)


def _match(
    box: tuple[int, int, int, int], truth: list[tuple[float, float, float, float, int]]
) -> int:
    """The digit a component is: most of it inside that digit's box and most of the digit's
    height (an IoU would fail thin glyphs: a sheared "1" has a wide box around a thin stroke)."""
    x, y, w, h = box
    for tx, ty, tw, th, d in truth:
        ix = max(0.0, min(x + w, tx + tw) - max(x, tx))
        iy = max(0.0, min(y + h, ty + th) - max(y, ty))
        if ix * iy >= 0.7 * w * h and iy >= 0.6 * th and h <= 1.3 * th:
            return d
    return NOT_DIGIT
