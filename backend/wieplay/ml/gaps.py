"""What happens between rallies (PLAN §10.1): warm-up, set breaks, timeouts, stoppages.
Plain Python: the API labels the gaps whenever it scores a video.

Everything between rallies is dead time; most of it is the 10-25 s between two rallies and
stays unlabelled. The rest:

- **warmup**: before the first serve of the match;
- **set_break**: between the last rally of a set and the first of the next;
- **timeout**: a team timeout in a set: a gap of ~30-120 s in which the players go to their
  benches (movement along the sides of the picture, little on court, no ball in play) and the
  referee whistles at its start and end. At most two per team and set;
- **stoppage**: another long interruption in a set (substitutions, an injury, a discussion,
  ball retrieval);
- **other_dead**: after the last rally of the match.

Without the activity of the video (analysed before it was kept), timeouts and stoppages are
told apart by their length only, with less confidence.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

from wieplay.ml.tracks import Series

GapKind = Literal["warmup", "set_break", "timeout", "stoppage", "other_dead"]
KINDS: tuple[GapKind, ...] = ("warmup", "set_break", "timeout", "stoppage", "other_dead")

MIN_LABEL_S = 30.0  # shorter gaps inside a set are ordinary dead time
MIN_STOPPAGE_S = 40.0  # a gap this long that is no timeout is a stoppage
MIN_EDGE_S = 20.0  # warm-up and after the match: only when this long
TIMEOUTS_PER_SET = 4  # two per team
PLAY_SHARE = 0.25  # a second with this share of frames with the ball in play is play


@dataclass(frozen=True)
class Activity:
    """The video's activity track (per second), as far as it is known."""

    ball: Series | None = None
    court: Series | None = None
    side: Series | None = None
    whistle: Series | None = None


@dataclass(frozen=True)
class GapEvidence:
    seconds: float
    ball_s: float  # seconds with the ball in play
    court: float | None  # movement in the middle of the court, x the video's median
    side: float | None  # movement along the sides (benches)
    whistles: int  # whistles in the middle of the gap


def evidence(act: Activity, start_ms: int, end_ms: int, margin_ms: int = 2000) -> GapEvidence:
    """Activity inside a gap, leaving out its first and last two seconds (the end whistle of
    one rally and the toss of the next) and the serve whistle's last 8 s."""
    a, b = start_ms + margin_ms, end_ms - margin_ms
    seconds = max(0.0, (end_ms - start_ms) / 1000)
    if b <= a:
        return GapEvidence(seconds, 0.0, None, None, 0)
    ball_s = 0.0
    if act.ball is not None:
        for k in range(a // 1000, -(-b // 1000)):
            v = act.ball.value(k)
            if v is not None and v >= PLAY_SHARE:
                ball_s += 1.0
    court = act.court.mean(a, b) if act.court is not None else None
    side = act.side.mean(a, b) if act.side is not None else None
    whistles = act.whistle.total(a, end_ms - 8000) if act.whistle is not None else 0
    return GapEvidence(seconds, ball_s, court, side, max(0, whistles))


@dataclass(frozen=True)
class GapLabel:
    kind: GapKind
    start_ms: int
    end_ms: int
    confidence: float


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def timeout_probability(ev: GapEvidence) -> float:
    """How much a long gap inside a set looks like a team timeout."""
    s = ev.seconds
    # Length: a 30 s timeout plus walking is 40-90 s; shorter is a substitution, much longer
    # an injury or a discussion.
    if s < 35:
        z = -1.0 - (35 - s) / 5
    elif s <= 100:
        z = 0.8
    else:
        z = 0.8 - (s - 100) / 30
    if ev.side is not None:
        z += 3.0 * (ev.side - 1.5)  # the players at the benches
    if ev.court is not None:
        z -= 1.5 * max(0.0, ev.court - 1.1)
    z -= 0.4 * max(0.0, ev.ball_s - 3)  # play: not a timeout
    if ev.whistles >= 1:
        z += 0.5
    return _sigmoid(z)


@dataclass(frozen=True)
class SetRange:
    first_ms: int  # start of the set's first rally
    last_ms: int  # end of its last rally


def classify(
    rallies: Sequence[tuple[int, int]],
    sets: Sequence[SetRange],
    duration_ms: int,
    act: Activity,
) -> list[GapLabel]:
    """Label the gaps of a video: rallies (start, end in ms, in time order, the coach's and
    the open proposals) and its sets in time order."""
    out: list[GapLabel] = []
    if not rallies:
        return out
    spans = sorted(rallies)
    if sets:
        first = sets[0].first_ms
        if first >= MIN_EDGE_S * 1000:
            conf = 0.95 if first >= 120_000 else 0.7
            out.append(GapLabel("warmup", 0, first, conf))
        for prev, nxt in pairwise(sets):
            if nxt.first_ms > prev.last_ms:
                out.append(GapLabel("set_break", prev.last_ms, nxt.first_ms, 0.9))
        last = sets[-1].last_ms
        if duration_ms - last >= MIN_EDGE_S * 1000:
            out.append(GapLabel("other_dead", last, duration_ms, 0.8))
    for st in sets:
        inside = [r for r in spans if st.first_ms <= r[0] and r[1] <= st.last_ms]
        candidates: list[tuple[float, GapLabel]] = []
        for (_, a), (b, _) in pairwise(inside):
            if (b - a) / 1000 < MIN_LABEL_S:
                continue
            ev = evidence(act, a, b)
            p = timeout_probability(ev)
            if p < 0.5 and ev.seconds < MIN_STOPPAGE_S:
                continue  # slow, but ordinary dead time
            candidates.append((p, GapLabel("timeout" if p >= 0.5 else "stoppage", a, b, 0.0)))
        # At most four timeouts per set: the likeliest ones.
        ranked = sorted(candidates, key=lambda c: -c[0])
        for n, (p, label) in enumerate(ranked):
            kind: GapKind = label.kind
            if kind == "timeout" and n >= TIMEOUTS_PER_SET:
                kind = "stoppage"
            conf = p if kind == "timeout" else 1.0 - p
            if act.side is None:
                conf = min(conf, 0.6)  # by length only
            out.append(GapLabel(kind, label.start_ms, label.end_ms, round(max(0.5, conf), 3)))
    return sorted(out, key=lambda g: g.start_ms)
