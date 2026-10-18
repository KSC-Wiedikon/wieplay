"""The club scoreboard's log of a game against its video (PLAN §10.10). Plain Python: the API
runs it.

The scorer at the club's LED scoreboard (point-hub) presses +1 a few seconds after each rally;
Wiedisync keeps every press with the board's clock and the bridge's `score-log` hands them over
by team (a = the home team, b = the away team), corrections already replayed. The video has a
clock of its own (a YouTube import has none at all), so the two are lined up here:

1. **One shift** (video ms = board ms + shift) for the whole log. Every pair (press, rally end)
   votes for the shift that puts the press 5 s after that end; around the busiest seconds the
   shift is refined to the one that puts most presses 0.3-8 s after a rally's end and the
   board's timeouts and set ends into long breaks between rallies (the anchors that tell a
   true shift from one that is a rally off); the middle of the best plateau wins.
2. **Per stretch** (a set, or the part of one after 10 minutes without a press: the board
   offline or restarted, the video cut between two files) the same fit again within 15
   minutes of the match's shift, kept only when it explains clearly more presses.
3. **Presses to rallies**: a monotone assignment (dynamic programme) of each press to the rally
   it follows, at most one per rally. A press fits its rally best 0.5-12 s after the end the
   video has, still in the rally's last part (the end can be late), less and less up to 45 s
   (a late press). A press with no rally is a rally the finder missed (counted before the next
   rally that has one); a rally with no press between two consecutive presses that found
   theirs (no untimed points or reset between them) is no point: a replay, or not a rally.
4. **Trust per set**: the log's final of a set must be the official one (Wiedisync's `sets`),
   most of its presses must have found a rally and nearly half of them 0.3-8 s after its end
   (a shift a rally off reaches about a third), else the set says nothing.
"""

from __future__ import annotations

import bisect
import itertools
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

Team = Literal["a", "b"]
Says = Literal["a", "b", "none"]
FORMAT = 1
KINDS = frozenset({"point", "jump", "undo", "reset", "set_end", "timeout", "serve", "match_end"})
MAX_EVENTS = 5000

TARGET_DELAY_MS = 5000  # a point is pressed about this long after the rally's end
BIN_MS = 1000
PEAKS = 6
PEAK_GAP_MS = 10_000
REFINE_MS = 6000
REFINE_STEP_MS = 250
PLATEAU_MS = 8000
GOOD_MS = (300, 8000)  # a press this long after a rally's end counts for the fit
LONG_BREAK_MS = 35_000  # a break between rallies this long can hold a timeout
ANCHOR_SLACK_MS = 3000
ANCHOR_WEIGHT = 2.0  # a timeout or set end in a long break counts like two presses
GAP_MS = 10 * 60 * 1000  # a new stretch after this long without a press
MIN_STRETCH = 8  # presses; a shorter stretch keeps the match's shift
STRETCH_SPAN_MS = 15 * 60 * 1000  # a stretch's own shift stays this close to the match's
OWN_SHIFT_GAIN = 0.1  # ... and must explain this share of its presses more (at least 3)
LATE_MS = 45_000  # a press this long after a rally's end is not its
NONE_GAP_MS = 5 * 60 * 1000  # no "no point" between two presses further apart than this
MIN_TRUSTED_MATCHED = 0.6  # a set's presses that found a rally, for its verdicts to count
MIN_FIT_SHARE = 0.45  # ... and that fell 0.3-8 s after a rally's end (a shift off by a rally
# reaches about a third)
NONE_P = 0.6  # how sure "no point" is


def fit_weight(delay_ms: float, inside: bool = False) -> float:
    """How well a press `delay_ms` after a rally's end fits that rally (0: not at all). `inside`:
    the press fell inside the rally as the video has it, 3 s after its start or later (the end
    can be late: the finder's, or a coach's that keeps the players' reaction)."""
    if delay_ms < -3000:
        return 0.6 if inside else 0.0
    if delay_ms < 500:
        return 0.8
    if delay_ms <= 12_000:
        return 1.0
    if delay_ms <= 25_000:
        return 0.8 - 0.5 * (delay_ms - 12_000) / 13_000
    if delay_ms <= LATE_MS:
        return 0.1
    return 0.0


def weight_at(v_ms: int, start_ms: int, end_ms: int) -> float:
    """fit_weight of a press at video time v for the rally start..end."""
    return fit_weight(v_ms - end_ms, inside=start_ms + 3000 <= v_ms < end_ms)


