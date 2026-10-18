"""Measure the serve side (PLAN §10.3, wieplay/ml/serve.py) against the servers a trusted score
log implies, the coaches' serve calls and serves labelled by eye; fit the rule's weights.

1. Dump what the database knows (read-only; in the API container, it has no numpy):

    ssh hetzner "sudo docker exec -i wieplay-prod-api-1 python - dump" \\
        < scripts/ml/serve_eval.py > serve-dump.json

2. Measure and fit on the worker side (signals from the NAS):

    cd backend && uv run python ../scripts/ml/serve_eval.py measure serve-dump.json \\
        --root /mnt/nas/wieplay/analysis [--eye LABELS.json] [--json]

The reference per rally: a coach's serve call, else the server the log implies (the winner of
the log's previous point in the set: no first rallies of a set, none after a rally with no
verdict, none right after points the log saw without a rally, since the alignment can be a
rally off there). Our side per set: the coach's, else the scoring's. Labels by eye
(LABELS.json: {video id: {rally id: "near" | "far"}}) are for halls without a log. Before is
the serve track stored on prod; after is this checkout's rule, its weights fitted on every log
game but the one measured (leave one game out) and, for the labelled halls, on all log games.

Prints per game the accuracy, per confidence band, and the errors by where the ball first
showed (left, centre, right third; near serves with a high or low toss). The dump holds rally
times, team sides and scores only; the labels hold no pictures.
"""

from __future__ import annotations

import json
import sys
from typing import Any

BANDS = ((0.5, 0.7), (0.7, 0.9), (0.9, 0.95), (0.95, 1.01))


# --- 1. the dump (API container) -----------------------------------------------------------------


def dump() -> None:
    from sqlalchemy import select

    from wieplay.core import calibration as cal
    from wieplay.core import scorelog, scoring
    from wieplay.core.analysis import latest_features
    from wieplay.core.config import get_settings
    from wieplay.core.db import make_engine, make_sessionmaker
    from wieplay.core.models import Period, Segment, Video

    sessions = make_sessionmaker(make_engine(get_settings().database_url))
    out: dict[str, Any] = {}
    with sessions() as db:
        videos = db.scalars(
            select(Video).where(Video.sport == "volleyball", Video.status == "ready")
        ).all()
        feats = latest_features(db, [v.id for v in videos])
        for v in videos:
            if v.id not in feats:
                continue
            rallies = db.scalars(
                select(Segment)
                .where(Segment.video_id == v.id, Segment.kind == "rally")
                .where(Segment.deleted_at.is_(None))
                .order_by(Segment.start_ms)
            ).all()
            periods = db.scalars(
                select(Period)
                .where(Period.video_id == v.id, Period.deleted_at.is_(None))
                .order_by(Period.start_ms)
            ).all()
            no = {p.id: p.no for p in periods}
            track = scoring.latest_track(db, v.id, "serve")
            confirmed = cal.current(db, v.id)
            out[str(v.id)] = {
                "title": v.title,
                "date": str(v.recorded_on),
                "features": feats[v.id].nas_path,
                "calibrated": confirmed is not None,
                "has_log": scorelog.stored(db, v) is not None,
                "serve_track": {"step_ms": track.step_ms, "p_near": track.channels["p_near"]}
                if track
                else None,
                "periods": [
                    {
                        "no": p.no,
                        "side": p.our_court_side_at_start or p.model_court_side,
                        "switch": p.side_switch_at_score,
                        "start": [p.start_us, p.start_them],
                    }
                    for p in periods
                ],
                "rallies": [
                    {
                        "id": str(r.id),
                        "start_ms": r.start_ms,
                        "period": no.get(r.period_id) if r.period_id else None,
                        "serving_manual": r.serving_manual,
                        "log_won_by": r.log_won_by,
                        "log_score": [r.log_score_us, r.log_score_them],
                        "missed": [r.model_missed_us or 0, r.model_missed_them or 0],
                    }
                    for r in rallies
                ],
            }
    json.dump(out, sys.stdout)


