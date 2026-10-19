"""From a photo's readings to a draft sheet (PLAN §10.3a, SS4; research note §3.6, §5.3 step 4).

The reader (wieplay/ml/sheet_cells.py, on the worker) gives every cell of the photo its likeliest
values with probabilities. This module, plain Python in the API, picks the cheapest sheet the
rules allow (cost = -log p of every value taken):

- **Which half is us.** Team A writes on the scorer's left in sets 1 and 3, B in 2 and 4 (rule
  2.3 a), so one choice ("we are A" or "B") orients sets 1-4; the deciding set is oriented on
  its own. The official set scores decide it almost always; without them, our roster's numbers
  in the lineups.
- **The service boxes** (research §3.2): a dynamic programme over the turns in the sheet's order
  with the score as its state, for either first server: every box at least the team's score,
  the set not over before the last box, ending on a set end (the official score when known),
  every box after the end blank. The receiving team's X in position I box 1 and the crosses in
  the S and R circles weigh in on who served first. A box the reader did not list a fitting
  value for can be filled in, dearly; more than a few of those and the set is not decoded
  (its readings are shown as they are). Max-marginals (the cheapest sheet with a box read
  otherwise) say how sure each box is.
- **Timeouts and substitutions**: the reading whose score lies on the decoded path.
- **Lineups, times**: their likeliest reading, our roster's numbers preferred in our lineup.

A cell is "to check" when the rules took another value than the reader's first (why: "rules"),
when another value is almost as cheap (why: "unsure"), or when nothing fits (why: "off_path",
"not_decoded"). Cells a person typed are kept and never proposed over (`fixed`). Nothing here is
stored or scored: the API writes the result as a draft the coach confirms.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

from wieplay.ml import sheet as sh

Side = sh.Side
Half = Literal["L", "R", "C"]
Value = int | str | None  # a number, "X", or None (blank)
Pos = tuple[int, int]  # (position 1-6, box)

P_FLOOR = 1e-4  # a value the reader did not list
COST_FORCED = -math.log(P_FLOOR)  # ≈ 9.2: a box the rules had to fill in
FORCED_SPAN = 6  # values tried beyond the reader's list: the team's score .. score + 6
MAX_FORCED = 2  # more boxes than this filled in by the rules: not decoded, shown as read
CHECK_MARGIN = 2.3  # another reading within e^2.3 ≈ 10x as likely: check it
SURE = 0.9  # a lineup number, substitution or time read at least this surely needs no check
AFTER_END_SURE = 0.5  # a mark after the set's last box, read less surely: no check
KNOWN_SURE = 0.5  # ... a number known from the roster or read surely elsewhere on the sheet
POOL_SURE = 0.95  # a number read this surely joins its team's numbers
ROSTER_COST = 1.2  # our lineup with a number not on our roster
FINAL_MISSING_COST = 2.5  # the winner's final not written (won on reception)
OFF_PATH_COST = 6.0  # a timeout or substitution whose score the set never had
MIRROR_COST = 1.5  # ... written the other way round
MARK_SLOPE = 12.0  # S/R crosses: ink difference → log-odds
PRUNE = 30.0


@dataclass(frozen=True)
class Cell:
    """A cell's reading: its likeliest values with probabilities, where it is in a photo; a
    value made impossible (`banned`) for the max-marginals."""

    alts: tuple[tuple[Value, float], ...]
    image: str | None = None
    quad: tuple[float, ...] | None = None
    banned: tuple[Value, ...] = ()

    def p(self, value: Value) -> float:
        if value in self.banned:
            return 0.0
        for v, p in self.alts:
            if v == value:
                return max(p, P_FLOOR)
        return P_FLOOR

    def cost(self, value: Value) -> float:
        p = self.p(value)
        return math.inf if p <= 0 else -math.log(p)

    @property
    def best(self) -> Value:
        return self.alts[0][0] if self.alts else None

    @property
    def p_blank(self) -> float:
        return self.p(None)

    @property
    def p_best(self) -> float:
        return self.alts[0][1] if self.alts else 0.0

    def top(self, k: int = 3) -> list[list[Any]]:
        return [[v, round(p, 4)] for v, p in self.alts[:k]]


BLANK_CELL = Cell(((None, 0.98),))


@dataclass
class Readings:
    """Every cell read on a sheet's photos by key (wieplay/ml/sheet_form.py), and the marks."""

    cells: dict[str, Cell] = field(default_factory=dict)
    marks: dict[str, float] = field(default_factory=dict)

    def get(self, key: str) -> Cell:
        return self.cells.get(key, BLANK_CELL)

    def has(self, key: str) -> bool:
        return key in self.cells

    def sets(self) -> list[int]:
        return sorted({int(k.split(".")[0][1:]) for k in self.cells})

    @classmethod
    def from_photos(cls, photos: Sequence[tuple[str, Mapping[str, Any]]]) -> Readings:
        """Merge the readings of several photos (id, the worker's per-photo result): per cell
        the one read most surely."""
        out = cls()
        for image_id, doc in photos:
            for key, c in (doc.get("cells") or {}).items():
                alts = tuple((_value(v), float(p)) for v, p in c.get("alts", []))
                q = c.get("quad")
                cell = Cell(alts, image_id, tuple(float(v) for v in q) if q else None)
                have = out.cells.get(key)
                if have is None or (alts and have.alts and alts[0][1] > have.alts[0][1]):
                    out.cells[key] = cell
            for key, ink in (doc.get("marks") or {}).items():
                out.marks[key] = max(out.marks.get(key, 0.0), float(ink))
        return out


