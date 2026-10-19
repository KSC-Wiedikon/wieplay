"""Who is on court at each rally, from the official scoresheet (PLAN §10.3a, §10.5). Plain
Python: the API, the players job and the stats run it.

A consistent sheet set gives every point in order with its server and each team's rotation;
the lineups and substitutions give the six shirt numbers in zones I-VI during each point
(wieplay/ml/sheet.py `on_court`). Zone I of the serving team is the server.

What that says about the people on the video at a serve (players job, wieplay/ml/identities.py):

- **The server** stands behind its end line: the one person of the serving team there is
  zone I's number. Sure with a court calibration (feet in metres); without one only when that
  person stands clearly apart, below (near end) or above (far end) everyone else of the team.
- **The serving team's other five** may stand anywhere since the 2025 rules (no positional
  fault for the serving team): only which numbers are on court.
- **The receiving team** must be in rotational order, but only relative to its neighbours
  (each front player nearer the net than the back player behind; left of the one to the right,
  in each row), and in practice it stands stacked and tight: a likelihood over the ways to
  give the people their zones (`receiving_zones`), soft evidence only, used where it is
  measurably right (PLAN §10.5).
- **Who may be read**: the six on court and the liberos (not on the sheet; from the roster
  snapshot); everyone else's number is a misread then.

Shirt numbers only: nothing here names a person.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from wieplay.core import court
from wieplay.ml import sheet as sh

Side = sh.Side
SIDES = sh.SIDES

# Zones I-VI as 1-6. Relations the receiving team keeps at the serve (FIVB 7.4): each pair is
# (the one nearer the net, the one behind it), or (the one on the left, the one on the right)
# as the team looks at the net.
FRONT_BACK: tuple[tuple[int, int], ...] = ((4, 5), (3, 6), (2, 1))
LEFT_RIGHT: tuple[tuple[int, int], ...] = ((4, 3), (3, 2), (5, 6), (6, 1))
FRONT_ROW = frozenset({2, 3, 4})
BACK_ROW = frozenset({1, 5, 6})


@dataclass(frozen=True)
class Lineup:
    """One point of a set as the sheet has it: who served (team and number), each team's six
    numbers in zones I-VI (None where the sheet has no number) and rotations so far."""

    set_no: int
    point: int  # 0-based, in the set's decoded points
    serving: Side
    server: int | None
    zones: Mapping[Side, tuple[int | None, ...] | None]
    rotation: Mapping[Side, int]
    winner: Side
    penalty: bool = False

    def numbers(self, side: Side) -> set[int]:
        found = self.zones.get(side)
        return {n for n in found or () if n is not None}

    def zone_of(self, side: Side, number: int) -> int | None:
        found = self.zones.get(side) or ()
        for zone, n in enumerate(found, start=1):
            if n == number:
                return zone
        return None

    def as_json(self) -> dict[str, object]:
        return {
            "set": self.set_no,
            "point": self.point + 1,
            "serving": self.serving,
            "server": self.server,
            "us": list(self.zones.get("us") or []) or None,
            "them": list(self.zones.get("them") or []) or None,
            "rotation_us": self.rotation["us"],
            "rotation_them": self.rotation["them"],
            "winner": self.winner,
            "penalty": self.penalty,
        }


def point_lineups(s: sh.SheetSet, d: sh.Decoded) -> list[Lineup]:
    """Every point of a decoded set with who was on court (substitutions applied)."""
    out = []
    for k, p in enumerate(d.points):
        zones: dict[Side, tuple[int | None, ...] | None] = {}
        for side in SIDES:
            found = sh.on_court(s, d, side, k)
            zones[side] = tuple(found) if found is not None else None
        serving = zones.get(p.server)
        out.append(
            Lineup(
                set_no=s.no,
                point=k,
                serving=p.server,
                server=serving[0] if serving else None,
                zones=zones,
                rotation={"us": p.rot_us, "them": p.rot_them},
                winner=p.winner,
                penalty=p.penalty,
            )
        )
    return out


def allowed(lineup: Lineup | None, side: Side, liberos: Iterable[int] = ()) -> set[int] | None:
    """The numbers that may be read on a team's players during a rally: its six on court and
    its liberos. None: not known (no sheet for the rally, or no lineup for the team)."""
    if lineup is None or lineup.zones.get(side) is None:
        return None
    return lineup.numbers(side) | set(liberos)


# --- the server ---------------------------------------------------------------------------------

# Behind the end line: feet this far past it (metres), within this much of the side lines.
BEHIND_M = 0.3
SIDE_SLACK_M = 1.5
# Two people behind the end line this close in depth: no telling which serves.
SERVER_APART_M = 1.0
# Without a calibration: the server's feet this far (box heights) below (near) or above (far)
# everyone else of the serving team.
SERVER_APART_H = 0.6


@dataclass(frozen=True)
class Seen:
    """A person at a moment: a key (the track), the feet in court metres (None without a
    calibration), the feet's height in the picture (0..1, down) and the box's height."""

    key: int
    foot: tuple[float, float] | None
    v: float
    h: float


def server_calibrated(people: Sequence[Seen], near_end: bool) -> int | None:
    """The key of the one person behind the serving team's end line (court metres), or None
    (nobody there, or two at about the same depth)."""
    behind: list[tuple[float, int]] = []
    for p in people:
        if p.foot is None:
            continue
        x, y = p.foot
        if not -SIDE_SLACK_M <= x <= court.WIDTH_M + SIDE_SLACK_M:
            continue
        depth = -y if near_end else y - court.LENGTH_M
        if depth >= BEHIND_M:
            behind.append((depth, p.key))
    if not behind:
        return None
    behind.sort(reverse=True)
    if len(behind) > 1 and behind[0][0] - behind[1][0] < SERVER_APART_M:
        return None
    return behind[0][1]


def server_uncalibrated(people: Sequence[Seen], near_end: bool) -> int | None:
    """Without a calibration: the serving team's person whose feet are clearly the lowest in
    the picture (near end line) or the highest (far end line), or None."""
    if len(people) < 2:
        return None
    order = sorted(people, key=lambda p: -p.v if near_end else p.v)
    first, second = order[0], order[1]
    scale = max(first.h, second.h, 1e-6)
    if abs(first.v - second.v) / scale < SERVER_APART_H:
        return None
    return first.key


# --- the receiving team's zones ------------------------------------------------------------------

# How sharply a relation is believed (metres): a pair the wrong way round by this much is
# e^-1 as likely as one the right way round by this much (people stack, overlap and step).
ORDER_SIGMA_M = 0.6


def _ok(margin: float, sigma: float) -> float:
    """log P(the relation holds) for a measured margin (logistic)."""
    z = margin / sigma
    return -math.log1p(math.exp(-z)) if z > -30 else z


def receiving_zones(
    people: Sequence[Seen], near_half: bool, sigma: float = ORDER_SIGMA_M
) -> dict[int, dict[int, float]]:
    """The receiving team at the serve: per person (key) the probability of each zone 1-6,
    over every way to give the people different zones, each weighed by how well it keeps the
    rotational order (FRONT_BACK, LEFT_RIGHT; relations with an empty zone say nothing). Court
    metres; `near_half`: the team plays on the camera's half (it looks away from the camera).
    At most six people (more: the six nearest the middle of the team's half)."""
    from wieplay.core import court

    pts = [p for p in people if p.foot is not None]
    if not pts:
        return {}
    mid_y = court.NET_Y / 2 if near_half else court.NET_Y + court.NET_Y / 2
    if len(pts) > 6:
        pts.sort(key=lambda p: abs(p.foot[1] - mid_y) + abs(p.foot[0] - court.WIDTH_M / 2))  # type: ignore[index]
        pts = pts[:6]

    def depth(p: Seen) -> float:  # distance from the net
        assert p.foot is not None
        return court.NET_Y - p.foot[1] if near_half else p.foot[1] - court.NET_Y

    def lateral(p: Seen) -> float:  # from the team's left
        assert p.foot is not None
        return p.foot[0] if near_half else court.WIDTH_M - p.foot[0]

    n = len(pts)
    totals: dict[int, dict[int, float]] = {p.key: dict.fromkeys(range(1, 7), 0.0) for p in pts}
    weights: list[tuple[float, tuple[int, ...]]] = []
    for zones in itertools.permutations(range(1, 7), n):
        at = {z: pts[i] for i, z in enumerate(zones)}
        logp = 0.0
        for front, back in FRONT_BACK:
            if front in at and back in at:
                logp += _ok(depth(at[back]) - depth(at[front]), sigma)
        for left, right in LEFT_RIGHT:
            if left in at and right in at:
                logp += _ok(lateral(at[right]) - lateral(at[left]), sigma)
        weights.append((logp, zones))
    top = max(w for w, _ in weights)
    norm = 0.0
    for logp, zones in weights:
        w = math.exp(logp - top)
        norm += w
        for i, z in enumerate(zones):
            totals[pts[i].key][z] += w
    return {k: {z: v / norm for z, v in zs.items()} for k, zs in totals.items()}


# --- the sheet's numbers per player ----------------------------------------------------------


@dataclass
class PlayerLine:
    """One shirt number's points from the sheet: its serves (points served), the points its
    team won on them, its longest run on one service turn, aces and errors (from the serve
    tags of the rallies the sheet was lined up with), points it was on court for and the
    difference won - lost then (plus/minus), also per rotation (1-6)."""

    served: int = 0
    won_on_serve: int = 0
    longest_run: int = 0
    aces: int = 0
    errors: int = 0
    tagged: int = 0  # serves with a graded tag
    on_court: int = 0
    plus_minus: int = 0
    by_rotation: list[int] = field(default_factory=lambda: [0] * 6)

    def as_json(self) -> dict[str, object]:
        return {
            "served": self.served,
            "won_on_serve": self.won_on_serve,
            "won_on_serve_pct": round(self.won_on_serve / self.served, 4) if self.served else None,
            "longest_run": self.longest_run,
            "aces": self.aces,
            "errors": self.errors,
            "tagged": self.tagged,
            "on_court": self.on_court,
            "plus_minus": self.plus_minus,
            "by_rotation": list(self.by_rotation),
        }


def player_lines(
    sets: Iterable[Sequence[Lineup]],
    grades: Mapping[tuple[int, int], str] | None = None,
    rotation: Callable[[Lineup, Side], int] | None = None,
) -> dict[tuple[Side, int], PlayerLine]:
    """Per (team, number) its PlayerLine over the sets' points. `grades`: the serve tag's
    grade of a point (set, 0-based point) where a tag graded it ("#" ace, "=" error);
    `rotation`: a team's rotation 1-6 at a point (default: 1 for the starting six, then one
    more per rotation). Liberos are not on the sheet: their points count for the player they
    replaced."""
    grades = grades or {}
    rot = rotation or (lambda lu, side: lu.rotation[side] % 6 + 1)
    out: dict[tuple[Side, int], PlayerLine] = {}

    def line(side: Side, number: int) -> PlayerLine:
        return out.setdefault((side, number), PlayerLine())

    for points in sets:
        run_key: tuple[Side, int] | None = None
        run = 0
        for lu in points:
            if lu.server is not None:
                key = (lu.serving, lu.server)
                server = line(*key)
                server.served += 1
                if lu.winner == lu.serving:
                    server.won_on_serve += 1
                    run = run + 1 if run_key == key else 1
                    run_key = key
                    server.longest_run = max(server.longest_run, run)
                else:
                    run_key, run = None, 0
                grade = grades.get((lu.set_no, lu.point))
                if grade is not None:
                    server.tagged += 1
                    server.aces += int(grade == "#")
                    server.errors += int(grade == "=")
            for side in SIDES:
                won = 1 if lu.winner == side else -1
                r = rot(lu, side)
                for number in lu.numbers(side):
                    player = line(side, number)
                    player.on_court += 1
                    player.plus_minus += won
                    if 1 <= r <= 6:
                        player.by_rotation[r - 1] += won
    return out
