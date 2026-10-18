"""Official scoresheets: from the service boxes to every point (PLAN §10.3a; the rules and the
algorithm in docs/research/scoresheets-2026-10.md §1.3 and §3). Plain Python: the API runs it.

When the serving team loses the serve, the scorer writes **that team's own score** in the box of
the player who served (Swiss Volley's rulebook, Teil IV 3.1 a). A team's boxes, read in
service-turn order and interleaved starting with the first server, give the winner and the
server of every point in order:

    score = 0:0, serving = the first server
    loop: v = the serving team's next box
          v - its score = the break points it won on this serve (each a point for it)
          both teams' boxes used up: the set is over (the last box is the winner's final)
          else one point for the other team (the side-out), which serves next

Turn order on the paper sheet: the first server's turn t is position (t mod 6) + 1, box
⌊t / 6⌋ + 1 (the printed small number, 1-8; 1-6 in a deciding set); the team that received
first has the X in position I box 1, so its turn t is position ((t + 1) mod 6) + 1, box
⌊(t + 1) / 6⌋ + 1. That is the sheet's own order: box 1 of positions I-VI, then box 2, ...

Everything else on the sheet carries a score (timeouts and substitutions with the requesting
team's score first), and the decoded points are a monotone path through the scores, so each note
lands in exactly one gap between two points. The checks (research §3.5) name the box they doubt;
an inconsistent set gets the cheapest single repair as a suggestion (§3.6).

Shirt numbers and scores only: nothing here names a person.
"""

from __future__ import annotations

import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

Side = Literal["us", "them"]
Severity = Literal["error", "incomplete", "warning"]
SanctionKind = Literal["W", "P", "E", "D", "DW", "DP"]

SIDES: tuple[Side, Side] = ("us", "them")
BOXES = 8  # boxes per position in sets 1-4 (printed small numbers 1-8)
BOXES_DECIDING = 6  # in a deciding set
TARGET, TARGET_DECIDING = 25, 15
SWITCH_AT = 8  # the deciding set's change of sides
MAX_TIMEOUTS = 2  # per team and set
MAX_SCORE = 99
POINT_KINDS: frozenset[str] = frozenset({"P", "DP"})  # sanctions that give the opponent a point

# Repairs (research §3.6): what an edit costs, for typed sheets (a typo model). A set's repair
# is offered only when it is clearly the cheapest (CLEAR cheaper than the next).
COST_SWAP = 1.0  # two digits swapped: 21 for 12
COST_NEIGHBOUR = 1.0  # one digit, a neighbouring key on the keypad
COST_DIGIT = 1.5  # one digit, another key
COST_LENGTH = 1.5  # a digit dropped or doubled: 1 for 13, 133 for 13
COST_OTHER = 3.0
COST_FILL = 2.0  # an empty box filled in
COST_INSERT = 2.5  # a box missing (the boxes after it move one on)
COST_DELETE = 2.0  # a box too many
CLEAR = 1.0
MAX_REPAIRS = 3

# A numeric keypad: 7 8 9 / 4 5 6 / 1 2 3 / 0.
_KEYPAD = {"7": (0, 0), "8": (0, 1), "9": (0, 2), "4": (1, 0), "5": (1, 1), "6": (1, 2),
           "1": (2, 0), "2": (2, 1), "3": (2, 2), "0": (3, 0)}  # fmt: skip


def other(side: Side) -> Side:
    return "them" if side == "us" else "us"


def is_over(a: int, b: int, target: int) -> bool:
    return max(a, b) >= target and abs(a - b) >= 2


def boxes_for(deciding: bool) -> int:
    return BOXES_DECIDING if deciding else BOXES


