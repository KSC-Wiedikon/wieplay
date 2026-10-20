"""The official scoresheet's labels for the players job (PLAN §10.5): shirt numbers from the
rotation, not only from reading them.

At each serve of a rally the sheet was lined up with (wieplay/core/lineups.py), the job knows
who served and the six numbers of each team on court (wieplay/ml/on_court.py). What it takes
from that (`USE`, each measured on the Legends game against the numbers read on the same
tracks and by eye, 19.10.2026):

- **"server"**: the server gets zone I's number. A camera close behind an end line has the near
  server below the picture and the far one too small by the far wall, so the server is the
  person behind the end line during the serve, else the one stepping onto the court from it
  just after (`entering_server`). Near end: 77 labels on 90 near serves, the reading on that
  track the server's number in 41 of 47; by eye the labelled person was the server in 25 of 25
  serve frames, the number legible and right in 16 of 16. Not the far end ("far_server"): 20
  labels, the 3 with a reading all different, not checkable by eye.
- **"teams"**: a piece of track reading a number its team does not have on court then and the
  other team does is the other team's (a blocker whose jumps put the feet past the net: our
  #2, #8 and #22 had sure cards of the other team).
- **"allowed"** (off): only the six on court and the liberos may be read during a rally. No
  better on Legends, worse on H1-H3 (both teams in the same club kit: a team mistaken for the
  other loses its own numbers).
- **"receivers"** (off): the receiving team's zones from the rotational order, soft
  (`on_court.receiving_zones`): on the near side the most likely zone's number was the one
  read on that track for 42 % of the front-row and 35 % of the back-row people, 9 of 94 on the
  far side; teams stack in reception and the libero takes a back-row place.

The labels go in with the shirt-number readings (`Track.reads`, a weight like a reading's
confidence), so the linking (wieplay/ml/identities.py) treats them alike: a number needs
several, a track whose labels and readings disagree is cut where they change. Nothing here
looks at a picture: positions and the sheet only.
"""

from __future__ import annotations

import bisect
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from wieplay.core import court
from wieplay.ml import on_court as oc
from wieplay.ml.players import NEAR, Collector, Track

Side = oc.Side

USE: frozenset[str] = frozenset({"server", "teams"})
# The serve moment: the people's feet over this stretch around the rally's start (the rally
# finder starts a rally at about the serve's hit; the server stands behind the line before).
SERVE_FROM_MS = -1000
SERVE_TO_MS = 200
# The server stepping onto the court: the person first seen this soon after the rally's start
# this close to the serving end line (near: in front of it by at most NEAR_ENTER_M; far: past
# FAR_ENTER_Y), the longest-followed one where the tracker saw them in pieces, all within
# ENTER_SPREAD_M of each other (else two people: no label).
ENTER_FROM_MS = -500
ENTER_TO_MS = 3000
NEAR_ENTER_M = 0.5
FAR_ENTER_Y = 16.0
ENTER_SPREAD_M = 2.5
# Without a calibration: the near server enters from the picture's bottom edge (H1-H3: 17
# labels on near serves, the reading on that track the server's number in 11 of 15).
ENTER_BOTTOM_V = 0.95
# A label's weight next to a reading's confidence (0..1; a number needs 1.2 of them).
SERVER_WEIGHT = 0.9
RECEIVER_WEIGHT = 0.6
RECEIVER_SURE = 0.9  # a receiver's zone is used from this probability on
RECEIVERS = "front"  # with "receivers": "front" | "all" (the back row's middle: the libero)


@dataclass(frozen=True)
class RallySheet:
    """What the sheet says about one rally (the job's inputs): the serving team, the
    server's number, both teams' six in zones I-VI, whether it is its set's first rally."""

    start_ms: int
    end_ms: int
    serving: Side
    server: int | None
    zones: Mapping[Side, tuple[int | None, ...] | None]
    set_no: int = 0
    first: bool = False
    # 1 for the sheet; the probability of the team memory's server (`prior`, PLAN §10.5b),
    # which knows our server only (no zones).
    weight: float = 1.0
    source: str = "sheet"  # sheet | memory

    def numbers(self, side: Side) -> set[int]:
        return {n for n in self.zones.get(side) or () if n is not None}


def from_inputs(rallies: Sequence[Mapping[str, Any]]) -> list[RallySheet | None]:
    """The job's rallies (core/players.rallies_with_sides): their `sheet` entries."""
    out: list[RallySheet | None] = []
    for r in rallies:
        s = r.get("sheet")
        if not s and r.get("prior"):
            out.append(_from_prior(r))
            continue
        if not s or s.get("serving") not in ("us", "them"):
            out.append(None)
            continue
        zones: dict[Side, tuple[int | None, ...] | None] = {}
        for side in oc.SIDES:
            found = s.get(side)
            zones[side] = tuple(found) if found and len(found) == 6 else None
        out.append(
            RallySheet(
                start_ms=int(r["start_ms"]),
                end_ms=int(r["end_ms"]),
                serving=s["serving"],
                server=s.get("server"),
                zones=zones,
                set_no=int(s.get("set") or 0),
                first=bool(s.get("first")),
            )
        )
    return out


