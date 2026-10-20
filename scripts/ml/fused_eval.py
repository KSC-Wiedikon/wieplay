"""The joint decode, measured (PLAN §10.3 "One decode, every source"): per game and per mix of
evidence, how many rallies the decode is sure of, how many of those are wrong, how many need the
coach, and how often the score line after a point is the right one.

Real games, offline (nothing here writes to a database or a bucket):

1. A dump of what the database knows about a video, read-only (psql on the database host,
   `scripts/ml/fused_dump.sql`): its rallies, sets, serve tags, the newest track of each kind
   (serve, activity, score_log, scoreboard), its game's official sets, the roster's numbers. One
   JSON object:
   {"video", "game", "rallies", "periods", "serves", "tracks": {kind: {step_ms, channels}},
   "roster"}.
2. The scoresheet as typed (a transcription by eye; format as scripts/ml/sheet_players_eval.py
   `sheet_docs`), and the reader's cells of its photo (`sheet_cells.read_photo(...).as_json()`,
   run offline on the photo; never in git).

    cd backend && uv run python ../scripts/ml/fused_eval.py DUMP.json SHEET.json CELLS.json \
        [--truth TRUTH.json] [--sure 0.97] [--weight 0.6] [--sheet-weight 0.96] [--sweep] \
        [--mixes a,c,d,e,c0,e0]

CELLS.json may be {} (no photo was read): then leave b and e' out of --mixes.

The reference per rally is the club scoreboard's log (PLAN §10.10: every point of these games
in order with its time; lined up with the rallies as the scoring does): its winner, "no point",
and the score after it. TRUTH.json corrects it where the video shows the log was wrong:
{"rallies": {"<rally id>": {"won": "us"|"them"|"none", "score": [us, them]}}}.

Mixes of evidence (the log is the reference in all of them; where it is evidence too, the
numbers say how well the decode keeps to it):

    a  the video and the official set scores
    b  + the sheet read from its photo, not confirmed
    c  + the sheet typed (instead of the read one)
    d  the video, the official set scores and the log
    e  everything: the typed sheet and the log
    e' the read sheet and the log
    c0 the typed sheet and the official set scores, without the video's evidence (no serve
       readings, no board in the picture: the rallies are still the finder's)
    e0 the typed sheet, the official set scores and the log, without the video's evidence

Dumps, sheets and cells hold club members' data: keep them outside git and delete them after
the measurement (PLAN §5.5).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace as NS
from typing import Any

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from wieplay.core import scoreboard as sboard  # noqa: E402
from wieplay.core import scorelog as core_log  # noqa: E402
from wieplay.core import scoresheet  # noqa: E402
from wieplay.core import scoring as core_scoring  # noqa: E402
from wieplay.ml import board as bd  # noqa: E402
from wieplay.ml import gaps  # noqa: E402
from wieplay.ml import scorelog as sl  # noqa: E402
from wieplay.ml import scoring as sc  # noqa: E402
from wieplay.ml import sheet_read as rd  # noqa: E402
from wieplay.ml import tracks as tr  # noqa: E402

MIXES = ("a", "b", "c", "d", "e", "e'", "c0", "e0")


def _rows(dump: dict[str, Any]) -> list[NS]:
    rows = [r for r in dump["rallies"] if r["deleted_at"] is None and r["kind"] == "rally"]
    rows.sort(key=lambda r: (r["start_ms"], r["id"]))
    return [NS(**r) for r in rows]


def _periods(dump: dict[str, Any]) -> list[NS]:
    return sorted((NS(**p) for p in dump["periods"]), key=lambda p: (p.start_ms, p.no))


def _official(dump: dict[str, Any]) -> list[tuple[int, int]]:
    g = dump["game"]
    return [(s[0], s[1]) if g["our_side"] == "home" else (s[1], s[0]) for s in g["sets"] or []]


def log_evidence(dump: dict[str, Any], rallies: list[NS]) -> core_log.Evidence | None:
    track = dump["tracks"].get("score_log")
    if not track:
        return None
    payload = core_log.unpack(track["channels"])
    log = sl.parse(payload)
    if log is None:
        return None
    alignment = sl.align(log, [sl.RallySpan(r.id, r.start_ms, r.end_ms) for r in rallies])
    g = dump["game"]
    us = "a" if g["our_side"] == "home" else "b"
    trust = sl.trust(log, {k + 1: (s[0], s[1]) for k, s in enumerate(g["sets"])}, alignment)
    stored = NS(track=NS(model_run_id=None, created_at=None), payload=payload, log=log)
    return core_log.Evidence(stored, alignment, trust, us)  # type: ignore[arg-type]


def base_sets(dump: dict[str, Any]) -> tuple[list[sc.SetIn], list[NS], list[NS], Any]:
    """The sets as core/scoring.score_video builds them, before the sheet and the log."""
    official = _official(dump)
    rallies = _rows(dump)
    periods = _periods(dump)
    t = dump["tracks"]
    serve = tr.series(t["serve"]["channels"], "p_near", t["serve"]["step_ms"])
    act = gaps.Activity(
        *(tr.series(t["activity"]["channels"], n, t["activity"]["step_ms"])
          for n in tr.ACTIVITY_CHANNELS)
    )  # fmt: skip
    by_period: dict[Any, list[int]] = {}
    for i, r in enumerate(rallies):
        p = core_scoring._period_of(periods, r.start_ms)  # type: ignore[arg-type]
        if p is not None:
            by_period.setdefault(p.id, []).append(i)
    serves = sorted((s["t_ms"], s["side"]) for s in dump.get("serves") or [])
    obs = []
    k = 0
    for i, r in enumerate(rallies):
        while k < len(serves) and serves[k][0] < r.start_ms - 2000:
            k += 1
        tag = serves[k][1] if k < len(serves) and serves[k][0] <= r.end_ms else None
        call = tag or r.serving_manual
        p = core_scoring._period_of(periods, r.start_ms)  # type: ignore[arg-type]
        gap = None
        if p is not None and by_period[p.id][0] != i:
            gap = gaps.evidence(act, rallies[i - 1].end_ms, r.start_ms)
        obs.append(core_scoring._obs(r, serve, call, gap))  # type: ignore[arg-type]
    shift = sc.balance([obs[i].p_near for idx in by_period.values() for i in idx])
    if shift:
        obs = [replace(o, p_near=sc.shifted(o.p_near, shift)) for o in obs]
    scored = [p for p in periods if by_period.get(p.id)]
    set_ins = [
        core_scoring._set_in(p, [obs[i] for i in by_period[p.id]], official)  # type: ignore[arg-type]
        for p in scored
    ]
    board = None
    if t.get("scoreboard"):
        found = bd.unpack(t["scoreboard"]["channels"])
        if found:
            timeline = bd.Timeline(found)
            original = sboard.timeline
            sboard.timeline = lambda db, vid: timeline  # type: ignore[assignment]
            try:
                board = sboard.board_for(None, NS(id=None), rallies, scored, by_period)  # type: ignore[arg-type]
            finally:
                sboard.timeline = original  # type: ignore[assignment]
    return set_ins, rallies, scored, board


def typed_use(docs: list[dict[str, Any]], official: list[tuple[int, int]]) -> scoresheet.SheetUse:
    return scoresheet.SheetUse(scoresheet.check(docs, official), True)


def read_use(
    cells_doc: dict[str, Any], official: list[tuple[int, int]], roster: set[int] | None
) -> tuple[scoresheet.SheetUse, Any]:
    """The photo's reading as the API keeps it: every set a draft, scored as read."""
    readings = rd.Readings.from_photos([("photo", cells_doc)])
    result = rd.read_sheet(readings, official, roster or None)
    docs = [
        scoresheet.SetSheetIn.model_validate(s.doc).model_dump(mode="json") for s in result.sets
    ]
    read = {
        str(s.no): {"state": "draft", "us_half": s.us_half, "proposed": s.doc, "cells": {
            k: p.as_json() for k, p in s.cells.items()}}
        for s in result.sets
    }  # fmt: skip
    drafts = {s.no for s in result.sets}
    use = scoresheet.SheetUse(scoresheet.check(docs, official, drafts), True, readings, read)
    return use, result


