"""The official scoresheet in the scoring, measured (PLAN §10.3a, research note §4.2).

Synthetic sets (made-up data):

    cd backend && uv run python ../scripts/ml/sheet_eval.py synthetic [--sets 300] [--seed 1]

Random sets to 25 at a 60 % side-out rate; each rally's serve evidence points at the right end
line with probability `serve` (else at the wrong one); a gap of 10-20 s before each rally, three
timeouts a set; some rallies missed by the rally finder and some extra ones (replays, proposals
that are not rallies). Our side at the start is known, the first server is not (the scoring
picks it); no scoreboard or log. Per observed rally, the winner is right when it matches the
truth (an extra rally is right when it is called no point). Each set is scored three ways: the
official final only (as before the sheet), the sheet lined up with the rallies, and the sheet
as a soft hint (a set the checks doubt).

A real game, with its trusted scoreboard log as a stand-in for the paper sheet (the log has every
point in order: the service boxes a scorer would have written are derived from it):

1. Dump what the database knows (read-only; in the API container):

    ssh hetzner "sudo docker exec -i wieplay-prod-api-1 python - dump 509" \\
        < scripts/ml/sheet_eval.py > sheet-dump.json

2. Measure (locally, this checkout's scoring):

    cd backend && uv run python ../scripts/ml/sheet_eval.py game sheet-dump.json

Scores the match as the API does (the same rallies, serve and activity tracks, coach's facts,
sets) with: the official score only; the log (today); the sheet built from the log, blind to the
log; the sheet and the log; the sheet blind to the coach too. The reference per rally is what the
log says about it (who won, or no point) where its set is trusted, and the coach's winners. The
dump holds rally times, sides, scores and team names only.
"""

from __future__ import annotations

import json
import random
import sys
from typing import Any

# --- synthetic sets -----------------------------------------------------------------------------


def synthetic_case(
    rng: random.Random,
    *,
    side_out: float = 0.6,
    serve: float = 0.92,
    miss: float = 0.03,
    extra: float = 0.02,
    no: int = 1,
) -> tuple[Any, Any, dict[str, str], str]:
    """(the set without the sheet, its sheet path, the truth per rally key, the first server)."""
    from wieplay.ml import scoring as sc
    from wieplay.ml import sheet as sh

    first: sc.Side = "us" if rng.random() < 0.5 else "them"
    winners = sh.play(rng, first, side_out)
    side0: sc.Court = "near" if rng.random() < 0.5 else "far"
    timeouts = set(rng.sample(range(5, len(winners) - 2), 3))
    rallies: list[sc.RallyObs] = []
    truth: dict[str, str] = {}
    server = first
    t = 30_000
    servers: list[sc.Side] = []
    for i, won in enumerate(winners):
        servers.append(server)
        t += int(rng.uniform(10_000, 20_000)) + (60_000 if i in timeouts else 0)
        length = int(rng.uniform(4_000, 12_000))
        if rng.random() >= miss:
            server_near = (server == "us") == (side0 == "near")
            p = rng.uniform(0.7, 0.98) if rng.random() < serve else rng.uniform(0.02, 0.3)
            key = f"{no}-{i}"
            rallies.append(
                sc.RallyObs(key, t, t + length, p if server_near else 1 - p, proposal=0.85)
            )
            truth[key] = won
        t += length
        if rng.random() < extra:  # a replay or a proposal that is not a rally
            t += int(rng.uniform(8_000, 15_000))
            key = f"{no}-x{i}"
            rallies.append(sc.RallyObs(key, t, t + 6_000, rng.random(), proposal=0.6))
            truth[key] = "none"
            t += 6_000
        server = won
    final = (winners.count("us"), winners.count("them"))
    st = sc.SetIn(no=no, rallies=rallies, official=final, our_side=side0)
    path = sc.SheetPath(tuple(winners), tuple(servers), tuple(False for _ in winners))
    return st, path, truth, first


def right(out: Any, truth: dict[str, str]) -> tuple[int, int, int]:
    """(answers right, rallies with a reference, flagged rallies)."""
    ok = n = flagged = 0
    for r in out.rallies:
        flagged += int(r.note is not None)
        if r.key not in truth:
            continue
        n += 1
        ok += int((r.won_by if r.point else "none") == truth[r.key])
    return ok, n, flagged


