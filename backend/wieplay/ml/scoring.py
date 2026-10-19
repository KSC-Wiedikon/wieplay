"""Who served each rally, who won it and the score (PLAN §10.3). Plain Python: the API runs it.

Volleyball is rally scoring: **the team that wins a rally serves the next one.** So the score
follows from who served each rally, plus the rules and the official result. One joint decode per
set weighs every source of evidence there is (PLAN §10.3, "One decode, every source"):

1. **Serve evidence** per rally: P(the server stood at the near end line), from the ball at the
   serve (wieplay/ml/serve.py, computed on the worker and stored as a track per video), evened
   out per match (`balance`: about half of a match's serves are near ones).
2. **Court side → team** per set: teams change sides after every set; in the deciding set when
   the leading team reaches 8. Who served first alternates in sets 1-4; the deciding set has a
   new toss. A coach's "we serve first" / "we start on the near side" for a set wins.
3. **Winner of rally i = serving team of rally i+1**; the last rally of a set goes to whoever
   reaches the set-winning score.
4. **The official set score** is a hard end point: a dynamic programme over (score, next server)
   finds the consistent sequences: a rally can be no point (a replay, a proposal that is not a
   rally), points can be missing (a rally the finder missed), a serve can be misread. Each
   choice has a cost (a negative log-probability, roughly); a coach's serve or winner costs a lot
   to contradict.
5. **The scoreboard**, when it was read (wieplay/ml/board.py): +1 for one team between two
   rallies names the winner of the first, no change says it was no point.
6. **The club scoreboard's log** (wieplay/ml/scorelog.py, PLAN §10.10), in the sets where its
   final is the official one: the point pressed after a rally names its winner, a rally with no
   press between two that have one is no point, presses with no rally of their own are points
   the finder missed (how many and for whom).
7. **The official scoresheet** (PLAN §10.3a): a typed or confirmed set is its exact sequence of
   points, which the rallies are lined up with (`SheetPath`); a set read from the photo and not
   confirmed (SS4) is soft evidence (`BoxCells`): each service box the reader's probabilities,
   so the state carries the side-outs so far (which box a side-out writes) and every path pays
   for the boxes it implies. Its timeouts, substitutions and sanctions are "score at this break"
   evidence (`BreakNote`), its penalty points come without a rally.
8. **Forward-backward** over the same lattice gives each rally's posterior: who won (or no
   point), who served, a point missing before it, and its answer with its score line. `judge`
   then says which rallies are **sure** (the posterior at least SURE and no source that speaks
   about the rally disagrees) and why the others need the coach (`Reason`).

Sets: a coach's sets when there are any, else proposed from the long breaks and the official
number of points per set (`propose_sets`).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Literal

Side = Literal["us", "them"]
Court = Literal["near", "far"]
Note = Literal[
    "coach",
    "sheet",
    "log",
    "board",
    "missing_before",
    "missing_after",
    "extra",
    "serve",
    "unsure",
]
BoardSays = Literal["us", "them", "none"]
Value = int | str | None  # a service box as read: a number, "X", or blank

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
C_LOG_SCORE = 6.0  # the score after a rally other than the log's (times how sure its press is)
MAX_MISS_LOG = 4  # missed points between two rallies when the log saw them
# Each match's serve readings are evened out: sides change after every set and each team serves
# about as often as it scores, so about half the serves of a match are near ones (40-60 %). A
# hall whose picture leans the readings one way (a camera high above the end line, the servers
# in line with it) is shifted back, in log-odds, by at most BALANCE_CAP; not below BALANCE_MIN
# readings.
BALANCE_MIN = 20
BALANCE_CAP = 3.0
# The official scoresheet (PLAN §10.3a): its points are lined up with the rallies.
C_PENALTY_RALLY = 4.0  # a penalty point (no rally, by the rules) lined up with a rally
# A doubtful sheet's winner contradicted: more than a sure serve reading, less than the
# scoreboard (C_BOARD) or its log (C_LOG), far less than a coach's fact.
C_SHEET_HINT = 5.0
FREE_EXTRA = 0.2  # a rally the sheet's "points cancelled" note announces
SHEET_FALLBACK = 20.0  # a sheet that cannot be lined up (used as a hint instead)
SHORT_GAP_MS = 8000
SHORT_BONUS = 1.5  # "no point" that much cheaper (about the odds seen on game 509)
# A sheet read from its photo and not confirmed (SS4): each box costs W_READ x -log p(value) of
# the reader's calibrated probability (P_FLOOR for a value it did not list). Measured on the two
# real sheets (scripts/ml/fused_eval.py, 19.10.2026): weaker than a typed sheet and the log,
# stronger than the serve evidence.
W_READ = 0.3
P_FLOOR = 1e-4
C_TYPED = 8.0  # a box a person typed into a draft, read otherwise
FINAL_MISSING = 2.5  # the winner's final not written (won on reception)
MAX_SIDE_OUTS = 127
# Timeouts and substitutions are "score at this break" evidence: a timeout needs a long break (30 s
# and the walk back), a substitution one long enough to swap; in log-odds, times W_NOTE.
W_NOTE = 0.5
TIMEOUT_MIN_S = 30.0
SUB_MIN_S = 12.0
NOTE_LLR = {"timeout": (1.6, -1.2), "sub": (0.3, -1.0)}  # (a long enough break, a short one)
# "Sure" (judge): the decoded answer and its score line at least this probable, and no source that
# speaks about the rally disagrees. Calibrated so that sure rallies had no error on the evaluation
# games (scripts/ml/fused_eval.py, PLAN §10.3).
SURE = 0.97
SOURCE_SURE = 0.9  # a serve reading this sure of the other team disagrees
MISSED_NEAR = 0.3  # a point without a rally this probable next to a rally: check it
BOX_SURE = 0.9  # a read box whose decoded value is less probable is unclear
BOX_DISAGREES = 0.9  # the reader this sure of another value than the decoded one disagrees
# The posteriors are computed with every cost divided by this: the costs are log-probabilities
# only roughly (a missed rally next to a replay, a rally cut in two: the cheapest path is often
# far ahead of the true one), so the plain posteriors were sure of wrong answers. Calibrated on the
# evaluation games so that sure rallies had no error (scripts/ml/fused_eval.py). The cheapest path
# (the score line) does not change with it.
TEMPERATURE = 2.0


def other(side: Side) -> Side:
    return "them" if side == "us" else "us"


def _logit(p: float) -> float:
    p = min(0.998, max(0.002, p))  # a serve track's step is 1/254
    return math.log(p / (1 - p))


def _sigmoid(z: float) -> float:
    return 1 / (1 + math.exp(-z)) if z >= 0 else math.exp(z) / (1 + math.exp(z))


def balance(p_near: Sequence[float | None]) -> float:
    """The shift (log-odds) that makes a match's serve readings average one half (`BALANCE_*`
    above); 0 with fewer than BALANCE_MIN readings."""
    z = [_logit(p) for p in p_near if p is not None]
    if len(z) < BALANCE_MIN:
        return 0.0
    lo, hi = -BALANCE_CAP, BALANCE_CAP
    for _ in range(40):
        mid = (lo + hi) / 2
        if sum(_sigmoid(v + mid) for v in z) < len(z) / 2:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2, 3)


def shifted(p: float | None, shift: float) -> float | None:
    """A serve reading moved by `shift` log-odds."""
    if p is None or shift == 0.0:
        return p
    return _sigmoid(_logit(p) + shift)


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
    # The set's score after this rally as the log had it (us, them): the score line too, not only
    # who won (a log that starts late, a missed rally before it, leave the winners and shift it).
    log_score: tuple[int, int] | None = None


@dataclass(frozen=True)
class SheetPath:
    """A consistent scoresheet set's points in order (wieplay/ml/sheet.py): who won and who
    served each, which were penalties (a point without a rally); after how many points the
    video has rallies the sheet does not count (cancelled points), and how many."""

    winners: tuple[Side, ...]
    servers: tuple[Side, ...]
    penalty: tuple[bool, ...]
    cancelled: Mapping[int, int] = field(default_factory=dict)

    def hint(self, start: tuple[int, int] = (0, 0)) -> dict[tuple[int, int], Side]:
        """The winner of the next point at each score of the path."""
        a, b = start
        out: dict[tuple[int, int], Side] = {}
        for w in self.winners:
            out[(a, b)] = w
            a, b = a + (w == "us"), b + (w == "them")
        return out


Pos = tuple[int, int]  # a service box: (position 1-6, box 1-8)


@dataclass(frozen=True)
class BoxCells:
    """A scoresheet set read from its photo and not confirmed (PLAN §10.3a, SS4), as evidence:
    per team and service box (position, box) the reader's likeliest values with their calibrated
    probabilities (wieplay/ml/sheet_read.py); the boxes a person typed into the draft (`fixed`:
    certain; None: a box emptied); what the marks say about who served first (the receiving
    team's X, the S/R crosses) as a cost per first server. A box's value is the serving team's
    score when it lost the serve (the scorer's rule), so a path through the set implies every box:
    each costs W_READ x -log p(that value), the boxes it leaves blank -log p(blank)."""

    boxes: Mapping[Side, Mapping[Pos, tuple[tuple[Value, float], ...]]]
    fixed: Mapping[Side, Mapping[Pos, int | None]] = field(default_factory=dict)
    deciding: bool = False
    first_cost: Mapping[Side, float] = field(default_factory=dict)
    weight: float = W_READ


NoteKind = Literal["timeout", "sub", "sub_back", "sanction"]


@dataclass(frozen=True)
class BreakNote:
    """A timeout, substitution (or a return) or sanction on the sheet, with the score written
    with it (us, them): it happened in the break where the set stood at that score."""

    kind: NoteKind
    side: Side
    score: tuple[int, int]
    sanction: str | None = None  # W, P, E, D, DW, DP
    index: int = 0  # in the set's timeouts, substitutions or sanctions

    @property
    def points(self) -> int:
        return self.score[0] + self.score[1]


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
    # The official scoresheet (PLAN §10.3a): a consistent set's points in order, which the
    # rallies are lined up with; for a doubtful set, the winner it gives at a score (a hint); a
    # set read from the photo and not confirmed: its boxes as soft evidence.
    sheet: SheetPath | None = None
    sheet_hint: Mapping[tuple[int, int], Side] | None = None
    cells: BoxCells | None = None
    # Its timeouts, substitutions and sanctions ("score at this break"); penalty points (the
    # score before it → the team it went to) for a set that is not lined up as a path.
    notes: tuple[BreakNote, ...] = ()
    penalties: Mapping[tuple[int, int], Side] = field(default_factory=dict)


@dataclass(frozen=True)
class Reason:
    """Why a rally needs the coach: a code (the review's text) and what the text names."""

    code: str
    params: Mapping[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {"code": self.code, **self.params}


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
    sheet_point: int | None = None  # the sheet's point it was lined up with (1-based)
    # Posteriors over every path the evidence allows (forward-backward): who won (or no point),
    # that we served, a point without a rally right before it; this answer with its score line.
    p_us: float = 0.0
    p_them: float = 0.0
    p_none: float = 0.0
    p_serve_us: float = 0.5
    p_missed: float = 0.0
    p_answer: float = 0.0
    # A set read from the photo: the service box of this rally's turn (team, position, box), its
    # decoded value and how probable that value is.
    box: tuple[Side, int, int] | None = None
    box_value: int | None = None
    box_p: float | None = None
    # judge(): sure needs no coach; else the reasons.
    sure: bool = False
    reasons: tuple[Reason, ...] = ()


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
    # The scoresheet: "path" (lined up with it), "hint" (its winners as a soft cost), "read"
    # (its boxes read from the photo, soft), None.
    sheet: Literal["path", "hint", "read"] | None = None
    point_rally: list[str | None] = field(default_factory=list)  # per sheet point, its rally
    penalties: int = 0  # the sheet's penalty points (points without a rally, by the rules)
    p_missed_after: float = 0.0
    # A set read from the photo: per box (team, position, box) its decoded value (None: blank)
    # and that value's posterior.
    boxes: dict[tuple[Side, int, int], tuple[int | None, float]] = field(default_factory=dict)
    # How sure the choice of our side and the first server is (score_match).
    sides_share: float = 1.0

    @property
    def flagged(self) -> int:
        return sum(1 for r in self.rallies if r.note is not None)


# --- sums of probabilities in cost space ---------------------------------------------------------


def _soft_add(acc: dict[int, list[float]], key: int, cost: float) -> None:
    """Accumulate exp(-cost) into acc[key] = [the least cost, the sum relative to it]."""
    e = acc.get(key)
    if e is None:
        acc[key] = [cost, 1.0]
    elif cost >= e[0]:
        e[1] += math.exp(e[0] - cost)
    else:
        e[1] = e[1] * math.exp(cost - e[0]) + 1.0
        e[0] = cost


def _soft_done(acc: Mapping[int, list[float]]) -> dict[int, float]:
    return {k: m - math.log(s) for k, (m, s) in acc.items()}


def _softmin(costs: Sequence[float]) -> float:
    finite = [c for c in costs if math.isfinite(c)]
    if not finite:
        return math.inf
    lo = min(finite)
    return lo - math.log(sum(math.exp(lo - c) for c in finite))


# --- the dynamic programme --------------------------------------------------------------------

# A state is (us, them, next server: 0 us / 1 them, side-outs so far), packed into one int. The
# side-outs (which service box the next side-out writes) are counted only for a set read from the
# photo; otherwise always 0.


def _pack(a: int, b: int, s: int, t: int = 0) -> int:
    return ((a * 128 + b) * 2 + s) * 128 + t


def _unpack(k: int) -> tuple[int, int, int, int]:
    rest, t = divmod(k, 128)
    ab, s = divmod(rest, 2)
    a, b = divmod(ab, 128)
    return a, b, s, t


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
    # The log's part of the costs (a point for us, for them, no point): not tempered in the
    # posteriors (TEMPERATURE), like a coach's fact.
    hard: tuple[float, float, float] = (0.0, 0.0, 0.0)
    # The score after it by the log, and what another score costs.
    log_score: tuple[int, int] | None = None
    log_score_cost: float = 0.0

    def score_cost(self, score: tuple[int, int]) -> float:
        if self.log_score is None or score == self.log_score:
            return 0.0
        return self.log_score_cost


def _tempered(cost: float, hard: float) -> float:
    """An edge's cost in the posteriors: its hard part (the log, a coach's fact) as it is, the
    rest divided by TEMPERATURE."""
    return hard + (cost - hard) / TEMPERATURE


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
    hard = [0.0, 0.0, 0.0]
    # A coach's winner silences the log on that rally: the log ranks below a coach's fact.
    log_p = 0.0 if r.won_fact is not None else r.log_p
    for says, p, weight in ((r.board, r.board_p, C_BOARD), (r.log, log_p, C_LOG)):
        if says is None or p <= 0:
            continue
        for k, answer in enumerate(("us", "them", "none")):
            if says != answer:
                costs[k] += weight * p
                if weight == C_LOG:
                    hard[k] += weight * p
    return _Rally(
        -math.log(p_extra) + costs[2],
        (rows[0], rows[1]),
        fact[r.serve_fact],
        fact[r.won_fact],
        (costs[0], costs[1]),
        (hard[0], hard[1], hard[2]),
        r.log_score if log_p > 0 and r.log in ("us", "them") else None,
        C_LOG_SCORE * log_p,
    )


Layer = dict[int, float]
Back = dict[int, tuple[int, int]]
SideOut = tuple[int, int, int]  # (the team that lost the serve 0/1, its turn, its score)
Score = tuple[int, int]
# (next state, cost, answer, side-outs on the way, scores passed, the cost's hard part)
Edge = tuple[int, float, int, tuple[SideOut, ...], tuple[Score, ...], float]


class _Structure:
    """Which states follow which (cached): depends on the score cap, the set's rules and whether
    side-outs are counted only, so the hypotheses of a set share it."""

    def __init__(self, cap: tuple[int, int], rules: SetRules, count: bool = False) -> None:
        self.cap = cap
        self.rules = rules
        self.count = count
        self._miss: dict[tuple[int, int], list[Edge]] = {}
        self._step: dict[int, tuple[bool, int, int, int, int, int]] = {}

    def miss(self, state: int, k_max: int) -> list[Edge]:
        """(next state, 0, missed points, the side-outs on the way, the scores passed) for 0..k_max
        points without a rally (the cost is filled in by the set)."""
        key = (state, k_max)
        found = self._miss.get(key)
        if found is not None:
            return found
        a, b, s, t = _unpack(state)
        out: list[Edge] = [(state, 0.0, 0, (), (), 0.0)]
        over = self.rules.over
        ca, cb = self.cap
        if self.count:
            frontier: list[tuple[int, int, int, int, tuple[SideOut, ...], tuple[Score, ...]]]
            frontier = [(a, b, s, t, (), ())]
            for k in range(1, k_max + 1):
                grown = []
                for fa, fb, fs, ft, so, passed in frontier:
                    if over(fa, fb):
                        continue
                    for w in (0, 1):
                        na, nb = fa + (w == 0), fb + (w == 1)
                        if na > ca or nb > cb:
                            continue
                        nso, nt = so, ft
                        if w != fs:
                            nso = (*so, (fs, ft // 2, fa if fs == 0 else fb))
                            nt = ft + 1
                        if nt > MAX_SIDE_OUTS:
                            continue
                        grown.append((na, nb, w, nt, nso, (*passed, (na, nb))))
                out.extend(
                    (_pack(na, nb, ns, nt), 0.0, k, nso, passed, 0.0)
                    for na, nb, ns, nt, nso, passed in grown
                )
                frontier = grown
        elif not over(a, b):
            for k in range(1, k_max + 1):
                for du in range(k + 1):
                    dt = k - du
                    na, nb = a + du, b + dt
                    if na > ca or nb > cb:
                        continue
                    # The score before the last missed point must still be playing.
                    if du and not over(na - 1, nb):
                        out.append((_pack(na, nb, 0), 0.0, k, (), ((na, nb),), 0.0))
                    if dt and not over(na, nb - 1):
                        out.append((_pack(na, nb, 1), 0.0, k, (), ((na, nb),), 0.0))
        self._miss[key] = out
        return out

    def step(self, state: int) -> tuple[bool, int, int, int, int, int]:
        """(set over, state after a point for us or -1, after a point for them or -1, sides
        switched, next server, points so far)."""
        found = self._step.get(state)
        if found is not None:
            return found
        a, b, s, t = _unpack(state)
        rules = self.rules
        over = rules.over(a, b)
        tu = t + (s == 1) if self.count else 0
        tt = t + (s == 0) if self.count else 0
        up = _pack(a + 1, b, 0, tu) if a + 1 <= self.cap[0] and tu <= MAX_SIDE_OUTS else -1
        tp = _pack(a, b + 1, 1, tt) if b + 1 <= self.cap[1] and tt <= MAX_SIDE_OUTS else -1
        switched = int(rules.switch_at is not None and max(a, b) >= rules.switch_at)
        out = (over, up, tp, switched, s, a + b)
        self._step[state] = out
        return out


@dataclass
class _Cells:
    """A read set's boxes for one first server: per team (0 us, 1 them) and turn, the cost of
    each listed value relative to the box left blank, of any other value, and where the box is."""

    rel: tuple[list[dict[int, float]], list[dict[int, float]]]
    floor: tuple[list[float], list[float]]
    pos: tuple[list[Pos], list[Pos]]
    base: float  # every box blank, plus what the marks say about the first server

    def box(self, team: int, turn: int, value: int) -> float:
        rel = self.rel[team]
        if turn >= len(rel):
            return math.inf
        return rel[turn].get(value, self.floor[team][turn])

    def side_outs(self, found: Sequence[SideOut]) -> float:
        return sum(self.box(team, turn, value) for team, turn, value in found)


def _turn_cells(first: bool, deciding: bool) -> list[Pos]:
    """(position 1-6, box) of a team's service turns in order (wieplay/ml/sheet.py `cells`)."""
    boxes = 6 if deciding else 8
    start = 0 if first else 1
    return [(k % 6 + 1, k // 6 + 1) for k in range(start, 6 * boxes)]


def _cells_for(cells: BoxCells, first: Side) -> _Cells:
    w = cells.weight
    rel: tuple[list[dict[int, float]], list[dict[int, float]]] = ([], [])
    floor: tuple[list[float], list[float]] = ([], [])
    pos: tuple[list[Pos], list[Pos]] = ([], [])
    base = cells.first_cost.get(first, 0.0)
    for team, side in enumerate(SIDES):
        read = cells.boxes.get(side) or {}
        fixed = cells.fixed.get(side) or {}
        for p in _turn_cells(side == first, cells.deciding):
            pos[team].append(p)
            if p in fixed:
                typed = fixed[p]
                blank = 0.0 if typed is None else C_TYPED
                values = {} if typed is None else {typed: -blank}
                rel[team].append(values)
                floor[team].append(C_TYPED - blank)
                base += blank
                continue
            alts = read.get(p, ())
            probs: dict[Value, float] = {}
            for v, pr in alts:
                probs[v] = max(probs.get(v, 0.0), pr)
            blank = w * -math.log(max(probs.get(None, P_FLOOR), P_FLOOR))
            values = {
                v: w * -math.log(max(pr, P_FLOOR)) - blank
                for v, pr in probs.items()
                if isinstance(v, int)
            }
            rel[team].append(values)
            floor[team].append(w * -math.log(P_FLOOR) - blank)
            base += blank
    return _Cells(rel, floor, pos, base)


def _note_cost(note: BreakNote, gap_s: float) -> float:
    llr = NOTE_LLR.get("sub" if note.kind == "sub_back" else note.kind)
    if llr is None:
        return 0.0
    need = TIMEOUT_MIN_S if note.kind == "timeout" else SUB_MIN_S
    return -W_NOTE * (llr[0] if gap_s >= need else llr[1])


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
    cells: _Cells | None = None
    gaps_s: list[float] = field(default_factory=list)  # gap j before rally j; [n] after the last
    notes: dict[tuple[int, int], list[BreakNote]] = field(default_factory=dict)
    penalties: dict[tuple[int, int], int] = field(default_factory=dict)

    def start(self) -> int:
        return _pack(self.st.start_us, self.st.start_them, 0 if self.first == "us" else 1)

    def terminal(self, state: int) -> float:
        a, b, s, t = _unpack(state)
        if self.st.official is not None:
            end = 0.0 if (a, b) == self.st.official else math.inf
        else:
            end = 0.0 if self.st.rules.over(a, b) else C_UNFINISHED
        if self.cells is not None and math.isfinite(end):
            # The team serving next writes its final in the box of its turn (or forgets it).
            end += min(self.cells.box(s, t // 2, a if s == 0 else b), FINAL_MISSING)
        return end

    def side_index(self, state: int) -> int:
        """0 when our side is near during a point at this score, else 1."""
        near = self.side0 == "near"
        if self.struct.step(state)[3]:
            near = not near
        return 0 if near else 1

    def hint(self, state: int) -> tuple[float, float]:
        """A doubtful scoresheet's cost of a point for us / for them at this score."""
        told = self.st.sheet_hint
        if not told:
            return 0.0, 0.0
        a, b, _, _ = _unpack(state)
        winner = told.get((a, b))
        if winner is None:
            return 0.0, 0.0
        return (0.0, C_SHEET_HINT) if winner == "us" else (C_SHEET_HINT, 0.0)

    def note_cost(self, score: tuple[int, int], gap_s: float) -> float:
        found = self.notes.get(score)
        return sum(_note_cost(n, gap_s) for n in found) if found else 0.0

    def rally_edges(self, state: int, j: int) -> list[Edge]:
        r = self.rallies[j]
        over, up, tp, switched, s, _ = self.struct.step(state)
        out: list[Edge] = [(state, r.extra, -1, (), (), r.hard[2])]
        if over:
            return out
        a, b, _, t = _unpack(state)
        side = (0 if self.side0 == "near" else 1) ^ switched
        base = r.point[side][s] + (C_FACT if r.serve_fact >= 0 and r.serve_fact != s else 0.0)
        hu, ht = self.hint(state)
        penalty = self.penalties.get((a, b))
        after = self.gaps_s[j + 1] if j + 1 < len(self.gaps_s) else 0.0
        for w, nxt, h in ((0, up, hu), (1, tp, ht)):
            if nxt < 0:
                continue
            facts = (C_FACT if r.won_fact == 1 - w else 0.0) + (
                C_FACT if r.serve_fact >= 0 and r.serve_fact != s else 0.0
            )
            cost = base + (C_FACT if r.won_fact == 1 - w else 0.0) + r.won[w] + h
            so: tuple[SideOut, ...] = ()
            if self.cells is not None and w != s:
                so = ((s, t // 2, a if s == 0 else b),)
                cost += self.cells.box(*so[0])
            if penalty is not None and penalty == w:
                cost += C_PENALTY_RALLY  # the sheet's penalty point has no rally
            score = (a + (w == 0), b + (w == 1))
            if self.notes:
                cost += self.note_cost(score, after)
            said = r.score_cost(score)
            cost += said
            if math.isfinite(cost):
                out.append((nxt, cost, s * 2 + w, so, (score,), r.hard[w] + facts + said))
        return out

    def gap_edges(self, state: int, j: int) -> list[Edge]:
        costs, pref = self.miss_costs[j], self.miss_pref[j]
        starts: list[tuple[int, float, tuple[SideOut, ...]]] = [(state, 0.0, ())]
        if self.penalties:
            a, b, s, t = _unpack(state)
            to = self.penalties.get((a, b))
            if to is not None and not self.struct.rules.over(a, b):
                _, up, tp, _, _, _ = self.struct.step(state)
                nxt = up if to == 0 else tp
                if nxt >= 0:
                    so: tuple[SideOut, ...] = ()
                    c = 0.0
                    if self.cells is not None and to != s:
                        so = ((s, t // 2, a if s == 0 else b),)
                        c = self.cells.box(*so[0])
                    # At this score the next point is the penalty, by the sheet: no other start.
                    starts = [(nxt, c, so)]
        out: list[Edge] = []
        gap_s = self.gaps_s[j] if j < len(self.gaps_s) else 0.0
        for s0, c0, so0 in starts:
            for nxt, _, k, so, passed, _ in self.struct.miss(s0, self.k_max[j]):
                said = _pref_cost(pref, s0, nxt) if pref else 0.0
                cost = c0 + costs[k] + said
                if so and self.cells is not None:
                    cost += self.cells.side_outs(so)
                if self.notes and passed:
                    cost += sum(self.note_cost(sc_, gap_s) for sc_ in passed)
                if math.isfinite(cost):
                    out.append((nxt, cost, k, so0 + so, passed, said))
        return out


def _pref_cost(pref: tuple[int, int], state: int, nxt: int) -> float:
    """Missed points won by other teams than the log says: per point off."""
    a0, b0, _, _ = _unpack(state)
    a1, b1, _, _ = _unpack(nxt)
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


@dataclass
class _Pass:
    """The forward pass: per layer the cheapest cost of each state (min) and the cost of all paths
    to it (sum, in cost space); back pointers. Layer 2i holds the states before gap i, 2i+1 those
    right before rally i, 2i+2 those after it; the last one the states after the final gap."""

    mins: list[Layer]
    sums: list[Layer]
    backs: list[Back]


def _forward(s: _Set, sums: bool = True) -> _Pass:
    n = len(s.rallies)
    inf = math.inf
    start = s.start()
    mins: list[Layer] = [{start: 0.0}]
    soft: list[Layer] = [{start: 0.0}]
    backs: list[Back] = []
    for i in range(n + 1):
        for kind in ("gap", "rally"):
            if kind == "rally" and i == n:
                break
            src_min, src_sum = mins[-1], soft[-1]
            nxt_min: Layer = {}
            acc: dict[int, list[float]] = {}
            bm: Back = {}
            for state, cost in src_min.items():
                c_sum = src_sum.get(state, cost)
                edges = s.gap_edges(state, i) if kind == "gap" else s.rally_edges(state, i)
                for nxt, c, opt, _, _, hard in edges:
                    total = cost + c
                    if total < nxt_min.get(nxt, inf):
                        nxt_min[nxt] = total
                        bm[nxt] = (state, opt)
                    if sums:
                        _soft_add(acc, nxt, c_sum + _tempered(c, hard))
            backs.append(bm)
            remaining = n - i if kind == "gap" else n - i - 1
            kept = nxt_min if kind == "gap" and i == n else _keep(nxt_min, s, remaining)
            mins.append(kept)
            if sums:
                done = _soft_done(acc)
                soft.append({k: done[k] for k in kept})
            else:
                soft.append(kept)
    return _Pass(mins, soft, backs)


_STRUCTURES: dict[tuple[tuple[int, int], SetRules, bool], _Structure] = {}


def _gap_model(st: SetIn) -> tuple[list[int], list[list[float]], list[tuple[int, int] | None]]:
    """Per gap (before each rally, and after the last): how many points may be missing there,
    what k missed points cost (the gap's length, play or whistles in it), and what the club
    scoreboard's log saw there without a rally (None: it says nothing)."""
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
    told = [r.log_missed for r in rallies] + [st.log_missed_after]
    return k_max, costs, told


def _gaps_s(st: SetIn) -> list[float]:
    """Each gap's length in seconds: before the set's first rally and after its last one, long
    (the set's break)."""
    rs = st.rallies
    inner = [max(0.0, (rs[i].start_ms - rs[i - 1].end_ms) / 1000) for i in range(1, len(rs))]
    return [120.0, *inner, 120.0]


def _notes_by_score(st: SetIn) -> dict[tuple[int, int], list[BreakNote]]:
    out: dict[tuple[int, int], list[BreakNote]] = {}
    for n in st.notes:
        if n.kind in ("timeout", "sub", "sub_back"):
            out.setdefault(n.score, []).append(n)
    return out


def _setup(st: SetIn, side0: Court, first: Side) -> _Set:
    if st.official is not None:
        cap = (max(st.official[0], st.start_us), max(st.official[1], st.start_them))
    else:
        cap = (st.rules.target + 20, st.rules.target + 20)
    count = st.cells is not None
    key = (cap, st.rules, count)
    struct = _STRUCTURES.get(key)
    if struct is None:
        if len(_STRUCTURES) > 64:
            _STRUCTURES.clear()
        struct = _STRUCTURES[key] = _Structure(cap, st.rules, count)
    k_max, costs, told = _gap_model(st)
    # The log: how many points it saw in a gap (and whose) without a rally of their own.
    pref: list[tuple[int, int] | None] = [None] * len(costs)
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
    rallies = [_prepare(r) for r in st.rallies]
    cells = _cells_for(st.cells, first) if st.cells is not None else None
    penalties = {score: 0 if w == "us" else 1 for score, w in st.penalties.items()}
    return _Set(
        st,
        side0,
        first,
        struct,
        rallies,
        k_max,
        costs,
        pref,
        cells,
        _gaps_s(st),
        _notes_by_score(st),
        penalties,
    )


# --- the scoresheet: its points lined up with the rallies --------------------------------------
#
# A consistent sheet (PLAN §10.3a) gives every point of the set in order: who won it and who
# served it. What it lacks is time, so the rallies are lined up with its points (a sequence
# alignment, Needleman-Wunsch style): the state is simply the number of the sheet's points
# played. Per rally: it is the next point (its serve evidence for that point's server, the
# board and the log for its winner, a coach's serve or winner), or no point (a replay, a
# proposal that is not a rally; free where the sheet says points were cancelled); before it,
# points without a rally (a penalty is free, any other point is a rally the finder missed).


@dataclass
class _SheetSet:
    st: SetIn
    path: SheetPath
    rallies: list[_Rally]
    side: list[int]  # per sheet point: 0 when our side is near, else 1
    k_max: list[int]
    base: list[list[float]]
    told: list[tuple[int, int] | None]
    np_before: list[int]  # non-penalty points among the first i
    us_before: list[int]  # points won by us among the first i
    short: list[float]  # how much cheaper "no point" is: a rally right after another
    gaps_s: list[float] = field(default_factory=list)
    # The sheet's timeouts and substitutions by the number of points played before them.
    notes: dict[int, list[BreakNote]] = field(default_factory=dict)
    scores: list[tuple[int, int]] = field(default_factory=list)  # after 0, 1, ... points

    @property
    def n(self) -> int:
        return len(self.path.winners)

    def note_cost(self, q: int, gap_s: float) -> float:
        found = self.notes.get(q)
        return sum(_note_cost(n, gap_s) for n in found) if found else 0.0

    def gap(self, j: int, i0: int, i1: int) -> float:
        """Points i0..i1-1 without a rally of their own in gap j (before rally j)."""
        k = i1 - i0
        k_np = self.np_before[i1] - self.np_before[i0]
        said = self.told[j]
        base = self.base[j]
        if said is None:
            cost = base[k_np] if k_np <= self.k_max[j] else math.inf
        else:
            k_log = min(MAX_MISS_LOG, said[0] + said[1])
            if k_np > max(self.k_max[j], k_log):
                return math.inf
            cost = base[k_np] if k_np < len(base) else base[-1] + C_MISS * (k_np - len(base) + 1)
            cost = 0.3 * k_np if k == k_log else cost + C_LOG_MISS * abs(k - k_log)
            if k:
                du = self.us_before[i1] - self.us_before[i0]
                cost += C_LOG_MISS * 0.5 * (abs(du - said[0]) + abs(k - du - said[1]))
        if self.notes and k and math.isfinite(cost):
            gap_s = self.gaps_s[j] if j < len(self.gaps_s) else 0.0
            cost += sum(self.note_cost(q, gap_s) for q in range(i0 + 1, i1 + 1))
        return cost

    def gap_options(self, j: int, i0: int) -> list[tuple[int, float]]:
        out = []
        for i1 in range(i0, self.n + 1):
            if self.np_before[i1] - self.np_before[i0] > max(self.k_max[j], MAX_MISS_LOG):
                break
            c = self.gap(j, i0, i1)
            if math.isfinite(c):
                out.append((i1, c))
        return out

    def point(self, j: int, i: int) -> float:
        """Rally j is the sheet's point i (0-based)."""
        r = self.rallies[j]
        srv = 0 if self.path.servers[i] == "us" else 1
        won = 0 if self.path.winners[i] == "us" else 1
        cost = r.point[self.side[i]][srv] + r.won[won]
        if r.serve_fact >= 0 and r.serve_fact != srv:
            cost += C_FACT
        if r.won_fact >= 0 and r.won_fact != won:
            cost += C_FACT
        if self.path.penalty[i]:
            cost += C_PENALTY_RALLY
        if self.notes:
            after = self.gaps_s[j + 1] if j + 1 < len(self.gaps_s) else 0.0
            cost += self.note_cost(i + 1, after)
        if r.log_score is not None:
            cost += r.score_cost(self.scores[i + 1])
        return cost

    def point_t(self, j: int, i: int) -> float:
        """point(), tempered for the posteriors (its log and a coach's facts as they are)."""
        r = self.rallies[j]
        srv = 0 if self.path.servers[i] == "us" else 1
        won = 0 if self.path.winners[i] == "us" else 1
        hard = r.hard[won] + (C_FACT if r.serve_fact >= 0 and r.serve_fact != srv else 0.0)
        hard += C_FACT if r.won_fact >= 0 and r.won_fact != won else 0.0
        if r.log_score is not None:
            hard += r.score_cost(self.scores[i + 1])
        return _tempered(self.point(j, i), hard)

    def none_t(self, j: int, i: int) -> float:
        hard = 0.0 if self.path.cancelled.get(i, 0) > 0 else self.rallies[j].hard[2]
        return _tempered(self.none(j, i), hard)

    def none(self, j: int, i: int) -> float:
        """Rally j is no point, after i of the sheet's points."""
        if self.path.cancelled.get(i, 0) > 0:
            return FREE_EXTRA
        return self.rallies[j].extra - self.short[j]


def _sheet_setup(st: SetIn, side0: Court) -> _SheetSet:
    assert st.sheet is not None
    path = st.sheet
    k_max, base, told = _gap_model(st)
    near0 = 0 if side0 == "near" else 1
    a, b = st.start_us, st.start_them
    side: list[int] = []
    np_before, us_before = [0], [0]
    for w, pen in zip(path.winners, path.penalty, strict=True):
        switched = st.rules.switch_at is not None and max(a, b) >= st.rules.switch_at
        side.append(near0 ^ int(switched))
        a, b = a + (w == "us"), b + (w == "them")
        np_before.append(np_before[-1] + (0 if pen else 1))
        us_before.append(us_before[-1] + (w == "us"))
    rallies = [_prepare(r) for r in st.rallies]
    # A serve comes at least ~8 s after the previous rally's end: a proposal followed that soon
    # by the next is often the first part of a rally the finder cut in two (the point is the
    # part with the rally's end), or a replay.
    rs = st.rallies
    short = [
        SHORT_BONUS
        if j + 1 < len(rs)
        and r.proposal is not None
        and r.won_fact is None
        and r.serve_fact is None
        and rs[j + 1].start_ms - r.end_ms < SHORT_GAP_MS
        else 0.0
        for j, r in enumerate(rs)
    ]
    # Notes by the points played before them: the path's scores name them.
    scores = [(st.start_us, st.start_them)]
    for w in path.winners:
        sa, sb = scores[-1]
        scores.append((sa + (w == "us"), sb + (w == "them")))
    at = {score: q for q, score in enumerate(scores)}
    notes: dict[int, list[BreakNote]] = {}
    for n in st.notes:
        q = at.get(n.score)
        if q is not None and q > 0 and n.kind in ("timeout", "sub", "sub_back"):
            notes.setdefault(q, []).append(n)
    return _SheetSet(
        st,
        path,
        rallies,
        side,
        k_max,
        base,
        told,
        np_before,
        us_before,
        short,
        _gaps_s(st),
        notes,
        scores,
    )


@dataclass
class _SheetPass:
    before_min: list[list[float]]  # per rally: each state's least cost after its gap
    before_sum: list[list[float]]
    end_min: list[float]  # after the final gap
    end_sum: list[float]
    back_gap: list[list[int]]
    back_rally: list[list[int]]


def _sheet_forward(sh: _SheetSet, sums: bool = True) -> _SheetPass:
    """Forward: per rally the cost of each state before it (after its gap), cheapest and of all
    paths; the states after the last gap; back pointers."""
    n, m = sh.n, len(sh.rallies)
    inf = math.inf
    out = _SheetPass([], [], [], [], [], [])
    cur_min = [inf] * (n + 1)
    cur_sum = [inf] * (n + 1)
    cur_min[0] = cur_sum[0] = 0.0
    for j in range(m + 1):
        g_min = [inf] * (n + 1)
        g_acc: list[list[float]] = [[] for _ in range(n + 1)]
        bg = [-1] * (n + 1)
        for i0, c0 in enumerate(cur_min):
            if not math.isfinite(c0):
                continue
            s0 = cur_sum[i0]
            for i1, c in sh.gap_options(j, i0):
                if c0 + c < g_min[i1]:
                    g_min[i1], bg[i1] = c0 + c, i0
                if sums:
                    g_acc[i1].append(s0 + c / TEMPERATURE)
        g_sum = [_softmin(a) for a in g_acc] if sums else g_min
        out.back_gap.append(bg)
        if j == m:
            out.end_min, out.end_sum = g_min, g_sum
            return out
        f_min = [inf] * (n + 1)
        f_acc: list[list[float]] = [[] for _ in range(n + 1)]
        bf = [-1] * (n + 1)
        for i, c0 in enumerate(g_min):
            if not math.isfinite(c0):
                continue
            s0 = g_sum[i]
            c = sh.none(j, i)
            if c0 + c < f_min[i]:
                f_min[i], bf[i] = c0 + c, i
            if sums:
                f_acc[i].append(s0 + sh.none_t(j, i))
            if i < n:
                c = sh.point(j, i)
                if c0 + c < f_min[i + 1]:
                    f_min[i + 1], bf[i + 1] = c0 + c, i
                if sums:
                    f_acc[i + 1].append(s0 + sh.point_t(j, i))
        out.before_min.append(g_min)
        out.before_sum.append(g_sum)
        out.back_rally.append(bf)
        cur_min = f_min
        cur_sum = [_softmin(a) for a in f_acc] if sums else f_min
    raise AssertionError("unreachable")


def sheet_cost(st: SetIn, side0: Court) -> float:
    sh = _sheet_setup(st, side0)
    return _sheet_forward(sh, sums=False).end_min[sh.n]


def _run_sheet(st: SetIn, side0: Court, first: Side) -> SetOut | None:
    """The rallies lined up with the sheet's points (section comment); None when they cannot be
    (more points missing than the gaps can hold)."""
    sh = _sheet_setup(st, side0)
    n, m = sh.n, len(sh.rallies)
    fw = _sheet_forward(sh)
    if not math.isfinite(fw.end_min[n]):
        return None
    inf = math.inf
    T = TEMPERATURE
    log_z = fw.end_sum[n]
    # Backward (all paths): from each state before gap j (bwd_a[j]) and before rally j
    # (bwd_b[j]) to the end, with the posteriors of each rally's answers and each gap's misses.
    bwd_a: list[list[float]] = [[inf] * (n + 1) for _ in range(m + 1)]
    bwd_b: list[list[float]] = [[inf] * (n + 1) for _ in range(m)]
    # The same, cheapest path only: the max-marginals the flags use (`confidence`).
    min_a: list[list[float]] = [[inf] * (n + 1) for _ in range(m + 1)]
    min_b: list[list[float]] = [[inf] * (n + 1) for _ in range(m)]
    for i0 in range(n + 1):
        ends = [c for i1, c in sh.gap_options(m, i0) if i1 == n]
        bwd_a[m][i0] = _softmin([c / T for c in ends])
        min_a[m][i0] = min(ends, default=inf)
    p_rally: list[dict[int, float]] = [{} for _ in range(m)]  # j → {point index or -1: P}
    p_gap = [0.0] * (m + 1)  # P(a non-penalty point without a rally in gap j)
    # The cur forward sums after each rally are not kept: rebuild them from before_sum.
    after_sum: list[list[float]] = []
    for j in range(m):
        f_acc: list[list[float]] = [[] for _ in range(n + 1)]
        for i, s0 in enumerate(fw.before_sum[j]):
            if not math.isfinite(s0):
                continue
            f_acc[i].append(s0 + sh.none_t(j, i))
            if i < n:
                f_acc[i + 1].append(s0 + sh.point_t(j, i))
        after_sum.append([_softmin(a) for a in f_acc])
    for i0 in range(n + 1):
        a0 = after_sum[m - 1][i0] if m else (0.0 if i0 == 0 else inf)
        if not math.isfinite(a0):
            continue
        for i1, c in sh.gap_options(m, i0):
            if i1 == n and sh.np_before[i1] > sh.np_before[i0]:
                p_gap[m] += math.exp(log_z - (a0 + c / T))
    for j in range(m - 1, -1, -1):
        for i in range(n + 1):
            c_none = sh.none(j, i)
            c_point = sh.point(j, i) if i < n else inf
            opts = [sh.none_t(j, i) + bwd_a[j + 1][i]]
            min_b[j][i] = c_none + min_a[j + 1][i]
            if i < n:
                opts.append(sh.point_t(j, i) + bwd_a[j + 1][i + 1])
                min_b[j][i] = min(min_b[j][i], c_point + min_a[j + 1][i + 1])
            bwd_b[j][i] = _softmin(opts)
            s0 = fw.before_sum[j][i]
            if math.isfinite(s0):
                p_none = math.exp(log_z - (s0 + opts[0]))
                if p_none > 0:
                    p_rally[j][-1] = p_rally[j].get(-1, 0.0) + p_none
                if i < n:
                    p = math.exp(log_z - (s0 + opts[1]))
                    if p > 0:
                        p_rally[j][i] = p_rally[j].get(i, 0.0) + p
        prev = after_sum[j - 1] if j > 0 else [0.0] + [inf] * n
        for i0 in range(n + 1):
            options = sh.gap_options(j, i0)
            bwd_a[j][i0] = _softmin([c / T + bwd_b[j][i1] for i1, c in options])
            min_a[j][i0] = min((c + min_b[j][i1] for i1, c in options), default=inf)
            a0 = prev[i0]
            if not math.isfinite(a0):
                continue
            for i1, c in options:
                if sh.np_before[i1] > sh.np_before[i0]:
                    p_gap[j] += math.exp(log_z - (a0 + c / T + bwd_b[j][i1]))
    # The best path along the back pointers: states before each gap and rally.
    state_after = [0] * (m + 1)  # state after gap j (before rally j); [m]: the end
    state_before = [0] * (m + 1)  # state before gap j
    state_after[m] = n
    state_before[m] = fw.back_gap[m][n]
    for j in range(m - 1, -1, -1):
        state_after[j] = fw.back_rally[j][state_before[j + 1]]
        state_before[j] = fw.back_gap[j][state_after[j]]

    path = sh.path
    start = (st.start_us, st.start_them)
    scores = [start]
    for w in path.winners:
        a, b = scores[-1]
        scores.append((a + (w == "us"), b + (w == "them")))
    point_rally: list[str | None] = [None] * n
    rallies_out: list[RallyOut] = []
    missed_np = extra = 0
    for j, r in enumerate(st.rallies):
        i_gap0, i = state_before[j], state_after[j]
        nxt = state_before[j + 1]
        du = sh.us_before[i] - sh.us_before[i_gap0]
        gap_points = (du, (i - i_gap0) - du)
        gap_np = sh.np_before[i] - sh.np_before[i_gap0]
        missed_np += gap_np
        post = p_rally[j]
        p_none = min(1.0, post.get(-1, 0.0))
        p_us = min(1.0, sum(p for k, p in post.items() if k >= 0 and path.winners[k] == "us"))
        p_them = min(1.0, sum(p for k, p in post.items() if k >= 0 and path.winners[k] == "them"))
        p_serve_us = min(1.0, sum(p for k, p in post.items() if k >= 0 and path.servers[k] == "us"))
        side_now: Court = "near" if sh.side[min(i, n - 1)] == 0 else "far"
        a_after, b_after = scores[nxt]
        # Max-marginals: the cheapest whole path with each answer for this rally.
        by: dict[str, float] = {"us": inf, "them": inf, "none": inf}
        for k, c0 in enumerate(fw.before_min[j]):
            if not math.isfinite(c0):
                continue
            by["none"] = min(by["none"], c0 + sh.none(j, k) + min_a[j + 1][k])
            if k < n:
                w = path.winners[k]
                by[w] = min(by[w], c0 + sh.point(j, k) + min_a[j + 1][k + 1])
        common: dict[str, Any] = {
            "p_us": round(p_us, 4),
            "p_them": round(p_them, 4),
            "p_none": round(p_none, 4),
            "p_missed": round(min(1.0, p_gap[j]), 4),
        }
        if nxt == i:  # no point
            extra += 1
            covered = path.cancelled.get(i, 0) > 0
            note: Note | None = None
            if r.log in ("us", "them") and r.log_p > 0:
                note = "log"
            elif r.board in ("us", "them") and r.board_p > 0 and r.log is None:
                note = "board"
            elif not covered:
                note = "extra"
            rallies_out.append(
                RallyOut(
                    r.key,
                    False,
                    None,
                    None,
                    a_after,
                    b_after,
                    round(_confidence({-1: by["none"], 0: min(by["us"], by["them"])}, -1), 3),
                    None,
                    side_now,
                    note,
                    gap_points,
                    p_serve_us=round(p_serve_us, 4),
                    p_answer=round(p_none, 4),
                    **common,
                )
            )
            continue
        point_rally[i] = r.key
        server, won = path.servers[i], path.winners[i]
        # How sure: this winner against the other and against no point at all (a rally the
        # finder cut in two leaves two candidates for one point: check it).
        conf = _confidence({0: by["us"], 1: by["them"], -1: by["none"]}, 0 if won == "us" else 1)
        serve_p = None
        if r.p_near is not None:
            p_us_near = r.p_near if side_now == "near" else 1.0 - r.p_near
            serve_p = p_us_near if server == "us" else 1.0 - p_us_near
        note = None
        coach = (r.serve_fact is not None and r.serve_fact != server) or (
            r.won_fact is not None and r.won_fact != won
        )
        if coach or (r.log is not None and r.log_p > 0 and r.log != won):
            note = "sheet"  # a coach or the scoreboard's log says otherwise
        elif r.board is not None and r.board_p > 0 and r.board != won and r.log is None:
            note = "board"
        elif gap_np:
            note = "missing_before"
        elif j == m - 1 and sh.np_before[n] - sh.np_before[i + 1]:
            note = "missing_after"
        elif conf < UNSURE and r.won_fact is None:
            note = "unsure"
        rallies_out.append(
            RallyOut(
                r.key,
                True,
                server,
                won,
                a_after,
                b_after,
                round(conf, 3),
                None if serve_p is None else round(serve_p, 3),
                side_now,
                note,
                gap_points,
                sheet_point=i + 1,
                p_serve_us=round(p_serve_us, 4),
                p_answer=round(min(1.0, post.get(i, 0.0)), 4),
                **common,
            )
        )
    tail0 = state_before[m]
    tail_np = sh.np_before[n] - sh.np_before[tail0]
    du_tail = sh.us_before[n] - sh.us_before[tail0]
    return SetOut(
        no=st.no,
        cost=fw.end_min[n],
        first_serve=first,
        our_side=side0,
        final=scores[n],
        rallies=rallies_out,
        missed=missed_np + tail_np,
        extra=extra,
        missed_after=(du_tail, (n - tail0) - du_tail),
        sheet="path",
        point_rally=point_rally,
        penalties=sum(1 for k, p in enumerate(path.penalty) if p and point_rally[k] is None),
        p_missed_after=round(min(1.0, p_gap[m]), 4),
    )


def _as_hint(st: SetIn) -> SetIn:
    """A set whose sheet cannot be lined up: its winners as a soft hint instead."""
    assert st.sheet is not None
    return replace(st, sheet=None, sheet_hint=st.sheet.hint((st.start_us, st.start_them)))


def _cells_base(st: SetIn, first: Side) -> float:
    return _cells_for(st.cells, first).base if st.cells is not None else 0.0


def set_cost(st: SetIn, side0: Court, first: Side) -> float:
    """The cost of the cheapest consistent sequence (no details): picks the hypothesis."""
    if st.sheet is not None:
        if st.sheet.servers and st.sheet.servers[0] != first:
            return math.inf  # the sheet says who served first
        c = sheet_cost(st, side0)
        return c if math.isfinite(c) else set_cost(_as_hint(st), side0, first) + SHEET_FALLBACK
    s = _setup(st, side0, first)
    end = _forward(s, sums=False).mins[-1]
    best = min((v + s.terminal(k) for k, v in end.items()), default=math.inf)
    return best + (s.cells.base if s.cells is not None else 0.0)


def _confidence(costs: dict[int, float], chosen: int) -> float:
    """Softmin share of the chosen answer among the answers' cheapest paths."""
    finite = {k: v for k, v in costs.items() if math.isfinite(v)}
    if chosen not in finite:
        return 0.5
    lo = finite[chosen]
    return 1.0 / sum(math.exp(-(v - lo)) for v in finite.values())


def run_set(st: SetIn, side0: Court, first: Side) -> SetOut:
    """The cheapest consistent sequence of one set, given our side at its start and who
    serves first, with each rally's posteriors (forward-backward)."""
    if st.sheet is not None:
        lined = _run_sheet(st, side0, first) if st.sheet.servers[:1] == (first,) else None
        if lined is not None:
            return lined
        hinted = run_set(_as_hint(st), side0, first)
        hinted.cost += SHEET_FALLBACK
        return hinted
    s = _setup(st, side0, first)
    n = len(s.rallies)
    fw = _forward(s)
    end = fw.mins[-1]
    finals = {k: v + s.terminal(k) for k, v in end.items()}
    best_state = min(finals, key=lambda k: finals[k]) if finals else None
    if best_state is None or not math.isfinite(finals[best_state]):
        return SetOut(
            st.no, math.inf, first, side0, (st.start_us, st.start_them), [], 0, 0, feasible=False
        )
    post = _posteriors(s, fw)

    # The best path, along the back pointers.
    states = [0] * len(fw.mins)
    choice = [0] * len(fw.backs)
    state = best_state
    for li in range(len(fw.mins) - 1, 0, -1):
        states[li] = state
        state, choice[li - 1] = fw.backs[li - 1][state]
    states[0] = state

    rallies_out: list[RallyOut] = []
    extra_total = 0
    cells = s.cells
    for i, r in enumerate(st.rallies):
        p_opt, p_score = post.rally[i], post.score[i]
        missed = choice[2 * i]
        opt = choice[2 * i + 1]
        a_prev, b_prev, _, _ = _unpack(states[2 * i])
        a_pre, b_pre, s_pre, t_pre = _unpack(states[2 * i + 1])
        gap_points = (a_pre - a_prev, b_pre - b_prev)
        a_after, b_after, _, _ = _unpack(states[2 * i + 2])
        side_now: Court = "near" if s.side_index(states[2 * i + 1]) == 0 else "far"
        p_none = min(1.0, p_opt.get(-1, 0.0))
        p_us = min(1.0, sum(p for k, p in p_opt.items() if k >= 0 and k % 2 == 0))
        p_them = min(1.0, sum(p for k, p in p_opt.items() if k >= 0 and k % 2 == 1))
        p_serve_us = min(1.0, post.serve_us[i])
        box = box_value = box_p = None
        if cells is not None:
            turn = t_pre // 2
            team_cells = cells.pos[s_pre]
            if turn < len(team_cells):
                p, b = team_cells[turn]
                box = (_side_of(s_pre), p, b)
                found = post.boxes.get(box)
                if found is not None:
                    box_value, box_p = found
        common: dict[str, Any] = {
            "p_us": round(p_us, 4),
            "p_them": round(p_them, 4),
            "p_none": round(p_none, 4),
            "p_serve_us": round(p_serve_us, 4),
            "p_missed": round(min(1.0, post.missed[i]), 4),
            "box": box,
            "box_value": box_value,
            "box_p": None if box_p is None else round(box_p, 4),
        }
        if opt == -1:
            extra_total += 1
            mm = post.cheapest[i]
            point = min((v for k, v in mm.items() if k >= 0), default=math.inf)
            conf = _confidence({-1: mm.get(-1, math.inf), 0: point}, -1)
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
                    p_answer=round(min(1.0, p_score.get((-1, a_after, b_after), 0.0)), 4),
                    **common,
                )
            )
            continue
        srv, w = divmod(opt, 2)
        mm = post.cheapest[i]
        by_winner = {
            ww: min((v for k, v in mm.items() if k >= 0 and k % 2 == ww), default=math.inf)
            for ww in (0, 1)
        }
        conf = _confidence(by_winner, w)
        serve_p = None
        if r.p_near is not None:
            p_us_near = r.p_near if side_now == "near" else 1.0 - r.p_near
            serve_p = p_us_near if srv == 0 else 1.0 - p_us_near
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
                p_answer=round(min(1.0, p_score.get((w, a_after, b_after), 0.0)), 4),
                **common,
            )
        )
    fa, fb, _, _ = _unpack(best_state)
    la, lb, _, _ = _unpack(states[-2])
    sheet: Literal["hint", "read"] | None = (
        "read" if st.cells is not None else "hint" if st.sheet_hint else None
    )
    return SetOut(
        no=st.no,
        cost=finals[best_state] + (cells.base if cells is not None else 0.0),
        first_serve=first,
        our_side=side0,
        final=(fa, fb),
        rallies=rallies_out,
        missed=sum(choice[0::2]),
        extra=extra_total,
        missed_after=(fa - la, fb - lb),
        sheet=sheet,
        p_missed_after=round(min(1.0, post.missed[n]), 4),
        boxes=post.boxes,
    )


@dataclass
class _Post:
    """Posteriors of the free decode: per rally its answers (-1 no point, else server * 2 +
    winner) and its answers with the score after it, that we served; per gap a point missing
    there; per read box its likeliest value and how probable it is."""

    rally: list[dict[int, float]]
    score: list[dict[tuple[int, int, int], float]]
    serve_us: list[float]
    missed: list[float]
    boxes: dict[tuple[Side, int, int], tuple[int | None, float]]
    # Max-marginals: per rally and answer the cheapest whole path with it (the flags' confidence).
    cheapest: list[dict[int, float]] = field(default_factory=list)


def _posteriors(s: _Set, fw: _Pass) -> _Post:
    """Backward over the states the forward pass kept, all paths: each edge's posterior is
    exp(-(forward + edge + backward - total))."""
    n = len(s.rallies)
    T = TEMPERATURE
    last = fw.sums[-1]
    finals = {k: v + s.terminal(k) / T for k, v in last.items()}
    log_z = _softmin(list(finals.values()))
    rally: list[dict[int, float]] = [{} for _ in range(n)]
    score: list[dict[tuple[int, int, int], float]] = [{} for _ in range(n)]
    serve_us = [0.0] * n
    missed = [0.0] * (n + 1)
    box_mass: dict[tuple[int, int, int], float] = {}  # (team, turn, value) → P
    beta: Layer = {k: s.terminal(k) / T for k in last}
    beta_min: Layer = {k: s.terminal(k) for k in fw.mins[-1]}
    cheapest: list[dict[int, float]] = [{} for _ in range(n)]
    cells = s.cells
    if cells is not None:
        for k, a_sum in last.items():
            a, b, sv, t = _unpack(k)
            bt = beta[k]
            if not math.isfinite(bt):
                continue
            p = math.exp(log_z - (a_sum + bt))
            value = a if sv == 0 else b
            if cells.box(sv, t // 2, value) <= FINAL_MISSING:
                key = (sv, t // 2, value)
                box_mass[key] = box_mass.get(key, 0.0) + p
    for li in range(len(fw.mins) - 2, -1, -1):
        is_gap = li % 2 == 0
        j = li // 2
        src = fw.sums[li]
        nb: Layer = {}
        nb_min: Layer = {}
        for state, a_min in fw.mins[li].items():
            a_sum = src.get(state, math.inf)
            edges = s.gap_edges(state, j) if is_gap else s.rally_edges(state, j)
            costs = []
            least = math.inf
            for nxt, c, opt, so, _, hard in edges:
                bn = beta.get(nxt)
                if bn is None or not math.isfinite(bn):
                    continue
                ct = _tempered(c, hard)
                costs.append(ct + bn)
                bm = beta_min.get(nxt, math.inf)
                least = min(least, c + bm)
                if not is_gap and a_min + c + bm < cheapest[j].get(opt, math.inf):
                    cheapest[j][opt] = a_min + c + bm
                if not math.isfinite(a_sum):
                    continue
                p = math.exp(log_z - (a_sum + ct + bn))
                if p < 1e-9:
                    continue
                if is_gap:
                    if opt > 0:
                        missed[j] += p
                else:
                    rally[j][opt] = rally[j].get(opt, 0.0) + p
                    if opt >= 0:
                        a1, b1, _, _ = _unpack(nxt)
                        key3 = (opt % 2, a1, b1)
                    else:
                        a1, b1, _, _ = _unpack(state)
                        key3 = (-1, a1, b1)
                    score[j][key3] = score[j].get(key3, 0.0) + p
                    if _unpack(state)[2] == 0:
                        serve_us[j] += p
                for team, turn, value in so:
                    key = (team, turn, value)
                    box_mass[key] = box_mass.get(key, 0.0) + p
            nb[state] = _softmin(costs)
            nb_min[state] = least
        beta = nb
        beta_min = nb_min
    boxes: dict[tuple[Side, int, int], tuple[int | None, float]] = {}
    if cells is not None:
        by_box: dict[tuple[int, int], dict[int, float]] = {}
        for (team, turn, value), p in box_mass.items():
            by_box.setdefault((team, turn), {})[value] = p
        for team in (0, 1):
            for turn, pos in enumerate(cells.pos[team]):
                found = by_box.get((team, turn), {})
                used = sum(found.values())
                best_v, best_p = max(found.items(), key=lambda kv: kv[1], default=(None, 0.0))
                blank = max(0.0, 1.0 - used)
                key4 = (_side_of(team), pos[0], pos[1])
                if blank >= best_p:
                    boxes[key4] = (None, round(min(1.0, blank), 4))
                else:
                    boxes[key4] = (best_v, round(min(1.0, best_p), 4))
    return _Post(rally, score, serve_us, missed, boxes, cheapest)


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
        replace(r, confidence=min(r.confidence, 0.5), note=r.note or "unsure", p_answer=0.0)
        for r in out.rallies
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
        share = 1.0 if told else _share(list(totals.values()))
        shares.append(share)
        for i in regular:
            chosen[i] = run(i, *hypothesis(sets[i], side_1, first_1))
            chosen[i].sides_share = round(share, 4)
    for i, st in enumerate(sets):
        if not st.deciding:
            continue
        options = [
            (cost(i, side, first), side, first)
            for side in ((st.our_side,) if st.our_side else COURTS)
            for first in ((st.first_serve,) if st.first_serve else SIDES)
        ]
        _, side, first = min(options, key=lambda o: o[0])
        share = _share([o[0] for o in options]) if len(options) > 1 else 1.0
        shares.append(share)
        chosen[i] = run(i, side, first)
        chosen[i].sides_share = round(share, 4)
    return MatchOut([chosen[i] for i in range(len(sets))], round(min(shares, default=1.0), 3))


# --- sure or not: what the coach is asked about --------------------------------------------------


def judge(
    st: SetIn,
    so: SetOut,
    *,
    extra: Mapping[str, Sequence[Reason]] | None = None,
    sure: float = SURE,
) -> SetOut:
    """Each rally sure or not, and why not (PLAN §10.1, §10.3). A rally is sure when the coach
    decided it (their winner is a fact), or when its decoded answer with its score line is at
    least `sure` probable (times how sure our side and the first server are) and no source that
    speaks about it disagrees: the log, the board, the sheet, the serve read in the video; and no
    point is missing next to it. `extra`: more reasons by rally key (a timeout that does not fit
    the score there)."""
    obs = {r.key: r for r in st.rallies}
    outs = so.rallies
    out: list[RallyOut] = []
    for k, ro in enumerate(outs):
        r = obs.get(ro.key)
        reasons: list[Reason] = list((extra or {}).get(ro.key, ()))
        replay = not ro.point and r is not None and r.proposal is None
        if r is not None and (r.won_fact is not None or (replay and r.log in (None, "none"))):
            # The coach decided it: their winner is a fact; a rally they accepted that is no
            # point is a replay.
            out.append(replace(ro, sure=True, reasons=()))
            continue
        if r is None:
            out.append(replace(ro, sure=False, reasons=(Reason("unsure"),)))
            continue
        decided = ro.won_by if ro.point else "none"
        own: list[Reason] = []
        # The log and the board, where they speak.
        if r.log is not None and r.log_p > 0 and r.log != decided:
            if r.log == "none":
                own.append(Reason("log_none"))
            elif so.sheet == "path" and ro.point:
                own.append(Reason("sheet_log", {"log": r.log, "sheet": decided}))
            else:
                own.append(Reason("log", {"team": r.log}))
        elif r.board is not None and r.board_p > 0 and r.log is None and r.board != decided:
            own.append(Reason("board", {"team": r.board}))
        elif ro.note in ("log", "board") and (r.log == decided or r.board == decided):
            # The log (or the board) decided against what the video alone was sure of
            # (wieplay/core/scorelog.py `flag_disagreements`).
            own.append(Reason("video", {"vs": ro.note}))
        if (
            ro.point
            and r.log_score is not None
            and r.log == decided
            and (ro.score_us, ro.score_them) != r.log_score
        ):
            own.append(Reason("log_score", {"score": list(r.log_score)}))
        if ro.note == "coach":
            own.append(Reason("coach"))  # a serve the coach called, against the score
        if not ro.point and not any(x.code in ("log_none", "log") for x in own):
            # No point: a replay, or no rally (reject it); "no point on the board log" where the
            # log says so too.
            own.append(Reason("log_none" if r.log == "none" else "extra"))
        # The video: this rally's serve, and the next rally's serve (this rally's winner).
        if ro.point and ro.won_by is not None:
            nxt = _next_point(outs, k)
            if nxt is not None and nxt.p_missed < MISSED_NEAR:
                nr = obs.get(nxt.key)
                if (
                    nr is not None
                    and nr.p_near is not None
                    and nr.serve_fact is None
                    and nxt.serve_p is not None
                    and nxt.serve_p <= 1.0 - SOURCE_SURE
                    and nxt.serving == ro.won_by
                    and not any(x.code == "video" for x in own)
                ):
                    source = "sheet" if so.sheet else "log" if r.log is not None else "score"
                    own.append(Reason("video", {"vs": source}))
        if (
            ro.point
            and ro.serve_p is not None
            and ro.serve_p <= 1.0 - SOURCE_SURE
            and r.serve_fact is None
            and _prev_point(outs, k) is None  # else the rally before it carries the doubt
        ):
            own.append(Reason("serve"))
        # A sheet read from its photo: the box of this rally's turn, read otherwise or unclear.
        if so.sheet == "read" and ro.box is not None and ro.point:
            read = (st.cells.boxes.get(ro.box[0], {}) if st.cells else {}).get(
                (ro.box[1], ro.box[2]), ()
            )
            fixed = (st.cells.fixed.get(ro.box[0], {}) if st.cells else {}).get(
                (ro.box[1], ro.box[2]), "-"
            )
            if fixed == "-":
                ranked = sorted(read, key=lambda x: -x[1])
                values = [v for v, _ in ranked if isinstance(v, int)]
                top, top_p = ranked[0] if ranked else (None, 0.0)
                disagrees = (
                    ro.box_value is not None
                    and isinstance(top, int)
                    and top != ro.box_value
                    and top_p >= BOX_DISAGREES
                )
                unclear = (
                    ro.box_p is not None
                    and ro.box_p < BOX_SURE
                    and ro.p_answer * so.sides_share < sure
                    and len(values) > 1
                )
                if disagrees or unclear:
                    shown = [ro.box_value] if ro.box_value is not None else []
                    shown += [v for v in values if v not in shown][: 2 - len(shown)]
                    own.append(Reason("sheet_box", {"values": shown[:2], "team": ro.box[0]}))
        # A point without a rally next to it: a rally the finder missed.
        # (Not a penalty point: it has no rally by the rules.)
        if ro.p_missed >= MISSED_NEAR or ro.note == "missing_before":
            own.append(Reason("missing_before"))
        elif k == len(outs) - 1 and (
            so.p_missed_after >= MISSED_NEAR or ro.note == "missing_after"
        ):
            own.append(Reason("missing_after"))
        p = ro.p_answer * so.sides_share
        if p < sure and not own and not reasons:
            if so.sides_share < sure and ro.p_answer >= sure:
                own.append(Reason("sides"))
            else:
                own.append(Reason("unsure", {"p": round(p, 3)}))
        reasons = own + reasons
        out.append(replace(ro, sure=not reasons and p >= sure, reasons=tuple(reasons)))
    so.rallies = out
    return so


def _next_point(outs: Sequence[RallyOut], k: int) -> RallyOut | None:
    for ro in outs[k + 1 :]:
        if ro.point:
            return ro
    return None


def _prev_point(outs: Sequence[RallyOut], k: int) -> RallyOut | None:
    for ro in reversed(outs[:k]):
        if ro.point:
            return ro
    return None


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
    end = _forward(s, sums=False).mins[-1]
    if not end:
        return None
    best = min(end, key=lambda k: end[k] + s.terminal(k))
    a, b, _, _ = _unpack(best)
    return a, b
