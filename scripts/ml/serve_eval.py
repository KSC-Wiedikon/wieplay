"""Measure the serve side (PLAN §10.3, wieplay/ml/serve.py) against the servers a trusted score
log implies, the coaches' serve calls and serves labelled by eye; fit the rule's weights.

1. Dump what the database knows (read-only; in the API container, it has no numpy):

    ssh hetzner "sudo docker exec -i wieplay-prod-api-1 python - dump" \\
        < scripts/ml/serve_eval.py > serve-dump.json

2. Measure and fit on the worker side (signals from the NAS):

    cd backend && uv run python ../scripts/ml/serve_eval.py measure serve-dump.json \\
        [--root /mnt/nas/wieplay/analysis] [--eye LABELS.json ...] [--hold VIDEO_ID ...] \\
        [--weights A,B,C] [--json]

The reference per rally: a coach's serve call, else the server the log implies (the winner of
the log's previous point in the set: no first rallies of a set, none after a rally with no
verdict, none right after points the log saw without a rally, since the alignment can be a
rally off there). Our side per set: the coach's, else the scoring's. Labels by eye
(LABELS.json: {video id: {rally id: "near" | "far"}}) are for halls without a log. Before is
the serve track stored in the dump; after is this checkout's rule with its weights fitted on
every other labelled game (leave one game out; `--hold` games are never fitted on, `--weights`
fixes them), each match's readings evened out as the scoring does (`balance`).

Prints the weights fitted on all games and, per game, the accuracy, per confidence band, and the
errors by where the ball first showed (left, centre, right third; near serves with a high or low
toss). The dump holds rally times, team sides and scores only; the labels hold no pictures."""

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


def measure(
    path: str,
    root: str,
    eye_paths: list[str],
    hold: list[str],
    weights: tuple[float, float, float] | None,
    as_json: bool,
) -> None:
    """Each labelled game: the stored track (before), and this checkout's rule with its weights
    fitted on every other labelled game (leave one game out) or the given ones, the match's
    readings evened out as the scoring does. Games in `hold` (video id prefixes) are never fitted
    on: they are measured with the weights of all the others."""
    from pathlib import Path

    import numpy as np
    from sklearn.linear_model import LogisticRegression

    from wieplay.ml import scoring, serve, tracks
    from wieplay.ml.features import Signals, ball_tracks

    data = json.loads(Path(path).read_text())
    eye: dict[str, dict[str, str]] = {}
    for e in eye_paths:
        eye.update(json.loads(Path(e).read_text()))
    games: list[dict[str, Any]] = []
    for vid, v in data.items():
        refs = {k: near for k, (near, _) in references(v).items()} if v["has_log"] else {}
        labels = {k: lab == "near" for k, lab in eye.get(vid, {}).items()}
        if not refs and not labels:
            continue
        truth = refs or labels
        sig = Signals.load(Path(root) / v["features"])
        ev = serve.evidence(sig, ball_tracks(sig))
        rallies = [r for r in v["rallies"] if r["period"] is not None]  # in sets, as the scoring
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
                "held": any(vid.startswith(h) for h in hold),
                "calibrated": v["calibrated"],
                "side_view": ev.side_view,
                "X": X,
                "known": np.array([r["id"] in truth for r in rallies]),
                "y": np.array([truth.get(r["id"], False) for r in rallies]),
                "before": before,
            }
        )

    col = {name: i for i, name in enumerate(serve.FEATURES)}

    def design(g: dict[str, Any]) -> Any:
        return np.column_stack(
            [g["X"][:, col["r_first"]], np.nan_to_num(g["X"][:, col["r_speed"]])]
        )

    def fit(train: list[dict[str, Any]]) -> tuple[float, float, float]:
        Xs, ys, ws = [], [], []
        for g in train:
            D = design(g)
            ok = ~np.isnan(D).any(axis=1) & g["known"]
            n = int(ok.sum())
            Xs.append(D[ok])
            ys.append(g["y"][ok])
            ws.append(np.full(n, 1.0 / max(n, 1)))  # each game counts the same
        X, y, w = np.concatenate(Xs), np.concatenate(ys), np.concatenate(ws)
        clf = LogisticRegression(C=10.0).fit(X, y, sample_weight=w * len(y))
        a, b = (float(c) for c in clf.coef_[0])
        return round(a, 2), round(b, 2), round(float(clf.intercept_[0]), 2)

    def balanced(p: Any) -> tuple[Any, float]:
        values = [None if np.isnan(v) else float(v) for v in p]
        shift = scoring.balance(values)
        out = [scoring.shifted(v, shift) for v in values]
        return np.array([np.nan if v is None else v for v in out]), shift

    fitting = [g for g in games if not g["held"] and not g["side_view"]]
    weights_all = fit(fitting) if fitting else None
    report: dict[str, Any] = {
        "weights_all": weights_all,
        "module": [serve.R_FIRST, serve.R_SPEED, serve.R_BIAS],
        "games": [],
    }
    for g in games:
        w = weights or (fit([o for o in fitting if o is not g]) if g in fitting else weights_all)
        raw = serve.p_near_v0(g["X"], w)
        if g["side_view"]:
            raw[:] = np.nan
        after, shift = balanced(raw)
        k = g["known"]
        report["games"].append(
            {
                "title": g["title"],
                "reference": g["kind"],
                "held_out": "fixed weights" if weights else "hall" if g["held"] else "game",
                "calibrated": g["calibrated"],
                "side_view": g["side_view"],
                "weights": w,
                "shift": shift,
                "near_share": round(float(np.nanmean(after >= 0.5)), 3) if len(after) else None,
                "before": _score(g["before"][k], g["y"][k]),
                "unbalanced": _score(raw[k], g["y"][k]),
                "after": _score(after[k], g["y"][k]),
                "errors_after": _where(g["X"][k], col, after[k], g["y"][k]),
                "errors_before": _where(g["X"][k], col, g["before"][k], g["y"][k]),
            }
        )
    if as_json:
        json.dump(report, sys.stdout, indent=1)
        return
    print(f"weights fitted on all the games fitted on: {weights_all}; module: {report['module']}")
    for r in report["games"]:
        court = "calibrated" if r["calibrated"] else "no calibration"
        side = ", camera at the side" if r["side_view"] else ""
        print(f"\n{r['title']} ({r['reference']}, {court}{side}; held out: {r['held_out']})")
        print(f"  weights {r['weights']}, shift {r['shift']:+.2f}, near share {r['near_share']}")
        for name in ("before", "unbalanced", "after"):
            sc = r[name]
            bands = "  ".join(f"≥{b['from']:.2f}: {b['right']}/{b['n']}" for b in sc["bands"])
            print(f"  {name:10s} {sc['right']}/{sc['n']} = {sc['accuracy']}   {bands}")
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

        def opts(name: str) -> list[str]:
            return [args[i + 1] for i, a in enumerate(args[:-1]) if a == name]

        fixed = opts("--weights")
        measure(
            args[0],
            (opts("--root") or ["/mnt/nas/wieplay/analysis"])[0],
            opts("--eye"),
            opts("--hold"),
            tuple(float(x) for x in fixed[0].split(",")) if fixed else None,  # type: ignore[arg-type]
            "--json" in args,
        )
    else:
        print(__doc__)
        sys.exit(2)