SCENARIOS = [
    ("complete, no extra rallies", {"miss": 0.0, "extra": 0.0}),
    ("3 % missed, 2 % extra (replays)", {"miss": 0.03, "extra": 0.02}),
    ("5 % missed, 3 % extra, serve reads 85 %", {"miss": 0.05, "extra": 0.03, "serve": 0.85}),
]


def synthetic(sets: int, seed: int) -> None:
    from dataclasses import replace

    from wieplay.ml import scoring as sc

    print("| Rally list | Official score only | With the sheet | Sheet as a hint | Flagged |")
    print("|---|---|---|---|---|")
    for name, kwargs in SCENARIOS:
        rng = random.Random(seed)  # noqa: S311 - made-up data
        totals = {k: [0, 0, 0] for k in ("official", "sheet", "hint")}
        for _ in range(sets):
            st, path, truth, first = synthetic_case(rng, **kwargs)  # type: ignore[arg-type]
            outs = {
                "official": sc.score_match([st]).sets[0],
                "sheet": sc.score_match([replace(st, sheet=path, first_serve=first)]).sets[0],
                "hint": sc.score_match([replace(st, sheet_hint=path.hint())]).sets[0],
            }
            for k, out in outs.items():
                for i, v in enumerate(right(out, truth)):
                    totals[k][i] += v
        pct = {k: v[0] / v[1] for k, v in totals.items()}
        flagged = totals["sheet"][2] / totals["sheet"][1]
        print(
            f"| {name} | {pct['official']:.1%} | {pct['sheet']:.1%} | {pct['hint']:.1%} | "
            f"{flagged:.1%} |"
        )


# --- a real game: the dump (API container; no new modules there) ------------------------------


def dump(game_id: int) -> None:
    from sqlalchemy import select

    from wieplay.core import scorelog, scoring
    from wieplay.core.config import get_settings
    from wieplay.core.db import make_engine, make_sessionmaker
    from wieplay.core.models import Event, Period, Segment, Video, WdGame

    sessions = make_sessionmaker(make_engine(get_settings().database_url))
    out: dict[str, Any] = {}
    with sessions() as db:
        game = db.get(WdGame, game_id)
        videos = db.scalars(
            select(Video).where(Video.game_id == game_id, Video.status == "ready")
        ).all()
        for v in videos:
            log = scorelog.stored(db, v)
            serve = scoring.latest_track(db, v.id, "serve")
            act = scoring.latest_track(db, v.id, "activity")
            rallies = db.scalars(
                select(Segment)
                .where(Segment.video_id == v.id, Segment.kind == "rally")
                .where(Segment.deleted_at.is_(None))
                .order_by(Segment.start_ms, Segment.id)
            ).all()
            periods = db.scalars(
                select(Period)
                .where(Period.video_id == v.id, Period.deleted_at.is_(None))
                .order_by(Period.start_ms, Period.no)
            ).all()
            serves = db.execute(
                select(Event.t_ms, Event.side, Event.source, Event.review).where(
                    Event.video_id == v.id, Event.code == "S", Event.deleted_at.is_(None)
                )
            ).all()
            out[str(v.id)] = {
                "game": {
                    "id": game_id,
                    "our_side": game.our_side if game else None,
                    "sets": game.sets if game else None,
                    "home": game.home_name if game else None,
                    "away": game.away_name if game else None,
                },
                "log": log.payload if log else None,
                "serve": {"step_ms": serve.step_ms, "channels": serve.channels} if serve else None,
                "activity": {"step_ms": act.step_ms, "channels": act.channels} if act else None,
                "periods": [
                    {
                        "no": p.no,
                        "start_ms": p.start_ms,
                        "end_ms": p.end_ms,
                        "start_us": p.start_us,
                        "start_them": p.start_them,
                        "first_serve": p.first_serve,
                        "our_court_side_at_start": p.our_court_side_at_start,
                        "side_switch_at_score": p.side_switch_at_score,
                        "model_first_serve": p.model_first_serve,
                        "model_court_side": p.model_court_side,
                        "source": p.source,
                        "review": p.review,
                    }
                    for p in periods
                ],
                "rallies": [
                    {
                        "id": str(r.id),
                        "start_ms": r.start_ms,
                        "end_ms": r.end_ms,
                        "source": r.source,
                        "review": r.review,
                        "confidence": r.confidence,
                        "won_by": r.won_by,
                        "serving_manual": r.serving_manual,
                    }
                    for r in rallies
                ],
                "serve_tags": [
                    [int(t), side]
                    for t, side, source, review in serves
                    if not (source == "model" and review in ("unreviewed", "rejected", "merged"))
                ],
            }
    json.dump(out, sys.stdout)


