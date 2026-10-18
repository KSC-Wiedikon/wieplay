"""Players v2 (PLAN §10.5): the tracks of one video linked into its people.

Tracks break: a player hidden behind the net or a teammate for a moment, a huddle after a
point, a stretch beside the court. Players v1 grouped every short track by colour, and
teammates wear the same kit: ~450 cards a match for ~25 people. v2 links by what the rules
allow (PLAN §5.7: shirt numbers and the court, nothing across videos), within the one
video and in memory only, in this order (wieplay/worker/players.py `identify`):

1. **teams** (`assign_teams`): per detection, the side of the net (feet near the net do not
   vote) and which team played there in that rally window (the serve, the set); windows
   with no answer take the kits' colours, and the kits correct a window only where both
   sides clearly wear the other team's shirts;
2. **numbers**: read on each track's crops (players.read_numbers); ours must be on the
   roster;
3. **cuts**: a track is cut where its readings change from one number to another
   (`split_swaps`) and after each meeting with another track (`split_encounters`): the
   tracker may have swapped two people there;
4. **chains** (`chains_of`): a track that ends and another of the same team that starts
   shortly after, close by on the court (in the picture without a calibration), alike and
   with no other number, are one person: a minimum-cost matching of track ends to track
   starts (the same number and the tracker's own continuation make a link cheaper); then
   longer gaps where nobody else could be them; a chain reading two numbers is cut;
5. **numbers to people** (`assign_numbers`): pieces claim the numbers they read, the
   surest first; a number spreads along its chain only briefly;
6. **cards** (players.cluster_tracks): pieces with the same number are one card; colour
   joins only pieces without a number; `absorb` gives a card without a number to the one
   numbered teammate missing while it is seen;
7. a **confidence** per card (`set_confidence`): its number read often enough for its time
   on court, and no bigger card with the same number; the rest is to be checked.

The look of a person is the colour of the head, shirt, shorts and legs regions (players.py
`descriptor`): no face, no re-identification model. It exists only in memory while the job
runs; nothing is stored or compared across videos.
"""

from __future__ import annotations

import bisect
import itertools
import math
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np
from scipy.optimize import linear_sum_assignment

from wieplay.ml.players import (
    FAR,
    NEAR,
    Cluster,
    Track,
    distance,
    fold_partials,
    settle_number,
)

# A chain with fewer detections in rally windows than this (1 s at 10 fps) and no number is
# not a player in play: someone crossing the court between rallies.
MIN_RALLY_DETECTIONS = 10
# Clusters below this confidence are marked for the coach to check.
UNCERTAIN = 0.5


# --- teams ----------------------------------------------------------------------------------


def _mean(ds: list[np.ndarray[Any, Any]], ws: list[float]) -> np.ndarray[Any, Any] | None:
    if not ds:
        return None
    w = np.asarray(ws, dtype=np.float64)
    out: np.ndarray[Any, Any] = (np.stack(ds) * w[:, None]).sum(axis=0) / max(w.sum(), 1e-9)
    return out


def _window_sides(t: Track) -> dict[tuple[int, int], int]:
    if t.window_sides:
        return t.window_sides
    return {(t.window, t.side): t.detections} if t.window >= 0 else {}


@dataclass
class Teams:
    """Which team played at the camera's end in each rally window (`near_us`), and what it
    took to know it."""

    near_us: dict[int, bool]
    windows: list[int]
    info: dict[str, Any]

    def assign(self, t: Track) -> None:
        """Set the track's team, and how sure its detections are of it (`team_share`)."""
        t.team = self.team_of(t)

    def team_of(self, t: Track) -> str:
        votes = {"us": 0, "them": 0}
        for (w, side), n in _window_sides(t).items():
            votes["us" if (side == NEAR) == self.near_us.get(w, True) else "them"] += n
        total = votes["us"] + votes["them"]
        t.team_share = max(votes.values()) / total if total >= TEAM_MIN_VOTES else 0.0
        if votes["us"] or votes["them"]:
            team = "us" if votes["us"] >= votes["them"] else "them"
        elif not self.windows:
            team = "us" if t.side == NEAR else "them"
        else:
            k = min(bisect.bisect_left(self.windows, t.window), len(self.windows) - 1)
            near_us = self.near_us.get(self.windows[max(k, 0)], True)
            team = "us" if (t.side == NEAR) == near_us else "them"
        return team


