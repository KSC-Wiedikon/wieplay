"""Who served each rally, who won it and the score (PLAN §10.3). Plain Python: the API runs it.

Volleyball is rally scoring: **the team that wins a rally serves the next one.** So the score
follows from who served each rally, plus the rules and the official result:

1. **Serve evidence** per rally: P(the server stood at the near end line), from the ball at the
   serve (wieplay/ml/serve.py, computed on the worker and stored as a track per video).
2. **Court side → team** per set: teams change sides after every set; in the deciding set when
   the leading team reaches 8. Who served first alternates in sets 1-4; the deciding set has a
   new toss. A coach's "we serve first" / "we start on the near side" for a set wins.
3. **Winner of rally i = serving team of rally i+1**; the last rally of a set goes to whoever
   reaches the set-winning score.
4. **Alignment with the official set score**: a dynamic programme over (score, next server)
   finds the cheapest consistent sequence: a rally can be no point (a replay, a proposal that
   is not a rally), points can be missing (a rally the finder missed), a serve can be misread.
   Each choice has a cost; a coach's serve or winner costs a lot to contradict.
5. Max-marginals (the cheapest path with the other answer) give each rally's confidence; a
   rally is flagged ("check") when the path contradicts its evidence or a coach, a point is
   missing next to it, it looks like no point, or the answer is unsure.
6. **The scoreboard**, when it was read (wieplay/ml/board.py): +1 for one team between two
   rallies names the winner of the first, no change says it was no point. Contradicting it
   costs more than a misread serve and a missed rally together (the board wins over the serve
   evidence), less than a coach's fact; a path that still disagrees is flagged ("board").
7. **The club scoreboard's log** (wieplay/ml/scorelog.py, PLAN §10.10), in the sets where its
   final is the official one: the point pressed after a rally names its winner, a rally with no
   press between two that have one is no point, presses with no rally of their own are points
   the finder missed (how many and for whom). The strongest evidence after a coach's: more
   than the board, less than a coach's fact; a path that disagrees is flagged ("log").

Sets: a coach's sets when there are any, else proposed from the long breaks and the official
number of points per set (`propose_sets`).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from typing import Literal

Side = Literal["us", "them"]
Court = Literal["near", "far"]
Note = Literal[
    "coach", "log", "board", "missing_before", "missing_after", "extra", "serve", "unsure"
]
BoardSays = Literal["us", "them", "none"]

SIDES: tuple[Side, Side] = ("us", "them")
COURTS: tuple[Court, Court] = ("near", "far")

# Costs (negative log-probabilities, roughly).
C_FACT = 12.0  # contradicting a coach's serve or winner
C_MISS = 3.0  # a point without a rally (the finder missed it), per point
MISS_TIME_S = 15.0  # a missed rally makes a gap about this much longer than the usual one
MISS_END = 1.5  # a missed last rally of a set (no gap to show it) costs this much more
C_UNFINISHED = 4.0  # without an official score: the set does not end on a set point
EXTRA_COACH = 7.0  # a rally a person stands behind is no point (a replay)
P_CLIP = 0.03  # serve evidence is never trusted beyond 97 %
MAX_MISS = 2  # missed points between two rallies
MISS_MIN_GAP_S = 10.0  # a shorter gap holds no missed rally (a rally cut in two is "extra")
BEAM = 14.0
UNSURE = 0.7  # a winner less sure than this is flagged
SERVE_DISAGREES = 0.25  # the path's server has at most this probability from the evidence
C_BOARD = 9.0  # contradicting the scoreboard (times how sure its reading is)
C_LOG = 11.0  # contradicting the club scoreboard's log (times how sure the alignment is)
C_LOG_MISS = 3.0  # per point a gap's missed points differ from the log's
MAX_MISS_LOG = 4  # missed points between two rallies when the log saw them


def other(side: Side) -> Side:
    return "them" if side == "us" else "us"


def flip(court: Court) -> Court:
    return "far" if court == "near" else "near"


@dataclass(frozen=True)
class RallyObs:
    """One rally as the scoring sees it. `p_near`: P(the server stood at the near end line),
    None without evidence. `serve_fact` / `won_fact`: a coach's (serve tag, serve called in
    review, the winner). `proposal`: the finder's confidence for a proposal nobody answered;
    None for a rally a person stands behind."""

    key: str
    start_ms: int
    end_ms: int
    p_near: float | None = None
    serve_fact: Side | None = None
    won_fact: Side | None = None
    proposal: float | None = None
    # In the gap before this rally: seconds with the ball in play, whistles in its middle
    # (a rally the finder missed shows there).
    gap_ball_s: float = 0.0
    gap_whistles: int = 0
    # The scoreboard about this rally: who won it, or "none" (no point); how sure (0..1).
    board: BoardSays | None = None
    board_p: float = 0.0
    # The club scoreboard's log (PLAN §10.10): the same, from the point pressed after it; and
    # the points (us, them) it saw right before this rally without one (None: it says nothing
    # about this gap).
    log: BoardSays | None = None
    log_p: float = 0.0
    log_missed: tuple[int, int] | None = None


@dataclass(frozen=True)
class SetRules:
    target: int = 25
    switch_at: int | None = None  # deciding set: sides change when the leader reaches this

    def over(self, a: int, b: int) -> bool:
        return max(a, b) >= self.target and abs(a - b) >= 2


DECIDING = SetRules(target=15, switch_at=8)


@dataclass
class SetIn:
    no: int
    rallies: list[RallyObs]
    rules: SetRules = field(default_factory=SetRules)
    start_us: int = 0
    start_them: int = 0
    official: tuple[int, int] | None = None
    first_serve: Side | None = None  # a coach's
    our_side: Court | None = None  # a coach's, at the start of the set
    deciding: bool = False
    log_missed_after: tuple[int, int] | None = None  # the log's points after the last rally


@dataclass(frozen=True)
class RallyOut:
    key: str
    point: bool  # False: looks like no point
    serving: Side | None
    won_by: Side | None
    score_us: int
    score_them: int
    confidence: float  # of won_by (or of "no point")
    serve_p: float | None  # P(this server) from the serve evidence alone
    our_side: Court
    note: Note | None
    missed: tuple[int, int] = (0, 0)  # points (us, them) without a rally right before it


@dataclass
class SetOut:
    no: int
    cost: float
    first_serve: Side
    our_side: Court
    final: tuple[int, int]
    rallies: list[RallyOut]
    missed: int
    extra: int
    feasible: bool = True
    missed_after: tuple[int, int] = (0, 0)  # points without a rally after the last one

    @property
    def flagged(self) -> int:
        return sum(1 for r in self.rallies if r.note is not None)


# --- the dynamic programme --------------------------------------------------------------------

# A state is (us, them, next server: 0 us / 1 them), packed into one int.


def _pack(a: int, b: int, s: int) -> int:
    return (a * 128 + b) * 2 + s


def _unpack(k: int) -> tuple[int, int, int]:
    ab, s = divmod(k, 2)
    a, b = divmod(ab, 128)
    return a, b, s


def _side_of(s: int) -> Side:
    return "us" if s == 0 else "them"


def _p_extra(r: RallyObs) -> float:
    if r.proposal is None or r.won_fact is not None or r.serve_fact is not None:
        return math.exp(-EXTRA_COACH)  # a person says it is a point
    return min(0.45, max(0.02, 0.5 * (1.0 - r.proposal)))


EXTRA_MIN = 1.0  # the least an extra rally costs more than a point (for the pruning bound)


@dataclass(frozen=True)
class _Rally:
    """A rally's costs, precomputed: no point; a point served by us / them with our side
    near / far; contradicting the coach."""

    extra: float
    point: tuple[tuple[float, float], tuple[float, float]]  # [side near=0/far=1][server]
    serve_fact: int  # -1 none, else the server a coach saw
    won_fact: int
    # The scoreboard and the log: the cost of a point for us / for them (no point: in extra).
    won: tuple[float, float] = (0.0, 0.0)


def _prepare(r: RallyObs) -> _Rally:
    p_extra = _p_extra(r)
    base = -math.log(1.0 - p_extra)
    rows = []
    for near_is_us in (True, False):
        if r.p_near is None:
            rows.append((base, base))
            continue
        p = min(1 - P_CLIP, max(P_CLIP, r.p_near))
        p_us = p if near_is_us else 1.0 - p
        rows.append((base - math.log(2.0 * p_us), base - math.log(2.0 * (1.0 - p_us))))
    fact = {"us": 0, "them": 1, None: -1}
    costs = [0.0, 0.0, 0.0]  # a point for us, for them, no point
    # A coach's winner silences the log on that rally: the log ranks below a coach's fact.
    log_p = 0.0 if r.won_fact is not None else r.log_p
    for says, p, weight in ((r.board, r.board_p, C_BOARD), (r.log, log_p, C_LOG)):
        if says is None or p <= 0:
            continue
        for k, answer in enumerate(("us", "them", "none")):
            if says != answer:
                costs[k] += weight * p
    return _Rally(
        -math.log(p_extra) + costs[2],
        (rows[0], rows[1]),
        fact[r.serve_fact],
        fact[r.won_fact],
        (costs[0], costs[1]),
    )


Layer = dict[int, float]
Back = dict[int, tuple[int, int]]


class _Structure:
    """Which states follow which (cached): depends on the score cap and the set's rules only,
    so the four hypotheses of a set share it."""

    def __init__(self, cap: tuple[int, int], rules: SetRules) -> None:
        self.cap = cap
        self.rules = rules
        self._miss: dict[tuple[int, int], list[tuple[int, int]]] = {}
        self._step: dict[int, tuple[bool, int, int, int, int, int]] = {}

    def miss(self, state: int, k_max: int) -> list[tuple[int, int]]:
        """(next state, missed points) for 0..k_max points without a rally."""
        key = (state, k_max)
        found = self._miss.get(key)
        if found is not None:
            return found
        a, b, _ = _unpack(state)
        out = [(state, 0)]
        over = self.rules.over
        ca, cb = self.cap
        if not over(a, b):
            for k in range(1, k_max + 1):
                for du in range(k + 1):
                    dt = k - du
                    na, nb = a + du, b + dt
                    if na > ca or nb > cb:
                        continue
                    # The score before the last missed point must still be playing.
                    if du and not over(na - 1, nb):
                        out.append((_pack(na, nb, 0), k))
                    if dt and not over(na, nb - 1):
                        out.append((_pack(na, nb, 1), k))
        self._miss[key] = out
        return out

    def step(self, state: int) -> tuple[bool, int, int, int, int, int]:
        """(set over, state after a point for us or -1, after a point for them or -1, sides
        switched, next server, points so far)."""
        found = self._step.get(state)
        if found is not None:
            return found
        a, b, s = _unpack(state)
        rules = self.rules
        over = rules.over(a, b)
        up = _pack(a + 1, b, 0) if a + 1 <= self.cap[0] else -1
        tp = _pack(a, b + 1, 1) if b + 1 <= self.cap[1] else -1
        switched = int(rules.switch_at is not None and max(a, b) >= rules.switch_at)
        out = (over, up, tp, switched, s, a + b)
        self._step[state] = out
        return out


@dataclass
class _Set:
    """One set under one hypothesis (our side at the start, the first server)."""

    st: SetIn
    side0: Court
    first: Side
    struct: _Structure
    rallies: list[_Rally]
    k_max: list[int]  # missed points allowed before each rally
    miss_costs: list[list[float]]  # cost of k missed points before each rally (and at the end)
    # The log's missed points (us, them) per gap, when it saw them: who won them.
    miss_pref: list[tuple[int, int] | None] = field(default_factory=list)

    def terminal(self, state: int) -> float:
        a, b, _ = _unpack(state)
        if self.st.official is not None:
            return 0.0 if (a, b) == self.st.official else math.inf
        return 0.0 if self.st.rules.over(a, b) else C_UNFINISHED

    def side_index(self, state: int) -> int:
        """0 when our side is near during a point at this score, else 1."""
        near = self.side0 == "near"
        if self.struct.step(state)[3]:
            near = not near
        return 0 if near else 1

    def rally_edges(self, state: int, r: _Rally) -> list[tuple[int, float, int]]:
        over, up, tp, switched, s, _ = self.struct.step(state)
        out = [(state, r.extra, -1)]
        if over:
            return out
        side = (0 if self.side0 == "near" else 1) ^ switched
        base = r.point[side][s] + (C_FACT if r.serve_fact >= 0 and r.serve_fact != s else 0.0)
        if up >= 0:
            out.append((up, base + (C_FACT if r.won_fact == 1 else 0.0) + r.won[0], s * 2))
        if tp >= 0:
            out.append((tp, base + (C_FACT if r.won_fact == 0 else 0.0) + r.won[1], s * 2 + 1))
        return out

    def miss_edges(self, state: int, i: int) -> list[tuple[int, float, int]]:
        costs, pref = self.miss_costs[i], self.miss_pref[i]
        return [
            (nxt, costs[k] + (_pref_cost(pref, state, nxt) if pref else 0.0), k)
            for nxt, k in self.struct.miss(state, self.k_max[i])
        ]


def _pref_cost(pref: tuple[int, int], state: int, nxt: int) -> float:
    """Missed points won by other teams than the log says: per point off."""
    a0, b0, _ = _unpack(state)
    a1, b1, _ = _unpack(nxt)
    return C_LOG_MISS * 0.5 * (abs(a1 - a0 - pref[0]) + abs(b1 - b0 - pref[1]))


def _keep(layer: Layer, s: _Set, remaining: int) -> Layer:
    """Beam pruning, with a lower bound of what reaching the official score still costs."""
    if not layer:
        return layer
    official = s.st.official
    if official is None:
        best = min(layer.values())
        return {k: v for k, v in layer.items() if v <= best + BEAM}
    total = official[0] + official[1]
    step = s.struct.step
    scored = {}
    for k, v in layer.items():
        need = total - step(k)[5]
        scored[k] = v + (
            C_MISS * (need - remaining) if need > remaining else EXTRA_MIN * (remaining - need)
        )
    best = min(scored.values())
    return {k: layer[k] for k, v in scored.items() if v <= best + BEAM}


def _forward(s: _Set, backs: list[Back] | None) -> list[Layer]:
    n = len(s.rallies)
    inf = math.inf
    start = _pack(s.st.start_us, s.st.start_them, 0 if s.first == "us" else 1)
    layers: list[Layer] = [{start: 0.0}]
    step, miss = s.struct.step, s.struct.miss
    near0 = 0 if s.side0 == "near" else 1
    for i in range(n + 1):
        g: Layer = {}
        bm: Back = {}
        costs, k_max, pref = s.miss_costs[i], s.k_max[i], s.miss_pref[i]
        for state, cost in layers[-1].items():
            for nxt, k in miss(state, k_max):
                total = cost + costs[k] + (_pref_cost(pref, state, nxt) if pref else 0.0)
                if total < g.get(nxt, inf):
                    g[nxt] = total
                    bm[nxt] = (state, k)
        if backs is not None:
            backs.append(bm)
        if i == n:  # after the last rally: missed points at the end of the set
            layers.append(g)
            break
        g = _keep(g, s, n - i)
        r = s.rallies[i]
        f: Layer = {}
        br: Back = {}
        for state, cost in g.items():
            over, up, tp, switched, srv, _ = step(state)
            total = cost + r.extra
            if total < f.get(state, inf):
                f[state] = total
                br[state] = (state, -1)
            if over:
                continue
            base = cost + r.point[near0 ^ switched][srv]
            if r.serve_fact >= 0 and r.serve_fact != srv:
                base += C_FACT
            if up >= 0:
                total = base + (C_FACT if r.won_fact == 1 else 0.0) + r.won[0]
                if total < f.get(up, inf):
                    f[up] = total
                    br[up] = (state, srv * 2)
            if tp >= 0:
                total = base + (C_FACT if r.won_fact == 0 else 0.0) + r.won[1]
                if total < f.get(tp, inf):
                    f[tp] = total
                    br[tp] = (state, srv * 2 + 1)
        f = _keep(f, s, n - i - 1)
        layers += [g, f]
        if backs is not None:
            backs.append(br)
    return layers


_STRUCTURES: dict[tuple[tuple[int, int], SetRules], _Structure] = {}


def _setup(st: SetIn, side0: Court, first: Side) -> _Set:
    if st.official is not None:
        cap = (max(st.official[0], st.start_us), max(st.official[1], st.start_them))
    else:
        cap = (st.rules.target + 20, st.rules.target + 20)
    key = (cap, st.rules)
    struct = _STRUCTURES.get(key)
    if struct is None:
        if len(_STRUCTURES) > 64:
            _STRUCTURES.clear()
        struct = _STRUCTURES[key] = _Structure(cap, st.rules)
    rallies = st.rallies
    gaps = (
        [60.0]
        + [
            max(0.0, (rallies[i].start_ms - rallies[i - 1].end_ms) / 1000)
            for i in range(1, len(rallies))
        ]
        + [60.0]
    )
    # A missed rally needs time: a gap longer than the set's usual one by a rally and its dead
    # time (none in a short gap, two only in a long one); play or the referee's whistles in
    # the gap make one likelier. Missing the set's last rally shows in no gap at all.
    inner = sorted(gaps[1:-1])
    usual = inner[len(inner) // 2] if inner else 15.0
    k_max = [0 if g < MISS_MIN_GAP_S else (1 if g < usual + 2 * MISS_TIME_S else MAX_MISS)
             for g in gaps]  # fmt: skip
    hints = [(r.gap_ball_s, r.gap_whistles) for r in rallies] + [(0.0, 0)]
    costs = [
        [0.0]
        + [
            max(
                0.5 * k,
                k * C_MISS
                + 0.4 * max(0.0, usual + k * MISS_TIME_S - g)
                - min(2.5, 0.8 * ball)
                - (1.0 if whistles >= 2 else 0.0),
            )
            for k in range(1, MAX_MISS + 1)
        ]
        for g, (ball, whistles) in zip(gaps, hints, strict=True)
    ]
    costs[0] = [0.0] + [k * C_MISS + MISS_END for k in range(1, MAX_MISS + 1)]
    costs[-1] = [0.0] + [k * C_MISS + MISS_END for k in range(1, MAX_MISS + 1)]
    # The log: how many points it saw in a gap (and whose) without a rally of their own.
    told = [r.log_missed for r in rallies] + [st.log_missed_after]
    pref: list[tuple[int, int] | None] = [None] * len(gaps)
    for i, said in enumerate(told):
        if said is None:
            continue
        k_log = min(MAX_MISS_LOG, said[0] + said[1])
        k_max[i] = max(k_max[i], k_log)
        base = costs[i] + [costs[i][-1] + C_MISS * j for j in range(1, k_max[i] - MAX_MISS + 1)]
        costs[i] = [
            0.3 * k if k == k_log else base[k] + C_LOG_MISS * abs(k - k_log)
            for k in range(k_max[i] + 1)
        ]
        pref[i] = said if k_log else None
    return _Set(st, side0, first, struct, [_prepare(r) for r in rallies], k_max, costs, pref)


def set_cost(st: SetIn, side0: Court, first: Side) -> float:
    """The cost of the cheapest consistent sequence (no details): picks the hypothesis."""
    s = _setup(st, side0, first)
    end = _forward(s, None)[-1]
    return min((v + s.terminal(k) for k, v in end.items()), default=math.inf)


def _confidence(costs: dict[int, float], chosen: int) -> float:
    """Softmin share of the chosen answer among the answers' cheapest paths."""
    finite = {k: v for k, v in costs.items() if math.isfinite(v)}
    if chosen not in finite:
        return 0.5
    lo = finite[chosen]
    return 1.0 / sum(math.exp(-(v - lo)) for v in finite.values())


