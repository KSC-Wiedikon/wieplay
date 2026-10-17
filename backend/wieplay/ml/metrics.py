"""How close proposed rallies are to a coach's rallies (PLAN §10.1: boundary F1 at ±1 s).

Plain Python, no numpy: the API scores proposals with it too. Times are milliseconds.

- **Boundary F1**: every rally has two boundaries (start, end). A proposed boundary counts
  when it lies within the tolerance of a true boundary of the same type, each true boundary
  matched at most once (closest pairs first).
- **Rally F1**: a proposed rally counts when it overlaps a true rally with IoU >= 0.5.
- **Live IoU**: overlap of the live time (union of rallies) over the union of both.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections.abc import Sequence
from typing import Any

Span = tuple[int, int]
TOLERANCE_MS = 1000


def _prf(tp: int, n_pred: int, n_true: int) -> dict[str, float]:
    precision = tp / n_pred if n_pred else (1.0 if n_true == 0 else 0.0)
    recall = tp / n_true if n_true else (1.0 if n_pred == 0 else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def match_points(pred: Sequence[int], true: Sequence[int], tol_ms: int) -> int:
    """One-to-one matches between two sets of times within the tolerance, closest first."""
    truth = sorted(true)
    pairs: list[tuple[int, int, int]] = []
    for i, p in enumerate(pred):
        lo, hi = bisect_left(truth, p - tol_ms), bisect_right(truth, p + tol_ms)
        pairs.extend((abs(truth[j] - p), i, j) for j in range(lo, hi))
    pairs.sort()
    used_p: set[int] = set()
    used_t: set[int] = set()
    for _, i, j in pairs:
        if i not in used_p and j not in used_t:
            used_p.add(i)
            used_t.add(j)
    return len(used_p)


def iou(a: Span, b: Span) -> float:
    inter = min(a[1], b[1]) - max(a[0], b[0])
    if inter <= 0:
        return 0.0
    return inter / (max(a[1], b[1]) - min(a[0], b[0]))


def match_rallies(pred: Sequence[Span], true: Sequence[Span], min_iou: float = 0.5) -> list[float]:
    """IoUs of the one-to-one matches with IoU >= min_iou (best pairs first)."""
    truth = sorted(true)
    starts = [t[0] for t in truth]
    pairs: list[tuple[float, int, int]] = []
    for i, p in enumerate(pred):
        # Only true rallies that can overlap: those starting before p ends.
        for j in range(bisect_left(starts, p[1]) - 1, -1, -1):
            if truth[j][1] <= p[0] and p[0] - truth[j][0] > 600_000:
                break  # nothing earlier overlaps (no rally is 10 minutes long)
            score = iou(p, truth[j])
            if score >= min_iou:
                pairs.append((-score, i, j))
    pairs.sort()
    used_p: set[int] = set()
    used_t: set[int] = set()
    found: list[float] = []
    for neg, i, j in pairs:
        if i not in used_p and j not in used_t:
            used_p.add(i)
            used_t.add(j)
            found.append(-neg)
    return found


def _union(spans: Sequence[Span]) -> list[Span]:
    out: list[Span] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def _length(spans: Sequence[Span]) -> int:
    return sum(e - s for s, e in spans)


def live_iou(pred: Sequence[Span], true: Sequence[Span]) -> float:
    a, b = _union(pred), _union(true)
    inter, i, j = 0, 0, 0
    while i < len(a) and j < len(b):
        lo, hi = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        inter += max(0, hi - lo)
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    union = _length(a) + _length(b) - inter
    return inter / union if union else 1.0


def evaluate(
    pred: Sequence[Span], true: Sequence[Span], tol_ms: int = TOLERANCE_MS
) -> dict[str, Any]:
    """Every number the review page and `rally-eval` show for one video."""
    starts = match_points([p[0] for p in pred], [t[0] for t in true], tol_ms)
    ends = match_points([p[1] for p in pred], [t[1] for t in true], tol_ms)
    matched = match_rallies(pred, true)
    return {
        "tolerance_ms": tol_ms,
        "rallies_true": len(true),
        "rallies_pred": len(pred),
        "boundary": _prf(starts + ends, 2 * len(pred), 2 * len(true)),
        "start": _prf(starts, len(pred), len(true)),
        "end": _prf(ends, len(pred), len(true)),
        "rally": _prf(len(matched), len(pred), len(true)),
        "mean_iou": round(sum(matched) / len(matched), 4) if matched else 0.0,
        "live_iou": round(live_iou(pred, true), 4),
        "live_s_true": round(_length(_union(true)) / 1000, 1),
        "live_s_pred": round(_length(_union(pred)) / 1000, 1),
    }


def summarise(per_video: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Macro average over videos (each game counts once) and the pooled boundary F1."""
    if not per_video:
        return {"videos": 0}
    n = len(per_video)
    pooled_tp = sum(
        round(v["boundary"]["recall"] * 2 * v["rallies_true"]) for v in per_video
    )  # matched boundaries
    pooled = _prf(
        pooled_tp,
        sum(2 * v["rallies_pred"] for v in per_video),
        sum(2 * v["rallies_true"] for v in per_video),
    )
    return {
        "videos": n,
        "boundary_f1": round(sum(v["boundary"]["f1"] for v in per_video) / n, 4),
        "rally_f1": round(sum(v["rally"]["f1"] for v in per_video) / n, 4),
        "live_iou": round(sum(v["live_iou"] for v in per_video) / n, 4),
        "boundary_f1_pooled": pooled["f1"],
    }