def assign_teams(tracks: Sequence[Track], near_team: Sequence[str | None]) -> Teams:
    """Our team or theirs for each track, from its detections in the rally windows: the
    side of the net and which team was on that side then (`near_team[window]`: "us", "them"
    or None). Windows with no answer take the team whose shirts look like the side's (kit
    colours learnt from the known windows, else from all, with "near in the first window =
    us" as the start: the coach can swap). A track never seen in a rally window takes the
    team of its side in the window nearest it."""
    groups: dict[tuple[int, int], list[tuple[Track, int]]] = defaultdict(list)
    for t in tracks:
        if t.referee:
            continue
        for key, n in _window_sides(t).items():
            groups[key].append((t, n))
    windows = sorted({w for w, _ in groups})

    def look(w: int, side: int) -> np.ndarray[Any, Any] | None:
        items = [
            (t.descriptor, n) for t, n in groups.get((w, side), []) if t.descriptor is not None
        ]
        return _mean([d for d, _ in items], [float(n) for _, n in items])

    known: dict[int, bool] = {}
    for w in windows:
        team = near_team[w] if 0 <= w < len(near_team) else None
        if team in ("us", "them"):
            known[w] = team == "us"
    state: dict[int, bool] = dict(known)
    unknown = [w for w in windows if w not in known]
    if unknown and windows:
        if not known:
            state[windows[0]] = True  # the anchor
        for _ in range(8):
            us_d = [d for w, v in state.items() if (d := look(w, NEAR if v else FAR)) is not None]
            them_d = [d for w, v in state.items() if (d := look(w, FAR if v else NEAR)) is not None]
            if not us_d or not them_d:
                break
            us_p, them_p = np.mean(np.stack(us_d), axis=0), np.mean(np.stack(them_d), axis=0)
            changed = False
            for w in unknown:
                if not known and w == windows[0]:
                    continue
                near, far = look(w, NEAR), look(w, FAR)
                keep = swap = 0.0
                if near is not None:
                    keep += distance(near, us_p)
                    swap += distance(near, them_p)
                if far is not None:
                    keep += distance(far, them_p)
                    swap += distance(far, us_p)
                value = keep <= swap
                if state.get(w) != value:
                    state[w], changed = value, True
            if not changed or known:
                break
    # The kits check the sides the rallies say: where both sides clearly look like the other
    # team's shirts, the window's sides are swapped (a serving side recorded the wrong way
    # round). Only clearly: the looks mix shirts with the floor around far players.
    corrected = 0
    for _ in range(2):
        us_d = [d for w, v in state.items() if (d := look(w, NEAR if v else FAR)) is not None]
        them_d = [d for w, v in state.items() if (d := look(w, FAR if v else NEAR)) is not None]
        if not us_d or not them_d:
            break
        us_p, them_p = np.mean(np.stack(us_d), axis=0), np.mean(np.stack(them_d), axis=0)
        if distance(us_p, them_p) < KIT_APART:
            break  # kits too alike to tell
        changed = False
        for w in windows:
            near, far = look(w, NEAR), look(w, FAR)
            if near is None or far is None:
                continue
            keep = distance(near, us_p if state[w] else them_p) + distance(
                far, them_p if state[w] else us_p
            )
            swap = distance(near, them_p if state[w] else us_p) + distance(
                far, us_p if state[w] else them_p
            )
            if swap < KIT_SWAP * keep:
                state[w] = not state[w]
                corrected += 1
                changed = True
        if not changed:
            break
    teams = Teams(
        state,
        windows,
        {
            "windows": len(windows),
            "known_windows": len(known),
            "kit_guessed_windows": len(unknown),
            "kit_corrected_windows": corrected,
        },
    )
    for t in tracks:
        t.team = teams.team_of(t)
    return teams


# The kits correct a window's sides only when the two teams' mean looks are this far apart
# and swapping the sides fits clearly better (the swapped distance below KIT_SWAP times the
# recorded one).
KIT_APART = 0.35
KIT_SWAP = 0.6
# A track is sure of its team with at least TEAM_MIN_VOTES detections in rallies (away from
# the net) and TEAM_SURE of them on one team's side: only those claim a number (a piece of
# their #7 whose feet were mostly at the net otherwise lands on our #7's card).
TEAM_MIN_VOTES = 10
TEAM_SURE = 0.75


# --- swaps ----------------------------------------------------------------------------------