def _value(v: Any) -> Value:
    if v is None or v == "":
        return None
    if v == "X":
        return "X"
    return int(v)


# --- the service boxes ----------------------------------------------------------------------------


@dataclass
class BoxSolution:
    first: Side
    values: dict[Side, list[int]]  # each team's boxes in turn order
    cost: float
    forced: int  # boxes the rules filled in with a value the reader did not list
    final: tuple[int, int]  # (us, them)
    final_missing: bool
    margins: dict[tuple[Side, int], float] = field(default_factory=dict)
    alternative: dict[tuple[Side, int], Value] = field(default_factory=dict)


Boxes = Mapping[Side, Mapping[Pos, Cell]]
Fixed = Mapping[Side, Mapping[Pos, Value]]


def solve_boxes(
    box: Boxes,
    first: Side,
    deciding: bool,
    official: tuple[int, int] | None,
    fixed: Fixed | None = None,
    margins: bool = True,
) -> BoxSolution | None:
    """The cheapest consistent service boxes when `first` served first; None when none exist.
    `official` is (us, them); `fixed`: boxes a person typed, kept as they are."""
    target = sh.TARGET_DECIDING if deciding else sh.TARGET
    second = sh.other(first)
    sides: tuple[Side, Side] = (first, second)
    cl = {s: sh.cells(s == first, deciding) for s in sides}
    fx = fixed or {}

    def cell(side: Side, i: int) -> Cell:
        return box.get(side, {}).get(cl[side][i], BLANK_CELL)

    def options(side: Side, i: int, score: int, over: bool) -> list[tuple[int, float, bool]]:
        pos = cl[side][i]
        if pos in fx.get(side, {}):
            v = fx[side][pos]
            return [(v, 0.0, False)] if isinstance(v, int) and v >= score else []
        c = cell(side, i)
        out: dict[int, tuple[float, bool]] = {}
        for v, _ in c.alts:
            if isinstance(v, int) and v >= score and v not in c.banned:
                out[v] = (c.cost(v), False)
        for v in range(score, score + (1 if over else FORCED_SPAN + 1)):
            if v not in out and v not in c.banned:
                out[v] = (COST_FORCED, True)
        return [(v, cost, forced) for v, (cost, forced) in out.items()]

    def blank_cost(side: Side, i: int) -> float:
        pos = cl[side][i]
        if pos in fx.get(side, {}):
            return 0.0 if fx[side][pos] is None else math.inf
        return cell(side, i).cost(None)

    suffix: dict[Side, list[float]] = {}
    for s in sides:
        acc = [0.0] * (len(cl[s]) + 1)
        for i in range(len(cl[s]) - 1, -1, -1):
            acc[i] = acc[i + 1] + blank_cost(s, i)
        suffix[s] = acc
    goal: tuple[int, int] | None = None  # (first, second)
    if official is not None:
        goal = (official[0], official[1]) if first == "us" else (official[1], official[0])

    n_turns = len(cl[first]) + len(cl[second])
    # alpha[j][(score first, score second)] = (cost, forced, back)
    alpha: list[dict[tuple[int, int], tuple[float, int, Any]]] = [{} for _ in range(n_turns + 1)]
    alpha[0][(0, 0)] = (0.0, 0, None)
    best: tuple[float, int, int, tuple[int, int], int, bool] | None = None
    for j in range(n_turns):
        serving = sides[j % 2]
        i = j // 2
        layer = alpha[j]
        if i >= len(cl[serving]) or not layer:
            continue
        floor = min(v[0] for v in layer.values())
        limit = min(floor + PRUNE, (best[0] if best else math.inf) + PRUNE / 2)
        if floor > limit:
            break
        k_other = (j + 1) // 2  # the other team's next box index
        rest = (
            suffix[serving][min(i + 1, len(cl[serving]))]
            + suffix[sh.other(serving)][min(k_other, len(cl[sh.other(serving)]))]
        )
        for state, (cost, forced, _) in layer.items():
            if cost > limit:
                continue
            a, b = state
            own, opp = (a, b) if serving == first else (b, a)
            over_now = sh.is_over(own, opp, target)
            for v, vc, vf in options(serving, i, own, over_now):
                if v > own and sh.is_over(v - 1, opp, target):
                    continue  # the set ended before this box's last point
                total = cost + vc
                nf = forced + int(vf)
                fa, fb = (v, b) if serving == first else (a, v)
                if sh.is_over(v, opp, target):
                    if goal is None or (fa, fb) == goal:
                        end = (total + rest, nf, j, state, v, False)
                        if best is None or end[0] < best[0]:
                            best = end
                    continue
                if sh.is_over(v, opp + 1, target):
                    # Won on reception: the winner's final would be its next box; when it is
                    # not written, the set still ends here (a warning, research §3.2).
                    fin = (fa, fb + 1) if serving == first else (fa + 1, fb)
                    if goal is None or fin == goal:
                        end = (total + rest + FINAL_MISSING_COST, nf, j, state, v, True)
                        if best is None or end[0] < best[0]:
                            best = end
                nxt = (fa, fb + 1) if serving == first else (fa + 1, fb)
                have = alpha[j + 1].get(nxt)
                if have is None or total < have[0]:
                    alpha[j + 1][nxt] = (total, nf, (state, v))
    if best is None:
        return None
    cost, forced, j_end, state, v_end, missing = best
    seq = [v_end]
    j, st = j_end, state
    while j > 0:
        _, _, (prev, v) = alpha[j][st]
        seq.append(v)
        st, j = prev, j - 1
    seq.reverse()
    values: dict[Side, list[int]] = {first: [], second: []}
    for k, v in enumerate(seq):
        values[sides[k % 2]].append(v)
    a, b = state
    serving = sides[j_end % 2]
    fa, fb = (v_end, b) if serving == first else (a, v_end)
    if missing:
        fa, fb = (fa, fb + 1) if serving == first else (fa + 1, fb)
    final = (fa, fb) if first == "us" else (fb, fa)
    sol = BoxSolution(first, values, cost, forced, final, missing)
    if margins:
        _margins(sol, box, cl, fx, deciding, official)
    return sol


