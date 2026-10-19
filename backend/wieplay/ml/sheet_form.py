"""The paper Matchblatt's layout, in millimetres (PLAN §10.3a, SS4; research note §1.2, §5.3).

Swiss Volley's official scoresheet is the FIVB-layout A3 form in three languages: sets 1-4 in a
2 x 2 grid of set blocks, the deciding set (5) as a wider block below, then sanctions, remarks,
the officials' approval, the result table and the team lists. Every number in this module was
measured on a clean scan of a filled sample sheet (the SVRZ's, 2025) and checked against two
phone photos of real sheets; no picture of the form is kept anywhere. Coordinates are page
millimetres (A3 landscape, 420 x 297, origin top left); a photo is matched to them by a
homography (wieplay/ml/sheet_register.py).

A set block (sets 1-4), left to right: the "SATZ n" column, the left team's half, its running
score ("Punkte": 1-48 in four columns, the timeouts "T" below), the right team's half, its
running score. Each half, top to bottom: a header (start or end time, team name, the A/B circle,
the S/R circles), the column titles I-VI, the starting lineup, the substitute's number, two
score rows (the substitution, the return), and four rows of service boxes: each position column
is split in two, boxes 1-4 on the left, 5-8 on the right (printed small numbers). Team A is on
the scorer's left in sets 1 and 3, B in sets 2 and 4 (rule 2.3 a). The deciding set has six
boxes per position (three rows), and a third half where the left team goes on after the change
of sides.

Plain Python (the API reads cell names); drawing and reading are in sheet_register.py and
sheet_cells.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PAGE_W, PAGE_H = 420.0, 297.0

Half = Literal["L", "R", "C"]  # left, right, and the deciding set's part after the change
Kind = Literal["box", "lineup", "sub_in", "score", "time", "mark"]

# Rows of a set block of sets 1-4, from its top: header, I-VI, lineup, substitute, two score
# rows, four service rows (mm, measured).
ROWS = (0.0, 7.33, 12.67, 17.5, 22.29, 27.1, 31.91, 36.58, 41.25, 45.87, 50.45)
# The deciding set: three service rows.
ROWS_5 = (0.0, 7.29, 12.64, 17.19, 21.7, 26.24, 30.73, 35.23, 39.69, 44.09)
SERVICE_ROW = 6  # index of the first service row in ROWS


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float

    @property
    def x1(self) -> float:
        return self.x + self.w

    @property
    def y1(self) -> float:
        return self.y + self.h

    def grow(self, left: float, top: float, right: float, bottom: float) -> Rect:
        """Grown by fractions of its own width and height."""
        return Rect(
            self.x - left * self.w,
            self.y - top * self.h,
            self.w * (1 + left + right),
            self.h * (1 + top + bottom),
        )

    def corners(self) -> list[tuple[float, float]]:
        return [(self.x, self.y), (self.x1, self.y), (self.x1, self.y1), (self.x, self.y1)]


@dataclass(frozen=True)
class HalfGeom:
    """One team's half of a set block: its left edge, its width (six position columns of two
    sub-columns each), its running-score column after it."""

    half: Half
    x: float
    w: float
    punkte_w: float
    punkte_cols: int  # 4 in sets 1-4 (1-48), 3 or 1 in the deciding set

    @property
    def sub_w(self) -> float:
        return self.w / 12

    def column(self, position: int) -> tuple[float, float]:
        """x and width of a position's column (1-6)."""
        return self.x + (position - 1) * 2 * self.sub_w, 2 * self.sub_w

    @property
    def punkte_x(self) -> float:
        return self.x + self.w


