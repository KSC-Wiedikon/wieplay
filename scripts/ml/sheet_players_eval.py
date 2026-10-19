"""The official scoresheet for the players, measured (PLAN §10.3a, §10.5).

Real games, offline (nothing here writes to a database or a bucket):

1. Dump what the database knows about a video, read-only, with psql on the database host
   (rallies, sets, the score log, the serve and activity tracks, the calibration, the roster
   snapshot, the players run's cards): `scripts/ml/sheet_players_dump.sql`.
2. A typed scoresheet as JSON (the sheet form's fields per set; see `sheet_docs`).
3. The players job's detection on a local copy of the video:
   `scripts/ml/players_eval.py detect VIDEO INPUTS.json OUT_DIR --gpu` (the inputs with a lead
   before each set's first rally: `inputs` below).

    cd backend && uv run python ../scripts/ml/sheet_players_eval.py inputs DUMP.json OUT.json
    cd backend && uv run python ../scripts/ml/sheet_players_eval.py align DUMP.json SHEET.json
    cd backend && uv run python ../scripts/ml/sheet_players_eval.py serves DUMP.json SHEET.json \
        OUT_DIR [--frames FOLDER]
    cd backend && uv run python ../scripts/ml/sheet_players_eval.py identify DUMP.json \
        SHEET.json OUT_DIR [--without sheet|server|receivers|allowed|lineup]
    cd backend && uv run python ../scripts/ml/sheet_players_eval.py breaks DUMP.json SHEET.json

`align`: the sheet's points lined up with the rallies as the API does (the scoring with the
sheet and the score log), against the log's own score after each rally. `serves`: at each
lined-up rally's serve, the people of both teams, the server found behind the end line and
the receiving team's zones (wieplay/ml/on_court.py), against the shirt numbers read on the
same tracks; `--frames` draws the serve frames with the labels for checking by eye (pictures
of club members: delete them after looking). `identify`: the job's linking with and without
the sheet's labels (sure cards, footage on sure cards, readings that agree). `breaks`: where
the sheet's timeouts, substitutions and sanctions land, against the log's presses.

Dumps, sheets, collectors and frames hold club members' data: keep them on the machine that
runs this, outside git, and delete them after the measurement (PLAN §5.5).
"""

from __future__ import annotations

import bisect
import json
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from types import SimpleNamespace as NS
from typing import Any

# --- the typed sheet ----------------------------------------------------------------------------


def sheet_docs(gt: dict[str, Any]) -> list[dict[str, Any]]:
    """A transcription ({"sets": {"1": {first_serve, lineup_us, lineup_them, boxes_us: {"p.b":
    value | "X"}, boxes_them, timeouts: [[side, own, opp]], subs: [[side, position, number,
    [own, opp], [own, opp] | null]], sanctions: [[side, kind, own, opp]], start, end}}}) as
    the sheet form's sets (core/scoresheet.py SetSheetIn)."""
    from wieplay.ml import sheet as sh

    out = []
    for no_text, s in sorted(gt["sets"].items(), key=lambda kv: int(kv[0])):
        no = int(no_text)
        deciding = no == 5
        n = sh.boxes_for(deciding)

        def grid(boxes: dict[str, Any], n: int = n) -> list[list[int | None]]:
            g: list[list[int | None]] = [[None] * n for _ in range(6)]
            for key, value in boxes.items():
                p, b = (int(x) for x in key.split("."))
                g[p - 1][b - 1] = value if isinstance(value, int) else None
            return g

        out.append(
            {
                "no": no,
                "first_serve": s["first_serve"],
                "lineup_us": s["lineup_us"],
                "lineup_them": s["lineup_them"],
                "boxes_us": grid(s["boxes_us"]),
                "boxes_them": grid(s["boxes_them"]),
                "timeouts": [{"side": t[0], "own": t[1], "opp": t[2]} for t in s["timeouts"]],
                "subs": [
                    {"side": u[0], "position": u[1], "number": u[2], "at": u[3], "back": u[4]}
                    for u in s["subs"]
                ],
                "sanctions": [
                    {"side": x[0], "kind": x[1], "own": x[2], "opp": x[3]}
                    for x in s.get("sanctions", [])
                ],
                "start": s.get("start"),
                "end": s.get("end"),
            }
        )
    return out


# --- the dump -----------------------------------------------------------------------------------