def _margins(
    sol: BoxSolution,
    box: Boxes,
    cl: Mapping[Side, list[Pos]],
    fixed: Fixed,
    deciding: bool,
    official: tuple[int, int] | None,
) -> None:
    """How sure each box is: the cost of the cheapest consistent sheet with that box read
    otherwise, minus the best (exact: solved again without the value). A box the reader is
    sure of, far ahead of its runner-up, is not searched."""
    for side in (sol.first, sh.other(sol.first)):
        n = len(sol.values[side])
        for i in range(min(len(cl[side]), n + 2)):
            pos = cl[side][i]
            if pos in fixed.get(side, {}):
                continue
            c = box.get(side, {}).get(pos, BLANK_CELL)
            chosen: Value = sol.values[side][i] if i < n else None
            runner = max((p for v, p in c.alts if v != chosen), default=P_FLOOR)
            if (
                c.p(chosen) >= 0.98
                and math.log(c.p(chosen) / max(runner, P_FLOOR)) > 2.5 * CHECK_MARGIN
            ):
                sol.margins[(side, i)] = 99.0
                continue
            alt_box = {s: dict(box.get(s, {})) for s in (sol.first, sh.other(sol.first))}
            alt_box[side][pos] = replace(c, banned=(chosen,))
            other = solve_boxes(alt_box, sol.first, deciding, official, fixed, margins=False)
            if other is None:
                sol.margins[(side, i)] = 99.0
                continue
            sol.margins[(side, i)] = other.cost - sol.cost
            vals = other.values[side]
            sol.alternative[(side, i)] = vals[i] if i < len(vals) else None