def reference(
    ev: core_log.Evidence | None, set_ins: list[sc.SetIn], truth: dict[str, Any]
) -> dict[str, tuple[str, tuple[int, int] | None]]:
    """Per rally the log's answer (winner or none, the score after it), corrected by TRUTH."""
    out: dict[str, tuple[str, tuple[int, int] | None]] = {}
    if ev is not None:
        for st in set_ins:
            for r in st.rallies:
                v = ev.verdict(r.key)
                if v is None:
                    continue
                won = core_log._side(v.says, ev.us)
                score = core_log.ours(v.score, ev.us) if v.score is not None else None
                out[r.key] = (won, score)
    for key, fix in (truth.get("rallies") or {}).items():
        out[key] = (fix["won"], tuple(fix["score"]) if fix.get("score") else None)
    return out


def decode(
    mix: str,
    dump: dict[str, Any],
    docs: list[dict[str, Any]],
    cells_doc: dict[str, Any] | None,
    truth: dict[str, Any],
    sure: float,
) -> tuple[list[Any], dict[str, tuple[str, tuple[int, int] | None]], float]:
    """The joint decode of one mix as the API runs it: per rally its output, the reference."""
    official = _official(dump)
    set_ins, rallies, _, board = base_sets(dump)
    ev = log_evidence(dump, rallies)
    ref = reference(ev, set_ins, truth)
    roster = {int(x["number"]) for x in dump.get("roster") or [] if x["side"] == "us"}
    if mix in ("b", "e'") and cells_doc is not None:
        use, _ = read_use(cells_doc, official, roster)
        set_ins = scoresheet.apply(use, set_ins)
    elif mix in ("c", "e", "c0", "e0"):
        set_ins = scoresheet.apply(typed_use(docs, official), set_ins)
    if mix in ("c0", "e0"):  # without the video's evidence of who served and won
        set_ins = [replace(st, rallies=[replace(r, p_near=None) for r in st.rallies])
                   for st in set_ins]  # fmt: skip
        board = None
    t0 = time.perf_counter()
    done = core_scoring.decide(
        set_ins, log_ev=ev if mix in ("d", "e", "e'", "e0") else None, board=board, sure=sure
    )
    outs = [
        replace(ro, p_answer=ro.p_answer * so.sides_share)
        for so in done.match.sets
        for ro in so.rallies
    ]
    return outs, ref, time.perf_counter() - t0