# --- a real game: measuring --------------------------------------------------------------------


def _sequence(log: Any, no: int, us: str) -> tuple[list[str], list[tuple[str, int, int]], str]:
    """The log's final point sequence of a set (corrections replayed: a score that goes down
    takes the newest points back), its timeouts (team, own score, other's) and the first
    server: the board's serve indicator before the set's first point ('' when not set; on a
    point the indicator is its winner, the next server)."""
    stack: list[str] = []
    timeouts: list[tuple[str, int, int]] = []
    score = (0, 0)
    first = ""
    for e in log.events:
        if e.period != no:
            continue
        a, b = e.score
        if e.kind == "reset":
            stack, score = [], (0, 0)
            continue
        while (a, b) != score and (a < score[0] or b < score[1]) and stack:
            last = stack.pop()
            score = (score[0] - (last == "a"), score[1] - (last == "b"))
        while score[0] < a:
            stack.append("a")
            score = (score[0] + 1, score[1])
        while score[1] < b:
            stack.append("b")
            score = (score[0], score[1] + 1)
        if not stack and e.kind != "point" and e.serving in ("a", "b"):
            first = e.serving  # the serve indicator before the set's first point

        if e.kind == "timeout" and e.side in ("a", "b"):
            own, opp = (a, b) if e.side == "a" else (b, a)
            timeouts.append(("us" if e.side == us else "them", own, opp))
    side = {"a": "us" if us == "a" else "them", "b": "them" if us == "a" else "us"}
    return [side[x] for x in stack], timeouts, side.get(first, "")