# --- a set ----------------------------------------------------------------------------------------


@dataclass
class Proposal:
    """One field of the form as read: its value, the reader's top readings, whether to check
    it and why, where it is (photo, quad)."""

    value: Any
    alts: list[list[Any]]
    check: bool
    why: str | None = None
    image: str | None = None
    quad: tuple[float, ...] | None = None

    def as_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"v": self.value, "alts": self.alts, "check": self.check}
        if self.why:
            out["why"] = self.why
        if self.image:
            out["img"] = self.image
        if self.quad:
            out["quad"] = [round(q, 5) for q in self.quad]
        return out


@dataclass
class SetRead:
    no: int
    us_half: Half
    doc: dict[str, Any]  # the proposed set (SetSheetIn's shape)
    cells: dict[str, Proposal]  # field key → proposal (box.us.2.1, lineup.them.3, to.us.1, ...)
    cost: float
    decoded: bool  # the boxes decode to a consistent set
    note: str | None = None

    @property
    def to_check(self) -> int:
        return sum(1 for p in self.cells.values() if p.check)

    def as_json(self) -> dict[str, Any]:
        return {
            "no": self.no,
            "us_half": self.us_half,
            "decoded": self.decoded,
            "note": self.note,
            "cost": round(self.cost, 2),
            "to_check": self.to_check,
            "cells": {k: p.as_json() for k, p in self.cells.items()},
        }


def _key(no: int, half: Half, *parts: object) -> str:
    return ".".join([f"s{no}", half, *[str(p) for p in parts]])


def halves_of(us_half: Half) -> dict[Side, Half]:
    """Which half of the set block each team writes on."""
    return {"us": us_half, "them": "R" if us_half == "L" else "L"}


def _halves(no: int, us_half: Half) -> dict[Side, Half]:
    return {"us": us_half, "them": "R" if us_half == "L" else "L"}


def box_cells(r: Readings, no: int, half: Half, deciding: bool, cont: bool) -> dict[Pos, Cell]:
    """A team's boxes as read; in the deciding set the left team goes on in part 3 (C): a box
    filled there is read from there."""
    out: dict[Pos, Cell] = {}
    rows = 3 if deciding else 4
    for p in range(1, 7):
        for b in range(1, 2 * rows + 1):
            c = r.get(_key(no, half, "box", p, b))
            if cont:
                c3 = r.get(_key(no, "C", "box", p, b))
                if r.has(_key(no, "C", "box", p, b)) and c3.p_blank < 0.5:
                    c = c3
            out[(p, b)] = c
    return out


def first_cost(
    r: Readings,
    no: int,
    halves: Mapping[Side, Half],
    first: Side,
    box: Mapping[Side, Mapping[Pos, Cell]],
) -> float:
    """What the marks say about who served first: the receiving team's X in its position I
    box 1, and the crosses in the S/R circles of both halves."""
    receiving = sh.other(first)
    cost = box[receiving].get((1, 1), BLANK_CELL).cost("X")
    m = 0.0
    seen = False
    for side, half in halves.items():
        s_key, r_key = _key(no, half, "s"), _key(no, half, "r")
        if s_key in r.marks and r_key in r.marks:
            seen = True
            d = r.marks[s_key] - r.marks[r_key]
            m += d if side == first else -d
    if seen:
        cost += math.log1p(math.exp(-MARK_SLOPE * m))
    return cost


def _scores_on(points: Sequence[tuple[int, int]]) -> set[tuple[int, int]]:
    return set(points)


