"""Run the players job's stages on a local video, for measuring and tuning (PLAN §10.5).

    python scripts/ml/players_eval.py detect   VIDEO INPUTS.json OUT_DIR [--gap 40] [--gpu]
    python scripts/ml/players_eval.py identify OUT_DIR [--gpu]

`detect` runs detection and tracking as the job does (the same functions) and keeps the
collected tracks in OUT_DIR/collector.pkl; `identify` links them into people from that cache
(numbers, chains, clusters) and writes OUT_DIR/summary.json and OUT_DIR/clusters.json (with
`--images` also the contact sheets and a picture per card for checking by eye), so the
linking can be changed and rerun in a minute. INPUTS.json is what the
API hands the job (rallies with their sides, calibration, setups, roster numbers) plus the
video's duration and size, e.g. from the API container:

    from wieplay.core import players
    {"rallies": players.rallies_with_sides(db, v), "calibration": players.calibration_for(db, v),
     "setups": players.setups_for(db, v), "roster": {"us": [...]},
     "duration_ms": v.duration_ms, "width": v.width, "height": v.height}

OUT_DIR holds crops of people (club members): keep it on the machine that runs it and delete
it after the measurement (PLAN §5.5). Run from backend/ with its venv (`uv run python
../scripts/ml/players_eval.py ...`), the weights in WIEPLAY_WORKER_PLAYERS_WEIGHTS.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import pickle
import sys
import threading
import time
from pathlib import Path
from typing import Any

from wieplay.ml import people
from wieplay.ml import players as pl
from wieplay.worker import players as job
from wieplay.worker.config import WorkerSettings

CUDA = ["CUDAExecutionProvider", "CPUExecutionProvider"]


class _Api:
    def __init__(self, inputs: dict[str, Any]) -> None:
        self._inputs = inputs

    def inputs(self, _job: str) -> dict[str, Any]:
        return self._inputs


class _Job:
    id = "local-eval"

    def __init__(self, params: dict[str, Any]) -> None:
        self.params = params


class _Ctx:
    """What the job's functions read from a worker context."""

    def __init__(self, settings: WorkerSettings, inputs: dict[str, Any], scratch: Path) -> None:
        self.settings = settings
        self.api = _Api(inputs)
        self.job = _Job({})
        self.cancel = threading.Event()
        self.gpu = None
        self.scratch = scratch
        self._last = 0.0

    def report(self, progress: float, message: str) -> None:
        if time.monotonic() - self._last > 30:
            print(f"  {progress:5.1%} {message}", file=sys.stderr, flush=True)
            self._last = time.monotonic()


def _settings(gpu: bool, gap_s: float) -> WorkerSettings:
    os.environ.setdefault("WIEPLAY_WORKER_API_URL", "http://unused")
    return WorkerSettings(
        players_providers=CUDA if gpu else [],
        players_join_gap_s=gap_s,
        analysis_threads=int(os.environ.get("THREADS", "8")),
    )


