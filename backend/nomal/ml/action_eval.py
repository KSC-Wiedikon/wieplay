"""How close suggested actions are to a coach's tags (PLAN §10.4a): contacts within ±0.3 s,
the skill and the side of the matched ones.

Plain Python, no numpy: the API scores the suggestions with it, the worker scores v0 and v1
on held-out games with it (nomal/ml/actions.py).

An action is (t_ms, code, side). A suggested action matches a coach's tag when their times
are within the tolerance, each tag matched at most once (closest pairs first):

- **contacts**: precision, recall and F1 of the matches, whatever the skill;
- **skill accuracy** and **side accuracy**: the share of matches with the coach's skill (side);
- **action F1**: like the contacts' F1, but a match counts only with the coach's skill and side;
  the number the learned model has to beat (v0 vs v1).
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Any

TOLERANCE_MS = 300
Action = tuple[int, str, str]  # t_ms, code, side


def _prf(tp: int, n_pred: int, n_true: int) -> tuple[float, float, float]:
    precision = tp / n_pred if n_pred else (1.0 if n_true == 0 else 0.0)
    recall = tp / n_true if n_true else (1.0 if n_pred == 0 else 0.0)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def match(
    pred: Sequence[int], true: Sequence[int], tol_ms: int = TOLERANCE_MS
) -> list[tuple[int, int]]:
    """One-to-one (pred index, true index) pairs within the tolerance, closest first."""
    order = sorted(range(len(true)), key=lambda j: true[j])
    times = [true[j] for j in order]
    pairs: list[tuple[int, int, int]] = []
    for i, p in enumerate(pred):
        lo, hi = bisect_left(times, p - tol_ms), bisect_right(times, p + tol_ms)
        pairs.extend((abs(times[k] - p), i, order[k]) for k in range(lo, hi))
    pairs.sort()
    used_p: set[int] = set()
    used_t: set[int] = set()
    out: list[tuple[int, int]] = []
    for _, i, j in pairs:
        if i not in used_p and j not in used_t:
            used_p.add(i)
            used_t.add(j)
            out.append((i, j))
    return sorted(out)


def evaluate(
    pred: Sequence[Action], true: Sequence[Action], tol_ms: int = TOLERANCE_MS
) -> dict[str, Any]:
    """The scores of one game (or of the rallies a coach tagged in it)."""
    pairs = match([p[0] for p in pred], [t[0] for t in true], tol_ms)
    same_skill = sum(1 for i, j in pairs if pred[i][1] == true[j][1])
    same_side = sum(1 for i, j in pairs if pred[i][2] == true[j][2])
    both = sum(1 for i, j in pairs if pred[i][1] == true[j][1] and pred[i][2] == true[j][2])
    p, r, f = _prf(len(pairs), len(pred), len(true))
    ap, ar, af = _prf(both, len(pred), len(true))
    confusion: Counter[str] = Counter(f"{true[j][1]}>{pred[i][1]}" for i, j in pairs)
    return {
        "coach": len(true),
        "suggested": len(pred),
        "matched": len(pairs),
        "precision": round(p, 4),
        "recall": round(r, 4),
        "f1": round(f, 4),
        "skill_accuracy": round(same_skill / len(pairs), 4) if pairs else None,
        "side_accuracy": round(same_side / len(pairs), 4) if pairs else None,
        "action_precision": round(ap, 4),
        "action_recall": round(ar, 4),
        "action_f1": round(af, 4),
        # coach skill > suggested skill, for the matched pairs
        "confusion": dict(sorted(confusion.items())),
        "tolerance_ms": tol_ms,
    }


def contact_recall(
    candidates: Sequence[int], true: Sequence[int], tol_ms: int = TOLERANCE_MS
) -> float | None:
    """Share of the coach's tags with a contact candidate within the tolerance (before any
    labelling: what the ball's path can find at all)."""
    if not true:
        return None
    return round(len(match(candidates, true, tol_ms)) / len(true), 4)


def summarise(per_game: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Means over games (each game weighs the same) of the main scores."""
    games = [g for g in per_game if g and g.get("coach")]
    if not games:
        return {"games": 0}
    out: dict[str, Any] = {"games": len(games)}
    for key in ("precision", "recall", "f1", "action_f1", "skill_accuracy", "side_accuracy"):
        vals = [g[key] for g in games if g.get(key) is not None]
        out[key] = round(sum(vals) / len(vals), 4) if vals else None
    out["coach"] = sum(g["coach"] for g in games)
    out["suggested"] = sum(g["suggested"] for g in games)
    return out