def _pair(
    r: Readings,
    a: str,
    b: str,
    side: Side,
    path: set[tuple[int, int]],
) -> tuple[tuple[int, int] | None, float, float, Proposal | None]:
    """A score written as two cells (own, other): the likeliest pair whose score the set went
    through, or nothing. Returns (pair or None, cost, cost of the runner-up, proposal)."""
    ca, cb = r.get(a), r.get(b)
    blank = ca.cost(None) + cb.cost(None)
    options: list[tuple[float, tuple[int, int] | None, bool]] = [(blank, None, True)]
    for va, pa in ca.alts:
        for vb, pb in cb.alts:
            if not (isinstance(va, int) and isinstance(vb, int)):
                continue
            c = -math.log(max(pa, P_FLOOR)) - math.log(max(pb, P_FLOOR))
            us_them = (va, vb) if side == "us" else (vb, va)
            if us_them in path:
                options.append((c, (va, vb), True))
            elif (us_them[1], us_them[0]) in path:
                options.append((c + MIRROR_COST, (va, vb), True))
            else:
                options.append((c + OFF_PATH_COST, (va, vb), False))
    options.sort(key=lambda o: o[0])
    best_cost, pair, on = options[0]
    runner = options[1][0] if len(options) > 1 else math.inf
    if pair is None:
        return None, best_cost, runner, None
    top1 = (ca.best, cb.best)
    sure = ca.p(pair[0]) * cb.p(pair[1])
    why = None
    if not on:
        why = "off_path"
    elif top1 != pair:
        why = "rules"
    elif runner - best_cost < CHECK_MARGIN or sure < SURE:
        why = "unsure"
    quad = _merge_quads(ca.quad, cb.quad)
    prop = Proposal(
        list(pair), _pair_alts(ca, cb), why is not None, why, ca.image or cb.image, quad
    )
    return pair, best_cost, runner, prop


def _pair_alts(ca: Cell, cb: Cell) -> list[list[Any]]:
    out = []
    for va, pa in ca.alts[:3]:
        for vb, pb in cb.alts[:3]:
            out.append(([va, vb], pa * pb))
    out.sort(key=lambda t: -t[1])
    return [[v, round(p, 4)] for v, p in out[:3]]


def _merge_quads(
    a: tuple[float, ...] | None, b: tuple[float, ...] | None
) -> tuple[float, ...] | None:
    """The quad around two neighbouring cells (left, right)."""
    if a is None or b is None:
        return a or b
    return (a[0], a[1], b[2], b[3], b[4], b[5], a[6], a[7])


def _number(c: Cell, *, roster: set[int] | None = None) -> tuple[Value, Proposal | None]:
    """A single number cell (lineup, substitute): the likeliest reading, a known number
    preferred (`roster`: our roster, or the numbers read surely elsewhere on the sheet for that
    team). A known number read at least half surely needs no check."""
    options: list[tuple[float, Value]] = []
    for v, p in c.alts:
        if v == "X":
            continue
        cost = -math.log(max(p, P_FLOOR))
        if roster and isinstance(v, int) and v not in roster:
            cost += ROSTER_COST
        options.append((cost, v))
    if not options:
        return None, None
    options.sort(key=lambda o: o[0])
    cost, v = options[0]
    if v is None:
        return None, None
    runner = options[1][0] if len(options) > 1 else math.inf
    known = bool(roster) and isinstance(v, int) and v in (roster or set())
    why = None
    if v != c.best:
        why = "rules"
    elif (c.p(v) < (KNOWN_SURE if known else SURE)) or runner - cost < CHECK_MARGIN / (
        2 if known else 1
    ):
        why = "unsure"
    return v, Proposal(v, c.top(), why is not None, why, c.image, c.quad)


def number_pools(r: Readings, halves_of: Mapping[int, Mapping[Side, Half]]) -> dict[Side, set[int]]:
    """Per team the shirt numbers read surely in its lineups or as substitutes on the sheet: the
    same players start set after set, so a doubtful number that is one of them is likely."""
    out: dict[Side, set[int]] = {"us": set(), "them": set()}
    for no, halves in halves_of.items():
        for side, half in halves.items():
            for p in range(1, 7):
                for what in ("lineup", "sub_in"):
                    c = r.get(_key(no, half, what, p))
                    if isinstance(c.best, int) and c.p_best >= POOL_SURE:
                        out[side].add(c.best)
    return out


