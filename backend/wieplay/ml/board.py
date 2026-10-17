"""The scoreboard over a match (PLAN §10.3, step 5): from readings every half second to the
scores it showed and when. Plain Python, no numpy: the worker smooths, the API reads the
result when it scores a video.

1. **Stable runs**: confident readings of the same score in a row (a head in front of the
   board in between does not break a run; one stray misreading inside a run is ignored). A
   score counts only when the board showed it for at least 2 s.
2. **Consistent path**: of those runs, the sequence with the most support in which the score
   only goes up, +1 per point (a jump of a few points when the board was hidden, at a cost),
   back to about 0:0 at a new set (cheap when the set count, if read, went up). Runs off that
   path are misreadings.
3. **Segments**: the kept runs, each a time range with the score it showed; the API asks "what
   did the board show at this moment" (unknown between two segments: the flip happened then).
"""

from __future__ import annotations

import base64
import json
import zlib
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal

FORMAT = 1
STEP_MS = 500  # 2 readings a second
MIN_CONF = 0.5
MIN_SAMPLES = 4  # 2 s at 2 fps
MIN_SPAN_MS = 1500
MAX_JUMP = 8  # points the board can move while it was hidden
JUMP_COST = 0.4  # per point beyond the first: missed flips are common when it is hidden
NEW_SET_COST = 2.0
NEW_SET_NOT_OVER = 3.0  # a new set although the score before it did not end a set
NEW_SET_MAX = 3  # a new set starts near 0:0
LOOKBACK = 80
NOISE_SAMPLES = 2  # a different reading this short between two runs of one score is noise

ChangeKind = Literal["start", "step", "jump", "set"]


@dataclass(frozen=True)
class Sample:
    t_ms: int
    left: int | None
    right: int | None
    confidence: float = 0.0
    sets: tuple[int, int] | None = None


@dataclass
class Run:
    left: int
    right: int
    first_ms: int
    last_ms: int
    samples: int
    conf_sum: float
    sets: tuple[int, int] | None = None

    @property
    def support(self) -> float:
        return self.conf_sum

    @property
    def confidence(self) -> float:
        return self.conf_sum / max(1, self.samples)


@dataclass(frozen=True)
class Segment:
    """What the board showed from first_ms to last_ms (both seen)."""

    first_ms: int
    last_ms: int
    left: int
    right: int
    set_no: int
    confidence: float
    kind: ChangeKind


def _sets_mode(samples: Sequence[Sample]) -> tuple[int, int] | None:
    counts: dict[tuple[int, int], int] = {}
    for s in samples:
        if s.sets is not None:
            counts[s.sets] = counts.get(s.sets, 0) + 1
    if not counts:
        return None
    best = max(counts, key=lambda k: counts[k])
    return best if counts[best] >= 2 else None


def runs(samples: Sequence[Sample], min_conf: float = MIN_CONF) -> list[Run]:
    """Confident readings grouped into runs of one score (in time order); short stray
    readings between two runs of the same score are dropped and the runs joined."""
    sure = [
        s
        for s in sorted(samples, key=lambda s: s.t_ms)
        if s.left is not None and s.right is not None and s.confidence >= min_conf
    ]
    groups: list[list[Sample]] = []
    for s in sure:
        if groups and (groups[-1][0].left, groups[-1][0].right) == (s.left, s.right):
            groups[-1].append(s)
        else:
            groups.append([s])
    # Join A, noise, A.
    merged = True
    while merged:
        merged = False
        for i in range(1, len(groups) - 1):
            a, x, b = groups[i - 1], groups[i], groups[i + 1]
            same = (a[0].left, a[0].right) == (b[0].left, b[0].right)
            if same and len(x) <= NOISE_SAMPLES and len(x) < min(len(a), len(b)):
                groups[i - 1 : i + 2] = [a + b]
                merged = True
                break
    out = []
    for g in groups:
        left, right = g[0].left, g[0].right
        assert left is not None and right is not None
        out.append(
            Run(
                left,
                right,
                g[0].t_ms,
                g[-1].t_ms,
                len(g),
                sum(s.confidence for s in g),
                _sets_mode(g),
            )
        )
    return out


def stable(found: Sequence[Run]) -> list[Run]:
    return [r for r in found if r.samples >= MIN_SAMPLES and r.last_ms - r.first_ms >= MIN_SPAN_MS]


def set_over(a: int, b: int) -> bool:
    """Could end a set: 15 (a deciding set) or more, two clear."""
    return max(a, b) >= 15 and abs(a - b) >= 2