def detect(
    video: Path,
    inputs_path: Path,
    out: Path,
    gap_s: float,
    gpu: bool,
    imgsz: int = job.IMGSZ,
    high: float | None = None,
) -> None:
    inputs = json.loads(inputs_path.read_text())
    out.mkdir(parents=True, exist_ok=True)
    settings = _settings(gpu, gap_s)
    ctx = _Ctx(settings, inputs, out)
    windows = job.windows_from(inputs["rallies"], int(inputs["duration_ms"]))
    spans = job.spans_of(windows, round(gap_s * 1000))
    width, height = int(inputs["width"]), int(inputs["height"])
    setups = pl.Setups(inputs.get("setups") or [], inputs.get("calibration"), width / height)
    weights = settings.players_weights
    detector = people.PersonDetector(
        weights / people.DETECTOR_FILE if weights else None,
        imgsz=imgsz,
        threads=settings.analysis_threads,
        providers=settings.players_providers or None,
        gpu_mem_limit_mib=settings.gpu_mem_limit_mib,
    )
    source = job.NasSource(
        [{"path": str(video), "offset_ms": 0, "duration_ms": int(inputs["duration_ms"])}]
    )
    started = time.perf_counter()
    dump: list[Any] = []
    found = job.detect_and_track(
        ctx,  # type: ignore[arg-type]
        source,
        windows,
        spans,
        setups,
        (width, height),
        detector,
        job.DETECT_FPS,
        job.tracker_params(job.DETECT_FPS, high),
        dump,
    )
    wall = time.perf_counter() - started
    with (out / "collector.pkl").open("wb") as handle:
        pickle.dump(
            {"col": found.col, "windows": windows, "spans": spans, "inputs": inputs}, handle
        )
    with (out / "detections.pkl").open("wb") as handle:
        pickle.dump(dump, handle)  # every box before tracking, to compare trackers offline
    timing = {**found.timing, "wall_s": round(wall, 1), "providers": detector.providers}
    (out / "detect.json").write_text(json.dumps(timing, indent=2))
    print(json.dumps(timing))


def identify(out: Path, gpu: bool, images: bool = False) -> None:
    with (out / "collector.pkl").open("rb") as handle:
        cached = pickle.load(handle)  # noqa: S301 - our own cache
    settings = _settings(gpu, 0)
    weights = settings.players_weights
    reader = people.NumberReader(
        weights / people.OCR_DET_FILE if weights else None,
        weights / people.OCR_REC_FILE if weights else None,
    )
    inputs = cached["inputs"]
    roster = {"us": [int(n) for n in (inputs.get("roster") or {}).get("us", [])]}
    started = time.perf_counter()
    setups = pl.Setups(
        inputs.get("setups") or [],
        inputs.get("calibration"),
        int(inputs["width"]) / int(inputs["height"]),
    )
    found = job.identify(
        cached["col"],
        cached["windows"],
        reader,
        roster,
        job.DETECT_FPS,
        calibrated=setups.calibrated,
    )
    found.stats["identify_s"] = round(time.perf_counter() - started, 1)
    if images:  # crops of people: only when asked, and deleted after looking
        sheets = out / "sheets"
        sheets.mkdir(exist_ok=True)
        for old in sheets.glob("sheet-*.jpg"):
            old.unlink()
        for n, page in enumerate(found.sheets.pages):
            (sheets / f"sheet-{n}.jpg").write_bytes(page)
    fps = job.DETECT_FPS
    clusters = [
        {
            "key": c.key,
            "team": c.team,
            "referee": c.referee,
            "number": c.number,
            "number_conf": c.number_conf,
            "confidence": c.confidence,
            "seconds": round(c.detections / fps, 1),
            "chains": len(c.tracks),
            "pieces": sum(len(t.members or [t]) for t in c.tracks),
            "votes": {
                str(k): round(v, 2) for k, v in sorted(_votes(c).items(), key=lambda kv: -kv[1])[:4]
            },
            "crops": found.sheets.cluster_crops.get(c.key, []),
        }
        for c in found.clusters
    ]
    (out / "clusters.json").write_text(json.dumps(clusters, indent=1))
    if images:
        review(found, out / "review")
    (out / "summary.json").write_text(json.dumps(found.stats, indent=2))
    print(json.dumps(found.stats))