def read_set(
    r: Readings,
    no: int,
    us_half: Half,
    official: tuple[int, int] | None,
    roster: set[int] | None = None,
    fixed: Fixed | None = None,
    margins: bool = True,
    first_serve: Side | None = None,
    pools: Mapping[Side, set[int]] | None = None,
) -> SetRead:
    """One set as read, with us in `us_half` (`first_serve`: as a person typed it; `pools`: the
    numbers read surely elsewhere on the sheet, per team)."""
    deciding = no == 5
    halves = _halves(no, us_half)
    box = {
        side: box_cells(r, no, half, deciding, cont=deciding and half == "L")
        for side, half in halves.items()
    }
    best: tuple[float, BoxSolution] | None = None
    for first in [first_serve] if first_serve else sh.SIDES:
        sol = solve_boxes(box, first, deciding, official, fixed, margins=False)
        if sol is None:
            continue
        total = sol.cost + first_cost(r, no, halves, first, box)
        if best is None or total < best[0]:
            best = (total, sol)
    doc: dict[str, Any] = {"no": no, "deciding": deciding}
    cells: dict[str, Proposal] = {}
    lineup_cost = 0.0

    def known(side: Side) -> set[int] | None:
        """Our roster with what was read surely of ours; theirs: what was read surely."""
        pool = set((pools or {}).get(side, set()))
        if side == "us" and roster:
            pool |= roster
        return pool or None

    for side, half in halves.items():
        lineup: list[int | None] = []
        for p in range(1, 7):
            c = r.get(_key(no, half, "lineup", p))
            v, prop = _number(c, roster=known(side))
            lineup.append(v if isinstance(v, int) else None)
            if prop is not None:
                cells[f"lineup.{side}.{p}"] = prop
            if side == "us" and roster and isinstance(v, int) and v not in roster:
                lineup_cost += ROSTER_COST
        doc[f"lineup_{side}"] = lineup if any(v is not None for v in lineup) else None
    decoded = best is not None and best[1].forced <= MAX_FORCED
    note = None
    path: set[tuple[int, int]] = set()
    if decoded and best is not None:
        _, sol = best
        if margins:
            cl = {s: sh.cells(s == sol.first, deciding) for s in sh.SIDES}
            _margins(sol, box, cl, fixed or {}, deciding, official)
        doc["first_serve"] = sol.first
        decoded_set = _fill_boxes(doc, sol, deciding)
        path = _scores_on(sh.decode(decoded_set).scores())
        for side in sh.SIDES:
            is_first = side == sol.first
            cl_side = sh.cells(is_first, deciding)
            vals = sol.values[side]
            for i, pos in enumerate(cl_side):
                if pos in (fixed or {}).get(side, {}):
                    continue
                c = box[side].get(pos, BLANK_CELL)
                value: Value = vals[i] if i < len(vals) else None
                if value is None and c.best is None:
                    continue
                margin = sol.margins.get((side, i), 99.0)
                why = None
                if value is None and i >= len(vals) and c.p_best < AFTER_END_SURE:
                    pass  # after the set's last box: a stray mark the reader half saw
                elif value != c.best:
                    why = "rules"
                elif margin < CHECK_MARGIN:
                    why = "unsure"
                if value is None and why is None:
                    continue
                cells[f"box.{side}.{pos[0]}.{pos[1]}"] = Proposal(
                    value, c.top(), why is not None, why, c.image, c.quad
                )
            if not is_first:
                x = box[side].get((1, 1), BLANK_CELL)
                if x.best != "X":
                    cells[f"box.{side}.1.1"] = Proposal(
                        "X", x.top(), True, "rules", x.image, x.quad
                    )
        cells["first_serve"] = Proposal(sol.first, [], False)
        set_cost = best[0] + lineup_cost
    else:
        # Not decodable as read (a scorer's holes, a misread beyond repair): the readings as
        # they are, nothing filled in; to check is what the reader was not sure of and what
        # breaks the order of the team's scores.
        note = "not_decoded" if best is None else "too_many_repairs"
        doc["first_serve"] = None
        for side in halves:
            grid: list[list[int | None]] = [[None] * (6 if deciding else 8) for _ in range(6)]
            typed = (fixed or {}).get(side, {})
            out_of_order = _out_of_order(box[side], typed)
            for (p, b), c in box[side].items():
                if (p, b) in typed:
                    v = typed[(p, b)]
                    grid[p - 1][b - 1] = v if isinstance(v, int) else None
                elif isinstance(c.best, int):
                    grid[p - 1][b - 1] = c.best
                    doubtful = c.p_best < SURE or (p, b) in out_of_order
                    cells[f"box.{side}.{p}.{b}"] = Proposal(
                        c.best,
                        c.top(),
                        doubtful,
                        "not_decoded" if doubtful else None,
                        c.image,
                        c.quad,
                    )
                elif c.best == "X" and (p, b) == (1, 1):
                    doc["first_serve"] = sh.other(side)
            doc[f"boxes_{side}"] = grid
        doc["circled_us"] = doc["circled_them"] = None
        set_cost = math.inf
    # Timeouts and substitutions, on the path when there is one.
    timeouts: list[dict[str, Any]] = []
    subs: list[dict[str, Any]] = []
    for side, half in halves.items():
        for k in (1, 2):
            pair, _, _, prop = _pair(
                r, _key(no, half, "to", k, 0), _key(no, half, "to", k, 1), side, path
            )
            if pair is not None and prop is not None:
                if not path:
                    prop = replace(prop, check=True, why=prop.why or "not_decoded")
                timeouts.append({"side": side, "own": pair[0], "opp": pair[1]})
                cells[f"to.{side}.{k}"] = prop
        lineup = doc.get(f"lineup_{side}") or []
        for p in range(1, 7):
            number_cell = r.get(_key(no, half, "sub_in", p))
            number, nprop = _number(number_cell, roster=known(side))
            at, _, _, aprop = _pair(
                r, _key(no, half, "sub_at", p, 0), _key(no, half, "sub_at", p, 1), side, path
            )
            back, _, _, bprop = _pair(
                r, _key(no, half, "sub_back", p, 0), _key(no, half, "sub_back", p, 1), side, path
            )
            if number is None and at is None:
                continue
            subs.append(
                {
                    "side": side,
                    "position": p,
                    "number": number if isinstance(number, int) else None,
                    "at": list(at) if at else None,
                    "back": list(back) if back else None,
                }
            )
            for name, prop in (("number", nprop), ("at", aprop), ("back", bprop)):
                if prop is None:
                    continue
                if not path and name != "number":
                    prop = replace(prop, check=True, why=prop.why or "not_decoded")
                if name == "number" and isinstance(number, int) and number in lineup:
                    prop = replace(prop, check=True, why="on_court")
                cells[f"sub.{side}.{p}.{name}"] = prop
    doc["timeouts"] = timeouts
    doc["subs"] = subs
    doc["sanctions"] = []
    doc["cancelled"] = []
    for name, half, part in (("start", "L", "start"), ("end", "R", "end")):
        hh = r.get(_key(no, half, part, "h"))
        mm = r.get(_key(no, half, part, "m"))
        doc[name] = None
        if (
            isinstance(hh.best, int)
            and isinstance(mm.best, int)
            and 0 <= hh.best <= 23
            and mm.best <= 59
        ):
            sure = hh.p_best * mm.p_best
            text = f"{hh.best:02d}:{mm.best:02d}"
            doc[name] = text
            cells[name] = Proposal(
                text,
                [],
                sure < SURE,
                "unsure" if sure < SURE else None,
                hh.image,
                _merge_quads(hh.quad, mm.quad),
            )
    _check_times(doc, cells)
    doc["use"] = True
    return SetRead(no, us_half, doc, cells, set_cost, decoded, note)