def _step(a: Run, b: Run) -> tuple[float, ChangeKind] | None:
    """The cost of the board going from run a to run b, and how; None when it cannot."""
    sets_a, sets_b = a.sets, b.sets
    more_sets = sets_a is not None and sets_b is not None and sum(sets_b) > sum(sets_a)
    dl, dr = b.left - a.left, b.right - a.right
    if dl >= 0 and dr >= 0 and 1 <= dl + dr <= MAX_JUMP and not more_sets:
        d = dl + dr
        return JUMP_COST * (d - 1), ("step" if d == 1 else "jump")
    if b.left + b.right <= NEW_SET_MAX and (a.left + a.right) > (b.left + b.right):
        cost = NEW_SET_COST if more_sets else NEW_SET_COST + 1.0
        if not set_over(a.left, a.right):
            cost += NEW_SET_NOT_OVER
        return cost, "set"
    return None


def path(found: Sequence[Run]) -> list[tuple[Run, ChangeKind]]:
    """The consistent sequence of runs with the most support (minus the cost of jumps and new
    sets); every other run is a misreading."""
    n = len(found)
    if n == 0:
        return []
    best = [r.support for r in found]
    back: list[int] = [-1] * n
    kinds: list[ChangeKind] = ["start"] * n
    for j in range(n):
        for i in range(max(0, j - LOOKBACK), j):
            step = _step(found[i], found[j])
            if step is None:
                continue
            value = best[i] + found[j].support - step[0]
            if value > best[j]:
                best[j], back[j], kinds[j] = value, i, step[1]
    j = max(range(n), key=lambda k: best[k])
    out: list[tuple[Run, ChangeKind]] = []
    while j >= 0:
        out.append((found[j], kinds[j]))
        j = back[j]
    out.reverse()
    return out


def segments(samples: Sequence[Sample], min_conf: float = MIN_CONF) -> list[Segment]:
    """The board over the match: what it showed and when (module docstring)."""
    out: list[Segment] = []
    set_no = 1
    for run, kind in path(stable(runs(samples, min_conf))):
        if kind == "set":
            set_no += 1
        sets = run.sets
        out.append(
            Segment(
                run.first_ms,
                run.last_ms,
                run.left,
                run.right,
                sum(sets) + 1 if sets is not None else set_no,
                round(run.confidence, 3),
                kind,
            )
        )
    return out


def summary(samples: Sequence[Sample], found: Sequence[Segment]) -> dict[str, Any]:
    read = sum(1 for s in samples if s.left is not None and s.confidence >= MIN_CONF)
    return {
        "samples": len(samples),
        "read": read,
        "read_share": round(read / len(samples), 4) if samples else None,
        "segments": len(found),
        "covered_ms": sum(s.last_ms - s.first_ms for s in found),
        "jumps": sum(1 for s in found if s.kind == "jump"),
        "sets": max((s.set_no for s in found), default=0),
    }


# --- stored with the video (analysis_track kind "scoreboard") ---------------------------------


def pack(found: Sequence[Segment]) -> dict[str, str]:
    rows = [[s.first_ms, s.last_ms, s.left, s.right, s.set_no, s.confidence, s.kind] for s in found]
    data = json.dumps({"format": FORMAT, "segments": rows}, separators=(",", ":")).encode()
    return {"segments": base64.b64encode(zlib.compress(data, 9)).decode()}


def unpack(channels: dict[str, str]) -> list[Segment]:
    """Raises ValueError on anything that is not a packed list of segments."""
    try:
        raw = json.loads(zlib.decompress(base64.b64decode(channels["segments"], validate=True)))
        rows = raw["segments"]
        out = [
            Segment(int(r[0]), int(r[1]), int(r[2]), int(r[3]), int(r[4]), float(r[5]), r[6])
            for r in rows
        ]
    except (KeyError, TypeError, IndexError, ValueError, zlib.error) as exc:
        raise ValueError(f"not a scoreboard track: {exc}") from exc
    for s in out:
        if (
            s.last_ms < s.first_ms
            or s.left < 0
            or s.right < 0
            or s.kind not in ("start", "step", "jump", "set")
        ):
            raise ValueError("bad scoreboard segment")
    return out


def as_dicts(found: Sequence[Segment]) -> list[dict[str, Any]]:
    return [asdict(s) for s in found]


# --- the board at a moment ----------------------------------------------------------------------


class Timeline:
    """What the board showed at a given time, where it was seen."""

    def __init__(self, found: Sequence[Segment]) -> None:
        self.segments = sorted(found, key=lambda s: s.first_ms)

    def at(self, t_ms: int, slack_ms: int = 0) -> Segment | None:
        for s in self.segments:
            if s.first_ms - slack_ms <= t_ms <= s.last_ms + slack_ms:
                return s
            if s.first_ms - slack_ms > t_ms:
                break
        return None

    def within(self, start_ms: int, end_ms: int) -> list[Segment]:
        return [s for s in self.segments if s.last_ms >= start_ms and s.first_ms <= end_ms]