# --- 2. the reference ---------------------------------------------------------------------------


def references(v: dict[str, Any]) -> dict[str, tuple[bool, str]]:
    """{rally id: (near?, "coach" | "log")}."""
    periods = {p["no"]: p for p in v["periods"]}
    by_set: dict[int, list[dict[str, Any]]] = {}
    for r in v["rallies"]:
        if r["period"] is not None:
            by_set.setdefault(r["period"], []).append(r)
    out: dict[str, tuple[bool, str]] = {}
    for no, rallies in by_set.items():
        p = periods[no]
        if p["side"] not in ("near", "far"):
            continue
        last: str | None = None  # the winner of the log's previous point
        score = tuple(p["start"])
        for r in rallies:
            side = p["side"]
            switch = p["switch"] or (8 if no == 5 else None)
            if switch is not None and max(score) >= switch:
                side = "far" if side == "near" else "near"
            server, source = None, "log"
            if r["log_won_by"] in ("us", "them") and last is not None and sum(r["missed"]) == 0:
                server = last
            if r["serving_manual"] in ("us", "them"):
                server, source = r["serving_manual"], "coach"
            if server is not None:
                out[r["id"]] = ((server == "us") == (side == "near"), source)
            if r["log_won_by"] in ("us", "them"):
                last = r["log_won_by"]
                if r["log_score"][0] is not None:
                    score = tuple(r["log_score"])
            elif r["log_won_by"] is None:
                last = None
    return out


# --- 3. measuring (worker side) -----------------------------------------------------------------


def measure(path: str, root: str, eye_path: str | None, as_json: bool) -> None:
    from pathlib import Path

    import numpy as np
    from sklearn.linear_model import LogisticRegression

    from wieplay.ml import serve, tracks
    from wieplay.ml.features import Signals, ball_tracks

    data = json.loads(Path(path).read_text())
    eye = json.loads(Path(eye_path).read_text()) if eye_path else {}
    games: list[dict[str, Any]] = []
    for vid, v in data.items():
        refs = {k: near for k, (near, _) in references(v).items()} if v["has_log"] else {}
        labels = {k: lab == "near" for k, lab in eye.get(vid, {}).items()}
        if not refs and not labels:
            continue
        sig = Signals.load(Path(root) / v["features"])
        ev = serve.evidence(sig, ball_tracks(sig))
        truth = refs or labels
        rallies = [r for r in v["rallies"] if r["id"] in truth]
        X = serve.features_at(ev, [r["start_ms"] / 1000 for r in rallies])
        before = np.full(len(rallies), np.nan)
        if v["serve_track"]:
            st = v["serve_track"]
            series = tracks.Series(st["step_ms"], tracks.decode(st["p_near"]))
            for i, r in enumerate(rallies):
                found = series.near(r["start_ms"], 2)
                before[i] = np.nan if found is None else found
        games.append(
            {
                "id": vid,
                "title": v["title"],
                "kind": "log" if refs else "eye",
                "calibrated": v["calibrated"],
                "X": X,
                "y": np.array([truth[r["id"]] for r in rallies]),
                "before": before,
            }
        )

    col = {name: i for i, name in enumerate(serve.FEATURES)}

    def fit(train: list[dict[str, Any]]) -> tuple[float, float, float]:
        Xs, ys, ws = [], [], []
        for g in train:
            D = np.column_stack(
                [g["X"][:, col["r_first"]], np.nan_to_num(g["X"][:, col["r_speed"]])]
            )
            ok = ~np.isnan(D).any(axis=1)
            Xs.append(D[ok])
            ys.append(g["y"][ok])
            n = int(ok.sum())
            ws.append(np.full(n, 1.0 / max(n, 1)))  # each game counts the same
        X, y, w = np.concatenate(Xs), np.concatenate(ys), np.concatenate(ws)
        clf = LogisticRegression(C=10.0).fit(X, y, sample_weight=w * len(y))
        a, b = (float(c) for c in clf.coef_[0])
        return round(a, 1), round(b, 1), round(float(clf.intercept_[0]), 1)

    logs = [g for g in games if g["kind"] == "log"]
    weights_all = fit(logs) if logs else None
    report: dict[str, Any] = {
        "weights_all_logs": weights_all,
        "module": [serve.R_FIRST, serve.R_SPEED, serve.R_BIAS],
        "games": [],
    }
    for g in games:
        others = [o for o in logs if o is not g]
        w = fit(others) if others else None
        after = serve.p_near_v0(g["X"], w) if w else np.full(len(g["y"]), np.nan)
        report["games"].append(
            {
                "title": g["title"],
                "reference": g["kind"],
                "calibrated": g["calibrated"],
                "weights": w,
                "before": _score(g["before"], g["y"]),
                "after": _score(after, g["y"]),
                "errors_after": _where(g["X"], col, after, g["y"]),
                "errors_before": _where(g["X"], col, g["before"], g["y"]),
            }
        )
    if as_json:
        json.dump(report, sys.stdout, indent=1)
        return
    print(f"weights fitted on all log games: {weights_all}; in the module: {report['module']}")
    for r in report["games"]:
        court = "calibrated" if r["calibrated"] else "no calibration"
        print(f"\n{r['title']} ({r['reference']}, {court}) weights {r['weights']}")
        for name in ("before", "after"):
            s = r[name]
            bands = "  ".join(f"≥{b['from']:.2f}: {b['right']}/{b['n']}" for b in s["bands"])
            print(f"  {name:6s} {s['right']}/{s['n']} = {s['accuracy']}   {bands}")
        print(f"  errors before {r['errors_before']}")
        print(f"  errors after  {r['errors_after']}")