def game(path: str, as_json: bool) -> None:
    from dataclasses import replace
    from types import SimpleNamespace

    from wieplay.core import scorelog as core_log
    from wieplay.core import scoring as core_scoring
    from wieplay.core.scoresheet import path_of
    from wieplay.ml import gaps
    from wieplay.ml import scorelog as sl
    from wieplay.ml import scoring as sc
    from wieplay.ml import sheet as sh
    from wieplay.ml import tracks as tr

    data = json.loads(open(path).read())  # noqa: SIM115 - a dump file
    report: dict[str, Any] = {}
    for vid, v in data.items():
        g = v["game"]
        us = "a" if g["our_side"] == "home" else "b"
        official = [tuple(s if us == "a" else s[::-1]) for s in g["sets"]]
        rallies = [SimpleNamespace(**r) for r in v["rallies"]]
        periods = [SimpleNamespace(**p) for p in v["periods"]]
        serve = tr.series(v["serve"]["channels"], "p_near", v["serve"]["step_ms"])
        act = gaps.Activity(
            *(
                tr.series(v["activity"]["channels"], name, v["activity"]["step_ms"])
                for name in tr.ACTIVITY_CHANNELS
            )
        )
        tags = sorted(v["serve_tags"])
        calls: list[str | None] = []
        for r in rallies:
            found = next((s for t, s in tags if r.start_ms - 2000 <= t <= r.end_ms), None)
            calls.append(found or r.serving_manual)
        by_period: dict[int, list[int]] = {}
        for i, r in enumerate(rallies):
            p = core_scoring._period_of(periods, r.start_ms)  # type: ignore[arg-type]
            if p is not None:
                by_period.setdefault(p.no, []).append(i)
        obs = []
        for i, r in enumerate(rallies):
            p = core_scoring._period_of(periods, r.start_ms)  # type: ignore[arg-type]
            gap = None
            if p is not None and by_period[p.no][0] != i:
                gap = gaps.evidence(act, rallies[i - 1].end_ms, r.start_ms)
            obs.append(core_scoring._obs(r, serve, calls[i], gap))  # type: ignore[arg-type]
        shift = sc.balance([obs[i].p_near for idx in by_period.values() for i in idx])
        if shift:
            obs = [replace(o, p_near=sc.shifted(o.p_near, shift)) for o in obs]
        scored = [p for p in periods if by_period.get(p.no)]
        base = [
            core_scoring._set_in(p, [obs[i] for i in by_period[p.no]], official)  # type: ignore[arg-type]
            for p in scored
        ]

        # The log, lined up as the API does; its verdicts are the reference.
        log = sl.parse(v["log"])
        assert log is not None
        alignment = sl.align(log, [sl.RallySpan(r.id, r.start_ms, r.end_ms) for r in rallies])
        trust = sl.trust(log, {k + 1: (s[0], s[1]) for k, s in enumerate(g["sets"])}, alignment)
        stored = SimpleNamespace(track=SimpleNamespace(model_run_id=None, created_at=None),
                                 payload=v["log"], log=log)  # fmt: skip
        ev = core_log.Evidence(stored, alignment, trust, us)  # type: ignore[arg-type]
        reference = {}
        for r in rallies:
            said = ev.says(r.id)
            if said is not None:
                reference[r.id] = said[0]
        coach = {r.id: r.won_by for r in rallies if r.won_by in ("us", "them")}
        log_scores = {}
        for r in rallies:
            verdict = ev.verdict(r.id)
            if verdict is not None and verdict.score is not None:
                a, b = verdict.score
                log_scores[r.id] = (a, b) if us == "a" else (b, a)

        # The sheet the scorer would have written: the log's points, written the official way.
        sheet_sets = {}
        timeout_report = []
        for st in base:
            winners, timeouts, first = _sequence(log, st.no, us)
            p = next(p for p in scored if p.no == st.no)
            first = first or p.first_serve or p.model_first_serve
            grids, circled = sh.encode(first, winners, deciding=st.deciding)  # type: ignore[arg-type]
            s = sh.SheetSet(
                st.no, first, grids, st.deciding, circled,  # type: ignore[arg-type]
                timeouts=[sh.Timeout(t, own, opp) for t, own, opp in timeouts],  # type: ignore[arg-type]
            )  # fmt: skip
            d = sh.decode(s, official[st.no - 1])
            assert [q.winner for q in d.points] == winners and d.consistent, (st.no, d.issues)
            sheet_sets[st.no] = (s, d)

        def with_sheet(sets: list[Any], sheets: dict[int, Any] = sheet_sets) -> list[Any]:
            out = []
            for st in sets:
                s, d = sheets[st.no]
                out.append(replace(st, sheet=path_of(d.points, ()), first_serve=s.first_serve))
            return out

        def blind(sets: list[Any]) -> list[Any]:
            out = []
            for st in sets:
                rs = [replace(r, won_fact=None, serve_fact=None) for r in st.rallies]
                out.append(replace(st, rallies=rs))
            return out

        with_log = core_log.apply(ev, base)
        # As after a review: the rallies the log calls no point rejected (replays, proposals
        # that are not rallies); the missed ones cannot be added without their times.
        nones = {k for k, x in reference.items() if x == "none"}
        reviewed = [replace(st, rallies=[r for r in st.rallies if r.key not in nones])
                    for st in base]  # fmt: skip
        runs = {
            "official only": base,
            "log (today)": with_log,
            "sheet, no log": with_sheet(base),
            "sheet and log": with_sheet(with_log),
            "sheet, no log, blind to the coach": with_sheet(blind(base)),
            "official only, non-rallies rejected": reviewed,
            "sheet, non-rallies rejected": with_sheet(reviewed),
        }
        results = {}
        for name, sets in runs.items():
            match = sc.score_match(sets)
            outs = {o.key: o for so in match.sets for o in so.rallies}
            agree = sum(
                1
                for k, ref in reference.items()
                if k in outs and (outs[k].won_by if outs[k].point else "none") == ref
            )
            points_ref = {k: x for k, x in reference.items() if x != "none"}
            agree_points = sum(
                1 for k, ref in points_ref.items() if k in outs and outs[k].won_by == ref
            )
            coach_ok = sum(1 for k, w in coach.items() if k in outs and outs[k].won_by == w)
            wrong = [
                k for k, ref in reference.items()
                if k in outs and (outs[k].won_by if outs[k].point else "none") != ref
            ]  # fmt: skip
            wrong_flagged = sum(1 for k in wrong if outs[k].note is not None)
            # The score shown after each rally the log says was a point (what a coach sees).
            score_ok = sum(
                1 for k in points_ref
                if k in outs and (outs[k].score_us, outs[k].score_them) == log_scores.get(k)
            )  # fmt: skip
            flags: dict[str, int] = {}
            for o in outs.values():
                if o.note:
                    flags[o.note] = flags.get(o.note, 0) + 1
            results[name] = {
                "vs_log": f"{agree}/{len(reference)}",
                "vs_log_share": round(agree / len(reference), 4) if reference else None,
                "vs_log_points": f"{agree_points}/{len(points_ref)}",
                "vs_coach": f"{coach_ok}/{len(coach)}",
                "wrong_flagged": f"{wrong_flagged}/{len(wrong)}",
                "score_after": f"{score_ok}/{len(points_ref)}",
                "finals": [list(so.final) for so in match.sets],
                "on_official": sum(
                    1 for st, so in zip(sets, match.sets, strict=True) if so.final == st.official
                ),
                "flagged": sum(flags.values()),
                "flags": dict(sorted(flags.items())),
                "sheet_sets": [so.sheet for so in match.sets],
                "aligned": sum(sum(1 for k in so.point_rally if k) for so in match.sets),
                "missed": sum(so.missed for so in match.sets),
                "penalties": sum(so.penalties for so in match.sets),
                "extra": sum(so.extra for so in match.sets),
            }
            if name == "sheet, no log":
                # Where the sheet's timeouts land against the log's own timeout presses.
                for so in match.sets:
                    s, d = sheet_sets[so.no]
                    log_times = [
                        (e.t_ms or 0) + st.shift_ms
                        for e in log.events
                        if e.kind == "timeout" and e.period == so.no and e.t_ms is not None
                        for st in alignment.stretches
                        if st.period == so.no
                    ]
                    placed = core_timeouts(so, s, d, rallies)
                    for start, end in placed:
                        timeout_report.append(
                            {"set": so.no, "gap_s": round((end - start) / 1000, 1),
                             "log_press_inside": any(start - 3000 <= t <= end for t in log_times)}
                        )  # fmt: skip
        report[vid] = {
            "game": f"{g['home']} - {g['away']}",
            "official": [list(s) for s in official],
            "rallies": len(rallies),
            "log_reference": len(reference),
            "log_no_point": sum(1 for x in reference.values() if x == "none"),
            "coach_winners": len(coach),
            "sheet_points": sum(len(d.points) for _, d in sheet_sets.values()),
            "sheet_first_servers": {no: s.first_serve for no, (s, _) in sheet_sets.items()},
            "sheet_timeouts": timeout_report,
            "runs": results,
        }
    if as_json:
        json.dump(report, sys.stdout, indent=1)
        return
    for vid, r in report.items():
        print(f"{r['game']} (video {vid[:8]}): {r['rallies']} rallies, official {r['official']}")
        print(f"  reference: the log on {r['log_reference']} rallies ({r['log_no_point']} no "
              f"point), the coach's {r['coach_winners']} winners; the sheet: "
              f"{r['sheet_points']} points, first servers {r['sheet_first_servers']}")  # fmt: skip
        head = [
            "Scoring with",
            "vs log",
            "points vs log",
            "score after",
            "vs coach",
            "sets on official",
            "flagged",
            "wrong ones flagged",
            "aligned / missed / extra",
        ]
        print("  | " + " | ".join(head) + " |")
        print("  |" + "---|" * len(head))
        for name, x in r["runs"].items():
            cells = [
                name,
                f"{x['vs_log']} ({x['vs_log_share']:.1%})",
                x["vs_log_points"],
                x["score_after"],
                x["vs_coach"],
                str(x["on_official"]),
                f"{x['flagged']} {x['flags']}",
                x["wrong_flagged"],
                f"{x['aligned']} / {x['missed']} / {x['extra']}",
            ]
            print("  | " + " | ".join(cells) + " |")
        inside = sum(1 for t in r["sheet_timeouts"] if t["log_press_inside"])
        print(f"  sheet timeouts placed: {len(r['sheet_timeouts'])}, the log's press in the same "
              f"break: {inside}")  # fmt: skip


def core_timeouts(so: Any, s: Any, d: Any, rallies: list[Any]) -> list[tuple[int, int]]:
    """The breaks the API makes of a set's sheet timeouts (wieplay/core/scoresheet.py)."""
    from wieplay.core.scoresheet import SetCheck, SheetUse, timeout_gaps
    from wieplay.ml import scoring as sc

    use = SheetUse([SetCheck({}, s, d, None)], True)
    found = timeout_gaps(use, sc.MatchOut([so], 1.0), rallies)
    return [(start, end) for start, end, _, _ in found]


if __name__ == "__main__":
    args = sys.argv[1:]

    def opt(name: str, default: str) -> str:
        return args[args.index(name) + 1] if name in args[:-1] else default

    if args[:1] == ["synthetic"]:
        synthetic(int(opt("--sets", "300")), int(opt("--seed", "1")))
    elif args[:1] == ["dump"] and len(args) >= 2:
        dump(int(args[1]))
    elif args[:1] == ["game"] and len(args) >= 2:
        game(args[1], "--json" in args)
    else:
        print(__doc__)
        sys.exit(2)