# --- the log ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LogEvent:
    kind: str
    t_ms: int | None  # the board's clock
    server_ms: int | None  # when Wiedisync received it (revisions only)
    period: int
    score: tuple[int, int]  # the set's score (a, b) after it
    sets: tuple[int, int]
    side: Team | None = None
    serving: Team | None = None
    delta: tuple[int, int] | None = None


@dataclass(frozen=True)
class ScoreLog:
    source: str  # live_match_logs | live_scores_revisions (OpenVolley's e-scoresheet: later)
    events: tuple[LogEvent, ...]
    finals: dict[int, tuple[int, int]]  # set no -> (a, b)
    board_names: tuple[str | None, str | None] = (None, None)
    channel: str | None = None

    @property
    def points(self) -> list[LogEvent]:
        """The presses with a time, in the board's order."""
        return [e for e in self.events if e.kind == "point" and e.t_ms is not None]


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return round(value)
    return None


def _team(value: Any) -> Team | None:
    if value == "a":
        return "a"
    return "b" if value == "b" else None


def parse(payload: Mapping[str, Any]) -> ScoreLog | None:
    """The bridge's answer -> a ScoreLog; None when the game has no log. ValueError on junk."""
    if payload.get("version") != FORMAT:
        raise ValueError("unknown score log version")
    source = payload.get("source")
    if source is None:
        return None
    raw = payload.get("events")
    if not isinstance(source, str) or not isinstance(raw, list) or len(raw) > MAX_EVENTS:
        raise ValueError("not a score log")
    events = []
    for e in raw:
        if not isinstance(e, dict) or e.get("kind") not in KINDS:
            raise ValueError("bad score log event")
        period = _int(e.get("period"))
        a, b = _int(e.get("points_a")), _int(e.get("points_b"))
        if period is None or not 1 <= period <= 9 or a is None or b is None or a < 0 or b < 0:
            raise ValueError("bad score log event")
        delta = e.get("delta")
        events.append(
            LogEvent(
                kind=str(e["kind"]),
                t_ms=_int(e.get("board_ms")),
                server_ms=_int(e.get("server_ms")),
                period=period,
                score=(a, b),
                sets=(_int(e.get("sets_a")) or 0, _int(e.get("sets_b")) or 0),
                side=_team(e.get("side")),
                serving=_team(e.get("serving")),
                delta=(_int(delta[0]) or 0, _int(delta[1]) or 0)
                if isinstance(delta, list) and len(delta) == 2
                else None,
            )
        )
    finals: dict[int, tuple[int, int]] = {}
    for s in payload.get("sets") or []:
        if isinstance(s, dict):
            no, a, b = _int(s.get("no")), _int(s.get("a")), _int(s.get("b"))
            if no is not None and a is not None and b is not None:
                finals[no] = (a, b)
    names = payload.get("board_names")
    channel = payload.get("channel")
    return ScoreLog(
        source=source,
        events=tuple(events),
        finals=finals,
        board_names=(names.get("a"), names.get("b")) if isinstance(names, dict) else (None, None),
        channel=channel if isinstance(channel, str) else None,
    )


# --- the shift ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Fit:
    shift_ms: int
    matched: int  # presses 0.3-8 s after a rally's end
    anchors: int  # timeouts and set ends that fell in a long break between rallies
    score: float  # sum of fit weights, each press against the latest rally end before it
    median_delay_ms: float

    @property
    def key(self) -> tuple[float, float]:
        return self.matched + ANCHOR_WEIGHT * self.anchors, self.score


@dataclass(frozen=True)
class VideoShape:
    """The video's rallies as the fit sees them: their ends (sorted) and the long breaks."""

    ends: list[int]
    breaks: list[tuple[int, int]]  # (end of a rally, start of the next), LONG_BREAK_MS or more

    @staticmethod
    def of(spans: Sequence[tuple[int, int]]) -> VideoShape:
        spans = sorted(spans)
        breaks = [
            (a[1], b[0]) for a, b in itertools.pairwise(spans) if b[0] - a[1] >= LONG_BREAK_MS
        ]
        return VideoShape(sorted(e for _, e in spans), breaks)


