"""Spike S0: compare the ball trackers without ground truth, and cut crops to check by eye.

    python s0_report.py OUT_DIR CLIPS_DIR [--crops N]

Per clip and model: share of frames with a detection; "stuck" detections (the same spot for
most of 4 s: lamps, lines, a ball in a cart); detections that are part of a plausible track
(>= 8 linked frames at 30 fps, < 120 px per frame); agreement between models (both see a ball
within 20 px). With --crops, a sheet per clip of N random detections per model, each a
120x120 window of the source frame around the detection (the ring marks the spot), for a
person to count real balls: that gives each model's precision.
"""

import argparse
import csv
import json
import random
from pathlib import Path

import cv2
import numpy as np

MODELS = ["vball", "vballfast", "wasb", "tracknet"]
SHORT = {"vball": "vbV1", "vballfast": "vbFast", "wasb": "wasb", "tracknet": "tnet"}


def load(path: Path) -> dict[int, tuple[float, float]]:
    out = {}
    with open(path) as f:
        for r in csv.DictReader(f):
            if r["vis"] == "1":
                out[int(r["frame"])] = (float(r["x"]), float(r["y"]))
    return out


def frames_of(path: Path) -> list[int]:
    with open(path) as f:
        return [int(r["frame"]) for r in csv.DictReader(f)]


def stuck(det: dict[int, tuple[float, float]], step: int) -> set[int]:
    keys = sorted(det)
    pts = np.array([det[k] for k in keys]) if keys else np.zeros((0, 2))
    idx = np.array(keys)
    out = set()
    span = 60 * step  # +-2 s
    for i, k in enumerate(keys):
        lo, hi = np.searchsorted(idx, k - span), np.searchsorted(idx, k + span)
        near = np.hypot(*(pts[lo:hi] - pts[i]).T) < 6
        if near.sum() > 45:
            out.add(k)
    return out


def tracked(det: dict[int, tuple[float, float]], step: int, bad: set[int]) -> set[int]:
    keys = sorted(k for k in det if k not in bad)
    out, run = set(), []
    for k in keys:
        if (
            run
            and k - run[-1] <= 2 * step
            and np.hypot(*np.subtract(det[k], det[run[-1]])) < 120 * (k - run[-1]) / step
        ):
            run.append(k)
        else:
            if len(run) >= 8:
                out.update(run)
            run = [k]
    if len(run) >= 8:
        out.update(run)
    return out


def crops(
    clip: Path, picks: list[tuple[str, int, float, float]], dest: Path, per_row: int = 10
) -> None:
    cap = cv2.VideoCapture(str(clip))
    tiles = []
    for model, frame, x, y in picks:
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame)
        ok, img = cap.read()
        tile = np.zeros((120, 120, 3), np.uint8)
        if ok:
            pad = cv2.copyMakeBorder(img, 60, 60, 60, 60, cv2.BORDER_CONSTANT)
            tile = pad[int(y) : int(y) + 120, int(x) : int(x) + 120].copy()
            cv2.circle(tile, (60, 60), 16, (0, 255, 255), 1)
        tile = cv2.resize(tile, (180, 180), interpolation=cv2.INTER_NEAREST)
        cv2.putText(
            tile,
            f"{SHORT[model]} {len(tiles) % per_row}",
            (4, 14),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 255),
            1,
        )
        tiles.append(tile)
    while len(tiles) % per_row:
        tiles.append(np.zeros_like(tiles[0]))
    rows = [np.hstack(tiles[i : i + per_row]) for i in range(0, len(tiles), per_row)]
    cv2.imwrite(str(dest), np.vstack(rows), [cv2.IMWRITE_JPEG_QUALITY, 90])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("clips")
    ap.add_argument("--step", type=int, default=2)
    ap.add_argument("--crops", type=int, default=0)
    ap.add_argument("--models", default="vballfast,wasb,tracknet")
    a = ap.parse_args()
    out, rnd = Path(a.out), random.Random(16)
    report = []
    for clip in sorted({p.name.split(".")[0] for p in out.glob(f"*.s{a.step}.csv")}):
        det, good = {}, {}
        n = 0
        for m in MODELS:
            p = out / f"{clip}.{m}.s{a.step}.csv"
            if not p.exists():
                continue
            n = len(frames_of(p))
            det[m] = load(p)
            bad = stuck(det[m], a.step)
            good[m] = tracked(det[m], a.step, bad)
            report.append(
                {
                    "clip": clip,
                    "model": m,
                    "frames": n,
                    "detected": round(len(det[m]) / n, 3),
                    "stuck": round(len(bad) / n, 3),
                    "in_track": round(len(good[m]) / n, 3),
                }
            )
        names = list(det)
        for i, m1 in enumerate(names):
            for m2 in names[i + 1 :]:
                both = set(det[m1]) & set(det[m2])
                agree = sum(np.hypot(*np.subtract(det[m1][k], det[m2][k])) < 20 for k in both)
                report.append(
                    {
                        "clip": clip,
                        "pair": f"{m1}+{m2}",
                        "both": round(len(both) / n, 3),
                        "agree": round(agree / n, 3),
                    }
                )
        if a.crops:
            picks = []
            for m in a.models.split(","):
                if m in det and det[m]:
                    for k in rnd.sample(sorted(det[m]), min(a.crops, len(det[m]))):
                        picks.append((m, k, *det[m][k]))
            crops(Path(a.clips) / f"{clip}.mp4", picks, out / f"crops-{clip}.jpg")
    for r in report:
        print(json.dumps(r))


if __name__ == "__main__":
    main()