def _rows(dump: dict[str, Any]) -> list[NS]:
    """The rallies the scoring and the players job see."""
    rows = [r for r in dump["rallies"] if r["deleted_at"] is None and r["kind"] == "rally"]
    rows.sort(key=lambda r: (r["start_ms"], r["id"]))
    return [NS(**r) for r in rows]


def _periods(dump: dict[str, Any]) -> list[NS]:
    return sorted((NS(**p) for p in dump["periods"]), key=lambda p: (p.start_ms, p.no))


def _official(dump: dict[str, Any]) -> list[tuple[int, int]]:
    g = dump["game"]
    sets = g["sets"] or []
    return [(s[0], s[1]) if g["our_side"] == "home" else (s[1], s[0]) for s in sets]


def inputs(dump: dict[str, Any], lead_ms: int) -> dict[str, Any]:
    """What the API hands the players job (core/players.job_inputs), from a dump."""
    from wieplay.core import calibration
    from wieplay.core import players as core_players

    periods = _periods(dump)
    starts = [p.start_ms for p in periods]
    out = []
    seen: set[int] = set()
    for seg in _rows(dump):
        if seg.review in ("rejected", "merged"):
            continue
        k = bisect.bisect_right(starts, seg.start_ms) - 1
        period = periods[k] if k >= 0 else None
        if period is not None and period.end_ms is not None and seg.start_ms > period.end_ms:
            period = None
        item: dict[str, Any] = {
            "id": seg.id,
            "start_ms": seg.start_ms,
            "end_ms": seg.end_ms,
            "near_team": core_players.near_team(seg, period),  # type: ignore[arg-type]
        }
        if period is not None and period.no not in seen:
            seen.add(period.no)
            item["lead_ms"] = lead_ms
        out.append(item)
    cals = {c["status"]: c for c in dump.get("calibrations") or []}
    row = cals.get("confirmed")
    if row is None:
        found = cals.get("proposed")
        row = found if found and (found["confidence"] or 0) >= calibration.AUTO_CONFIDENT else None
    setups: list[dict[str, Any]] = []
    cal = None
    if row is not None:
        fields = ("segments", "landmarks", "image_points", "homography", "camera",
                  "reproj_error_cm", "max_error_cm", "method", "checked")  # fmt: skip
        ns = NS(**{k: row.get(k) for k in fields})
        setups = [
            {
                "from_ms": int(s["from_ms"]),
                "to_ms": None if s.get("to_ms") is None else int(s["to_ms"]),
                "homography": s.get("homography"),
                "camera": s.get("camera"),
            }
            for s in calibration.segments_of(ns)  # type: ignore[arg-type]
        ]
        cal = {"homography": row["homography"], "camera": row["camera"], "status": row["status"]}
    roster = {"us": sorted({int(x["number"]) for x in dump["roster"] or [] if x["side"] == "us"})}
    v = dump["video"]
    return {
        "rallies": out,
        "calibration": cal,
        "setups": setups,
        "roster": roster,
        "duration_ms": v["duration_ms"],
        "width": v["width"],
        "height": v["height"],
    }


# --- the sheet lined up with the rallies (as core/scoring.score_video does) ---------------------


