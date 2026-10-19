"""The players' cards against by-eye labels (PLAN §10.5, players v3): purity and the answers a
coach needs in "Identify players".

    python scripts/ml/players_labels.py sample OUT_DIR VIDEO [--n 320] [--seed 11]
    python scripts/ml/players_labels.py again  OUT_DIR VIDEO      # a second look at the "?"
    python scripts/ml/players_labels.py score  OUT_DIR [--reads reads.json]

OUT_DIR is a `players_eval.py detect` folder (collector.pkl) with the job's inputs as
OUT_DIR/inputs.json (core/players.job_inputs, the sheet included). `sample` draws detections
uniformly over the players' footage (the job's own linking), and writes label sheets: per
sample the person at that frame from the video (the box drawn) and two crops of its piece.
Label them by eye in OUT_DIR/labels/labels.txt, one "ID LABEL" per line: "us7", "them12",
"n12" (a number, the team not tellable: one club kit), "us?"/"them?"/"?" (cannot tell),
"x" (not a player). `again` draws the "?" ones larger with more crops of their piece;
overrides go in labels2.txt. `score` runs the job's linking (worker/players.identify, the
shirt numbers read once and kept in OUT_DIR/READS) and prints, per card kind, the purity by
eye, the footage on sure cards and the simulated queue: sure cards named from the start, a
person card answered whole (its main person), a pieces card piece-wise (the coach ticks one
person's among the 24 biggest pieces shown), the most footage named per answer first; the
share of the players' footage named right after each answer (relink included).

Pictures of club members: keep OUT_DIR on the machine that runs this and delete it after the
measurement (PLAN §5.5). Run from backend/ with its venv and WIEPLAY_WORKER_PLAYERS_WEIGHTS.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import pickle
import random
import subprocess
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from wieplay.ml import identities, people, serve_labels
from wieplay.ml import players as pl
from wieplay.worker import players as job

FPS = job.DETECT_FPS
SHOWN = 24  # pieces shown at once on a pieces card (core/identify.PIECES_SHOWN)
PIECES = getattr(pl, "PIECES", "pieces")


def card_kind(c: pl.Cluster) -> str:
    return str(getattr(c, "kind", "person"))


# --- the job's linking, from the cache -------------------------------------------------------


def load(out: Path) -> dict[str, Any]:
    with (out / "collector.pkl").open("rb") as h:
        cached: dict[str, Any] = pickle.load(h)  # noqa: S301 - our own cache
    cached["inputs"] = json.loads((out / "inputs.json").read_text())
    return cached


def reads_fn(out: Path, name: str, allowed: dict[str, set[int]]) -> Any:
    path = out / name

    def reads(tracks: list[pl.Track]) -> int:
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
        reads_out = {str(t.track_id): t.reads for t in tracks if t.reads}
        path.write_text(json.dumps({"crops": n, "reads": reads_out}))
        return n

    return reads


def link(out: Path, cached: dict[str, Any], reads: str) -> job.Identified:
    inputs = cached["inputs"]
    roster = {k: [int(n) for n in v] for k, v in (inputs.get("roster") or {}).items()}
    allowed = {team: set(nums) for team, nums in roster.items() if nums}
    aspect = int(inputs["width"]) / int(inputs["height"])
    setups = pl.Setups(inputs.get("setups") or [], inputs.get("calibration"), aspect)
    return job.identify(
        cached["col"],
        cached["windows"],
        None,
        roster,
        FPS,
        calibrated=setups.calibrated,
        sheet=serve_labels.sheet_inputs(inputs),
        reads=reads_fn(out, reads, allowed),
    )


def owners(found: job.Identified) -> dict[int, list[tuple[int, int, int, int]]]:
    """Tracker track id → [(first, last, piece id, card key)] of the players' cards."""
    by: dict[int, list[tuple[int, int, int, int]]] = defaultdict(list)
    for c in found.clusters:
        if c.referee:
            continue
        for t in c.tracks:
            for m in t.members or [t]:
                src = m.data.track_id if m.data is not None else m.track_id
                by[src].append((m.first, m.last, t.track_id, c.key))
    return by


def locate(
    by: dict[int, list[tuple[int, int, int, int]]], frame: int, src: int
) -> tuple[int, int] | None:
    for f0, f1, piece, key in by.get(src, ()):
        if f0 <= frame <= f1:
            return piece, key
    return None


# --- pictures for labelling -------------------------------------------------------------------