@dataclass(frozen=True)
class BlockGeom:
    """A set block: where it is and its halves."""

    set_no: int
    x: float
    y: float
    w: float
    rows: tuple[float, ...]
    halves: tuple[HalfGeom, ...]
    satz_w: float

    @property
    def h(self) -> float:
        return self.rows[-1]

    @property
    def rect(self) -> Rect:
        return Rect(self.x, self.y, self.w, self.h)

    @property
    def deciding(self) -> bool:
        return self.set_no == 5

    @property
    def service_rows(self) -> int:
        return len(self.rows) - 1 - SERVICE_ROW

    def row(self, k: int) -> tuple[float, float]:
        """y and height of row k (0 = header)."""
        return self.y + self.rows[k], self.rows[k + 1] - self.rows[k]

    def half(self, half: Half) -> HalfGeom:
        return next(h for h in self.halves if h.half == half)


def _block(set_no: int, x: float, y: float, satz: float, parts: list[float]) -> BlockGeom:
    """parts: half, punkte, half, punkte widths (sets 1-4)."""
    hl = HalfGeom("L", x + satz, parts[0], parts[1], 4)
    hr = HalfGeom("R", hl.punkte_x + parts[1], parts[2], parts[3], 4)
    return BlockGeom(set_no, x, y, satz + sum(parts), ROWS, (hl, hr), satz)


# Sets 1 and 3 have the label panel on their left; 2 and 4 a slightly wider SATZ column.
_LEFT = (7.35, [61.16, 16.3, 61.32, 16.44])
_RIGHT = (8.13, [61.08, 16.33, 60.87, 16.32])
_X_LEFT, _X_RIGHT = 73.8, 238.47
_Y_TOP, _Y_MID, _Y_FIVE = 46.89, 99.62, 152.02


def _deciding() -> BlockGeom:
    x = 73.99
    hl = HalfGeom("L", x + 7.42, 61.06, 9.69, 1)
    hr = HalfGeom("R", hl.punkte_x + 9.69, 61.22, 9.74, 3)
    change_w = 2.44  # the "Seitenwechsel" label between part 2 and part 3
    hc = HalfGeom("C", hr.punkte_x + 9.74 + change_w, 60.83, 10.58, 3)
    return BlockGeom(5, x, _Y_FIVE, hc.punkte_x + 10.58 - x, ROWS_5, (hl, hr, hc), 7.42)


BLOCKS: dict[int, BlockGeom] = {
    1: _block(1, _X_LEFT, _Y_TOP, *_LEFT),
    2: _block(2, _X_RIGHT, _Y_TOP, *_RIGHT),
    3: _block(3, _X_LEFT, _Y_MID, *_LEFT),
    4: _block(4, _X_RIGHT, _Y_MID, *_RIGHT),
    5: _deciding(),
}

# Other printed boxes (outer frames and their main rules), for the registration only.
FRAMES: list[Rect] = [
    # The label panels left of sets 1, 3 and 5.
    Rect(9.86, 53.81, 63.84, 43.35),
    Rect(9.86, 53.81 + 52.73, 63.84, 43.35),
    Rect(10.21, 158.62, 63.78, 39.28),
    # Sanctions, remarks, approval, final result, team lists.
    Rect(10.4, 198.04, 73.05, 92.9),
    Rect(85.98, 198.23, 137.62, 28.96),
    Rect(85.98, 229.4, 139.74, 61.5),
    Rect(225.72, 198.49, 71.56, 92.4),
    Rect(296.2, 152.4, 112.08, 138.5),
]