def align(dump: dict[str, Any], docs: list[dict[str, Any]], with_log: bool = True) -> Any:
    """The scoring with the sheet (and the log): per rally key its set and the sheet's point
    (0-based index into the set's decoded points), the checks, the match."""
    from wieplay.core import scorelog as core_log
    from wieplay.core import scoresheet
    from wieplay.core import scoring as core_scoring
    from wieplay.ml import gaps
    from wieplay.ml import scorelog as sl
    from wieplay.ml import scoring as sc
    from wieplay.ml import tracks as tr

    official = _official(dump)
    rallies = _rows(dump)
    periods = _periods(dump)
    serve = tr.series(dump["serve"]["channels"], "p_near", dump["serve"]["step_ms"])
    act = gaps.Activity(
        *(
            tr.series(dump["activity"]["channels"], name, dump["activity"]["step_ms"])
            for name in tr.ACTIVITY_CHANNELS
        )
    )
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
        obs.append(core_scoring._obs(r, serve, r.serving_manual, gap))  # type: ignore[arg-type]
    shift = sc.balance([obs[i].p_near for idx in by_period.values() for i in idx])
    if shift:
        obs = [replace(o, p_near=sc.shifted(o.p_near, shift)) for o in obs]
    scored = [p for p in periods if by_period.get(p.no)]
    set_ins = [
        core_scoring._set_in(p, [obs[i] for i in by_period[p.no]], official)  # type: ignore[arg-type]
        for p in scored
    ]
    checks = scoresheet.check(docs, official)
    use = scoresheet.SheetUse(checks, True)
    set_ins = scoresheet.apply(use, set_ins)
    if with_log and dump.get("log"):
        from wieplay.core.scorelog import unpack

        payload = unpack(dump["log"]["channels"])
        log = sl.parse(payload)
        assert log is not None
        alignment = sl.align(log, [sl.RallySpan(r.id, r.start_ms, r.end_ms) for r in rallies])
        g = dump["game"]
        us = "a" if g["our_side"] == "home" else "b"
        trust = sl.trust(log, {k + 1: (s[0], s[1]) for k, s in enumerate(g["sets"])}, alignment)
        stored = NS(track=NS(model_run_id=None, created_at=None), payload=payload, log=log)
        ev = core_log.Evidence(stored, alignment, trust, us)  # type: ignore[arg-type]
        set_ins = core_log.apply(ev, set_ins)
    match = sc.score_match(set_ins)
    by_no = use.by_no()
    point_of: dict[str, tuple[int, int]] = {}
    for so in match.sets:
        c = by_no.get(so.no)
        if c is None or so.sheet != "path":
            continue
        offset = len(c.decoded.points) - len(so.point_rally)
        for out in so.rallies:
            if out.sheet_point is not None:
                point_of[out.key] = (so.no, offset + out.sheet_point - 1)
    return NS(point_of=point_of, checks=checks, use=use, match=match, rallies=rallies)


def reference_points(dump: dict[str, Any], checks: Any) -> dict[str, tuple[int, int]]:
    """Per rally the sheet's point by the log's score after it (the log is trusted on these
    games: every point in order with its time)."""
    by_no = {c.no: c for c in checks}
    periods = _periods(dump)
    from wieplay.core import scoring as core_scoring

    out = {}
    for r in _rows(dump):
        if r.log_won_by not in ("us", "them") or r.log_score_us is None:
            continue
        p = core_scoring._period_of(periods, r.start_ms)  # type: ignore[arg-type]
        if p is None or p.no not in by_no:
            continue
        k = by_no[p.no].decoded.index_of((r.log_score_us, r.log_score_them))
        if k is not None and k > 0:
            out[r.id] = (p.no, k - 1)
    return out


def cmd_align(dump: dict[str, Any], docs: list[dict[str, Any]]) -> None:
    for with_log in (True, False):
        a = align(dump, docs, with_log)
        ref = reference_points(dump, a.checks)
        ok = sum(1 for k, v in ref.items() if a.point_of.get(k) == v)
        lined = sum(1 for k in ref if k in a.point_of)
        print(
            f"with the log {with_log}: sets {[c.status for c in a.checks]}, rallies lined up "
            f"{len(a.point_of)}, the log's points {len(ref)}, lined up with the same point "
            f"{ok}/{len(ref)} ({lined} lined up at all)"
        )


# --- who stood where at each serve ----------------------------------------------------------------


def lineups_by_rally(a: Any) -> dict[str, Any]:
    """Per lined-up rally its lineup (wieplay/ml/on_court.py)."""
    from wieplay.ml import on_court

    by_no = {c.no: c for c in a.checks}
    tables = {no: on_court.point_lineups(c.sheet, c.decoded) for no, c in by_no.items()}
    return {key: tables[no][k] for key, (no, k) in a.point_of.items() if k < len(tables[no])}