def summary(
    outs: list[Any],
    ref: dict[str, tuple[str, tuple[int, int] | None]],
    sure: float,
    open_keys: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Sure (no reason, the posterior at least `sure`), wrong among sure, the score line; of the
    open suggestions (`open_keys`), how many need the coach."""
    n = len(outs)
    sure_ = [ro for ro in outs if not ro.reasons and ro.p_answer >= sure]
    sure_keys = {ro.key for ro in sure_}
    open_need = sum(1 for ro in outs if ro.key in open_keys and ro.key not in sure_keys)
    wrong_sure = []
    unverifiable = 0
    for ro in sure_:
        found = ref.get(ro.key)
        if found is None:
            unverifiable += 1
            continue
        won, score = found
        answer = ro.won_by if ro.point else "none"
        line = (ro.score_us, ro.score_them)
        if answer != won or (score is not None and ro.point and line != score):
            wrong_sure.append((ro.key, answer, won, line, score, ro.p_answer))
    points = [(ro, ref[ro.key]) for ro in outs if ro.key in ref and ref[ro.key][0] != "none"]
    line_right = sum(
        1
        for ro, (_, score) in points
        if score is not None and (ro.score_us, ro.score_them) == score
    )
    winner_right = sum(1 for ro, (won, _) in points if ro.point and ro.won_by == won)
    told = [(ro, ref[ro.key]) for ro in outs if ro.key in ref]
    answer_right = sum(1 for ro, (won, _) in told if (ro.won_by if ro.point else "none") == won)
    reasons = Counter(ro.reasons[0].code for ro in outs if ro.reasons)
    return {
        "rallies": n,
        "sure": len(sure_),
        "sure_share": round(len(sure_) / n, 3) if n else 0.0,
        "wrong_sure": len(wrong_sure),
        "wrong_sure_list": wrong_sure,
        "unverifiable_sure": unverifiable,
        "needs_coach": n - len(sure_),
        "open": len(open_keys & {ro.key for ro in outs}),
        "open_needs_coach": open_need,
        "told": len(told),
        "answer_right": answer_right,
        "points": len(points),
        "winner_right": winner_right,
        "line_right": line_right,
        "reasons": dict(reasons),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("dump", type=Path)
    parser.add_argument("sheet", type=Path)
    parser.add_argument("cells", type=Path)
    parser.add_argument("--truth", type=Path)
    parser.add_argument("--sure", type=float, default=sc.SURE)
    parser.add_argument("--weight", type=float, default=sc.W_READ)
    parser.add_argument("--note-weight", type=float, default=sc.W_NOTE)
    parser.add_argument("--temperature", type=float, default=sc.TEMPERATURE)
    parser.add_argument("--box-disagrees", type=float, default=sc.BOX_DISAGREES)
    parser.add_argument(
        "--sheet-weight", type=float, default=sc.W_SHEET, help="the typed sheet against the log"
    )
    parser.add_argument("--mixes", default=",".join(MIXES))
    parser.add_argument(
        "--sweep", help="thresholds to report, e.g. 0.9,0.95,0.97,0.99 (one decode per mix)"
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()
    from sheet_players_eval import sheet_docs

    sc.W_READ = args.weight
    sc.W_NOTE = args.note_weight
    sc.TEMPERATURE = args.temperature
    sc.BOX_DISAGREES = args.box_disagrees
    sc.W_SHEET = args.sheet_weight
    sc.C_SHEET_WON = sc.C_LOG * args.sheet_weight
    sc.C_SHEET_SCORE = sc.C_LOG_SCORE * args.sheet_weight
    dump = json.loads(args.dump.read_text())
    docs = sheet_docs(json.loads(args.sheet.read_text()))
    cells_doc = json.loads(args.cells.read_text())
    truth = json.loads(args.truth.read_text()) if args.truth else {}
    print(
        f"{dump['video']['title']}: read weight {args.weight}, note weight {args.note_weight}, "
        f"temperature {args.temperature}, sheet weight {args.sheet_weight}"
    )
    thresholds = [float(x) for x in args.sweep.split(",")] if args.sweep else [args.sure]
    open_keys = {
        r["id"]
        for r in dump["rallies"]
        if r["deleted_at"] is None and r["source"] == "model" and r["review"] == "unreviewed"
    }
    print(
        "mix  sure>=  rallies  sure (share)  wrong among sure  needs coach  (open sugg.)  "
        "answer = log  winner right  score line right  s"
    )
    for mix in args.mixes.split(","):
        outs, ref, seconds = decode(mix, dump, docs, cells_doc, truth, 0.0)
        for tau in thresholds:
            m = summary(outs, ref, tau, open_keys)
            print(
                f"{mix:3}  {tau:5}  {m['rallies']:7}  {m['sure']:4} ({m['sure_share']:.0%})  "
                f"{m['wrong_sure']:16}  {m['needs_coach']:11}  "
                f"{m['open_needs_coach']:4}/{m['open']:<6}  "
                f"{m['answer_right']:4}/{m['told']:<7}  {m['winner_right']:4}/{m['points']:<7}  "
                f"{m['line_right']:4}/{m['points']:<11}  {seconds:.2f}"
            )
            if args.verbose:
                print("     reasons", m["reasons"])
                for w in m["wrong_sure_list"]:
                    print("     wrong sure:", w)


if __name__ == "__main__":
    main()