def cells(first: bool, deciding: bool = False) -> list[tuple[int, int]]:
    """(position 1-6, box 1-8) of a team's service turns in order (module docstring)."""
    start = 0 if first else 1
    return [(k % 6 + 1, k // 6 + 1) for k in range(start, 6 * boxes_for(deciding))]


# --- the sheet --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Timeout:
    side: Side  # who called it
    own: int  # the requesting team's score, written first on the sheet
    opp: int

    def score(self) -> tuple[int, int]:
        """(us, them)."""
        return (self.own, self.opp) if self.side == "us" else (self.opp, self.own)


@dataclass(frozen=True)
class Sub:
    """A substitution in the column of a starting position (1-6): the number coming in and the
    score then, the starting player's return and its score (the substituting team's score
    first, as on the sheet)."""

    side: Side
    position: int
    number_in: int | None
    at: tuple[int, int] | None
    back: tuple[int, int] | None = None


@dataclass(frozen=True)
class Sanction:
    """A sanction with the score when it was given (the sanctioned team's score first). A
    penalty (P, DP) is a point for the opponent without a rally."""

    side: Side
    kind: SanctionKind
    own: int
    opp: int

    def score(self) -> tuple[int, int]:
        return (self.own, self.opp) if self.side == "us" else (self.opp, self.own)


@dataclass(frozen=True)
class Cancelled:
    """Rallies the video has after this score that the sheet does not count (a rotation fault
    found after the rally, points cancelled; research §1.4)."""

    us: int
    them: int
    rallies: int


Grid = Sequence[Sequence[int | None]]  # [position 0-5][box 0-7]


@dataclass(frozen=True)
class SheetSet:
    no: int
    first_serve: Side | None
    boxes: Mapping[Side, Grid]
    deciding: bool = False
    # The circled finals: (position, box) per team, when the coach marked them.
    circled: Mapping[Side, tuple[int, int] | None] = field(default_factory=dict)
    lineup: Mapping[Side, Sequence[int | None] | None] = field(default_factory=dict)
    timeouts: Sequence[Timeout] = ()
    subs: Sequence[Sub] = ()
    sanctions: Sequence[Sanction] = ()
    cancelled: Sequence[Cancelled] = ()

    @property
    def target(self) -> int:
        return TARGET_DECIDING if self.deciding else TARGET

    def value(self, side: Side, position: int, box: int) -> int | None:
        grid = self.boxes.get(side) or ()
        if position - 1 >= len(grid) or box - 1 >= len(grid[position - 1]):
            return None
        return grid[position - 1][box - 1]

    def has_boxes(self) -> bool:
        return any(v is not None for s in SIDES for col in self.boxes.get(s) or () for v in col)


# --- what decoding finds ---------------------------------------------------------------------


@dataclass(frozen=True)
class BoxRef:
    side: Side
    position: int
    box: int

    def as_json(self) -> dict[str, Any]:
        return {"side": self.side, "position": self.position, "box": self.box}


@dataclass(frozen=True)
class Issue:
    """A check that failed (research §3.5): `code`, how bad, the box (or note) it doubts and
    what the message needs. error: the set contradicts itself; incomplete: not finished yet;
    warning: a note (timeout, substitution, lineup) that does not fit, the points still do."""

    code: str
    severity: Severity
    box: BoxRef | None = None
    params: Mapping[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "box": self.box.as_json() if self.box else None,
            "params": dict(self.params),
        }


@dataclass(frozen=True)
class Point:
    """One point of the set: who won it, who served it, the score after it (us, them), the
    server's turn (0-based) and starting position, each team's rotations so far (side-outs it
    has won; the player in zone I started in position (r mod 6) + 1), and whether it was a
    penalty (a point without a rally)."""

    winner: Side
    server: Side
    us: int
    them: int
    turn: int
    position: int
    rot_us: int
    rot_them: int
    penalty: bool = False

    def score(self) -> tuple[int, int]:
        return self.us, self.them

    def rotation(self, side: Side) -> int:
        return self.rot_us if side == "us" else self.rot_them


@dataclass
class Decoded:
    points: list[Point]
    final: tuple[int, int]
    issues: list[Issue]
    last: BoxRef | None  # the last box read
    complete: bool  # every box read and the set ends on a set end
    # Where each timeout, substitution and sanction lands: the number of points played before it
    # (0: before the first serve). None: not on the path.
    timeout_at: list[int | None] = field(default_factory=list)
    sub_at: list[tuple[int | None, int | None]] = field(default_factory=list)
    sanction_at: list[int | None] = field(default_factory=list)

    @property
    def consistent(self) -> bool:
        return self.complete and not any(i.severity in ("error", "incomplete") for i in self.issues)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    def scores(self) -> list[tuple[int, int]]:
        """The score after 0, 1, ... points."""
        return [(0, 0)] + [p.score() for p in self.points]

    def index_of(self, score: tuple[int, int]) -> int | None:
        """How many points were played when the score was this (None: never)."""
        for k, s in enumerate(self.scores()):
            if s == score:
                return k
        return None


def turns(s: SheetSet, side: Side) -> list[tuple[int, int, int | None]]:
    """A team's service turns up to its last filled box: (position, box, value or None)."""
    first = s.first_serve == side
    out = [(p, b, s.value(side, p, b)) for p, b in cells(first, s.deciding)]
    last = max((i for i, (_, _, v) in enumerate(out) if v is not None), default=-1)
    return out[: last + 1]


def decode(s: SheetSet, official: tuple[int, int] | None = None) -> Decoded:
    """The set's points from its service boxes, with the checks (module docstring)."""
    issues: list[Issue] = []
    if s.first_serve is None:
        if s.has_boxes():
            issues.append(Issue("first_serve", "incomplete"))
        return Decoded([], (0, 0), issues or [Issue("empty", "incomplete")], None, False)
    first = s.first_serve
    seqs = {side: turns(s, side) for side in SIDES}
    for side in SIDES:
        for p, b, v in seqs[side]:
            if v is None:
                issues.append(Issue("hole", "error", BoxRef(side, p, b)))
    if not seqs[first] and not seqs[other(first)]:
        return Decoded([], (0, 0), [Issue("empty", "incomplete")], None, False)

    points: list[Point] = []
    score = {"us": 0, "them": 0}
    used = {"us": 0, "them": 0}
    rot = {"us": 0, "them": 0}
    serving: Side = first
    last: BoxRef | None = None
    read_all = False

    def add(winner: Side, server: Side, turn: int, position: int) -> None:
        r_us, r_them = rot["us"], rot["them"]
        score[winner] += 1
        points.append(
            Point(winner, server, score["us"], score["them"], turn, position, r_us, r_them)
        )
        if winner != server:
            rot[winner] += 1

    def now() -> dict[str, int]:
        return {"us": score["us"], "them": score["them"]}

    def next_box(side: Side) -> BoxRef | None:
        n = used[side]
        if n < len(seqs[side]):
            return BoxRef(side, seqs[side][n][0], seqs[side][n][1])
        cl = cells(side == first, s.deciding)
        return BoxRef(side, *cl[n]) if n < len(cl) else None

    while True:
        over = is_over(score["us"], score["them"], s.target)
        k = used[serving]
        if k >= len(seqs[serving]):
            rest = used[other(serving)] < len(seqs[other(serving)])
            if over and rest:  # boxes after the end of the set
                issues.append(Issue("over", "error", next_box(other(serving)), now()))
            else:
                issues.append(Issue("missing", "incomplete", next_box(serving), now()))
            break
        position, box_no, value = seqs[serving][k]
        ref = BoxRef(serving, position, box_no)
        if value is None:  # a hole (flagged above): the order after it is unknown
            break
        used[serving] += 1
        run = value - score[serving]
        if run < 0:
            issues.append(Issue("low", "error", ref, {"value": value, "score": score[serving]}))
            break
        if over and run > 0:
            issues.append(Issue("over", "error", ref, now()))
            break
        ended_early = False
        for _ in range(run):
            add(serving, serving, k, position)
            if is_over(score["us"], score["them"], s.target) and score[serving] < value:
                issues.append(Issue("over", "error", ref, now()))
                ended_early = True
                break
        if ended_early:
            break
        last = ref
        if all(used[side] >= len(seqs[side]) for side in SIDES):
            read_all = True
            a, b = score["us"], score["them"]
            won = other(serving)
            if not is_over(a, b, s.target) and is_over(
                a + (won == "us"), b + (won == "them"), s.target
            ):
                # Won on reception, the winner's final not written in its next server's box.
                add(won, serving, k, position)
                issues.append(Issue("final_missing", "warning", next_box(won), now()))
            break
        if is_over(score["us"], score["them"], s.target):
            # The set is over, yet the sheet goes on.
            rest = used[other(serving)] < len(seqs[other(serving)])
            nxt = next_box(other(serving)) if rest else next_box(serving)
            issues.append(Issue("over", "error", nxt, now()))
            break
        add(other(serving), serving, k, position)  # the side-out
        serving = other(serving)

    final = (score["us"], score["them"])
    complete = read_all and is_over(*final, s.target)
    if read_all and not is_over(*final, s.target):
        issues.append(Issue("not_over", "incomplete", last, {"us": final[0], "them": final[1]}))
    out = Decoded(points, final, issues, last, complete)
    if complete:
        _check_circled(s, out, seqs)
        if official is not None and tuple(official) != final:
            out.issues.append(
                Issue(
                    "official",
                    "error",
                    last,
                    {
                        "us": final[0],
                        "them": final[1],
                        "official_us": official[0],
                        "official_them": official[1],
                    },
                )
            )
    _place_notes(s, out)
    _check_lineups(s, out)
    return out


def _check_circled(
    s: SheetSet, out: Decoded, seqs: Mapping[Side, list[tuple[int, int, int | None]]]
) -> None:
    """Each team's circled final is its last box and holds its final score."""
    for side in SIDES:
        mark = s.circled.get(side)
        if mark is None:
            continue
        final = out.final[0] if side == "us" else out.final[1]
        seq = seqs[side]
        last = seq[-1] if seq else None
        at = BoxRef(side, *mark)
        if last is None or (last[0], last[1]) != tuple(mark) or last[2] != final:
            expected = BoxRef(side, last[0], last[1]) if last else None
            out.issues.append(
                Issue(
                    "circled",
                    "error",
                    at,
                    {
                        "final": final,
                        "value": s.value(side, *mark),
                        "expected": expected.as_json() if expected else None,
                    },
                )
            )


def _place_notes(s: SheetSet, out: Decoded) -> None:
    """Timeouts, substitutions and sanctions on the path; penalties mark their point."""
    known = out.points and out.complete
    per_side = {side: 0 for side in SIDES}
    for t in s.timeouts:
        per_side[t.side] += 1
        at = out.index_of(t.score())
        if at is None and known:
            mirror = out.index_of((t.score()[1], t.score()[0]))
            out.issues.append(
                Issue(
                    "timeout_mirror" if mirror is not None else "timeout_off",
                    "warning",
                    None,
                    {"side": t.side, "own": t.own, "opp": t.opp},
                )
            )
            at = mirror
        out.timeout_at.append(at)
    for side in SIDES:
        if per_side[side] > MAX_TIMEOUTS:
            out.issues.append(Issue("timeouts_many", "warning", None, {"side": side}))
    for sub in s.subs:
        places: list[int | None] = []
        for score in (sub.at, sub.back):
            if score is None:
                places.append(None)
                continue
            us_them = score if sub.side == "us" else (score[1], score[0])
            at = out.index_of(us_them)
            if at is None and known:
                out.issues.append(
                    Issue(
                        "sub_off",
                        "warning",
                        None,
                        {
                            "side": sub.side,
                            "position": sub.position,
                            "own": score[0],
                            "opp": score[1],
                        },
                    )
                )
            places.append(at)
        if places[0] is not None and places[1] is not None and places[1] < places[0]:
            out.issues.append(
                Issue("sub_order", "warning", None, {"side": sub.side, "position": sub.position})
            )
        out.sub_at.append((places[0], places[1]))
    for sanction in s.sanctions:
        at = out.index_of(sanction.score())
        out.sanction_at.append(at)
        if at is None:
            if known:
                out.issues.append(
                    Issue(
                        "sanction_off",
                        "warning",
                        None,
                        {
                            "side": sanction.side,
                            "kind": sanction.kind,
                            "own": sanction.own,
                            "opp": sanction.opp,
                        },
                    )
                )
            continue
        if sanction.kind not in POINT_KINDS:
            continue
        if at < len(out.points) and out.points[at].winner == other(sanction.side):
            out.points[at] = replace(out.points[at], penalty=True)
        elif known:
            out.issues.append(
                Issue(
                    "penalty_off",
                    "warning",
                    None,
                    {
                        "side": sanction.side,
                        "kind": sanction.kind,
                        "own": sanction.own,
                        "opp": sanction.opp,
                    },
                )
            )


def _check_lineups(s: SheetSet, out: Decoded) -> None:
    for side in SIDES:
        lineup = s.lineup.get(side)
        if not lineup:
            continue
        numbers = [n for n in lineup if n is not None]
        if len(numbers) != len(set(numbers)):
            out.issues.append(Issue("lineup_twice", "warning", None, {"side": side}))
        for sub in s.subs:
            if sub.side == side and sub.number_in is not None and sub.number_in in numbers:
                out.issues.append(
                    Issue(
                        "sub_on_court",
                        "warning",
                        None,
                        {"side": side, "position": sub.position, "number": sub.number_in},
                    )
                )


# --- who stood where -------------------------------------------------------------------------


def zone_of(start_position: int, rotations: int) -> int:
    """The zone (1-6) of a player who started in this position after so many rotations."""
    return (start_position - 1 - rotations) % 6 + 1


def on_court(s: SheetSet, out: Decoded, side: Side, k: int) -> list[int | None] | None:
    """The six numbers of a team in zones I-VI during point k (0-based), substitutions
    applied; None without its lineup. Libero replacements are not on the sheet."""
    lineup = s.lineup.get(side)
    if not lineup or len(lineup) != 6:
        return None
    slots = list(lineup)
    for sub, (came, went) in zip(s.subs, out.sub_at, strict=False):
        if sub.side != side or not 1 <= sub.position <= 6 or came is None:
            continue
        if came <= k and (went is None or k < went):
            slots[sub.position - 1] = sub.number_in
    if k < len(out.points):
        r = out.points[k].rotation(side)
    else:
        last = out.points[-1] if out.points else None
        r = 0 if last is None else last.rotation(side) + int(last.winner == side != last.server)
    zones: list[int | None] = [None] * 6
    for position, number in enumerate(slots, start=1):
        zones[zone_of(position, r) - 1] = number
    return zones


# --- repairs ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Repair:
    """One edit that makes the set consistent: change a box's value (or fill an empty one),
    insert a box (the team's boxes after it move one on), delete a box (they move one back)."""

    kind: Literal["change", "insert", "delete"]
    box: BoxRef
    old: int | None
    new: int | None
    cost: float
    final: tuple[int, int]

    def as_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "box": self.box.as_json(),
            "old": self.old,
            "new": self.new,
            "cost": round(self.cost, 2),
            "final": list(self.final),
        }


def typo_cost(old: int | None, new: int) -> float:
    """How likely a typed `old` stands for `new` (a cost: lower is likelier)."""
    if old is None:
        return COST_FILL
    a, b = str(old), str(new)
    if len(a) == len(b) == 2 and a[::-1] == b:
        return COST_SWAP
    if len(a) == len(b):
        diffs = [(x, y) for x, y in zip(a, b, strict=True) if x != y]
        if len(diffs) == 1:
            (x, y), = diffs  # fmt: skip
            (r1, c1), (r2, c2) = _KEYPAD[x], _KEYPAD[y]
            return COST_NEIGHBOUR if abs(r1 - r2) + abs(c1 - c2) == 1 else COST_DIGIT
    if abs(len(a) - len(b)) == 1:
        long, short = (a, b) if len(a) > len(b) else (b, a)
        if long[1:] == short or long[:-1] == short:
            return COST_LENGTH
    return COST_OTHER + 0.05 * abs(old - new)


def _walk(
    first: Side, values: Mapping[Side, Sequence[int | None]], target: int
) -> tuple[int, int] | None:
    """The final of a sequence of turn values when they decode cleanly to a set end, else None
    (decode() without the bookkeeping, for the repair search)."""
    score = {"us": 0, "them": 0}
    used = {"us": 0, "them": 0}
    serving: Side = first
    n = {side: len(values[side]) for side in SIDES}
    while True:
        over = is_over(score["us"], score["them"], target)
        k = used[serving]
        if k >= n[serving]:
            return None
        v = values[serving][k]
        used[serving] += 1
        if v is None or v < score[serving] or (over and v > score[serving]):
            return None
        if v > score[serving]:
            # The set may end on this box's last point, never before it.
            pre = dict(score)
            pre[serving] = v - 1
            if is_over(pre["us"], pre["them"], target):
                return None
            score[serving] = v
        final = (score["us"], score["them"])
        if used["us"] >= n["us"] and used["them"] >= n["them"]:
            if is_over(*final, target):
                return final
            won = other(serving)  # won on reception, the final not written (decode)
            final = (final[0] + (won == "us"), final[1] + (won == "them"))
            return final if is_over(*final, target) else None
        if is_over(*final, target):
            return None
        score[other(serving)] += 1
        serving = other(serving)


def _goal(s: SheetSet, official: tuple[int, int] | None) -> tuple[int, int] | None:
    if official is not None:
        return (int(official[0]), int(official[1]))
    marks = [s.circled.get(side) for side in SIDES]
    if all(m is not None for m in marks):
        values = [s.value(side, *m) for side, m in zip(SIDES, marks, strict=True) if m]
        if all(v is not None for v in values):
            return (int(values[0] or 0), int(values[1] or 0))
    return None


def repairs(s: SheetSet, official: tuple[int, int] | None = None) -> list[Repair]:
    """The cheapest single edits that make the set decode to a set end (the official score when
    known, else the circled finals when marked), cheapest first."""
    if s.first_serve is None or not s.has_boxes():
        return []
    first = s.first_serve
    goal = _goal(s, official)
    seqs = {side: turns(s, side) for side in SIDES}
    values = {side: [v for _, _, v in seqs[side]] for side in SIDES}
    cell_lists = {side: cells(side == first, s.deciding) for side in SIDES}
    found: dict[tuple[str, Side, int, int | None], Repair] = {}

    def fits(final: tuple[int, int]) -> bool:
        return goal is None or final == goal

    def keep(r: Repair) -> None:
        key = (r.kind, r.box.side, r.box.position * 10 + r.box.box, r.new)
        if key not in found or found[key].cost > r.cost:
            found[key] = r

    for side in SIDES:
        seq = values[side]
        cl = cell_lists[side]
        for k in range(len(seq) + 1):
            before = next((v for v in reversed(seq[:k]) if v is not None), None)
            if k < len(seq):
                old = seq[k]
                after = next((v for v in seq[k + 1 :] if v is not None), None)
                for v in _candidates(old, before, after, goal, s.target):
                    if v == old:
                        continue
                    trial = {**values, side: [*seq[:k], v, *seq[k + 1 :]]}
                    final = _walk(first, trial, s.target)
                    if final is not None and fits(final):
                        keep(Repair("change", BoxRef(side, *cl[k]), old, v,
                                    typo_cost(old, v), final))  # fmt: skip
                trial = {**values, side: [*seq[:k], *seq[k + 1 :]]}
                final = _walk(first, trial, s.target)
                if final is not None and fits(final):
                    keep(Repair("delete", BoxRef(side, *cl[k]), old, None, COST_DELETE, final))
            if k < len(cl):
                at = next((v for v in seq[k:] if v is not None), None)
                for v in _candidates(None, before, at, goal, s.target):
                    trial = {**values, side: [*seq[:k], v, *seq[k:]]}
                    final = _walk(first, trial, s.target)
                    if final is not None and fits(final):
                        keep(Repair("insert", BoxRef(side, *cl[k]), None, v, COST_INSERT, final))
    ranked = sorted(found.values(), key=lambda r: (r.cost, r.box.side, r.box.box, r.box.position))
    return ranked[:MAX_REPAIRS]


def _candidates(
    old: int | None, prev: int | None, nxt: int | None, goal: tuple[int, int] | None, target: int
) -> Iterable[int]:
    """Values worth trying in a box: between its neighbours, and what a typo of the old value
    could have meant."""
    lo = 0 if prev is None else prev + 1
    top = max(goal) if goal else target + 15
    hi = top if nxt is None or nxt <= lo else nxt - 1
    out = set(range(lo, min(hi, top) + 1))
    if old is not None:
        text = str(old)
        variants = {text[::-1], text[1:], text[:-1]}
        variants |= {text[:i] + d + text[i + 1 :] for i in range(len(text)) for d in "0123456789"}
        out |= {int(v) for v in variants if v and v.isdigit() and int(v) <= top}
    return sorted(out)


def clear_repair(found: Sequence[Repair]) -> Repair | None:
    """The repair to suggest: the cheapest, when it is clearly cheaper than the next."""
    if not found:
        return None
    if len(found) == 1 or found[0].cost + CLEAR <= found[1].cost:
        return found[0]
    return None


# --- from points to boxes (the simulator, a log as a stand-in sheet) ---------------------------


def encode(
    first: Side, winners: Sequence[Side], deciding: bool = False
) -> tuple[dict[Side, list[list[int | None]]], dict[Side, tuple[int, int] | None]]:
    """The service boxes a scorer writes for this sequence of points (the inverse of decode):
    each team's grid [position][box] and where its final is circled. ValueError when a team
    needs more turns than the grid has."""
    score = {"us": 0, "them": 0}
    values: dict[Side, list[int]] = {"us": [], "them": []}
    serving: Side = first
    for w in winners:
        if w != serving:
            values[serving].append(score[serving])
            serving = w
        score[w] += 1
    values[serving].append(score[serving])
    n = boxes_for(deciding)
    grids: dict[Side, list[list[int | None]]] = {}
    circled: dict[Side, tuple[int, int] | None] = {}
    for side in SIDES:
        cl = cells(side == first, deciding)
        if len(values[side]) > len(cl):
            raise ValueError("more service turns than boxes")
        grid: list[list[int | None]] = [[None] * n for _ in range(6)]
        for (p, b), v in zip(cl, values[side], strict=False):
            grid[p - 1][b - 1] = v
        grids[side] = grid
        circled[side] = cl[len(values[side]) - 1] if values[side] else None
    return grids, circled


def play(rng: random.Random, first: Side, side_out: float, deciding: bool = False) -> list[Side]:
    """A random set by the rules: the receiving team wins a rally with probability `side_out`."""
    target = TARGET_DECIDING if deciding else TARGET
    a = b = 0
    serving: Side = first
    out: list[Side] = []
    while not is_over(a, b, target):
        won = other(serving) if rng.random() < side_out else serving
        out.append(won)
        a, b = a + (won == "us"), b + (won == "them")
        serving = won
    return out


def synthetic_set(
    rng: random.Random, no: int = 1, side_out: float = 0.6, deciding: bool = False
) -> tuple[SheetSet, list[Side]]:
    """A random set and its sheet as a scorer writes it (made-up shirt numbers)."""
    first: Side = "us" if rng.random() < 0.5 else "them"
    while True:
        winners = play(rng, first, side_out, deciding)
        try:
            grids, circled = encode(first, winners, deciding)
        except ValueError:
            continue
        break
    pool = list(range(1, 20))
    rng.shuffle(pool)
    lineup = {"us": pool[:6], "them": pool[6:12]}
    return (
        SheetSet(no, first, grids, deciding, circled, lineup),  # type: ignore[arg-type]
        winners,
    )


# --- the sheet's own numbers ------------------------------------------------------------------


@dataclass
class Line:
    """A team's rallies: points, side-out (won when the other team served) and break points
    (won on its own serve)."""

    points: int = 0
    lost: int = 0
    served: int = 0
    break_won: int = 0
    received: int = 0
    side_out_won: int = 0

    def add_point(self, p: Point, side: Side) -> None:
        if p.winner == side:
            self.points += 1
        else:
            self.lost += 1
        if p.server == side:
            self.served += 1
            self.break_won += int(p.winner == side)
        else:
            self.received += 1
            self.side_out_won += int(p.winner == side)

    def add(self, other_line: Line) -> None:
        for name in ("points", "lost", "served", "break_won", "received", "side_out_won"):
            setattr(self, name, getattr(self, name) + getattr(other_line, name))

    def as_json(self) -> dict[str, Any]:
        return {
            "points": self.points,
            "lost": self.lost,
            "served": self.served,
            "break_won": self.break_won,
            "received": self.received,
            "side_out_won": self.side_out_won,
            "side_out_pct": round(self.side_out_won / self.received, 4) if self.received else None,
            "break_point_pct": round(self.break_won / self.served, 4) if self.served else None,
        }


@dataclass
class SideStats:
    team: Line = field(default_factory=Line)
    rotations: list[Line] = field(default_factory=lambda: [Line() for _ in range(6)])
    longest_run: int = 0

    def add(self, more: SideStats) -> None:
        self.team.add(more.team)
        for mine, theirs in zip(self.rotations, more.rotations, strict=True):
            mine.add(theirs)
        self.longest_run = max(self.longest_run, more.longest_run)


def set_stats(points: Sequence[Point]) -> dict[Side, SideStats]:
    """Per team: its line, per rotation (k = 0-5 rotations from the starting six) and its
    longest run of points."""
    out = {side: SideStats() for side in SIDES}
    run_side: Side | None = None
    run = 0
    for p in points:
        for side in SIDES:
            out[side].team.add_point(p, side)
            out[side].rotations[p.rotation(side) % 6].add_point(p, side)
        run = run + 1 if p.winner == run_side else 1
        run_side = p.winner
        out[p.winner].longest_run = max(out[p.winner].longest_run, run)
    return out