def run_set(st: SetIn, side0: Court, first: Side) -> SetOut:
    """The cheapest consistent sequence of one set, given our side at its start and who
    serves first, with each rally's confidence (max-marginals)."""
    s = _setup(st, side0, first)
    n = len(s.rallies)
    backs: list[Back] = []
    fwd = _forward(s, backs)
    end = fwd[-1]
    finals = {k: v + s.terminal(k) for k, v in end.items()}
    best_state = min(finals, key=lambda k: finals[k]) if finals else None
    if best_state is None or not math.isfinite(finals[best_state]):
        return SetOut(
            st.no, math.inf, first, side0, (st.start_us, st.start_them), [], 0, 0, feasible=False
        )

    # Backward: the cheapest way from each kept state to the end. Layer 2i holds the states
    # before rally i's missed points, 2i+1 those right before rally i, 2n+1 the end.
    bwd: list[Layer] = [{} for _ in fwd]
    bwd[-1] = {k: s.terminal(k) for k in end}
    for li in range(len(fwd) - 2, -1, -1):
        after = bwd[li + 1]
        out: Layer = {}
        for state in fwd[li]:
            edges = (
                s.miss_edges(state, li // 2)
                if li % 2 == 0
                else (s.rally_edges(state, s.rallies[li // 2]))
            )
            best = math.inf
            for nxt, c, _ in edges:
                v = after.get(nxt)
                if v is not None and c + v < best:
                    best = c + v
            out[state] = best
        bwd[li] = out

    # The best path, along the back pointers.
    states = [0] * len(fwd)
    choice = [0] * len(backs)
    state = best_state
    for li in range(len(fwd) - 1, 0, -1):
        states[li] = state
        state, choice[li - 1] = backs[li - 1][state]
    states[0] = state

    rallies_out: list[RallyOut] = []
    extra_total = 0
    for i, r in enumerate(st.rallies):
        g_layer, after = fwd[2 * i + 1], bwd[2 * i + 2]
        per_opt: dict[int, float] = {}
        for state_g, c0 in g_layer.items():
            for nxt, c, opt in s.rally_edges(state_g, s.rallies[i]):
                v = after.get(nxt)
                if v is not None and c0 + c + v < per_opt.get(opt, math.inf):
                    per_opt[opt] = c0 + c + v
        missed = choice[2 * i]
        opt = choice[2 * i + 1]
        a_prev, b_prev, _ = _unpack(states[2 * i])
        a_pre, b_pre, _ = _unpack(states[2 * i + 1])
        gap_points = (a_pre - a_prev, b_pre - b_prev)
        a_after, b_after, _ = _unpack(states[2 * i + 2])
        side_now: Court = "near" if s.side_index(states[2 * i + 1]) == 0 else "far"
        if opt == -1:
            extra_total += 1
            point = min((v for k, v in per_opt.items() if k >= 0), default=math.inf)
            conf = _confidence({-1: per_opt.get(-1, math.inf), 0: point}, -1)
            board_overruled = r.board in ("us", "them") and r.board_p > 0
            log_overruled = r.log in ("us", "them") and r.log_p > 0
            board_overruled = board_overruled and r.log is None  # the log outranks the board
            rallies_out.append(
                RallyOut(
                    r.key,
                    False,
                    None,
                    None,
                    a_after,
                    b_after,
                    round(conf, 3),
                    None,
                    side_now,
                    "log" if log_overruled else "board" if board_overruled else "extra",
                    gap_points,
                )
            )
            continue
        srv, w = divmod(opt, 2)
        by_winner = {
            ww: min((v for k, v in per_opt.items() if k >= 0 and k % 2 == ww), default=math.inf)
            for ww in (0, 1)
        }
        conf = _confidence(by_winner, w)
        serve_p = None
        if r.p_near is not None:
            p_us = r.p_near if side_now == "near" else 1.0 - r.p_near
            serve_p = p_us if srv == 0 else 1.0 - p_us
        serving, won = _side_of(srv), _side_of(w)
        note: Note | None = None
        if (r.serve_fact is not None and r.serve_fact != serving) or (
            r.won_fact is not None and r.won_fact != won
        ):
            note = "coach"
        elif r.log is not None and r.log_p > 0 and r.log != won:
            note = "log"
        elif r.board is not None and r.board_p > 0 and r.board != won and r.log is None:
            note = "board"
        elif missed:
            note = "missing_before"
        elif i == n - 1 and choice[-1]:
            note = "missing_after"
        elif (
            serve_p is not None
            and serve_p < SERVE_DISAGREES
            and r.serve_fact is None
            and r.log is None  # the log names the winners: the serve reading was wrong
        ):
            note = "serve"
        elif conf < UNSURE and r.won_fact is None and r.board is None and r.log is None:
            note = "unsure"
        rallies_out.append(
            RallyOut(
                r.key,
                True,
                serving,
                won,
                a_after,
                b_after,
                round(conf, 3),
                None if serve_p is None else round(serve_p, 3),
                side_now,
                note,
                gap_points,
            )
        )
    fa, fb, _ = _unpack(best_state)
    la, lb, _ = _unpack(states[-2])
    return SetOut(
        no=st.no,
        cost=finals[best_state],
        first_serve=first,
        our_side=side0,
        final=(fa, fb),
        rallies=rallies_out,
        missed=sum(choice[0::2]),
        extra=extra_total,
        missed_after=(fa - la, fb - lb),
    )


# --- the match ----------------------------------------------------------------------------------


@dataclass
class MatchOut:
    sets: list[SetOut]
    # How sure the choice of our side and the first server is (0.5..1; 1 when a coach set
    # them): the cheapest hypothesis's share among all of them.
    sides_confidence: float


def _share(costs: Sequence[float]) -> float:
    finite = [c for c in costs if math.isfinite(c)]
    if not finite:
        return 0.5
    lo = min(finite)
    return 1.0 / sum(math.exp(-(c - lo)) for c in finite)


def _fallback(st: SetIn, side0: Court, first: Side) -> SetOut:
    """When the official score cannot be reached (the set's rallies are far off), score the set
    without it and flag every rally as unsure."""
    loose = replace(st, official=None)
    out = run_set(loose, side0, first)
    out.rallies = [
        replace(r, confidence=min(r.confidence, 0.5), note=r.note or "unsure") for r in out.rallies
    ]
    out.feasible = False
    out.cost += 50.0
    return out


def score_match(sets: Sequence[SetIn]) -> MatchOut:
    """Every set, with our side and the first server chosen for the whole match: sides change
    after every set and the first serve alternates (sets 1-4); the deciding set is on its
    own. A coach's side or first server of a set wins over the rules."""
    costs: dict[tuple[int, Court, Side], float] = {}

    def cost(idx: int, side0: Court, first: Side) -> float:
        key = (idx, side0, first)
        if key not in costs:
            st = sets[idx]
            c = set_cost(st, side0, first)
            if not math.isfinite(c) and st.official is not None:
                c = set_cost(replace(st, official=None), side0, first) + 50.0
            costs[key] = c
        return costs[key]

    def run(idx: int, side0: Court, first: Side) -> SetOut:
        st = sets[idx]
        out = run_set(st, side0, first)
        if not out.feasible and st.official is not None:
            out = _fallback(st, side0, first)
        return out

    def by_rule(st: SetIn, side1: Court, first1: Side) -> tuple[Court, Side]:
        """Our side and the first server of a set (1-4) if set 1 had side1 and first1."""
        odd = (st.no - 1) % 2 == 1
        return (flip(side1) if odd else side1), (other(first1) if odd else first1)

    def hypothesis(st: SetIn, side1: Court, first1: Side) -> tuple[Court, Side]:
        side, first = by_rule(st, side1, first1)
        return st.our_side or side, st.first_serve or first

    def disagrees(st: SetIn, side1: Court, first1: Side) -> float:
        """A coach's side or first server that the hypothesis contradicts: a set the coach
        did tells the rules where the others are."""
        side, first = by_rule(st, side1, first1)
        return C_FACT * (
            (st.our_side is not None and st.our_side != side)
            + (st.first_serve is not None and st.first_serve != first)
        )

    regular = [i for i, st in enumerate(sets) if not st.deciding]
    chosen: dict[int, SetOut] = {}
    shares: list[float] = []
    if regular:
        totals = {
            (side1, first1): sum(
                cost(i, *hypothesis(sets[i], side1, first1)) + disagrees(sets[i], side1, first1)
                for i in regular
            )
            for side1 in COURTS
            for first1 in SIDES
        }
        side_1, first_1 = min(totals, key=lambda h: totals[h])
        told = all(sets[i].our_side and sets[i].first_serve for i in regular)
        shares.append(1.0 if told else _share(list(totals.values())))
        for i in regular:
            chosen[i] = run(i, *hypothesis(sets[i], side_1, first_1))
    for i, st in enumerate(sets):
        if not st.deciding:
            continue
        options = [
            (cost(i, side, first), side, first)
            for side in ((st.our_side,) if st.our_side else COURTS)
            for first in ((st.first_serve,) if st.first_serve else SIDES)
        ]
        _, side, first = min(options, key=lambda o: o[0])
        shares.append(_share([o[0] for o in options]) if len(options) > 1 else 1.0)
        chosen[i] = run(i, side, first)
    return MatchOut([chosen[i] for i in range(len(sets))], round(min(shares, default=1.0), 3))


# --- sets from the breaks -----------------------------------------------------------------------

SET_BREAK_S = 150.0  # a break between sets is about 3 minutes; shorter ones cost more
MIN_BREAK_S = 45.0  # never a set boundary at a shorter gap


@dataclass(frozen=True)
class SetSpan:
    first: int  # index of the set's first rally
    last: int  # index of its last rally (inclusive)
    confidence: float


def _boundary_cost(gap_s: float) -> float:
    if gap_s >= SET_BREAK_S:
        return 0.0
    if gap_s >= MIN_BREAK_S:
        return (SET_BREAK_S - gap_s) / 25.0
    return math.inf


def _set_cost(n: int, points: int | None, deciding: bool) -> float:
    """How well n rallies fit a set of `points` points (unknown: a plausible set)."""
    if points is not None:
        return 1.2 * abs(n - points)
    low, high = (15, 45) if deciding else (25, 70)
    return 0.8 * max(0, low + 8 - n) + 0.8 * max(0, n - high)


def propose_sets(
    rallies: Sequence[tuple[int, int]],
    official_points: Sequence[int] | None,
    *,
    sure: Sequence[bool] | None = None,
    max_sets: int = 5,
) -> list[SetSpan]:
    """Split rallies (start, end in ms, in time order) into sets at the long breaks. With the
    official score, exactly that many sets, each with about its number of points; without,
    as many as the breaks and set sizes suggest. Rallies before the first set (warm-up) and
    after the last one can be left out; `sure` marks rallies a person stands behind, which
    are expensive to leave out."""
    n = len(rallies)
    if n == 0:
        return []
    sure = sure or [False] * n
    gaps = [rallies[0][0] / 1000] + [
        (rallies[i][0] - rallies[i - 1][1]) / 1000 for i in range(1, n)
    ]
    # Where a set can start: the first rally, or after a long enough break.
    starts = [0] + [i for i in range(1, n) if gaps[i] >= MIN_BREAK_S]
    left_out = [0.0] * (n + 1)  # cost of leaving out rallies [0, i)
    for i in range(n):
        left_out[i + 1] = left_out[i] + (EXTRA_COACH if sure[i] else 0.4)
    tail_out = [0.0] * (n + 1)  # cost of leaving out rallies [i, n)
    for i in range(n - 1, -1, -1):
        tail_out[i] = tail_out[i + 1] + (EXTRA_COACH if sure[i] else 0.4)

    counts = list(official_points) if official_points else None
    # With the official score: all its sets, or the first ones when the video stops early.
    k_options = list(range(len(counts), 0, -1)) if counts else list(range(1, max_sets + 1))
    best: tuple[float, list[int], int] | None = None
    for k in k_options:
        # dp[j][s]: cheapest cost of j sets where set j starts at starts[s]
        dp: list[dict[int, tuple[float, int]]] = [{} for _ in range(k + 1)]
        for s_idx, s in enumerate(starts):
            dp[1][s_idx] = (left_out[s] + _match_start_cost(gaps[s], s), -1)
        for j in range(1, k):
            for s_idx, (cost, _) in dp[j].items():
                s = starts[s_idx]
                for t_idx in range(s_idx + 1, len(starts)):
                    t = starts[t_idx]
                    points = counts[j - 1] if counts else None
                    c = cost + _set_cost(t - s, points, deciding=j == 5) + _boundary_cost(gaps[t])
                    if c < dp[j + 1].get(t_idx, (math.inf, 0))[0]:
                        dp[j + 1][t_idx] = (c, s_idx)
        for s_idx, (cost, _) in dp[k].items():
            s = starts[s_idx]
            points = counts[k - 1] if counts else None
            # The last set ends at some rally e >= s; the rest is left out.
            for e in range(s + 1, n + 1):
                c = cost + _set_cost(e - s, points, deciding=k == 5) + tail_out[e]
                if counts is None:
                    c += 2.0 * k  # every set needs its break: prefer fewer, larger sets
                else:
                    c += 8.0 * (len(counts) - k)  # official sets the video does not show
                if best is None or c < best[0]:
                    chain = [s_idx]
                    j, idx = k, s_idx
                    while j > 1:
                        idx = dp[j][idx][1]
                        chain.append(idx)
                        j -= 1
                    best = (c, [starts[x] for x in reversed(chain)], e)
    if best is None:
        return []
    _, firsts, end = best
    spans = []
    for j, s in enumerate(firsts):
        last = (firsts[j + 1] if j + 1 < len(firsts) else end) - 1
        before = gaps[s] if s > 0 else 0.0
        conf = 0.95 if s == 0 or before >= SET_BREAK_S else 0.6 + 0.35 * before / SET_BREAK_S
        spans.append(SetSpan(s, last, round(conf, 3)))
    return spans


def _match_start_cost(gap_s: float, first: int) -> float:
    """Starting the match at rally `first`: the first serve follows a long pause (warm-up,
    presentation) unless it is the video's first rally."""
    if first == 0:
        return 0.0
    return 0.0 if gap_s >= 60 else 3.0


def evidence_final(st: SetIn, side0: Court, first: Side) -> tuple[int, int] | None:
    """Where the serve evidence alone ends the set (no official score): how well it reads the
    match by itself."""
    loose = replace(st, official=None)
    s = _setup(loose, side0, first)
    end = _forward(s, None)[-1]
    if not end:
        return None
    best = min(end, key=lambda k: end[k] + s.terminal(k))
    a, b, _ = _unpack(best)
    return a, b
