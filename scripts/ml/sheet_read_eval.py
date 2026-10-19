"""Reading scoresheet photos, measured (PLAN §10.3a, SS4): the whole pipeline the worker and the
API run (register, cut, read, decode with the rules) against what is really written.

Synthetic sheets (made-up matches; digits from the datasets' test splits, never trained on):

    cd backend && uv run python ../scripts/ml/sheet_read_eval.py synthetic DATA_DIR \
        [--sheets 40] [--seed 3] [--carbon 0.35]

DATA_DIR as for scripts/ml/sheet_cells.py. Each sheet is a consistent match of three or four
sets (wieplay/ml/sheet.py's simulator) with lineups, substitutions and timeouts on the path and
the set times, written in pen or as a faint carbon copy and photographed like a phone does
(perspective, a turn, uneven light, a bend, blur, JPEG; some turned a quarter, some cut off at an
edge, some on another sheet). The official set scores and our roster are known, as for a league
match in WiePlay.

A real photo, against a transcription made by eye (never in git; the format is below):

    cd backend && uv run python ../scripts/ml/sheet_read_eval.py real PHOTO TRUTH.json

TRUTH.json: {"us_letter": "A"|"B", "official": [[us, them], ...], "roster": [numbers] | null,
"sets": {"1": {"first_serve": "us"|"them", "lineup_us": [6], "lineup_them": [6],
"boxes_us": {"p.b": value | "X"}, "boxes_them": {...}, "timeouts": [[side, own, opp]],
"subs": [[side, position, number, [own, opp] | null, [own, opp] | null]], "start": "hh:mm" | null,
"end": ..., "unsure": [field keys the transcriber is not sure of]}}}

Reported: the share of cells read right before the rules (the reader's first reading) and after
(the draft), the sets whose service boxes and first server came out exactly, the cells to check
per sheet, and the seconds per sheet (on this machine's CPU).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import numpy as np

BACKEND = Path(__file__).resolve().parents[2] / "backend"
sys.path.insert(0, str(BACKEND))

from wieplay.ml import sheet as sh  # noqa: E402
from wieplay.ml import sheet_cells as sc  # noqa: E402
from wieplay.ml import sheet_read as rd  # noqa: E402
from wieplay.ml import sheet_synth as ss  # noqa: E402

Fields = dict[str, Any]


def truth_fields(s: dict[str, Any]) -> Fields:
    """The fields of one set as the eval compares them."""
    out: Fields = {"first_serve": s["first_serve"]}
    for side in sh.SIDES:
        for k, v in s[f"boxes_{side}"].items():
            out[f"box.{side}.{k}"] = v
        for p, v in enumerate(s.get(f"lineup_{side}") or [], start=1):
            if v is not None:
                out[f"lineup.{side}.{p}"] = v
    for side, own, opp in s.get("timeouts", []):
        out[f"to.{side}.{own}:{opp}"] = True
    for side, p, number, at, back in s.get("subs", []):
        if number is not None:
            out[f"sub.{side}.{p}.number"] = number
        if at:
            out[f"sub.{side}.{p}.at"] = list(at)
        if back:
            out[f"sub.{side}.{p}.back"] = list(back)
    for name in ("start", "end"):
        if s.get(name):
            out[name] = s[name]
    return out


def doc_fields(doc: dict[str, Any]) -> Fields:
    """The same fields of a proposed set (the draft)."""
    out: Fields = {}
    first = doc.get("first_serve")
    if first:
        out["first_serve"] = first
        out[f"box.{sh.other(first)}.1.1"] = "X"
    for side in sh.SIDES:
        for p, col in enumerate(doc.get(f"boxes_{side}") or [], start=1):
            for b, v in enumerate(col, start=1):
                if v is not None:
                    out[f"box.{side}.{p}.{b}"] = v
        for p, v in enumerate(doc.get(f"lineup_{side}") or [], start=1):
            if v is not None:
                out[f"lineup.{side}.{p}"] = v
    for t in doc.get("timeouts", []):
        out[f"to.{t['side']}.{t['own']}:{t['opp']}"] = True
    for u in doc.get("subs", []):
        if u.get("number") is not None:
            out[f"sub.{u['side']}.{u['position']}.number"] = u["number"]
        if u.get("at"):
            out[f"sub.{u['side']}.{u['position']}.at"] = list(u["at"])
        if u.get("back"):
            out[f"sub.{u['side']}.{u['position']}.back"] = list(u["back"])
    for name in ("start", "end"):
        if doc.get(name):
            out[name] = doc[name]
    return out


def raw_fields(r: rd.Readings, no: int, us_half: str) -> Fields:
    """What the reader alone says (its first reading of each cell), in the true orientation."""
    halves = {"us": us_half, "them": "R" if us_half == "L" else "L"}
    out: Fields = {}
    rows = 3 if no == 5 else 4
    for side, half in halves.items():
        for p in range(1, 7):
            for b in range(1, 2 * rows + 1):
                v = r.get(f"s{no}.{half}.box.{p}.{b}").best
                if v is not None:
                    out[f"box.{side}.{p}.{b}"] = v
            v = r.get(f"s{no}.{half}.lineup.{p}").best
            if isinstance(v, int):
                out[f"lineup.{side}.{p}"] = v
            v = r.get(f"s{no}.{half}.sub_in.{p}").best
            if isinstance(v, int):
                out[f"sub.{side}.{p}.number"] = v
            for name in ("at", "back"):
                a = r.get(f"s{no}.{half}.sub_{name}.{p}.0").best
                b2 = r.get(f"s{no}.{half}.sub_{name}.{p}.1").best
                if isinstance(a, int) and isinstance(b2, int):
                    out[f"sub.{side}.{p}.{name}"] = [a, b2]
        for k in (1, 2):
            a = r.get(f"s{no}.{half}.to.{k}.0").best
            b2 = r.get(f"s{no}.{half}.to.{k}.1").best
            if isinstance(a, int) and isinstance(b2, int):
                out[f"to.{side}.{a}:{b2}"] = True
    for name, half in (("start", "L"), ("end", "R")):
        hh = r.get(f"s{no}.{half}.{name}.h").best
        mm = r.get(f"s{no}.{half}.{name}.m").best
        if isinstance(hh, int) and isinstance(mm, int):
            out[name] = f"{hh:02d}:{mm:02d}"
    return out


def compare(truth: Fields, got: Fields, skip: set[str] = frozenset()) -> tuple[int, int, list[str]]:  # type: ignore[assignment]
    """(right, total, wrong keys) over every field either side has (first_serve apart)."""
    keys = (set(truth) | set(got)) - {"first_serve"} - skip
    wrong = sorted(k for k in keys if truth.get(k) != got.get(k))
    return len(keys) - len(wrong), len(keys), wrong


def boxes_exact(truth: Fields, got: Fields) -> bool:
    keys = {k for k in set(truth) | set(got) if k.startswith("box.")}
    return all(truth.get(k) == got.get(k) for k in keys) and truth.get("first_serve") == got.get(
        "first_serve"
    )


def evaluate(
    gray: np.ndarray,
    reader: sc.CellReader,
    truth_sets: dict[str, Any],
    us_letter: str,
    official: list[tuple[int, int]] | None,
    roster: set[int] | None,
    verbose: bool = False,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    photo = sc.read_photo(gray, reader)
    t1 = time.perf_counter()
    readings = rd.Readings.from_photos([("photo", photo.as_json())])
    result = rd.read_sheet(readings, official, roster, sets=[int(n) for n in truth_sets])
    t2 = time.perf_counter()
    out: dict[str, Any] = {
        "read_s": t1 - t0,
        "decode_s": t2 - t1,
        "to_check": result.to_check,
        "letter_right": result.us_letter == us_letter,
        "sets": [],
    }
    for s in result.sets:
        truth = truth_sets[str(s.no)]
        tf = truth_fields(truth)
        skip = set(truth.get("unsure", []))
        true_half = rd.us_half_for(s.no, us_letter) if s.no != 5 else truth.get("us_half", "L")
        raw = raw_fields(readings, s.no, true_half)
        got = doc_fields(s.doc)
        r_ok, r_n, r_wrong = compare(tf, raw, skip)
        d_ok, d_n, d_wrong = compare(tf, got, skip)
        exact = boxes_exact(tf, got)
        # Wrong in the draft and not marked to check: what a coach who trusts the dashed cells
        # would miss (timeouts by their slot: a wrong score is a missing and an extra one).
        flagged = {k for k, p in s.cells.items() if p.check}
        timeouts_flagged = any(k.startswith("to.") for k in flagged)
        silent = [
            k
            for k in d_wrong
            if k not in flagged
            and not (k.startswith("to.") and timeouts_flagged)
            and not (k.startswith("sub.") and f"{k.rsplit('.', 1)[0]}.number" in flagged)
        ]
        out["sets"].append(
            {
                "no": s.no,
                "raw": (r_ok, r_n),
                "draft": (d_ok, d_n),
                "silent": silent,
                "exact": exact,
                "decoded": s.decoded,
                "to_check": s.to_check,
                "wrong": d_wrong,
                "raw_wrong": r_wrong,
                "note": s.note,
            }
        )
        if verbose:
            checks = sorted(k for k, p in s.cells.items() if p.check)
            print(
                f"  set {s.no}: raw {r_ok}/{r_n}, draft {d_ok}/{d_n}, boxes exact {exact}, "
                f"decoded {s.decoded}, to check {s.to_check}"
            )
            print(f"    draft wrong: {d_wrong}")
            print(f"    not marked:  {silent}")
            print(f"    raw wrong:   {r_wrong}")
            print(f"    to check:    {checks}")
    return out


def summarise(results: list[dict[str, Any]], label: str) -> None:
    raw = [s["raw"] for r in results for s in r["sets"]]
    draft = [s["draft"] for r in results for s in r["sets"]]
    sets = [s for r in results for s in r["sets"]]
    r_ok, r_n = sum(a for a, _ in raw), sum(b for _, b in raw)
    d_ok, d_n = sum(a for a, _ in draft), sum(b for _, b in draft)
    checks = [r["to_check"] for r in results]
    print(f"\n{label}: {len(results)} sheets, {len(sets)} sets, {d_n} cells")
    print(f"  cells right, the reader alone:   {r_ok}/{r_n} = {r_ok / max(r_n, 1):.1%}")
    print(f"  cells right, after the rules:    {d_ok}/{d_n} = {d_ok / max(d_n, 1):.1%}")
    silent = sum(len(s["silent"]) for s in sets)
    print(f"  wrong and not marked to check:   {silent}/{d_n} = {silent / max(d_n, 1):.1%}")
    print(f"  sets exact (boxes, first server): {sum(s['exact'] for s in sets)}/{len(sets)}")
    print(f"  sets decoded:                     {sum(s['decoded'] for s in sets)}/{len(sets)}")
    print(
        f"  cells to check per sheet: mean {statistics.mean(checks):.1f}, median "
        f"{statistics.median(checks):.0f}, max {max(checks)}"
    )
    letters = sum(r["letter_right"] for r in results)
    print(f"  orientation (we are A/B) right: {letters}/{len(results)}")
    print(
        f"  seconds per sheet: reading {statistics.mean(r['read_s'] for r in results):.1f} "
        f"(register, cut, CNN), decoding {statistics.mean(r['decode_s'] for r in results):.1f}"
    )


def synthetic(args: argparse.Namespace) -> None:
    sys.path.insert(0, str(Path(__file__).parent))
    from sheet_cells import glyph_source  # the training script's loader

    glyphs = glyph_source(args.data, "test")
    reader = sc.CellReader()
    results = []
    for k in range(args.sheets):
        rng = np.random.default_rng(args.seed * 1000 + k)
        letter = "A" if rng.random() < 0.5 else "B"
        content = ss.match_content(rng, sets=int(rng.integers(3, 5)), us_letter=letter)
        carbon = bool(rng.random() < args.carbon)
        page = ss.page(content, glyphs, rng, ink=ss.random_ink(rng, carbon))
        turns = int(rng.integers(1, 4)) if rng.random() < 0.2 else 0
        photo = ss.photograph(
            page, rng, quarter_turns=turns, crop=0.06 if rng.random() < 0.1 else 0.0
        )
        gray = ss.cv2.cvtColor(photo.image, ss.cv2.COLOR_BGR2GRAY)
        truth = {}
        for no, s in content.truth.items():
            t: dict[str, Any] = {"first_serve": s.first_serve}
            for side in sh.SIDES:
                boxes = {}
                for p in range(1, 7):
                    for b in range(1, 9):
                        v = s.value(side, p, b)
                        if v is not None:
                            boxes[f"{p}.{b}"] = v
                if s.first_serve != side:
                    boxes["1.1"] = "X"
                t[f"boxes_{side}"] = boxes
                t[f"lineup_{side}"] = list(s.lineup.get(side) or [])
            half = {side: ss.half_of(no, side, letter) for side in sh.SIDES}
            t["timeouts"] = [
                [side, a, b]
                for side in sh.SIDES
                for a, b in content.halves[(no, half[side])].timeouts
            ]
            t["subs"] = [
                [
                    side,
                    p,
                    n,
                    list(content.halves[(no, half[side])].sub_at[p]),
                    list(content.halves[(no, half[side])].sub_back[p])
                    if p in content.halves[(no, half[side])].sub_back
                    else None,
                ]
                for side in sh.SIDES
                for p, n in content.halves[(no, half[side])].sub_in.items()
            ]
            t["start"] = content.halves[(no, "L")].time
            t["end"] = content.halves[(no, "R")].time
            truth[str(no)] = t
        roster = set(content.truth[1].lineup.get("us") or []) | {
            int(v) for v in rng.integers(1, 25, 6)
        }
        official = [content.finals[no] for no in sorted(content.finals)]
        try:
            res = evaluate(gray, reader, truth, letter, official, roster, verbose=args.verbose)
        except Exception as exc:  # a photo the registration cannot place
            print(f"sheet {k}: {type(exc).__name__}: {exc}")
            continue
        res["carbon"] = carbon
        results.append(res)
        print(
            f"sheet {k} ({'carbon' if carbon else 'pen'}, {photo.params['width']} px, "
            f"turns {turns}): "
            + ", ".join(
                f"set {s['no']} {s['draft'][0]}/{s['draft'][1]}{' exact' if s['exact'] else ''}"
                for s in res["sets"]
            )
            + f"; to check {res['to_check']}",
            flush=True,
        )
    summarise(results, "synthetic")
    summarise([r for r in results if r["carbon"]], "synthetic, carbon copies")


def real(args: argparse.Namespace) -> None:
    import cv2

    truth = json.loads(args.truth.read_text())
    gray = cv2.imread(str(args.photo), cv2.IMREAD_GRAYSCALE)
    reader = sc.CellReader()
    official = [tuple(s) for s in truth["official"]] if truth.get("official") else None
    roster = set(truth["roster"]) if truth.get("roster") else None
    res = evaluate(gray, reader, truth["sets"], truth["us_letter"], official, roster, verbose=True)  # type: ignore[arg-type]
    summarise([res], f"real: {args.photo.name}")


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("synthetic")
    p.add_argument("data", type=Path)
    p.add_argument("--sheets", type=int, default=40)
    p.add_argument("--seed", type=int, default=3)
    p.add_argument("--carbon", type=float, default=0.35)
    p.add_argument("--verbose", action="store_true")
    q = sub.add_parser("real")
    q.add_argument("photo", type=Path)
    q.add_argument("truth", type=Path)
    args = parser.parse_args()
    synthetic(args) if args.cmd == "synthetic" else real(args)


if __name__ == "__main__":
    main()
