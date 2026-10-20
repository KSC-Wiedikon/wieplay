"""What a team's earlier games say about its next one (PLAN §10.5b, team memory): its usual
service orders and starting rotations, and the shirt numbers it plays with. Plain Python: the
API learns the prior from the team's sets and answers (wieplay/core/team_memory.py) and the
lineup decode, the serves' players and the players job use it where the sheet is missing.

**Service order.** A set's starting six in zones I-VI is also its service order: zone I serves
first, then whoever stood in zone II, and so on (the team rotates on every side-out it wins).
Teams keep their order from set to set and game to game: setter, outside, middle, opposite,
outside, middle, with the people in a role changing more often than the order. The prior holds
the orders seen, each turned to start at the setter where the roster names one (else turned to
fit the others best), the numbers per place of the order, and where the setter stood at the
first serve (the starting rotation, which a coach picks per set).

**Who serves.** In a set where we have served after `r` side-outs won, zone I holds the
starting lineup's place r mod 6 + 1: with an order O (the setter first) and the place
`offset` of O that stood in zone I at the first serve, our server is O[(offset + r) mod 6]
(`server`). The posterior over the offset weighs the prior's starting rotations and every
observation of a server (a coach's lineup or setter zone, a shirt number read on the server,
a serve a coach named) with a reading's reliability; each place holds the numbers the prior
saw there or someone else (OTHER_PLACE), updated by what was read at that place. Per rally
it gives our likely server and how sure that is.

Shirt numbers only: nothing here names a person or looks at a picture.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

Lineup6 = tuple[int | None, ...]

# An observed server is the number read (or named) right this often; otherwise any number.
OBSERVED_RIGHT = 0.85
# A coach's lineup or setter zone for a set: as good as certain.
COACH_RIGHT = 0.99
# The prior's weight on an order nobody played in full (the per-place consensus), relative to
# the mean of the orders seen.
CONSENSUS_WEIGHT = 0.5
# Smoothing of the starting rotations (a coach picks one per set; any of the six can come).
ZONE_SMOOTHING = 0.5
# Numbers a wrong reading may give (a team's roster is about this big).
WRONG_NUMBERS = 18
MAX_ORDERS = 8
# A place of the usual order holds someone the prior has not seen there this often (a
# substitute, another starter).
OTHER_PLACE = 0.2


@dataclass(frozen=True)
class SetStart:
    """A set's starting six (zones I-VI at the first serve), our setter's number in it when
    known, and how much the set counts (an older season's set counts less)."""

    lineup: Lineup6
    setter: int | None = None
    weight: float = 1.0

    def complete(self) -> bool:
        return len(self.lineup) == 6 and all(n is not None for n in self.lineup)


def turned(lineup: Sequence[int | None], start: int) -> tuple[int | None, ...]:
    """The cyclic order starting at index `start`."""
    k = len(lineup)
    return tuple(lineup[(start + i) % k] for i in range(k))


def setter_index(lineup: Sequence[int | None], setter: int | None) -> int | None:
    if setter is None:
        return None
    found = [i for i, n in enumerate(lineup) if n == setter]
    return found[0] if len(found) == 1 else None


@dataclass
class OrderPrior:
    """A team's service orders, each starting at the setter's place (index 0)."""

    orders: list[tuple[tuple[int, ...], float]] = field(default_factory=list)
    places: list[dict[int, float]] = field(default_factory=lambda: [{} for _ in range(6)])
    # The setter's zone (1-6) at the first serve, weighted.
    start_zones: dict[int, float] = field(default_factory=dict)
    setters: dict[int, float] = field(default_factory=dict)
    sets: float = 0.0

    @property
    def empty(self) -> bool:
        return not self.orders

    def consensus(self) -> tuple[int, ...] | None:
        """The likeliest number per place, each number once (places by their surest number)."""
        if self.empty:
            return None
        chosen: dict[int, int] = {}
        used: set[int] = set()
        options = sorted(
            ((w, place, n) for place, ws in enumerate(self.places) for n, w in ws.items()),
            reverse=True,
        )
        for _, place, n in options:
            if place in chosen or n in used:
                continue
            chosen[place] = n
            used.add(n)
        if len(chosen) < 6:
            return None
        return tuple(chosen[p] for p in range(6))

    def candidates(self) -> list[tuple[tuple[int, ...], float]]:
        """The orders to weigh: those seen, and the consensus (normalised weights)."""
        out = dict(self.orders)
        best = self.consensus()
        if best is not None and best not in out and out:
            out[best] = CONSENSUS_WEIGHT * sum(out.values()) / len(out)
        total = sum(out.values())
        ranked = sorted(out.items(), key=lambda kv: -kv[1])[:MAX_ORDERS]
        return [(o, w / total) for o, w in ranked] if total > 0 else []

    def zone_prior(self, zone: int) -> float:
        total = sum(self.start_zones.values()) + 6 * ZONE_SMOOTHING
        return (self.start_zones.get(zone, 0.0) + ZONE_SMOOTHING) / total

    def number_share(self, number: int) -> float:
        """How often the number is in the starting six (0..1 of the sets)."""
        if self.sets <= 0:
            return 0.0
        return min(1.0, sum(ws.get(number, 0.0) for ws in self.places) / self.sets)

    @classmethod
    def from_json(cls, data: Mapping[str, Any] | None) -> OrderPrior:
        """The prior as `as_json` wrote it (the orders as weighed there)."""
        if not data:
            return cls()
        prior = cls()
        prior.sets = float(data.get("sets") or 0.0)
        prior.orders = [
            (tuple(int(n) for n in o["order"]), float(o["weight"]))
            for o in data.get("orders") or []
            if len(o.get("order") or []) == 6
        ]
        places = data.get("places") or []
        prior.places = [
            {int(x["number"]): float(x["weight"]) for x in (places[k] if k < len(places) else [])}
            for k in range(6)
        ]
        prior.start_zones = {int(z): float(w) for z, w in (data.get("start_zones") or {}).items()}
        prior.setters = {int(n): float(w) for n, w in (data.get("setters") or {}).items()}
        return prior

    def as_json(self) -> dict[str, object]:
        return {
            "sets": round(self.sets, 2),
            "orders": [{"order": list(o), "weight": round(w, 3)} for o, w in self.candidates()],
            "places": [
                [
                    {"number": n, "weight": round(w, 3)}
                    for n, w in sorted(ws.items(), key=lambda x: -x[1])
                ]
                for ws in self.places
            ],
            "start_zones": {str(z): round(w, 3) for z, w in sorted(self.start_zones.items())},
            "setters": {str(n): round(w, 3) for n, w in sorted(self.setters.items())},
        }


def _fit(order: Sequence[int | None], places: Sequence[Mapping[int, float]]) -> tuple[int, float]:
    """The turn of a lineup without a known setter that agrees best with the places learnt."""
    best = (0, -1.0)
    for start in range(6):
        t = turned(order, start)
        score = sum(places[i].get(n, 0.0) for i, n in enumerate(t) if n is not None)
        if score > best[1]:
            best = (start, score)
    return best


def learn(starts: Iterable[SetStart]) -> OrderPrior:
    """The prior from a team's set starts: those with a known setter first (they fix where the
    order starts), then the others turned to fit them. Incomplete lineups count for the places
    they fill only."""
    prior = OrderPrior()
    pending: list[SetStart] = []
    counts: Counter[tuple[int, ...]] = Counter()
    weights: dict[tuple[int, ...], float] = defaultdict(float)

    def add(order: tuple[int | None, ...], weight: float, start_zone: int | None) -> None:
        for i, n in enumerate(order):
            if n is not None:
                prior.places[i][n] = prior.places[i].get(n, 0.0) + weight
        if all(n is not None for n in order):
            full = tuple(int(n) for n in order if n is not None)
            counts[full] += 1
            weights[full] += weight
        if start_zone is not None:
            prior.start_zones[start_zone] = prior.start_zones.get(start_zone, 0.0) + weight
        if order[0] is not None:
            prior.setters[int(order[0])] = prior.setters.get(int(order[0]), 0.0) + weight
        prior.sets += weight

    for s in starts:
        if len(s.lineup) != 6 or s.weight <= 0:
            continue
        i = setter_index(s.lineup, s.setter)
        if i is None:
            pending.append(s)
            continue
        add(turned(s.lineup, i), s.weight, i + 1)
    for s in pending:
        if prior.sets <= 0:
            break  # nothing to turn them to
        start, score = _fit(s.lineup, prior.places)
        if score <= 0:
            continue
        add(turned(s.lineup, start), s.weight, start + 1)
    prior.orders = sorted(weights.items(), key=lambda kv: -kv[1])
    return prior


# --- who serves -----------------------------------------------------------------------------------


def server(order: Sequence[int], offset: int, rotation: int) -> int:
    """Our server after `rotation` side-outs won, when the order's place `offset` stood in
    zone I at the set's first serve (offset = (1 - the setter's zone) mod 6)."""
    return int(order[(offset + rotation) % 6])


def offset_of_zone(setter_zone: int) -> int:
    """The order's place in zone I when the setter (place 0) stands in `setter_zone`."""
    return (1 - setter_zone) % 6


@dataclass(frozen=True)
class Seen:
    """A server observed: our rotation then (side-outs won before it), the number, and how
    often such an observation is right."""

    rotation: int
    number: int
    right: float = OBSERVED_RIGHT


@dataclass
class SetPosterior:
    """Per offset (which place of the usual order stood in zone I at the first serve) its
    probability, and per place the numbers it may hold given what was seen there."""

    offsets: list[float]
    places: list[list[dict[int, float]]]  # [offset][place] -> number -> probability

    def servers(self, rotation: int) -> dict[int, float]:
        out: dict[int, float] = defaultdict(float)
        for offset, p in enumerate(self.offsets):
            if p <= 0:
                continue
            for n, q in self.places[offset][(offset + rotation) % 6].items():
                out[n] += p * q
        return dict(out)

    def best(self, rotation: int) -> tuple[int, float] | None:
        found = self.servers(rotation)
        if not found:
            return None
        n = max(found, key=lambda k: found[k])
        return n, found[n]

    def lineup(self) -> tuple[Lineup6, float] | None:
        """The likeliest starting six (zones I-VI) and the probability of its rotation."""
        if not self.offsets or not any(self.offsets):
            return None
        offset = max(range(6), key=lambda o: self.offsets[o])
        six: list[int | None] = []
        for zone in range(6):
            dist = self.places[offset][(offset + zone) % 6]
            six.append(max(dist, key=lambda k: dist[k]) if dist else None)
        return tuple(six), self.offsets[offset]


def _place_dists(prior: OrderPrior) -> list[dict[int, float]]:
    out = []
    for ws in prior.places:
        total = sum(ws.values())
        out.append({n: (1 - OTHER_PLACE) * w / total for n, w in ws.items()} if total else {})
    return out


def _place_posterior(
    dist: Mapping[int, float], seen: Sequence[Seen]
) -> tuple[float, dict[int, float]]:
    """One place: the likelihood of what was seen there, and its numbers afterwards. Mass not
    on a known number is someone else (any of WRONG_NUMBERS numbers)."""
    other = 1.0 / WRONG_NUMBERS
    cands = dict(dist)
    for s in seen:
        cands.setdefault(s.number, 0.0)
    rest = max(0.0, 1.0 - sum(cands.values()))
    post: dict[int, float] = {}
    for n in cands:
        mass = cands[n] + rest * other  # a known number, or someone else wearing it
        lik = 1.0
        for s in seen:
            lik *= s.right if s.number == n else (1 - s.right) * other
        post[n] = mass * lik
    unseen = rest * max(0.0, 1.0 - other * len(cands))  # someone none of the readings named
    lik_unseen = 1.0
    for s in seen:
        lik_unseen *= (1 - s.right) * other
    total = sum(post.values()) + unseen * lik_unseen
    if total <= 0:
        return 0.0, {}
    return total, {n: v / total for n, v in post.items() if v > 0}


def posterior(
    prior: OrderPrior,
    seen: Sequence[Seen] = (),
    *,
    setter_zone: int | None = None,
    lineup: Sequence[int | None] | None = None,
) -> SetPosterior:
    """Our order and starting rotation in one set, from the prior's places and starting
    rotations, a coach's lineup or setter zone for the set, and the servers observed (each
    place may hold someone the prior has not seen there: OTHER_PLACE)."""
    if lineup is not None and len(lineup) == 6 and any(n is not None for n in lineup):
        # The coach's six for this set: certain at the start, substitutions aside.
        dists: list[dict[int, float]] = [
            {int(n): 1 - OTHER_PLACE / 4} if n is not None else {} for n in lineup
        ]
        offsets = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0]
        places: list[list[dict[int, float]]] = [[{} for _ in range(6)] for _ in range(6)]
        places[0] = [
            _place_posterior(d, [x for x in seen if x.rotation % 6 == k])[1]
            for k, d in enumerate(dists)
        ]
        return SetPosterior(offsets, places)
    if prior.empty and not seen and setter_zone is None:
        return SetPosterior([], [])
    dists = _place_dists(prior)
    logs: list[float] = []
    places_by_offset: list[list[dict[int, float]]] = []
    for offset in range(6):
        zone = (-offset) % 6 + 1  # where place 0 (the setter) stood
        p = math.log(prior.zone_prior(zone))
        if setter_zone is not None:
            p += math.log(COACH_RIGHT if zone == setter_zone else (1 - COACH_RIGHT) / 5)
        mine = []
        for k in range(6):
            at = [x for x in seen if (offset + x.rotation) % 6 == k]
            lik, post = _place_posterior(dists[k], at)
            p += math.log(lik) if lik > 0 else -1e9
            mine.append(post)
        logs.append(p)
        places_by_offset.append(mine)
    top = max(logs)
    weights = [math.exp(v - top) for v in logs]
    total = sum(weights)
    return SetPosterior([w / total for w in weights], places_by_offset)