def quality(
    times: Sequence[int], video: VideoShape, shift: int, anchors: Sequence[int] = ()
) -> Fit:
    """The presses (board ms) shifted against the latest rally end before each (+3 s); the
    anchors against the long breaks."""
    ends = video.ends
    total = 0.0
    good: list[int] = []
    for t in times:
        v = t + shift
        k = bisect.bisect_right(ends, v + 3000) - 1
        if k < 0:
            continue
        r = v - ends[k]
        total += fit_weight(r)
        if GOOD_MS[0] <= r <= GOOD_MS[1]:
            good.append(r)
    inside = 0
    starts = [b[0] for b in video.breaks]
    for t in anchors:
        v = t + shift
        k = bisect.bisect_right(starts, v + ANCHOR_SLACK_MS) - 1
        if k >= 0 and video.breaks[k][0] - ANCHOR_SLACK_MS <= v <= video.breaks[k][1]:
            inside += 1
    good.sort()
    median = float(good[len(good) // 2]) if good else math.inf
    return Fit(shift, len(good), inside, round(total, 4), median)


def fit_shift(
    times: Sequence[int],
    video: VideoShape,
    anchors: Sequence[int] = (),
    *,
    near: int | None = None,
    span_ms: int = 0,
) -> Fit | None:
    """The shift (video = board + shift) that puts most presses shortly after a rally's end and
    the board's timeouts and set ends into long breaks. `near`: only within `span_ms` of it."""
    if not times or not video.ends:
        return None
    votes: dict[int, int] = {}
    for t in times:
        for e in video.ends:
            s = e + TARGET_DELAY_MS - t
            if near is None or abs(s - near) <= span_ms:
                b = round(s / BIN_MS)
                votes[b] = votes.get(b, 0) + 1
    if not votes:
        return None
    smooth = {b: sum(votes.get(b + d, 0) for d in (-2, -1, 0, 1, 2)) for b in votes}
    peaks: list[int] = []
    for b in sorted(smooth, key=lambda x: (-smooth[x], x)):
        if all(abs(b - p) * BIN_MS >= PEAK_GAP_MS for p in peaks):
            peaks.append(b)
        if len(peaks) >= PEAKS:
            break
    fits = [
        quality(times, video, p * BIN_MS + step, anchors)
        for p in peaks
        for step in range(-REFINE_MS, REFINE_MS + 1, REFINE_STEP_MS)
        if near is None or abs(p * BIN_MS + step - near) <= span_ms
    ]
    if not fits:
        return None
    best = max(fits, key=lambda f: f.key)
    # The middle of the best plateau (within one press of the best, close to it): a press is
    # then as likely early as late in the window.
    plateau = sorted(
        {
            f.shift_ms
            for f in fits
            if abs(f.shift_ms - best.shift_ms) <= PLATEAU_MS and best.key[0] - f.key[0] <= 1
        }
    )
    return quality(times, video, plateau[len(plateau) // 2], anchors)


# --- stretches, assignment, verdicts ------------------------------------------------------------


@dataclass(frozen=True)
class RallySpan:
    key: str
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class Verdict:
    """What the log says about one rally: who won it ("a", "b") or "none" (no point); the set
    (the log's) and its score (a, b) after the rally; when the point was pressed (video ms),
    how long after the rally's end, and how well that fits (0..1)."""

    key: str
    says: Says
    p: float
    period: int
    score: tuple[int, int] | None = None
    at_ms: int | None = None
    delay_ms: int | None = None


@dataclass
class Stretch:
    period: int
    first: int  # indices into the log's timed presses
    last: int
    shift_ms: int
    own: bool  # its own shift, not the match's
    points: int = 0
    matched: int = 0  # presses that found a rally
    anchors: int = 0
    fit: int = 0  # presses 0.3-8 s after a rally's end


@dataclass
class Alignment:
    shift_ms: int | None  # the match's
    stretches: list[Stretch]
    verdicts: dict[str, Verdict]  # rally key -> what the log says
    missed: dict[str, tuple[int, int]]  # rally key -> presses (a, b) right before it, no rally
    missed_after: dict[int, tuple[int, int]]  # set -> presses after its last rally with one
    points: int  # timed presses in the log
    matched: int
    delays: list[int] = field(default_factory=list)

    @property
    def matched_share(self) -> float:
        return self.matched / self.points if self.points else 0.0


def _stretches(points: Sequence[LogEvent]) -> list[tuple[int, int, int]]:
    """(set, first, last) runs of the log's timed presses: per set, split at long gaps."""
    out: list[tuple[int, int, int]] = []
    for i, p in enumerate(points):
        prev = points[i - 1].t_ms if i else None
        long_gap = p.t_ms is not None and prev is not None and p.t_ms - prev > GAP_MS
        if not out or p.period != out[-1][0] or long_gap:
            out.append((p.period, i, i))
        else:
            out[-1] = (out[-1][0], out[-1][1], i)
    return out


def _assign(times: Sequence[int], spans: Sequence[RallySpan]) -> list[int | None]:
    """Each press (video ms, ascending) to a rally (an index into spans, sorted by end) or
    None: monotone, one press per rally at most, the sum of fit weights as large as possible."""
    n, m = len(times), len(spans)
    if not n or not m:
        return [None] * n
    ends = [r.end_ms for r in spans]
    # The rallies that can take a press: [lo, hi) per press.
    lo = [bisect.bisect_left(ends, t - LATE_MS) for t in times]
    hi = [bisect.bisect_right(ends, t + 120_000) for t in times]
    # dp[j][i]: the best sum with presses < j and rallies < i; how: 0 skip the press, 1 skip
    # the rally, 2 the press is the rally's.
    dp = [[0.0] * (m + 1) for _ in range(n + 1)]
    how = [[0] * (m + 1) for _ in range(n + 1)]
    for j in range(n + 1):
        for i in range(m + 1):
            best, step = 0.0, 0
            if j > 0:
                best = dp[j - 1][i]
            if i > 0 and dp[j][i - 1] > best:
                best, step = dp[j][i - 1], 1
            if j > 0 and i > 0 and lo[j - 1] <= i - 1 < hi[j - 1]:
                r = spans[i - 1]
                w = weight_at(times[j - 1], r.start_ms, r.end_ms)
                if w > 0 and dp[j - 1][i - 1] + w > best:
                    best, step = dp[j - 1][i - 1] + w, 2
            dp[j][i], how[j][i] = best, step
    out: list[int | None] = [None] * n
    j, i = n, m
    while j > 0 and i > 0:
        if how[j][i] == 2:
            out[j - 1] = i - 1
            j, i = j - 1, i - 1
        elif how[j][i] == 1:
            i -= 1
        else:
            j -= 1
    return out


def _anchors(
    log: ScoreLog, period: int | None = None, span: tuple[int, int] | None = None
) -> list[int]:
    """When the board's timeouts and set ends were pressed (of a set, within a span)."""
    return [
        e.t_ms
        for e in log.events
        if e.kind in ("timeout", "set_end")
        and e.t_ms is not None
        and (period is None or e.period == period)
        and (span is None or span[0] <= e.t_ms <= span[1])
    ]


def _untimed_between(log: ScoreLog, presses: Sequence[LogEvent]) -> Callable[[int, int], bool]:
    """(k0, k1), indices of two timed presses -> does the log have untimed points (a jump) or a
    reset between them?"""
    position = {id(e): n for n, e in enumerate(log.events)}
    at = [position[id(p)] for p in presses]

    def between(k0: int, k1: int) -> bool:
        return any(log.events[n].kind in ("jump", "reset") for n in range(at[k0] + 1, at[k1]))

    return between


def align(log: ScoreLog, rallies: Sequence[RallySpan]) -> Alignment:
    """The log against the video's rallies (module docstring, steps 1-3)."""
    spans = sorted(rallies, key=lambda r: (r.end_ms, r.start_ms))
    shape = VideoShape.of([(r.start_ms, r.end_ms) for r in spans])
    presses = sorted(log.points, key=lambda e: e.t_ms or 0)
    times = [p.t_ms or 0 for p in presses]
    whole = fit_shift(times, shape, _anchors(log))
    if whole is None:
        return Alignment(None, [], {}, {}, {}, len(presses), 0)

    stretches: list[Stretch] = []
    for period, first, last in _stretches(presses):
        part = times[first : last + 1]
        anchors = _anchors(log, period, (part[0] - 60_000, part[-1] + 60_000))
        fit, own = quality(part, shape, whole.shift_ms, anchors), False
        if len(part) >= MIN_STRETCH:
            mine = fit_shift(part, shape, anchors, near=whole.shift_ms, span_ms=STRETCH_SPAN_MS)
            gain = max(3.0, OWN_SHIFT_GAIN * len(part))
            if mine is not None and mine.key[0] >= fit.key[0] + gain:
                fit, own = mine, True
        stretches.append(
            Stretch(period, first, last, fit.shift_ms, own, len(part), 0, fit.anchors, fit.matched)
        )

    video = [0] * len(presses)
    for s in stretches:
        for k in range(s.first, s.last + 1):
            video[k] = times[k] + s.shift_ms
    order = sorted(range(len(presses)), key=lambda k: video[k])
    rally_of: list[int | None] = [None] * len(presses)
    for k, i in zip(order, _assign([video[k] for k in order], spans), strict=True):
        rally_of[k] = i
    for s in stretches:
        s.matched = sum(1 for k in range(s.first, s.last + 1) if rally_of[k] is not None)

    verdicts: dict[str, Verdict] = {}
    delays: list[int] = []
    for k, i in enumerate(rally_of):
        if i is None:
            continue
        p, r = presses[k], spans[i]
        delay = video[k] - r.end_ms
        side: Says = "a" if p.side == "a" else "b"
        weight = round(weight_at(video[k], r.start_ms, r.end_ms), 3)
        verdicts[r.key] = Verdict(r.key, side, weight, p.period, p.score, video[k], delay)
        delays.append(delay)

    # Presses without a rally: before the next rally of the set that took one, else after it.
    missed: dict[str, tuple[int, int]] = {}
    missed_after: dict[int, tuple[int, int]] = {}
    for k, i in enumerate(rally_of):
        if i is not None:
            continue
        p = presses[k]
        nxt = next(
            (
                rally_of[j]
                for j in range(k + 1, len(presses))
                if rally_of[j] is not None and presses[j].period == p.period
            ),
            None,
        )
        add = (1, 0) if p.side == "a" else (0, 1)
        if nxt is None:
            a, b = missed_after.get(p.period, (0, 0))
            missed_after[p.period] = (a + add[0], b + add[1])
        else:
            key = spans[nxt].key
            a, b = missed.get(key, (0, 0))
            missed[key] = (a + add[0], b + add[1])

    # Rallies with no press between two consecutive presses that found theirs: no point,
    # unless the log has untimed points or a reset between them, or a long gap.
    untimed = _untimed_between(log, presses)
    matched = sorted((i, k) for k, i in enumerate(rally_of) if i is not None)
    for (i0, k0), (i1, k1) in itertools.pairwise(matched):
        if i1 - i0 < 2 or k1 != k0 + 1 or presses[k0].period != presses[k1].period:
            continue
        if untimed(k0, k1) or (presses[k1].t_ms or 0) - (presses[k0].t_ms or 0) > NONE_GAP_MS:
            continue
        for i in range(i0 + 1, i1):
            key = spans[i].key
            verdicts.setdefault(key, Verdict(key, "none", NONE_P, presses[k0].period))

    return Alignment(
        whole.shift_ms,
        stretches,
        verdicts,
        missed,
        missed_after,
        len(presses),
        sum(1 for i in rally_of if i is not None),
        delays,
    )


# --- trust --------------------------------------------------------------------------------------


def trust(
    log: ScoreLog, official: Mapping[int, tuple[int, int]] | None, alignment: Alignment
) -> dict[int, dict[str, Any]]:
    """Per set of the log: its final and the official one (a, b), whether they agree, how many
    of its presses found a rally and how many fit well; `trusted` when the finals agree and the
    presses found their rallies."""
    out: dict[int, dict[str, Any]] = {}
    for no in sorted(set(log.finals) | {p.period for p in log.points}):
        final = log.finals.get(no)
        off = official.get(no) if official else None
        points = sum(s.points for s in alignment.stretches if s.period == no)
        matched = sum(s.matched for s in alignment.stretches if s.period == no)
        fit = sum(s.fit for s in alignment.stretches if s.period == no)
        agrees = final is not None and off is not None and tuple(final) == tuple(off)
        share = matched / points if points else 0.0
        fit_share = fit / points if points else 0.0
        out[no] = {
            "final": list(final) if final else None,
            "official": list(off) if off else None,
            "agrees": agrees,
            "points": points,
            "matched": matched,
            "matched_share": round(share, 4),
            "fit_share": round(fit_share, 4),
            "trusted": agrees and share >= MIN_TRUSTED_MATCHED and fit_share >= MIN_FIT_SHARE,
        }
    return out