def _out_of_order(cells: Mapping[tuple[int, int], Cell], typed: Mapping[Any, Any]) -> set[Any]:
    """The boxes whose number is not between the team's numbers before and after it (a team's
    scores rise box by box: I-VI of the first row, then of the next)."""
    seq: list[tuple[tuple[int, int], int]] = []
    for pos in sorted(set(cells) | set(typed), key=lambda pb: (pb[1], pb[0])):
        v = typed[pos] if pos in typed else cells[pos].best
        if isinstance(v, int):
            seq.append((pos, v))
    bad: set[Any] = set()
    for i, (pos, v) in enumerate(seq):
        if (i > 0 and v <= seq[i - 1][1]) or (i + 1 < len(seq) and v >= seq[i + 1][1]):
            bad.add(pos)
    return bad


def _check_times(doc: dict[str, Any], cells: dict[str, Proposal]) -> None:
    """A set lasts 10-60 minutes: otherwise both times are to check."""
    start, end = doc.get("start"), doc.get("end")
    if not (start and end):
        return
    a = int(start[:2]) * 60 + int(start[3:])
    b = int(end[:2]) * 60 + int(end[3:])
    if not 10 <= (b - a) % (24 * 60) <= 60:
        for name in ("start", "end"):
            if name in cells:
                cells[name] = replace(cells[name], check=True, why="unsure")