def _from_prior(r: Mapping[str, Any]) -> RallySheet | None:
    """Our server from the team's memory (core/lineups.py `prior_for_video`): a label weighed
    by its probability; nobody's zones."""
    p = r["prior"]
    server = p.get("server")
    if p.get("serving") != "us" or not isinstance(server, int):
        return None
    return RallySheet(
        start_ms=int(r["start_ms"]),
        end_ms=int(r["end_ms"]),
        serving="us",
        server=server,
        zones={"us": None, "them": None},
        set_no=int(p.get("set") or 0),
        weight=min(max(float(p.get("p") or 0.0), 0.0), 1.0),
        source="memory",
    )


@dataclass
class SheetInputs:
    """What the job gets from the sheet: per rally of its inputs what the sheet says (None:
    not lined up), the liberos (the roster snapshot's, by number), the middle blockers, and
    every number the sheet has per team (lineups and substitutions)."""

    rallies: list[RallySheet | None]
    liberos: dict[str, set[int]] = field(default_factory=dict)
    middles: dict[str, set[int]] = field(default_factory=dict)
    numbers: dict[str, set[int]] = field(default_factory=dict)

    @property
    def any(self) -> bool:
        return any(r is not None for r in self.rallies)


def sheet_inputs(found: Mapping[str, Any]) -> SheetInputs | None:
    """The job's inputs (core/players.job_inputs) as SheetInputs; None without a sheet."""
    rallies = from_inputs(found.get("rallies") or [])
    if not any(r is not None for r in rallies):
        return None
    extra = found.get("sheet") or {}

    def sets(name: str) -> dict[str, set[int]]:
        return {k: {int(n) for n in v} for k, v in (extra.get(name) or {}).items()}

    return SheetInputs(rallies, sets("liberos"), sets("middles"), sets("numbers"))


@dataclass
class Label:
    """A number for a track at a frame, from the sheet."""

    track: int  # the tracker's track id (TrackData.track_id)
    frame: int
    number: int
    weight: float
    kind: str  # server | receiver
    team: Side
    zone: int | None = None


@dataclass
class Found:
    labels: list[Label] = field(default_factory=list)
    info: dict[str, int] = field(default_factory=lambda: defaultdict(int))


def _frames_between(col: Collector, t0: int, t1: int) -> range:
    lo = bisect.bisect_left(col.times, t0)
    hi = bisect.bisect_right(col.times, t1)
    return range(lo, hi)