# Two people in one track (the tracker swapped them where they crossed): its readings change
# from one number to another, each side read at least SWAP_VOTES with SWAP_SHARE of its
# readings. (The look, colour histograms, is too noisy for this: within one person's track
# the means of two halves differ by 0.68 at the median, 18.10.2026.)
SWAP_VOTES = 1.2
SWAP_SHARE = 0.75
MAX_SPLITS = 4


def _top(reads: Sequence[tuple[int, int, float]]) -> tuple[int | None, float, float]:
    votes: dict[int, float] = defaultdict(float)
    for _, n, c in reads:
        votes[n] += c
    if not votes:
        return None, 0.0, 0.0
    n, v = max(votes.items(), key=lambda kv: kv[1])
    return n, v, sum(votes.values())


def fold_reads(reads: Sequence[tuple[int, int, float]]) -> list[tuple[int, int, float]]:
    """Readings with a single digit taken as the two-digit number it is part of, where the
    track reads that number too (players.fold_partials): "2" in a track of "12" is no swap."""
    votes: dict[int, float] = defaultdict(float)
    for _, n, c in reads:
        votes[n] += c
    folded = fold_partials(dict(votes))
    host = {}
    for d in votes:
        if d not in folded:
            host[d] = next(n for n in folded if n >= 10 and str(d) in (str(n)[0], str(n)[1]))
    return [(f, host.get(n, n), c) for f, n, c in reads]