def _fill_boxes(doc: dict[str, Any], sol: BoxSolution, deciding: bool) -> sh.SheetSet:
    n = sh.boxes_for(deciding)
    grids: dict[Side, list[list[int | None]]] = {}
    circled: dict[Side, tuple[int, int] | None] = {}
    for side in sh.SIDES:
        grid: list[list[int | None]] = [[None] * n for _ in range(6)]
        cl = sh.cells(side == sol.first, deciding)
        vals = sol.values[side]
        for (p, b), v in zip(cl, vals, strict=False):
            grid[p - 1][b - 1] = v
        grids[side] = grid
        circled[side] = cl[len(vals) - 1] if vals else None
        doc[f"boxes_{side}"] = grid
        mark = circled[side]
        doc[f"circled_{side}"] = list(mark) if mark else None
    return sh.SheetSet(doc["no"], sol.first, grids, deciding, circled)


# --- the whole sheet ------------------------------------------------------------------------------


@dataclass
class SheetRead:
    us_letter: Literal["A", "B"]
    sets: list[SetRead]

    @property
    def to_check(self) -> int:
        return sum(s.to_check for s in self.sets)

    def as_json(self) -> dict[str, Any]:
        return {
            "us_letter": self.us_letter,
            "to_check": self.to_check,
            "sets": [s.as_json() for s in self.sets],
        }


def us_half_for(no: int, us_letter: str) -> Half:
    from wieplay.ml import sheet_form as sf

    left = sf.left_letter(no)
    return "L" if left == us_letter else "R"


def read_sheet(
    r: Readings,
    official: Sequence[tuple[int, int]] | None = None,
    roster: set[int] | None = None,
    fixed: Mapping[int, Fixed] | None = None,
    sets: Sequence[int] | None = None,
    us_letter: Literal["A", "B"] | None = None,
    first: Mapping[int, Side] | None = None,
) -> SheetRead:
    """Every set the photos show, decoded; we are team A or B, whichever fits better (or as
    given). `fixed`: per set the boxes a person typed."""
    numbers = [
        no for no in (sets if sets is not None else r.sets()) if 1 <= no <= 5 and _has_any(r, no)
    ]
    fixed = fixed or {}
    first = first or {}

    def off(no: int) -> tuple[int, int] | None:
        return official[no - 1] if official and no <= len(official) else None

    letters: list[Literal["A", "B"]] = [us_letter] if us_letter else ["A", "B"]
    best: tuple[float, Literal["A", "B"], list[SetRead]] | None = None
    for letter in letters:
        total = 0.0
        out: list[SetRead] = []
        for no in numbers:
            if no == 5:
                options = [
                    read_set(
                        r,
                        no,
                        h,
                        off(no),
                        roster,
                        fixed.get(no),
                        margins=False,
                        first_serve=first.get(no),
                    )
                    for h in ("L", "R")
                ]
                pick = min(options, key=lambda s: s.cost)
            else:
                pick = read_set(
                    r,
                    no,
                    us_half_for(no, letter),
                    off(no),
                    roster,
                    fixed.get(no),
                    margins=False,
                    first_serve=first.get(no),
                )
            out.append(pick)
            total += pick.cost if math.isfinite(pick.cost) else 200.0
        if best is None or total < best[0]:
            best = (total, letter, out)
    assert best is not None
    _, letter, picks = best
    pools = number_pools(r, {s.no: _halves(s.no, s.us_half) for s in picks})
    # Again with the margins, for the chosen orientation only.
    final = [
        read_set(
            r,
            s.no,
            s.us_half,
            off(s.no),
            roster,
            fixed.get(s.no),
            first_serve=first.get(s.no),
            pools=pools,
        )
        for s in picks
    ]
    return SheetRead(letter, final)


def _has_any(r: Readings, no: int) -> bool:
    prefix = f"s{no}."
    return any(k.startswith(prefix) and c.best is not None for k, c in r.cells.items())