def people_at(
    col: Collector, tracks: Sequence[Track], t0: int, t1: int
) -> dict[int, tuple[oc.Seen, int, Track]]:
    """The people seen between two times: per tracker track its feet (median), its middle
    frame there and its Track (referees left out)."""
    frames = _frames_between(col, t0, t1)
    if not frames:
        return {}
    by_id = {t.data.track_id: t for t in tracks if t.data is not None and not t.referee}
    out: dict[int, tuple[oc.Seen, int, Track]] = {}
    for tid, t in by_id.items():
        data = t.data
        assert data is not None
        lo = bisect.bisect_left(data.frames, frames.start)
        hi = bisect.bisect_left(data.frames, frames.stop)
        if hi - lo < 2:
            continue
        feet = [f for f in data.foot_at[lo:hi] if f is not None]
        boxes = data.boxes[lo:hi]
        v = float(np.median([b[3] for b in boxes]))
        h = float(np.median([b[3] - b[1] for b in boxes]))
        foot = None
        if feet:
            arr = np.asarray(feet)
            foot = (float(np.median(arr[:, 0])), float(np.median(arr[:, 1])))
        out[tid] = (oc.Seen(tid, foot, v, h), data.frames[(lo + hi) // 2], t)
    return out


def entering_server(
    col: Collector,
    tracks: Sequence[Track],
    start_ms: int,
    serving_near: bool,
    calibrated: bool,
) -> tuple[int, int] | None:
    """(tracker track id, frame) of the server stepping onto the court after the serve, or
    None (see ENTER_FROM_MS)."""
    lo = bisect.bisect_left(col.times, start_ms + ENTER_FROM_MS)
    hi = bisect.bisect_right(col.times, start_ms + ENTER_TO_MS)
    cands: list[tuple[int, int, int, tuple[float, float] | None]] = []
    for t in tracks:
        data = t.data
        if data is None or t.referee or t.hi <= t.lo:
            continue
        first = data.frames[t.lo]
        if not lo <= first < hi:
            continue
        foot = data.foot_at[t.lo]
        if calibrated:
            if foot is None:
                continue
            x, y = foot
            if not -oc.SIDE_SLACK_M <= x <= court.WIDTH_M + oc.SIDE_SLACK_M:
                continue
            if (serving_near and y > NEAR_ENTER_M) or (not serving_near and y < FAR_ENTER_Y):
                continue
        else:
            if not serving_near or data.boxes[t.lo][3] < ENTER_BOTTOM_V:
                continue  # uncalibrated: only the near server, from the bottom edge
        cands.append((t.detections, data.track_id, first, foot))
    if not cands:
        return None
    best = max(cands)
    if calibrated and best[3] is not None:
        bx, by = best[3]
        for _, _, _, foot in cands:
            if foot is not None and abs(foot[0] - bx) + abs(foot[1] - by) > ENTER_SPREAD_M:
                return None
    return best[1], best[2]


def _side_of(seen: oc.Seen, split_v: float | None) -> int:
    if seen.foot is not None:
        return NEAR if seen.foot[1] < court.NET_Y else 1
    return 1 if seen.v < (split_v if split_v is not None else 0.6) else NEAR


def serve_labels(
    col: Collector,
    tracks: Sequence[Track],
    rallies: Sequence[RallySheet | None],
    near_team: Sequence[str | None],
    calibrated: bool,
    liberos: Mapping[str, set[int]] | None = None,
    middles: Mapping[str, set[int]] | None = None,
    receivers: str | None = None,
    use: frozenset[str] | None = None,
) -> Found:
    """The sheet's labels at each rally's serve (module docstring). `near_team[i]`: the team
    at the camera's end in rally i (smoothed over the set); `middles`: the middle blockers per
    team (the roster's positions), whose back-row zone the libero usually takes; `use`: which
    labels (USE)."""
    use = USE if use is None else use
    if receivers is None:
        receivers = RECEIVERS if "receivers" in use else "none"
    found = Found()
    split_v = col.split_v
    for i, rs in enumerate(rallies):
        if rs is None:
            continue
        found.info["rallies"] += 1
        near = near_team[i] if i < len(near_team) else None
        if near not in ("us", "them"):
            continue
        seen = people_at(col, tracks, rs.start_ms + SERVE_FROM_MS, rs.start_ms + SERVE_TO_MS)
        if not seen:
            continue
        found.info["seen"] += 1
        serving_near = rs.serving == near
        # The server: behind the serving team's end line, else stepping onto the court.
        far_ok = serving_near or "far_server" in use
        if rs.server is not None and "server" in use and far_ok:
            if calibrated:
                key = oc.server_calibrated([s for s, _, _ in seen.values()], serving_near)
            else:
                mine = [
                    s for s, _, _ in seen.values()
                    if (_side_of(s, split_v) == NEAR) == serving_near
                ]  # fmt: skip
                key = oc.server_uncalibrated(mine, serving_near)
            at: tuple[int, int] | None = None
            if key is not None:
                at = (key, seen[key][1])
                found.info["servers_behind"] += 1
            else:
                at = entering_server(col, tracks, rs.start_ms, serving_near, calibrated)
                if at is not None:
                    found.info["servers_entering"] += 1
            if at is not None:
                found.labels.append(
                    Label(
                        at[0], at[1], rs.server, SERVER_WEIGHT * rs.weight, "server", rs.serving, 1
                    )
                )
                found.info["servers"] += 1
        # The receiving team: its zones from the rotational order (calibrated only).
        receiving: Side = "them" if rs.serving == "us" else "us"
        zones = rs.zones.get(receiving)
        if receivers == "none" or not calibrated or zones is None:
            continue
        half = [
            s for s, _, _ in seen.values()
            if s.foot is not None and (_side_of(s, split_v) == NEAR) != serving_near
            and -oc.SIDE_SLACK_M <= s.foot[0] <= court.WIDTH_M + oc.SIDE_SLACK_M
        ]  # fmt: skip
        if len(half) < 4:
            continue
        found.info["receiving_seen"] += 1
        probs = oc.receiving_zones(half, near_half=not serving_near)
        libs = (liberos or {}).get(receiving, set())
        mids = (middles or {}).get(receiving, set())
        for key, zp in probs.items():
            zone, p = max(zp.items(), key=lambda kv: kv[1])
            if p < RECEIVER_SURE:
                continue
            number = zones[zone - 1]
            if number is None:
                continue
            if zone in oc.BACK_ROW:
                if receivers != "all":
                    continue
                if libs and (not mids or number in mids):
                    continue  # the libero's place, likely
            _, frame, _ = seen[key]
            found.labels.append(
                Label(key, frame, number, RECEIVER_WEIGHT * p, "receiver", receiving, zone)
            )
            found.info["receivers"] += 1
    return found


def add_labels(tracks: Sequence[Track], labels: Sequence[Label]) -> int:
    """The labels as readings of the tracks they were found on (`Track.reads`, kept sorted);
    returns how many went in."""
    by_id: dict[int, list[Track]] = defaultdict(list)
    for t in tracks:
        if t.data is not None:
            by_id[t.data.track_id].append(t)
    n = 0
    for lab in labels:
        for t in by_id.get(lab.track, ()):
            data = t.data
            assert data is not None
            if t.first <= lab.frame <= t.last:
                t.reads.append((lab.frame, lab.number, lab.weight))
                t.reads.sort()
                n += 1
                break
    return n


class Allowed:
    """Who may be read on a team at an analysed frame: during a rally the sheet lined up, its
    six on court and its liberos (unknown liberos: any number the sheet never names for the
    team, a libero is on no lineup); in the dead time between two rallies of a set, those of
    either (substitutions happen there); elsewhere the roster, or anyone."""

    def __init__(
        self,
        col: Collector,
        sheet: SheetInputs,
        roster: Mapping[str, set[int]] | None = None,
    ) -> None:
        self.roster = {k: set(v) for k, v in (roster or {}).items() if v}
        self.sheet = sheet
        self.known = sorted(
            ((r.start_ms, r.end_ms, r) for r in sheet.rallies if r is not None),
            key=lambda x: x[0],
        )
        self.starts = [s for s, _, _ in self.known]
        self.times = col.times

    def _rallies(self, t: int) -> list[RallySheet]:
        k = bisect.bisect_right(self.starts, t) - 1
        if k >= 0 and t <= self.known[k][1] + 1000:
            return [self.known[k][2]]
        before = self.known[k][2] if k >= 0 else None
        after = self.known[k + 1][2] if k + 1 < len(self.known) else None
        if before is not None and after is not None and before.set_no == after.set_no:
            return [before, after]
        if after is not None and (before is None or after.first):
            return [after]  # before a set's first serve: its starting six (and the liberos)
        return []

    def ok(self, team: str, frame: int, number: int) -> bool:
        if team not in ("us", "them") or frame >= len(self.times):
            return True
        found = self._rallies(self.times[frame])
        if not found or any(r.zones.get(team) is None for r in found):  # type: ignore[call-overload]
            roster = self.roster.get(team)
            return roster is None or number in roster
        if any(number in r.numbers(team) for r in found):  # type: ignore[arg-type]
            return True
        liberos = self.sheet.liberos.get(team)
        if liberos:
            return number in liberos
        return number not in self.sheet.numbers.get(team, set())


def on_court_ok(allowed: Allowed, team: str, frame: int, number: int) -> bool:
    """The number is on this team's court (or one of its known liberos) at that frame."""
    if team not in ("us", "them") or frame >= len(allowed.times):
        return False
    found = allowed._rallies(allowed.times[frame])
    if not found or any(r.zones.get(team) is None for r in found):  # type: ignore[call-overload]
        return False
    return any(number in r.numbers(team) for r in found) or number in allowed.sheet.liberos.get(  # type: ignore[arg-type]
        team, set()
    )


def fix_teams(tracks: Sequence[Track], allowed: Allowed) -> int:
    """A piece whose readings name a number its team does not have on court then (nor as a
    known libero), and the other team has, is the other team's. Returns how many changed."""
    from wieplay.ml.players import NUMBER_MIN_VOTES, NUMBER_SHARE, fold_partials

    changed = 0
    for t in tracks:
        if t.referee or not t.reads or t.team not in ("us", "them"):
            continue
        votes: dict[int, float] = defaultdict(float)
        for _, n, c in t.reads:
            votes[n] += c
        folded = fold_partials(dict(votes), t.team)
        number, score = max(folded.items(), key=lambda kv: kv[1])
        if score < NUMBER_MIN_VOTES or score < NUMBER_SHARE * sum(folded.values()):
            continue
        frames = [f for f, n, _ in t.reads if n == number] or [f for f, _, _ in t.reads]
        other = "them" if t.team == "us" else "us"
        # Its own team's unknown libero could wear it: unlikely to be the very number the
        # other team has on court then, so only its own court and known liberos count.
        mine = sum(1 for f in frames if on_court_ok(allowed, t.team, f, number))
        theirs = sum(1 for f in frames if on_court_ok(allowed, other, f, number))
        if mine == 0 and theirs * 2 > len(frames):
            t.team = other
            t.team_share = 1.0
            changed += 1
    return changed