def sheet_for_job(dump: dict[str, Any], inputs_: dict[str, Any], a: Any) -> Any:
    """The SheetInputs the job would get (core/lineups.py), from the alignment."""
    from wieplay.ml import serve_labels as slb

    lineups = lineups_by_rally(a)
    numbers: dict[str, set[int]] = defaultdict(set)
    for c in a.checks:
        for side in ("us", "them"):
            numbers[side] |= {n for n in c.sheet.lineup.get(side) or () if n is not None}
        for sub in c.sheet.subs:
            if sub.number_in is not None:
                numbers[sub.side].add(sub.number_in)
    rallies = []
    for r in inputs_["rallies"]:
        lu = lineups.get(r.get("id"))
        if lu is None:
            rallies.append(None)
            continue
        rallies.append(
            slb.RallySheet(
                start_ms=int(r["start_ms"]),
                end_ms=int(r["end_ms"]),
                serving=lu.serving,
                server=lu.server,
                zones=dict(lu.zones),
                set_no=lu.set_no,
                first=bool(r.get("lead_ms")),
            )
        )
    roster = dump.get("roster") or []
    liberos = {"us": {int(x["number"]) for x in roster if x["side"] == "us" and x["is_libero"]}}
    middles = {
        "us": {int(x["number"]) for x in roster if x["side"] == "us" and x["position"] == "middle"}
    }
    return slb.SheetInputs(rallies, liberos, middles, dict(numbers))


def load_collector(out_dir: str) -> dict[str, Any]:
    import pickle
    from pathlib import Path

    with (Path(out_dir) / "collector.pkl").open("rb") as handle:
        return pickle.load(handle)  # type: ignore[no-any-return]  # noqa: S301 - our own cache


def cached_reads(out_dir: str, allowed: dict[str, set[int]]) -> Any:
    """Reads the shirt numbers once (the job's reader and budget) and keeps them in
    OUT_DIR/reads.json, so every variant sees the same readings."""
    import os
    from pathlib import Path

    from wieplay.ml import people
    from wieplay.ml import players as pl
    from wieplay.worker import players as job

    path = Path(out_dir) / "reads.json"

    def reads(tracks: list[Any]) -> int:
        if path.exists():
            saved = json.loads(path.read_text())
            for t in tracks:
                frames = set(t.data.frames) if t.data is not None else set()
                t.reads = [
                    tuple(r) for r in saved["reads"].get(str(t.track_id), []) if r[0] in frames
                ]
                if not t.referee:
                    pl.vote(t, allowed or None)
            return int(saved["crops"])
        weights = Path(os.environ["WIEPLAY_WORKER_PLAYERS_WEIGHTS"])
        reader = people.NumberReader(
            weights / people.OCR_DET_FILE, weights / people.OCR_REC_FILE, threads=2
        )
        n = pl.read_numbers(tracks, reader, job.ocr_budget, allowed or None, job.OCR_WORKERS)
        path.write_text(
            json.dumps({"crops": n, "reads": {str(t.track_id): t.reads for t in tracks if t.reads}})
        )
        return n

    return reads


def _majority(reads: list[tuple[int, int, float]]) -> int | None:
    votes: dict[int, float] = defaultdict(float)
    for _, n, c in reads:
        votes[n] += c
    if not votes:
        return None
    from wieplay.ml.players import fold_partials

    folded = fold_partials(dict(votes))
    return max(folded.items(), key=lambda kv: kv[1])[0]


def cmd_serves(
    dump: dict[str, Any], docs: list[dict[str, Any]], out_dir: str, args: list[str]
) -> None:
    """The sheet's labels at the serves against the numbers read on the same tracks."""
    from wieplay.ml import identities
    from wieplay.ml import players as pl
    from wieplay.ml import serve_labels as slb

    cached = load_collector(out_dir)
    col, windows, inp = cached["col"], cached["windows"], cached["inputs"]
    a = align(dump, docs)
    sheet = sheet_for_job(dump, inp, a)
    tracks = pl.finish_tracks(col)
    sides = identities.smooth_sides([w["near_team"] for w in windows])
    identities.assign_teams(tracks, sides)
    roster = {k: set(v) for k, v in (inp.get("roster") or {}).items() if v}
    cached_reads(out_dir, roster)(tracks)
    rally_near: list[str | None] = [None] * len(sheet.rallies)
    for w, win in enumerate(windows):
        for i in win.get("rallies") or ():
            rally_near[i] = sides[w]
    calibrated = bool(inp.get("setups") or inp.get("calibration"))
    found = slb.serve_labels(
        col, tracks, sheet.rallies, rally_near, calibrated, sheet.liberos, sheet.middles,
        receivers=args[args.index("--receivers") + 1] if "--receivers" in args else None,
    )  # fmt: skip
    by_id = {t.data.track_id: t for t in tracks if t.data is not None}
    window = int(args[args.index("--within") + 1]) if "--within" in args else 100
    tally: dict[str, Counter[str]] = defaultdict(Counter)
    for lab in found.labels:
        t = by_id.get(lab.track)
        if t is None:
            continue
        near_reads = [r for r in t.reads if abs(r[0] - lab.frame) <= window]
        said = _majority(near_reads)
        row = "front" if lab.zone in (2, 3, 4) else "back" if lab.kind == "receiver" else "end"
        end = "near" if t.side == pl.NEAR else "far"
        key = f"{lab.kind} {row} {end} ({lab.team})"
        if said is None:
            tally[key]["no reading"] += 1
        elif said == lab.number:
            tally[key]["agrees"] += 1
        else:
            tally[key]["differs"] += 1
    print(json.dumps(dict(found.info)))
    for key in sorted(tally):
        c = tally[key]
        told = c["agrees"] + c["differs"]
        share = f"{c['agrees'] / told:.0%}" if told else "-"
        print(f"{key}: labels {sum(c.values())}, with a reading {told}, agree {share} {dict(c)}")
    if "--frames" in args:
        draw_serves(dump, a, found, col, by_id, args[args.index("--frames") + 1], inp)