def _score(p: Any, y: Any) -> dict[str, Any]:
    import numpy as np

    ok = ~np.isnan(p)
    right = (p[ok] >= 0.5) == y[ok]
    conf = np.maximum(p[ok], 1 - p[ok])
    bands = []
    for lo, hi in BANDS:
        m = (conf >= lo) & (conf < hi)
        bands.append({"from": lo, "n": int(m.sum()), "right": int(right[m].sum())})
    return {
        "n": int(ok.sum()),
        "right": int(right.sum()),
        "accuracy": round(float(right.mean()), 4) if ok.any() else None,
        "without_evidence": int((~ok).sum()),
        "bands": bands,
    }


def _where(X: Any, col: dict[str, int], p: Any, y: Any) -> dict[str, int]:
    """Wrong answers by where the ball first showed (third of the picture) and by side; near
    serves by toss (the ball rising more than a fifth of the picture in its first 0.6 s)."""
    import numpy as np

    out: dict[str, int] = {}
    for i in np.flatnonzero(~np.isnan(p) & ((p >= 0.5) != y)):
        x = X[i, col["x_first"]]
        third = "left" if x < 1 / 3 else "right" if x > 2 / 3 else "centre"
        side = "near" if y[i] else "far"
        key = f"{side} {third}"
        if y[i]:
            rise = X[i, col["y_first"]] - X[i, col["y_min"]]
            key += " high toss" if rise > 0.2 else " low toss"
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


if __name__ == "__main__":
    if sys.argv[1:2] == ["dump"]:
        dump()
    elif sys.argv[1:2] == ["measure"] and len(sys.argv) >= 3:
        args = sys.argv[2:]

        def opt(name: str) -> str | None:
            return args[args.index(name) + 1] if name in args else None

        measure(
            args[0], opt("--root") or "/mnt/nas/wieplay/analysis", opt("--eye"), "--json" in args
        )
    else:
        print(__doc__)
        sys.exit(2)