# --- the cells ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    """Something written on the form that is read: a service box, a lineup number, a
    substitute's number, half of a score ("12" of "12:15"), half of a time, a mark.

    `key` names it on the sheet: s<set>.<half>.<what>[.<position>][.<box or part>], e.g.
    s1.L.box.2.1 (set 1, left half, position II, box 1), s2.R.to.1.0 (set 2, right half, first
    timeout, the requesting team's score), s1.L.sub_at.6.1, s3.L.lineup.4, s1.L.start.h.
    `rect` is the printed cell; `window` what the reader looks at (handwriting overflows)."""

    key: str
    kind: Kind
    set_no: int
    half: Half
    rect: Rect
    window: Rect
    position: int | None = None
    box: int | None = None
    part: int | None = None


# What the reader sees around each kind of cell (fractions of the cell): scores spill over the
# box lines, mostly to the right and below.
BOX_GROW = (0.35, 0.28, 0.35, 0.28)
COLUMN_GROW = (0.08, 0.32, 0.08, 0.32)
SCORE_GROW = (0.25, 0.28, 0.25, 0.28)
TIME_GROW = (0.05, 0.05, 0.05, 0.05)

# The header's fields, from the half's left edge (mm): left half "Beginn" time, right half
# "Ende" time; the S and R circles (top and bottom of the header) next to the A/B circle.
START_FIELD = (8.4, 22.0)
END_FIELD = (41.0, 60.8)
SR_LEFT = (56.8, 60.8)
SR_RIGHT = (35.0, 39.8)


def _rect(x: float, y: float, w: float, h: float) -> Rect:
    return Rect(round(x, 3), round(y, 3), round(w, 3), round(h, 3))


def service_rect(b: BlockGeom, h: HalfGeom, position: int, box: int) -> Rect:
    rows = b.service_rows
    col = 0 if box <= rows else 1
    row = (box - 1) % rows
    x = h.x + ((position - 1) * 2 + col) * h.sub_w
    y, height = b.row(SERVICE_ROW + row)
    return _rect(x, y, h.sub_w, height)


def block_cells(b: BlockGeom) -> list[Cell]:
    out: list[Cell] = []
    boxes = 2 * b.service_rows
    for h in b.halves:
        pre = f"s{b.set_no}.{h.half}"
        for position in range(1, 7):
            cx, cw = h.column(position)
            y, height = b.row(2)
            lineup = _rect(cx, y, cw, height)
            out.append(
                Cell(
                    f"{pre}.lineup.{position}",
                    "lineup",
                    b.set_no,
                    h.half,
                    lineup,
                    lineup.grow(*COLUMN_GROW),
                    position,
                )
            )
            y, height = b.row(3)
            sub = _rect(cx, y, cw, height)
            out.append(
                Cell(
                    f"{pre}.sub_in.{position}",
                    "sub_in",
                    b.set_no,
                    h.half,
                    sub,
                    sub.grow(*COLUMN_GROW),
                    position,
                )
            )
            for row, name in ((4, "sub_at"), (5, "sub_back")):
                y, height = b.row(row)
                for part in (0, 1):
                    r = _rect(cx + part * h.sub_w, y, h.sub_w, height)
                    out.append(
                        Cell(
                            f"{pre}.{name}.{position}.{part}",
                            "score",
                            b.set_no,
                            h.half,
                            r,
                            r.grow(*SCORE_GROW),
                            position,
                            None,
                            part,
                        )
                    )
            for box in range(1, boxes + 1):
                r = service_rect(b, h, position, box)
                out.append(
                    Cell(
                        f"{pre}.box.{position}.{box}",
                        "box",
                        b.set_no,
                        h.half,
                        r,
                        r.grow(*BOX_GROW),
                        position,
                        box,
                    )
                )
        # Timeouts: the two rows under "T" in the running-score column (its last two rows).
        n = len(b.rows) - 1
        for k, row in enumerate((n - 2, n - 1), start=1):
            y, height = b.row(row)
            for part in (0, 1):
                r = _rect(h.punkte_x + part * h.punkte_w / 2, y, h.punkte_w / 2, height)
                out.append(
                    Cell(
                        f"{pre}.to.{k}.{part}",
                        "score",
                        b.set_no,
                        h.half,
                        r,
                        r.grow(*SCORE_GROW),
                        None,
                        k,
                        part,
                    )
                )
        y, height = b.row(0)
        if h.half != "C":
            lo, hi = START_FIELD if h.half == "L" else END_FIELD
            name = "start" if h.half == "L" else "end"
            mid = (lo + hi) / 2
            for part, (a, z) in enumerate(((lo, mid + 0.6), (mid - 0.6, hi))):
                r = _rect(h.x + a, y + 0.3, z - a, height - 0.6)
                out.append(
                    Cell(
                        f"{pre}.{name}.{'hm'[part]}",
                        "time",
                        b.set_no,
                        h.half,
                        r,
                        r.grow(*TIME_GROW),
                        None,
                        None,
                        part,
                    )
                )
            sx0, sx1 = SR_LEFT if h.half == "L" else SR_RIGHT
            for part, name in enumerate(("s", "r")):
                r = _rect(h.x + sx0, y + part * height / 2, sx1 - sx0, height / 2)
                out.append(Cell(f"{pre}.{name}", "mark", b.set_no, h.half, r, r, None, None, part))
    return out


CELLS: list[Cell] = [c for b in BLOCKS.values() for c in block_cells(b)]
BY_KEY: dict[str, Cell] = {c.key: c for c in CELLS}


def cells_of(set_no: int) -> list[Cell]:
    return [c for c in CELLS if c.set_no == set_no]


# --- the printed lines, for the registration ------------------------------------------------------


Segment = tuple[float, float, float, float]  # x0, y0, x1, y1 (horizontal or vertical)


def block_lines(b: BlockGeom) -> list[Segment]:
    """The rules printed in a set block: its frame, every row, the position columns and their
    split in the service rows, the running-score column."""
    out: list[Segment] = []
    x0, x1 = b.x, b.x + b.w
    for k, dy in enumerate(b.rows):
        y = b.y + dy
        if k in (0, len(b.rows) - 1):
            out.append((x0, y, x1, y))
            continue
        for h in b.halves:
            out.append((h.x, y, h.x + h.w, y))
    y_top, y_bot = b.y, b.y + b.h
    out.append((x0, y_top, x0, y_bot))
    out.append((x1, y_top, x1, y_bot))
    y_cols = b.y + b.rows[1]
    y_split = b.y + b.rows[SERVICE_ROW]
    for h in b.halves:
        out.append((h.x, y_top, h.x, y_bot))
        out.append((h.punkte_x, y_top, h.punkte_x, y_bot))
        for position in range(2, 7):
            cx, _ = h.column(position)
            out.append((cx, y_cols, cx, y_bot))
        for position in range(1, 7):
            cx, _ = h.column(position)
            out.append((cx + h.sub_w, y_split, cx + h.sub_w, y_bot))
        # The running score's columns down to the "T" label, then the two timeout rows.
        n = len(b.rows)
        y_t = b.y + b.rows[n - 4]
        for k in range(1, h.punkte_cols):
            px = h.punkte_x + k * h.punkte_w / h.punkte_cols
            out.append((px, y_cols, px, y_t))
        for k in (n - 4, n - 3, n - 2):
            y = b.y + b.rows[k]
            out.append((h.punkte_x, y, h.punkte_x + h.punkte_w, y))
    return out


def frame_lines(r: Rect) -> list[Segment]:
    return [
        (r.x, r.y, r.x1, r.y),
        (r.x, r.y1, r.x1, r.y1),
        (r.x, r.y, r.x, r.y1),
        (r.x1, r.y, r.x1, r.y1),
    ]


def lines(set_no: int | None = None) -> list[Segment]:
    """Every rule of the form (or of one set block)."""
    if set_no is not None:
        return block_lines(BLOCKS[set_no])
    out = [s for b in BLOCKS.values() for s in block_lines(b)]
    for r in FRAMES:
        out += frame_lines(r)
    return out


def extent(set_no: int | None = None) -> Rect:
    """The part of the page that holds the lines (the whole form, or one set block)."""
    if set_no is not None:
        return BLOCKS[set_no].rect
    return Rect(9.86, 46.89, 408.28 - 9.86, 290.94 - 46.89)


def left_letter(set_no: int) -> Literal["A", "B"] | None:
    """The team on the scorer's left: A in sets 1 and 3, B in 2 and 4; the deciding set's
    left team is drawn anew (None)."""
    if set_no == 5:
        return None
    return "A" if set_no % 2 else "B"