def draw_serves(
    dump: dict[str, Any],
    a: Any,
    found: Any,
    col: Any,
    by_id: dict[int, Any],
    folder: str,
    inp: dict[str, Any],
) -> None:
    """The serve frames with every person's box and the sheet's labels (for checking by eye;
    pictures of club members: delete them after looking)."""
    import random
    import subprocess
    from pathlib import Path

    from PIL import Image, ImageDraw

    out = Path(folder)
    out.mkdir(parents=True, exist_ok=True)
    video = dump["files"][0]["nas_path"].replace("/nas/", "/mnt/nas/wieplay/", 1)
    by_frame: dict[int, list[Any]] = defaultdict(list)
    for lab in found.labels:
        by_frame[lab.frame].append(lab)
    frames = sorted(by_frame)
    rng = random.Random(3)  # noqa: S311 - a sample
    pick = sorted(rng.sample(frames, min(30, len(frames))))
    w, h = int(inp["width"]), int(inp["height"])
    for f in pick:
        t_ms = col.times[f]
        args = ["ffmpeg", "-v", "error", "-ss", f"{t_ms / 1000:.3f}", "-i", video,
                "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]  # fmt: skip
        raw = subprocess.run(  # noqa: S603 - ffmpeg from PATH with an argument list
            args, capture_output=True, check=True
        ).stdout
        img = Image.frombytes("RGB", (w, h), raw)
        d = ImageDraw.Draw(img)
        labels = {lab.track: lab for lab in by_frame[f]}
        for row in (r for r in col.rows if r[0] == f):
            _, tid, u1, v1, u2, v2 = row
            lab = labels.get(tid)
            colour = (
                (255, 255, 0)
                if lab is None
                else (255, 0, 0)
                if lab.kind == "server"
                else (0, 255, 255)
            )
            d.rectangle([u1 * w, v1 * h, u2 * w, v2 * h], outline=colour, width=2)
            t = by_id.get(tid)
            text = f"{tid}"
            if lab is not None:
                text += f" #{lab.number} z{lab.zone}"
            if t is not None and t.reads:
                near_reads = [r for r in t.reads if abs(r[0] - f) <= 100]
                said = _majority(near_reads)
                if said is not None:
                    text += f" r{said}"
            d.text((u1 * w, v1 * h - 12), text, fill=colour)
        img.save(out / f"serve-{t_ms // 60000:03d}m{t_ms // 1000 % 60:02d}s.jpg", quality=85)
    print(f"{len(pick)} frames in {out}")


def card_metrics(found: Any, ocr: dict[str, list[list[float]]], sheet: Any) -> dict[str, Any]:
    """Sure cards, footage on sure cards, readings (the reader's, not the sheet's labels) that
    agree with their card's number, sure cards whose number the sheet never has for the team."""
    from wieplay.ml import identities

    players = [c for c in found.clusters if not c.referee]
    sure = [c for c in players if c.confidence >= identities.UNCERTAIN]
    agree = told = 0
    for c in players:
        if c.number is None:
            continue
        for t in c.tracks:
            for m in t.members or [t]:
                data = m.data
                if data is None:
                    continue
                f0, f1 = data.frames[m.lo], data.frames[m.hi - 1]
                for frame, number, _ in ocr.get(str(data.track_id), []):
                    if f0 <= frame <= f1:
                        told += 1
                        agree += int(number == c.number)
    numbers = sheet.numbers if sheet is not None else {}
    off_sheet = [
        f"{c.team} #{c.number}"
        for c in sure
        if numbers.get(c.team) and c.number not in numbers[c.team]
        and c.number not in sheet.liberos.get(c.team, set())
    ]  # fmt: skip
    total = sum(c.detections for c in players) or 1
    return {
        "cards": len(players),
        "cards_us": sum(1 for c in players if c.team == "us"),
        "cards_them": sum(1 for c in players if c.team == "them"),
        "sure_us": sorted(c.number for c in sure if c.team == "us" and c.number is not None),
        "sure_them": sorted(c.number for c in sure if c.team == "them" and c.number is not None),
        "sure_share": round(sum(c.detections for c in sure) / total, 3),
        "agree": f"{agree}/{told} ({agree / max(told, 1):.0%})",
        "off_sheet": off_sheet,
    }


VARIANTS: dict[str, frozenset[str] | None] = {
    "before (no sheet)": None,
    "who may be read": frozenset({"allowed"}),
    "server (near end)": frozenset({"server"}),
    "server (both ends)": frozenset({"server", "far_server"}),
    "server (near) + receivers' front row": frozenset({"server", "receivers"}),
    "who may be read + server (near)": frozenset({"allowed", "server"}),
    "who may be read + server (both)": frozenset({"allowed", "server", "far_server"}),
    "teams from numbers": frozenset({"teams"}),
    "teams + server (near)": frozenset({"teams", "server"}),
    "teams + who may be read": frozenset({"teams", "allowed"}),
    "teams + who may be read + server (near)": frozenset({"teams", "allowed", "server"}),
}


def drop_lead(col: Any, windows: list[dict[str, Any]]) -> None:
    """The detections before each set's first rally taken out (as if the job had no lead)."""
    import bisect as bs

    lead: set[int] = set()
    for w in windows:
        if w.get("lead_ms"):
            lo = bs.bisect_left(col.times, w["start_ms"] - w["lead_ms"])
            hi = bs.bisect_left(col.times, w["start_ms"])
            lead.update(range(lo, hi))
    col.rows = [r for r in col.rows if r[0] not in lead]
    for tid in list(col.tracks):
        tr = col.tracks[tid]
        keep = [k for k, f in enumerate(tr.frames) if f not in lead]
        if not keep:
            del col.tracks[tid]
            continue
        for name in ("frames", "sides", "foot_v", "heights_px", "boxes", "foot_at"):
            values = getattr(tr, name)
            setattr(tr, name, [values[k] for k in keep])
        pairs = [
            (d, f) for d, f in zip(tr.descriptors, tr.desc_frames, strict=True) if f not in lead
        ]
        tr.descriptors = [d for d, _ in pairs]
        tr.desc_frames = [f for _, f in pairs]
        tr.slots = {k: c for k, c in tr.slots.items() if c.frame not in lead}


def cmd_identify(
    dump: dict[str, Any], docs: list[dict[str, Any]], out_dir: str, args: list[str]
) -> None:
    """The job's linking with and without the sheet (VARIANTS), the same readings for all."""
    import copy
    from pathlib import Path

    from wieplay.worker import players as job

    cached = load_collector(out_dir)
    inp = cached["inputs"]
    a = align(dump, docs)
    sheet = sheet_for_job(dump, inp, a)
    roster = {k: [int(n) for n in v] for k, v in (inp.get("roster") or {}).items()}
    calibrated = bool(inp.get("setups") or inp.get("calibration"))
    reads = cached_reads(out_dir, {k: set(v) for k, v in roster.items() if v})
    ocr_path = Path(out_dir) / "reads.json"
    no_lead = "--no-lead" in args
    args = [x for x in args if x != "--no-lead"]
    only = [v for v in VARIANTS if not args or any(x in v for x in args)]
    for name in only:
        use = VARIANTS[name]
        col = copy.deepcopy(cached["col"])
        if no_lead:
            drop_lead(col, cached["windows"])
        found = job.identify(
            col, cached["windows"], None, roster, job.DETECT_FPS, calibrated=calibrated,
            sheet=sheet if use else None, reads=reads, sheet_use=use,
        )  # fmt: skip
        ocr = json.loads(ocr_path.read_text())["reads"]
        m = card_metrics(found, ocr, sheet)
        m["labels"] = {k: v for k, v in found.stats.items() if k.startswith("sheet_")}
        print(name, json.dumps(m), flush=True)


def _placed(a: Any, point_of: dict[str, tuple[int, int]], rows: list[NS]) -> list[Any]:
    from wieplay.core import sheet_notes as sn

    facts = [
        sn.RallyFacts(
            r.id, r.start_ms, r.end_ms,
            (r.score_us_after, r.score_them_after) if r.score_us_after is not None else None,
            (r.log_score_us, r.log_score_them) if r.log_score_us is not None else None,
        )
        for r in rows
    ]  # fmt: skip
    out = []
    for c in a.checks:
        pr = {k: key for key, (no, k) in point_of.items() if no == c.no}
        out.extend(sn.place(sn.notes_of(c.sheet, c.decoded), pr, facts))
    return out


def cmd_breaks(dump: dict[str, Any], docs: list[dict[str, Any]], with_log: bool = True) -> None:
    """Where the sheet's timeouts, substitutions and sanctions land, with the API's alignment
    (the sheet and the log), against the break the log's own point times give (the
    reference alignment), and the log's timeout presses."""
    from wieplay.core import scorelog as core_log
    from wieplay.core import sheet_notes as sn
    from wieplay.ml import scorelog as sl

    a = align(dump, docs, with_log=with_log)
    ref = reference_points(dump, a.checks)
    rows = [r for r in _rows(dump) if r.review not in ("rejected", "merged")]
    # The score line as the API shows it: the score after each rally the alignment lined up.
    for r in rows:
        r.score_us_after = r.score_them_after = None
    by_no = {c.no: c for c in a.checks}
    by_id = {r.id: r for r in rows}
    for key, (no, k) in a.point_of.items():
        if key in by_id:
            p = by_no[no].decoded.points[k]
            by_id[key].score_us_after, by_id[key].score_them_after = p.us, p.them
    placed = _placed(a, a.point_of, rows)
    truth = _placed(a, ref, rows)
    payload = core_log.unpack(dump["log"]["channels"])
    log = sl.parse(payload)
    assert log is not None
    alignment = sl.align(log, [sl.RallySpan(r.id, r.start_ms, r.end_ms) for r in _rows(dump)])
    presses: dict[int, list[int]] = defaultdict(list)
    for e in log.events:
        if e.kind == "timeout" and e.t_ms is not None:
            for st in alignment.stretches:
                if st.period == e.period:
                    presses[e.period].append(e.t_ms + st.shift_ms)
    tally: dict[str, Counter[str]] = defaultdict(Counter)
    for p, t in zip(placed, truth, strict=True):
        kind = p.note.kind
        c = tally[kind]
        c["notes"] += 1
        if p.start_ms is None:
            c["not placed"] += 1
            continue
        if t.start_ms is None:
            c["no reference"] += 1
        elif t.start_ms == p.start_ms:
            c["same break as the log"] += 1
        else:
            c["another break"] += 1
        if kind == "timeout":
            inside = any(
                p.start_ms - 3000 <= x <= (p.end_ms or p.start_ms + 60_000)
                for x in presses.get(p.note.set_no, [])
            )
            c["log's timeout press in it"] += int(inside)
        for issue in p.issues:
            c[f"flag {issue}"] += 1
    for kind, c in tally.items():
        print(kind, dict(c))
    print(json.dumps(sn.summary(placed)))


def main() -> None:
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    cmd = args[0]
    if cmd == "inputs":
        dump = json.load(open(args[1]))  # noqa: SIM115
        lead = int(args[args.index("--lead") + 1]) if "--lead" in args else 60_000
        json.dump(inputs(dump, lead), open(args[2], "w"))  # noqa: SIM115
        return
    dump = json.load(open(args[1]))  # noqa: SIM115
    docs = sheet_docs(json.load(open(args[2])))  # noqa: SIM115
    if cmd == "align":
        cmd_align(dump, docs)
    elif cmd == "serves":
        cmd_serves(dump, docs, args[3], args[4:])
    elif cmd == "identify":
        cmd_identify(dump, docs, args[3], args[4:])
    elif cmd == "breaks":
        cmd_breaks(dump, docs, "--no-log" not in args)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