def number_switch(reads: Sequence[tuple[int, int, float]]) -> int | None:
    """Where the readings of a track change from one number to another: the frame between
    the two readings around the change, or None."""
    best: tuple[float, int] | None = None
    for k in range(1, len(reads)):
        if reads[k - 1][0] == reads[k][0]:
            continue
        na, va, ta = _top(reads[:k])
        nb, vb, tb = _top(reads[k:])
        if na is None or nb is None or na == nb:
            continue
        if va < SWAP_VOTES or vb < SWAP_VOTES or va < SWAP_SHARE * ta or vb < SWAP_SHARE * tb:
            continue
        if best is None or va + vb > best[0]:
            best = (va + vb, (reads[k - 1][0] + reads[k][0] + 1) // 2)
    return best[1] if best is not None else None


def split_swaps(
    tracks: Sequence[Track],
    col: Any,
    teams: Teams,
    allowed: dict[str, set[int]] | None = None,
) -> tuple[list[Track], int]:
    """Tracks cut where two people swapped (number_switch); the parts get new
    ids, their own readings, votes and team. Returns the tracks and the number of cuts."""
    from wieplay.ml.players import _fallback, make_track, vote

    fallback = _fallback(col)
    next_id = max((t.track_id for t in tracks), default=0) + 1_000_000
    out: list[Track] = []
    cuts = 0
    work = list(tracks)
    depth: dict[int, int] = {}
    while work:
        t = work.pop()
        data = t.data
        at = None
        if data is not None and not t.referee and depth.get(id(t), 0) < MAX_SPLITS:
            ok = allowed.get(t.team) if allowed else None
            reads = [r for r in t.reads if not ok or r[1] in ok]
            at = number_switch(fold_reads(reads))
        if at is None or data is None:
            out.append(t)
            continue
        k = bisect.bisect_left(data.frames, at, t.lo, t.hi)
        if k - t.lo < 2 or t.hi - k < 2:
            out.append(t)
            continue
        cuts += 1
        level = depth.get(id(t), 0) + 1
        for lo, hi in ((t.lo, k), (k, t.hi)):
            part = make_track(data, col, fallback, lo, hi, track_id=next_id)
            next_id += 1
            part.reads = [r for r in t.reads if part.first <= r[0] <= part.last]
            part.team = teams.team_of(part)
            vote(part, allowed)
            depth[id(part)] = level
            work.append(part)
    out.sort(key=lambda t: t.track_id)
    return out, cuts


# Two tracks whose boxes overlap this much (IoU) for ENCOUNTER_FRAMES frames met: the tracker
# may have swapped the two people there (18.10.2026, the Legends game: two tracks crossing at
# the net, each reading #7 on a different side of the crossing). Both are cut where they part,
# and the stitching decides who continues as whom, with the numbers read on either side.
ENCOUNTER_IOU = 0.3
ENCOUNTER_FRAMES = 2


def encounters(col: Any, ids: set[int]) -> dict[int, list[int]]:
    """Where tracks met: per tracker track id, the frames after each meeting (sorted)."""
    from wieplay.ml.bytetrack import iou_matrix

    by_frame: dict[int, list[tuple[int, tuple[float, float, float, float]]]] = defaultdict(list)
    for frame, tid, u1, v1, u2, v2 in col.rows:
        if tid in ids:
            by_frame[frame].append((tid, (u1, v1, u2, v2)))
    met: dict[tuple[int, int], list[int]] = defaultdict(list)
    for frame, items in by_frame.items():
        if len(items) < 2:
            continue
        boxes = np.asarray([b for _, b in items], dtype=np.float64)
        iou = iou_matrix(boxes, boxes)
        rows, cols = np.nonzero(np.triu(iou, 1) >= ENCOUNTER_IOU)
        for r, c in zip(rows.tolist(), cols.tolist(), strict=True):
            a, b = items[r][0], items[c][0]
            met[(min(a, b), max(a, b))].append(frame)
    out: dict[int, set[int]] = defaultdict(set)
    for (a, b), frames in met.items():
        frames.sort()
        run = [frames[0]]
        for f in [*frames[1:], None]:
            if f is not None and f - run[-1] <= 2:
                run.append(f)
                continue
            if len(run) >= ENCOUNTER_FRAMES:
                out[a].add(run[-1] + 1)
                out[b].add(run[-1] + 1)
            if f is not None:
                run = [f]
    return {k: sorted(v) for k, v in out.items()}


def split_encounters(
    tracks: Sequence[Track],
    col: Any,
    teams: Teams,
    allowed: dict[str, set[int]] | None = None,
) -> tuple[list[Track], int]:
    """Tracks cut after each meeting with another track (encounters); the parts get new ids,
    their readings, votes and team. Returns the tracks and the number of cuts."""
    from wieplay.ml.players import _fallback, make_track, vote

    by_source = {t.data.track_id: t for t in tracks if t.data is not None and not t.referee}
    cuts_at = encounters(col, set(by_source))
    fallback = _fallback(col)
    next_id = max((t.track_id for t in tracks), default=0) + 2_000_000
    out: list[Track] = [t for t in tracks if t.data is None or t.referee]
    cuts = 0
    for source, t in by_source.items():
        data = t.data
        assert data is not None
        bounds = [t.lo]
        for frame in cuts_at.get(source, []):
            k = bisect.bisect_left(data.frames, frame, t.lo, t.hi)
            if k - bounds[-1] >= 2 and t.hi - k >= 2:
                bounds.append(k)
        if len(bounds) == 1:
            out.append(t)
            continue
        bounds.append(t.hi)
        cuts += len(bounds) - 2
        for lo, hi in itertools.pairwise(bounds):
            part = make_track(data, col, fallback, lo, hi, track_id=next_id)
            next_id += 1
            part.reads = [r for r in t.reads if part.first <= r[0] <= part.last]
            part.team = teams.team_of(part)
            vote(part, allowed)
            out.append(part)
    out.sort(key=lambda t: t.track_id)
    return out, cuts


# --- chains ---------------------------------------------------------------------------------


@dataclass
class StitchParams:
    max_gap_ms: int = 4000  # a track this long gone is lost, not hidden
    speed_m_s: float = 5.0  # how far a player gets while hidden: slack + speed * gap
    slack_m: float = 1.0
    speed_h_s: float = 1.5  # without a calibration: box heights per second
    slack_h: float = 0.6
    max_look: float = 0.9  # a colour distance above this is someone else (another kit)
    max_cost: float = 1.6
    max_reach_m: float = 1e9  # the reach never exceeds this (the long pass)
    max_reach_h: float = 1e9
    # Link only where the end has no other possible start and the start no other end: over
    # a long gap the place says little, so only who else could it be decides.
    unique: bool = False

    def as_dict(self) -> dict[str, float | int | bool]:
        return dict(self.__dict__)


# The second pass over the chains of the first: a person lost for up to 20 s (hidden for a
# rally's end and the next serve) is found again when nobody else of the team could be them.
LONG = StitchParams(
    max_gap_ms=20_000,
    speed_m_s=3.0,
    slack_m=2.0,
    speed_h_s=0.8,
    slack_h=1.0,
    max_cost=2.5,
    max_reach_m=12.0,
    max_reach_h=6.0,
    unique=True,
)


def _centre(box: tuple[float, float, float, float]) -> tuple[float, float, float]:
    u1, v1, u2, v2 = box
    return (u1 + u2) / 2, v2, max(v2 - v1, 1e-6)


def link_cost(a: Track, b: Track, p: StitchParams) -> float:
    """What it costs to say track `b` continues track `a` (inf: it cannot)."""
    gap_ms = b.t0_ms - a.t1_ms
    if gap_ms <= 0 or gap_ms > p.max_gap_ms or a.team != b.team or a.referee != b.referee:
        return math.inf
    if a.number is not None and b.number is not None and a.number != b.number:
        return math.inf
    gap = gap_ms / 1000
    if a.last_foot is not None and b.first_foot is not None:
        d = math.dist(a.last_foot, b.first_foot)
        reach = min(p.max_reach_m, p.slack_m + p.speed_m_s * gap)
    elif a.last_box is not None and b.first_box is not None:
        ua, va, ha = _centre(a.last_box)
        ub, vb, hb = _centre(b.first_box)
        d = math.hypot(ua - ub, va - vb) / ((ha + hb) / 2)
        reach = min(p.max_reach_h, p.slack_h + p.speed_h_s * gap)
    else:
        return math.inf
    if d > reach:
        return math.inf
    look = 0.0
    if a.descriptor is not None and b.descriptor is not None:
        look = distance(a.descriptor, b.descriptor)
        if look > p.max_look:
            return math.inf
    cost = d / reach + 0.5 * look + 0.1 * gap
    # The tracker kept them together (a track cut where it met another): likely, unless the
    # numbers say otherwise. The same number on both sides: most likely one person.
    if a.data is not None and a.data is b.data:
        cost -= CONTINUITY_BONUS
    na, nb = _likely_number(a), _likely_number(b)
    if na is not None and nb is not None:
        cost += -NUMBER_BONUS if na == nb else NUMBER_BONUS
    return max(0.0, cost)


NUMBER_BONUS = 0.6
CONTINUITY_BONUS = 0.25


def _likely_number(t: Track) -> int | None:
    """The track's number, or its leading reading when that has some weight."""
    if t.number is not None:
        return t.number
    if not t.votes:
        return None
    n, v = max(t.votes.items(), key=lambda kv: kv[1])
    return n if v >= 0.6 else None


def stitch(tracks: Sequence[Track], params: StitchParams | None = None) -> list[list[Track]]:
    """The pieces of one person, in time order: every track belongs to exactly one chain.
    Ends are matched to starts at minimum total cost (per group of tracks that could link)."""
    p = params or StitchParams()
    items = sorted(tracks, key=lambda t: (t.t0_ms, t.track_id))
    starts = [t.t0_ms for t in items]
    edges: list[tuple[int, int, float]] = []
    for i, a in enumerate(items):
        lo = bisect.bisect_right(starts, a.t1_ms)
        hi = bisect.bisect_right(starts, a.t1_ms + p.max_gap_ms)
        for j in range(lo, hi):
            cost = link_cost(a, items[j], p)
            if cost <= p.max_cost:
                edges.append((i, j, cost))
    if p.unique:
        n_out: dict[int, int] = defaultdict(int)
        n_in: dict[int, int] = defaultdict(int)
        for i, j, _ in edges:
            n_out[i] += 1
            n_in[j] += 1
        edges = [e for e in edges if n_out[e[0]] == 1 and n_in[e[1]] == 1]
    # Groups of ends and starts that share candidates, each solved exactly.
    parent = list(range(2 * len(items)))

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i, j, _ in edges:
        ra, rb = find(i), find(len(items) + j)
        if ra != rb:
            parent[ra] = rb
    groups: dict[int, list[tuple[int, int, float]]] = defaultdict(list)
    for e in edges:
        groups[find(e[0])].append(e)
    successor: dict[int, int] = {}
    for group in groups.values():
        ends = sorted({i for i, _, _ in group})
        begins = sorted({j for _, j, _ in group})
        matrix = np.full((len(ends), len(begins)), 1e9)
        ei = {v: k for k, v in enumerate(ends)}
        bi = {v: k for k, v in enumerate(begins)}
        for i, j, c in group:
            matrix[ei[i], bi[j]] = c
        rows, cols = linear_sum_assignment(matrix)
        for r, c in zip(rows, cols, strict=True):
            if matrix[r, c] < 1e9:
                successor[ends[int(r)]] = begins[int(c)]
    has_pred = set(successor.values())
    chains: list[list[Track]] = []
    for i in range(len(items)):
        if i in has_pred:
            continue
        chain = [items[i]]
        k = i
        while k in successor:
            k = successor[k]
            chain.append(items[k])
        chains.append(chain)
    return chains


def split_conflicts(chain: list[Track]) -> list[list[Track]]:
    """A chain whose pieces read different numbers holds two people (a swap the tracker or
    the stitching made): cut it before each piece that reads another number."""
    out: list[list[Track]] = [[]]
    current: int | None = None
    for t in chain:
        if t.number is not None and current is not None and t.number != current and out[-1]:
            out.append([])
        if t.number is not None:
            current = t.number
        out[-1].append(t)
    return [c for c in out if c]


def as_chain(members: list[Track]) -> Track:
    """One person's pieces as one track (the chain's id is its first piece's)."""
    if len(members) == 1 and not members[0].members:
        t = members[0]
        t.members = [t]
        t.segments = [(t.first, t.last)]
        return t
    members = sorted(members, key=lambda t: t.first)
    n = sum(m.detections for m in members)
    near = sum(m.detections for m in members if m.side == NEAR)
    descs = [(m.descriptor, m.detections) for m in members if m.descriptor is not None]
    votes: dict[int, float] = defaultdict(float)
    window_sides: dict[tuple[int, int], int] = defaultdict(int)
    for m in members:
        for k, v in m.votes.items():
            votes[k] += v
        for key, c in m.window_sides.items():
            window_sides[key] += c
    longest = max(members, key=lambda m: m.detections)
    in_windows = [m.window for m in members if m.window >= 0]
    chain = Track(
        track_id=members[0].track_id,
        first=members[0].first,
        last=max(m.last for m in members),
        detections=n,
        side=NEAR if near * 2 >= n else FAR,
        window=min(in_windows) if in_windows else -1,
        team=members[0].team,
        descriptor=_mean([d for d, _ in descs], [float(w) for _, w in descs]),
        votes=dict(votes),
        height_px=float(sum(m.height_px * m.detections for m in members) / max(n, 1)),
        foot=longest.foot,
        crops=sorted((c for m in members for c in m.crops), key=lambda c: -c.quality),
        referee=sum(m.detections for m in members if m.referee) * 2 > n,
        t0_ms=members[0].t0_ms,
        t1_ms=max(m.t1_ms for m in members),
        first_box=members[0].first_box,
        last_box=members[-1].last_box,
        first_foot=members[0].first_foot,
        last_foot=members[-1].last_foot,
        window_sides=dict(window_sides),
        members=members,
        segments=[(m.first, m.last) for m in members],
        team_share=sum(m.team_share * m.detections for m in members) / max(n, 1),
    )
    settle_number(chain)
    return chain


def chains_of(
    tracks: Sequence[Track],
    params: StitchParams | None = None,
    long: StitchParams | None = LONG,
) -> list[Track]:
    """Tracks → chains (stitched, cut where the number changes), each as one Track: a first
    pass over short gaps, a second (`long`) over the chains it made."""
    out: list[Track] = []
    for chain in stitch(tracks, params):
        for part in split_conflicts(chain):
            out.append(as_chain(part))
    if long is not None:
        linked: list[Track] = []
        for group in stitch(out, long):
            members = [m for c in group for m in (c.members or [c])]
            for part in split_conflicts(members):
                linked.append(as_chain(part))
        out = linked
    out.sort(key=lambda t: t.track_id)
    return out


# A piece without a number takes its chain's number only this close (frames, 10 fps) to a
# piece that read it: further on, a chain of far players has often passed someone on.
PROPAGATE_FRAMES = 100
# Nor to a piece that looks this different from the one that read it (the libero's shirt).
LOOK_APART = 0.9  # as far as the stitching allows (StitchParams.max_look)


def assign_numbers(chains: Sequence[Track]) -> list[Track]:
    """The pieces of the chains given to the people their numbers name, and the chains cut
    there (a chain may hold a swap no reading caught): per team, the pieces that read a
    number claim it, the surest first; a piece seen at the same time as one already holding
    the number is a misread and loses it. A piece without a number takes the number of the
    nearest numbered piece of its chain (in time) unless that person is seen elsewhere
    then. Each chain becomes runs of pieces with one number (or none), as tracks."""
    out: list[Track] = []
    for team in ("us", "them"):
        mine = [c for c in chains if c.team == team and not c.referee]
        held: dict[int, list[tuple[int, int]]] = defaultdict(list)

        def free(n: int, t: Track, held: dict[int, list[tuple[int, int]]] = held) -> bool:
            return not any(s0 <= t.last and t.first <= e0 for s0, e0 in held[n])

        owner: dict[int, int | None] = {}
        claims = [
            m
            for c in mine
            for m in (c.members or [c])
            if m.number is not None and m.team_share >= TEAM_SURE
        ]
        claims.sort(key=lambda m: (-m.number_conf, -m.detections))
        for m in claims:
            n = m.number
            assert n is not None
            if free(n, m):
                held[n].append((m.first, m.last))
                owner[id(m)] = n
            else:
                owner[id(m)] = None
        for c in mine:
            members = c.members or [c]
            numbered = [(k, owner[id(m)]) for k, m in enumerate(members) if owner.get(id(m))]
            for m in members:
                if id(m) in owner:
                    continue

                # The nearest in time (the gap between the two), the earlier one on a tie.
                def nearness(
                    kn: tuple[int, int | None], m: Track = m, members: list[Track] = members
                ) -> tuple[int, bool]:
                    o = members[kn[0]]
                    return max(o.first - m.last, m.first - o.last, 0), o.first > m.first

                options = sorted(numbered, key=nearness)
                owner[id(m)] = None
                own = _likely_number(m)
                if m.team_share < TEAM_SURE:
                    continue  # unsure of its team: no number of either
                for kn in options:
                    n = kn[1]
                    if nearness(kn)[0] > PROPAGATE_FRAMES:
                        break  # too long since the number was read: anyone by now
                    if own is not None and own != n:
                        break  # it reads another number itself, if not often enough
                    src = members[kn[0]]
                    if (
                        m.descriptor is not None
                        and src.descriptor is not None
                        and distance(m.descriptor, src.descriptor) > LOOK_APART
                    ):
                        continue  # another kit (the libero who came on for them)
                    if n is not None and free(n, m):
                        held[n].append((m.first, m.last))
                        owner[id(m)] = n
                        break
            for _, run in itertools.groupby(members, key=lambda m: owner.get(id(m))):
                pieces = list(run)
                piece = as_chain(pieces) if len(pieces) > 1 or pieces[0] is not c else c
                n = owner.get(id(pieces[0]))
                if n is not None:
                    piece.number = n
                    piece.number_conf = max(piece.number_conf, max(m.number_conf for m in pieces))
                else:  # its number (if any) is held by someone seen at the same time
                    piece.number, piece.number_conf = None, 0.0
                out.append(piece)
    out.extend(c for c in chains if c.referee)
    out.sort(key=lambda t: t.track_id)
    return out


# Someone who stands still through the rallies is not playing (a line judge, the bench, a
# coach, people behind the court where no calibration masks them): their feet stay within
# STILL_HEIGHTS box heights of where they stand for 90 % of STILL_MIN_S of rally time.
STILL_HEIGHTS = 0.6
STILL_MIN_S = 20.0


def still(chain: Track, col: Any) -> bool:
    """A chain whose person barely moves during the rallies (see STILL_HEIGHTS)."""
    points: list[tuple[float, float, float]] = []
    for m in chain.members or [chain]:
        data = m.data
        if data is None:
            continue
        for k in range(m.lo, m.hi):
            f = data.frames[k]
            if f < len(col.windows) and col.windows[f] >= 0:
                u1, v1, u2, v2 = data.boxes[k]
                points.append(((u1 + u2) / 2, v2, max(v2 - v1, 1e-6)))
    if len(points) < STILL_MIN_S * 10:
        return False
    arr = np.asarray(points)
    centre = np.median(arr[:, :2], axis=0)
    height = float(np.median(arr[:, 2]))
    spread = np.linalg.norm(arr[:, :2] - centre, axis=1) / height
    return float(np.percentile(spread, 90)) < STILL_HEIGHTS


def in_play(chain: Track) -> bool:
    """Seen during the rallies (or carrying a number): a player, not someone crossing the
    court between two rallies."""
    return (
        chain.referee or chain.number is not None or chain.rally_detections >= MIN_RALLY_DETECTIONS
    )


# --- who is missing -------------------------------------------------------------------------

ABSORB_GAP_MS = 15_000


def _intervals(c: Cluster) -> list[tuple[int, int]]:
    return sorted((m.t0_ms, m.t1_ms) for t in c.tracks for m in (t.members or [t]))


def _gap_ms(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> int | None:
    """The shortest time between two sets of intervals; None when they overlap."""
    best: int | None = None
    starts = [s for s, _ in b]
    for s0, e0 in a:
        k = bisect.bisect_left(starts, s0)
        for j in (k - 1, k):
            if 0 <= j < len(b):
                s1, e1 = b[j]
                if s0 <= e1 and s1 <= e0:
                    return None
                gap = s1 - e0 if s1 > e0 else s0 - e1
                best = gap if best is None else min(best, gap)
        if k < len(b) and b[k][0] <= e0:
            return None
    return best


def absorb(clusters: list[Cluster], max_gap_ms: int | None = None) -> int:
    """A card without a number joins the numbered card of its team that is missing while it
    is seen and was seen just before or after it (within `max_gap_ms`): the one teammate
    it can be. Only when no other numbered card comes close (half the gap or less) and the
    two look alike. Returns how many cards were joined."""
    from wieplay.ml.players import _cluster_number

    max_gap_ms = ABSORB_GAP_MS if max_gap_ms is None else max_gap_ms
    joined = 0
    for team in ("us", "them"):
        anchors = [c for c in clusters if c.team == team and not c.referee and c.number is not None]
        spans = {id(a): _intervals(a) for a in anchors}
        rest = [c for c in clusters if c.team == team and not c.referee and c.number is None]
        rest.sort(key=lambda c: -c.detections)
        for u in rest:
            mine = _intervals(u)
            options = []
            for a in anchors:
                gap = _gap_ms(mine, spans[id(a)])
                if gap is not None and gap <= max_gap_ms:
                    options.append((gap, a))
            if not options:
                continue
            options.sort(key=lambda o: o[0])
            gap, best = options[0]
            if len(options) > 1 and options[1][0] < 2 * gap:
                continue
            da = _mean_look(best)
            du = _mean_look(u)
            if da is not None and du is not None and distance(da, du) > StitchParams().max_look:
                continue
            best.tracks = sorted([*best.tracks, *u.tracks], key=lambda t: t.first)
            spans[id(best)] = _intervals(best)
            _cluster_number(best)
            clusters.remove(u)
            joined += 1
    clusters.sort(key=lambda c: (c.referee, c.team != "us", -c.detections))
    for i, c in enumerate(clusters):
        c.key = i + 1
    return joined


def _mean_look(c: Cluster) -> np.ndarray[Any, Any] | None:
    items = [(t.descriptor, t.detections) for t in c.tracks if t.descriptor is not None]
    return _mean([d for d, _ in items], [float(n) for _, n in items])


# --- confidence -----------------------------------------------------------------------------


def confidence(c: Cluster, fps: float) -> float:
    """How sure WiePlay is that a card is one whole person: its number read often and only
    that one, and its time on court. 0..1; below UNCERTAIN the card asks to be checked."""
    if c.referee:
        return 1.0
    votes: dict[int, float] = defaultdict(float)
    for t in c.tracks:
        for k, v in t.votes.items():
            votes[k] += v
    votes = defaultdict(float, fold_partials(dict(votes)))
    total = sum(votes.values())
    top = votes.get(c.number, 0.0) if c.number is not None else 0.0
    seconds = sum(t.detections for t in c.tracks) / max(fps, 1e-6)
    on_court = min(1.0, seconds / 120)
    if c.number is None:
        # Without a number nothing ties the pieces together but the tracker and the court:
        # always to be checked (the longer, the likelier one person).
        return round(0.2 + 0.2 * on_court, 3)
    # A number read often enough for the time on the card: at least once every two minutes.
    support = min(1.0, top / max(3.0, seconds / 120))
    score = 0.15 + 0.6 * c.number_conf * support + 0.25 * on_court
    if total >= 1.0 and top / total < 0.8:
        score *= top / total  # numbers disagree: two people on one card
    return round(min(1.0, max(0.0, score)), 3)


def set_confidence(clusters: Sequence[Cluster], fps: float) -> None:
    """Each card's confidence; a card suggesting the number of a bigger card of its team
    (seen at the same time, or the two would be one card) is to be checked whatever its
    readings say: at least one of the two is wrong."""
    held: set[tuple[str, int]] = set()
    for c in sorted(clusters, key=lambda c: -c.detections):
        c.confidence = confidence(c, fps)
        if c.referee or c.number is None:
            continue
        if (c.team, c.number) in held:
            c.confidence = round(min(c.confidence, UNCERTAIN - 0.01) * 0.6, 3)
        held.add((c.team, c.number))