def grab(
    video: Path, t_ms: int, box: tuple[float, float, float, float], height: int = 230
) -> Image.Image:
    args = [
        "ffmpeg",
        "-v",
        "error",
        "-ss",
        f"{t_ms / 1000:.3f}",
        "-i",
        str(video),
        "-frames:v",
        "1",
        "-f",
        "image2pipe",
        "-vcodec",
        "png",
        "-",
    ]
    img = Image.open(io.BytesIO(subprocess.run(args, capture_output=True, check=True).stdout))  # noqa: S603
    img = img.convert("RGB")
    w, h = img.size
    x1, y1, x2, y2 = box[0] * w, box[1] * h, box[2] * w, box[3] * h
    bh = max(y2 - y1, 8)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    a, b = max(0, int(cx - bh * 0.64)), max(0, int(cy - bh * 0.85))
    tile = img.crop((a, b, min(w, int(cx + bh * 0.64)), min(h, int(cy + bh * 0.85))))
    ImageDraw.Draw(tile).rectangle(
        (x1 - a, y1 - b, x2 - a, y2 - b), outline=(255, 40, 40), width=max(1, int(bh / 60))
    )
    s = height / max(tile.size[1], 1)
    return tile.resize((max(1, int(tile.size[0] * s)), height), Image.LANCZOS)


def strip(images: list[Image.Image], height: int = 230) -> Image.Image:
    scaled = [
        im.resize((max(1, int(im.size[0] * height / max(im.size[1], 1))), height), Image.LANCZOS)
        for im in images
    ]
    out = Image.new("RGB", (max(1, sum(i.size[0] + 3 for i in scaled)), height), (12, 12, 12))
    x = 0
    for im in scaled:
        out.paste(im, (x, 0))
        x += im.size[0] + 3
    return out