# --- numbers --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class NumberFacts:
    """What the team's earlier games say about one of its shirt numbers."""

    number: int
    games: int = 0  # games it was named in (a sheet's lineup or substitution, a coach's answer)
    answers: int = 0  # a coach named a card, a tag or a serve with it
    sets: float = 0.0  # sets in the starting six or subbed in
    libero: bool = False


def number_prior(facts: Iterable[NumberFacts], games: int) -> dict[int, float]:
    """Per number how likely a person of ours on the video wears it, from how many of the
    team's games it was named in (Laplace-smoothed; 0..1)."""
    out: dict[int, float] = {}
    for f in facts:
        out[f.number] = (f.games + 0.5) / (games + 1.0) if games > 0 else 0.5
    return out


def reading_confidence(read_conf: float, prior: float, roster_size: int = WRONG_NUMBERS) -> float:
    """How sure a card's number is, from the reader's confidence and how often the team plays
    that number: a misread lands on any of `roster_size` numbers, a team's regular is read
    more often than a rare one (Bayes on "the reading is right")."""
    read_conf = min(max(read_conf, 0.0), 1.0)
    prior = min(max(prior, 1e-3), 1.0)
    right = read_conf * prior
    wrong = (1.0 - read_conf) * (1.0 / roster_size)
    return right / (right + wrong) if right + wrong > 0 else 0.0


# A card of ours is offered as recognised (named in one step with "Accept all") when its read
# number is this sure with the team's prior and the reader itself read it at least this well.
MEMORY_SURE = 0.95
MEMORY_MIN_READ = 0.6


def recognised(read_conf: float | None, prior: float | None) -> bool:
    """A card's read number the team's memory backs enough to offer it as named."""
    if read_conf is None or prior is None or read_conf < MEMORY_MIN_READ:
        return False
    return reading_confidence(read_conf, prior) >= MEMORY_SURE