def review(found: Any, folder: Path, per_row: int = 12, sample: int = 36) -> None:
    """One picture per card: a crop of its pieces (tracks) in time order, with each piece's
    time, length and readings, for checking by eye. At most `sample` pieces, drawn with a
    chance in proportion to their length: the share of wrong pictures estimates the share of
    the card's time that shows someone else."""
    import random

    from PIL import Image, ImageDraw

    folder.mkdir(exist_ok=True)
    for old in folder.glob("card-*.jpg"):
        old.unlink()
    rng = random.Random(7)  # noqa: S311 - a reproducible sample, no secret
    for c in found.clusters:
        pieces = [m for t in c.tracks for m in (t.members or [t])]
        if len(pieces) > sample:
            weights = [m.detections for m in pieces]
            chosen: set[int] = set()
            while len(chosen) < sample:
                chosen.add(rng.choices(range(len(pieces)), weights)[0])
            pieces = [pieces[i] for i in chosen]
        pieces.sort(key=lambda m: m.first)
        cells = []
        for m in pieces:
            cell = Image.new("RGB", (100, 230), (24, 24, 24))
            if m.crops:
                img = Image.open(io.BytesIO(m.crops[0].jpeg)).convert("RGB")
                img.thumbnail((100, 190))
                cell.paste(img, (0, 0))
            reads = ",".join(str(n) for _, n, _ in m.reads[:5])
            draw = ImageDraw.Draw(cell)
            draw.text(
                (2, 192),
                f"{m.t0_ms // 60000}:{m.t0_ms // 1000 % 60:02d} {m.detections}",
                fill=(255, 255, 0),
            )
            draw.text((2, 208), f"#{m.number} {reads}"[:18], fill=(0, 255, 255))
            cells.append(cell)
        rows = max(1, -(-len(cells) // per_row))
        sheet = Image.new("RGB", (100 * per_row, 230 * rows + 20), (10, 10, 10))
        ImageDraw.Draw(sheet).text(
            (4, 4), f"card {c.key} {c.team} #{c.number} conf {c.confidence}", fill=(255, 255, 255)
        )
        for k, cell in enumerate(cells):
            sheet.paste(cell, ((k % per_row) * 100, 20 + (k // per_row) * 230))
        sheet.save(folder / f"card-{c.key:03d}.jpg", quality=80)


def _votes(c: pl.Cluster) -> dict[int, float]:
    votes: dict[int, float] = {}
    for t in c.tracks:
        for k, v in t.votes.items():
            votes[k] = votes.get(k, 0.0) + v
    return votes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="stage", required=True)
    d = sub.add_parser("detect")
    d.add_argument("video", type=Path)
    d.add_argument("inputs", type=Path)
    d.add_argument("out", type=Path)
    d.add_argument("--gap", type=float, default=40.0, help="join rallies closer than this (s)")
    d.add_argument("--gpu", action="store_true")
    d.add_argument("--imgsz", type=int, default=job.IMGSZ)
    d.add_argument("--high", type=float, default=None, help="tracker: a sure box's score")
    d.add_argument("--loose", action="store_true", help="a looser tracker (4 s, IoU 0.05)")
    i = sub.add_parser("identify")
    i.add_argument("out", type=Path)
    i.add_argument("--gpu", action="store_true")
    i.add_argument(
        "--without",
        action="append",
        default=[],
        choices=["absorb", "propagate", "long", "encounters"],
        help="leave a step of the linking out (to measure what it does)",
    )
    i.add_argument(
        "--images",
        action="store_true",
        help="also write the contact sheets and a picture per card (crops of people)",
    )
    args = parser.parse_args()
    if args.stage == "detect" and args.loose:
        job.TRACK_BUFFER_S, job.MATCH_COST, job.SECOND_COST, job.UNCONFIRMED_COST = (
            4,
            0.95,
            0.7,
            0.8,
        )
    if args.stage == "detect":
        detect(args.video, args.inputs, args.out, args.gap, args.gpu, args.imgsz, args.high)
    else:
        from wieplay.ml import identities

        if "absorb" in args.without:
            identities.ABSORB_GAP_MS = 0
        if "propagate" in args.without:
            identities.PROPAGATE_FRAMES = -1
        if "long" in args.without:
            identities.LONG.max_gap_ms = 0
        if "encounters" in args.without:
            identities.ENCOUNTER_FRAMES = 10**9
        identify(args.out, args.gpu, args.images)


if __name__ == "__main__":
    main()