def sheet(
    entries: list[tuple[int, list[Image.Image], str]],
    path: Path,
    per_row: int,
    cell: tuple[int, int],
) -> None:
    rows = max(1, -(-len(entries) // per_row))
    out = Image.new("RGB", (per_row * cell[0], rows * cell[1]), (12, 12, 12))
    draw = ImageDraw.Draw(out)
    for k, (sid, images, text) in enumerate(entries):
        r, c = divmod(k, per_row)
        x, y = c * cell[0], r * cell[1]
        im = strip(images)
        im.thumbnail((cell[0] - 6, cell[1] - 26))
        out.paste(im, (x + 3, y + 3))
        draw.rectangle((x, y, x + cell[0] - 1, y + cell[1] - 1), outline=(70, 70, 70))
        draw.text((x + 4, y + cell[1] - 21), f"{sid} {text}"[:60], fill=(255, 255, 0))
    out.save(path, quality=88)


def pieces_by_track(found: job.Identified) -> dict[int, list[pl.Track]]:
    out: dict[int, list[pl.Track]] = defaultdict(list)
    for c in found.clusters:
        for t in c.tracks:
            for m in t.members or [t]:
                if m.data is not None:
                    out[m.data.track_id].append(m)
    return out


def cmd_sample(out: Path, video: Path, n: int, seed: int, reads: str) -> None:
    cached = load(out)
    found = link(out, cached, reads)
    col = cached["col"]
    by = owners(found)
    rows = [(f, tid, (u1, v1, u2, v2)) for f, tid, u1, v1, u2, v2 in col.rows if locate(by, f, tid)]
    picked = sorted(random.Random(seed).sample(rows, n), key=lambda r: r[0])  # noqa: S311
    members = pieces_by_track(found)
    folder = out / "labels"
    folder.mkdir(exist_ok=True)
    items = []
    for k, (f, tid, box) in enumerate(picked):
        m = next(m for m in members[tid] if m.first <= f <= m.last)
        crops = sorted(m.crops, key=lambda c: -c.quality)[:2]
        items.append(
            {
                "id": k,
                "frame": f,
                "track": tid,
                "t_ms": int(col.times[f]),
                "box": list(box),
                "side": "N" if m.side == pl.NEAR else "F",
                "team": m.team,
                "reads": sorted({r[1] for r in m.reads}),
                "_crops": crops,
            }
        )
    with ThreadPoolExecutor(max_workers=8) as pool:
        imgs = list(pool.map(lambda it: grab(video, it["t_ms"], tuple(it["box"])), items))
    for s in range(0, len(items), 16):
        entries = []
        for it, img in zip(items[s : s + 16], imgs[s : s + 16], strict=True):
            extra = [pl.decode_jpeg(c.jpeg) for c in it["_crops"]]
            t = it["t_ms"] // 1000
            reads = ",".join(map(str, it["reads"]))
            text = f"{it['side']} {it['team']} {t // 60}:{t % 60:02d} r{reads}"
            entries.append((it["id"], [img, *(Image.fromarray(e) for e in extra)], text))
        sheet(entries, folder / f"sheet-{s // 16:03d}.jpg", 4, (360, 262))
    for it in items:
        it.pop("_crops")
    (folder / "samples.json").write_text(json.dumps(items))
    print(f"{len(items)} samples on {-(-len(items) // 16)} sheets in {folder}")


def labels(out: Path) -> dict[int, dict[str, Any]]:
    folder = out / "labels"
    samples = {s["id"]: s for s in json.loads((folder / "samples.json").read_text())}
    for name in ("labels.txt", "labels2.txt"):
        path = folder / name
        if path.exists():
            for line in path.read_text().splitlines():
                if line.strip():
                    k, lab = line.split()
                    samples[int(k)]["label"] = lab
    return samples


def tellable(lab: str) -> bool:
    return not lab.endswith("?") and lab not in ("x", "usL", "orL")


def cmd_again(out: Path, video: Path, reads: str) -> None:
    cached = load(out)
    found = link(out, cached, reads)
    members = pieces_by_track(found)
    todo = [
        s
        for s in labels(out).values()
        if not tellable(s.get("label", "?")) and s.get("label") != "x"
    ]
    with ThreadPoolExecutor(max_workers=8) as pool:
        imgs = list(pool.map(lambda s: grab(video, s["t_ms"], tuple(s["box"]), 370), todo))
    folder = out / "labels2"
    folder.mkdir(exist_ok=True)
    for k in range(0, len(todo), 8):
        entries = []
        for s, img in zip(todo[k : k + 8], imgs[k : k + 8], strict=True):
            m = next(
                (m for m in members.get(s["track"], []) if m.first <= s["frame"] <= m.last), None
            )
            crops = []
            if m is not None:
                near = sorted(m.crops, key=lambda c: abs(c.frame - s["frame"]))[:3]
                crops = (
                    near
                    + [c for c in sorted(m.crops, key=lambda c: -c.quality) if c not in near][:3]
                )
            pics = [img, *(Image.fromarray(pl.decode_jpeg(c.jpeg)) for c in crops)]
            entries.append((s["id"], pics, f"{s['side']} {s['team']} {s.get('label')}"))
        sheet(entries, folder / f"sheet-{k // 8:03d}.jpg", 2, (720, 300))
    print(f"{len(todo)} samples to look at again in {folder}; overrides in labels/labels2.txt")


# --- the queue, simulated ---------------------------------------------------------------------


def cmd_score(out: Path, reads: str, old: bool = False) -> None:
    cached = load(out)
    found = link(out, cached, reads)
    samples = labels(out)
    numbers_only = any(s.get("label", "").startswith("n") for s in samples.values())
    times = cached["col"].times
    by = owners(found)

    def lab(k: int) -> str:
        v = samples[k].get("label", "?")
        return v if v == "x" or tellable(v) else "?"

    def name(team: str, number: int | None) -> str | None:
        return None if number is None else (f"n{number}" if numbers_only else f"{team}{number}")

    on_card: dict[int, list[str]] = defaultdict(list)
    on_piece: dict[int, list[str]] = defaultdict(list)
    for k, s in samples.items():
        loc = locate(by, s["frame"], s["track"])
        if loc:
            on_card[loc[1]].append(lab(k))
            on_piece[loc[0]].append(lab(k))
    cards = [c for c in found.clusters if not c.referee]
    total_samples = sum(1 for k in samples if lab(k) != "x")

    def spans(c: pl.Cluster) -> list[tuple[int, int]]:
        return sorted(
            (int(times[m.first]), int(times[m.last])) for t in c.tracks for m in (t.members or [t])
        )

    def overlap(a: list[tuple[int, int]], b: list[tuple[int, int]]) -> bool:
        return any(s0 <= e1 and s1 <= e0 for s0, e0 in a for s1, e1 in b)

    def main(ls: list[str]) -> str | None:
        known = Counter(x for x in ls if x != "?")
        return known.most_common(1)[0][0] if known else None

    # the state: named[card key] = label; named pieces of pieces cards
    named: dict[int, str] = {}
    pieces_named: list[tuple[str, list[str]]] = []
    for c in cards:
        if c.confidence >= identities.UNCERTAIN and (n := name(c.team, c.number)):
            named[c.key] = n

    # Players v2 (`old`): its cards to check were several people as a rule, named piece-wise
    # with the same "several people" answer.
    def as_pieces(c: pl.Cluster) -> bool:
        return card_kind(c) == PIECES or (old and c.confidence < identities.UNCERTAIN)

    groups = {
        c.key: [(t.track_id, t.detections, list(on_piece.get(t.track_id, []))) for t in c.tracks]
        for c in cards
        if as_pieces(c)
    }
    persons = [c for c in cards if not as_pieces(c)]

    def share() -> tuple[float, float]:
        ok = bad = 0
        for c in persons:
            if (n := named.get(c.key)) and n not in ("?", "x"):
                ok += sum(1 for s in on_card.get(c.key, []) if s == n)
                bad += sum(1 for s in on_card.get(c.key, []) if s not in (n, "?"))
        for n, ss in pieces_named:
            if n not in ("?", "x"):
                ok += sum(1 for s in ss if s == n)
                bad += sum(1 for s in ss if s not in (n, "?"))
        return ok / max(total_samples, 1), bad / max(total_samples, 1)

    curve = [(0, *share())]
    answers = 0
    while answers < 1000:
        cands: list[tuple[float, str, int]] = []
        for c in persons:
            if c.key not in named:
                cands.append((float(c.detections), "card", c.key))
        for key, ps in groups.items():
            if ps:
                cands.append(
                    (
                        0.5 * sum(n for _, n, _ in sorted(ps, key=lambda p: -p[1])[:SHOWN]),
                        "pieces",
                        key,
                    )
                )
        if not cands:
            break
        _, kind, key = max(cands)
        answers += 1
        if kind == "card":
            c = next(c for c in persons if c.key == key)
            n = main(on_card.get(key, []))
            named[key] = n or "?"
            if n is not None:  # relink: unsure cards reading the named number
                for o in persons:
                    if (
                        o.key not in named
                        and name(o.team, o.number) == n
                        and o.number_conf >= 0.5
                        and not overlap(spans(o), spans(c))
                    ):
                        named[o.key] = n
        else:
            ps = sorted(groups[key], key=lambda p: -p[1])
            view, rest = ps[:SHOWN], ps[SHOWN:]
            n = main([s for _, _, ss in view for s in ss])
            if n is None:
                groups[key] = []
            else:
                moved = [s for _, _, ss in view if main(ss) == n for s in ss]
                pieces_named.append((n, moved))
                groups[key] = [p for p in view if main(p[2]) != n and p[2]] + rest
        curve.append((answers, *share()))

    def answers_to(x: float) -> int | None:
        return next((a for a, s, _ in curve if s >= x), None)

    def purity(cs: list[pl.Cluster]) -> str:
        m = n = 0
        for c in cs:
            t = [x for x in on_card.get(c.key, []) if x != "?"]
            if t:
                m += Counter(t).most_common(1)[0][1]
                n += len(t)
        return f"{m}/{n}"

    ordered = sorted(cards, key=lambda c: -c.detections)
    sure = [c for c in cards if c.confidence >= identities.UNCERTAIN]
    footage = sum(c.detections for c in cards)
    numbered = [c for c in cards if card_kind(c) != PIECES and c.number is not None]
    numbered_ok = sum(
        1 for c in numbered for s in on_card.get(c.key, []) if s == name(c.team, c.number)
    )
    numbered_n = sum(1 for c in numbered for s in on_card.get(c.key, []) if s != "?")
    print(
        json.dumps(
            {
                "cards": len(cards),
                "us": sum(c.team == "us" for c in cards),
                "them": sum(c.team == "them" for c in cards),
                "pieces_cards": sum(card_kind(c) == PIECES for c in cards),
                "sure_us": sum(c.team == "us" for c in sure),
                "sure_them": sum(c.team == "them" for c in sure),
                "footage_on_sure": round(sum(c.detections for c in sure) / max(footage, 1), 3),
                "numbered_cards_right": f"{numbered_ok}/{numbered_n}",
                "purity_10_biggest_person_cards": purity(
                    [c for c in ordered if card_kind(c) != PIECES][:10]
                ),
                "purity_10_biggest_cards_to_check": purity(
                    [c for c in ordered if c.confidence < identities.UNCERTAIN][:10]
                ),
                "purity_10_biggest_pieces_cards": purity(
                    [c for c in ordered if card_kind(c) == PIECES][:10]
                ),
                "tellable": round(
                    sum(1 for k in samples if lab(k) not in ("x", "?")) / max(total_samples, 1), 3
                ),
                "named_at_start": round(curve[0][1], 3),
                "wrong_at_start": round(curve[0][2], 3),
                "answers_to": {x: answers_to(x) for x in (0.5, 0.6, 0.7, 0.75, 0.9)},
                "named_at_most": round(curve[-1][1], 3),
                "answers": answers,
            }
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("out", type=Path)
    s.add_argument("video", type=Path)
    s.add_argument("--n", type=int, default=320)
    s.add_argument("--seed", type=int, default=11)
    a = sub.add_parser("again")
    a.add_argument("out", type=Path)
    a.add_argument("video", type=Path)
    c = sub.add_parser("score")
    c.add_argument("out", type=Path)
    c.add_argument(
        "--old", action="store_true", help="players v2: its cards to check answered piece-wise"
    )
    for p in (s, a, c):
        p.add_argument("--reads", default="reads.json", help="the readings' cache in OUT_DIR")
    args = parser.parse_args()
    if args.cmd == "sample":
        cmd_sample(args.out, args.video, args.n, args.seed, args.reads)
    elif args.cmd == "again":
        cmd_again(args.out, args.video, args.reads)
    else:
        cmd_score(args.out, args.reads, args.old)


if __name__ == "__main__":
    main()
