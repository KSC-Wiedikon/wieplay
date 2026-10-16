"""The ball in each frame: VballNet-Fast (MIT, TrackNetV4-based, ONNX) on the CPU.

Spike S0 (docs/research/spike-s0-2026-10.md): the best of the off-the-shelf trackers on our
footage, ~125 frames/s on the Lenovo's CPU at 512x288. It takes three consecutive frames
(30 fps) and returns one heatmap per frame; the ball is the heatmap's peak when it is at
least 0.5. Weights: nomal/ml/weights/ (README there: source, licence, SHA-256).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from nomal.ml.features import HEIGHT, WIDTH, Arr

WEIGHTS = Path(__file__).resolve().parent / "weights" / "VballNetFastV1_155_h288_w512.onnx"
WEIGHTS_SHA256 = "6985d90318dd309b295b0bfa7ba88169069cd81721c0c16a214466871025a0da"
MODEL_NAME = "VballNetFastV1_155"
GROUP = 3
THRESHOLD = 0.5
# The reference code takes the centroid of the largest blob above the threshold; a ball is a
# few pixels wide at 512x288, so the thresholded pixels around the peak give the same point.
WINDOW = 8


@dataclass
class Detections:
    vis: Arr  # uint8
    x: Arr  # float32, -1 when not visible
    y: Arr
    score: Arr  # float32


def weights_sha256(path: Path = WEIGHTS) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class BallTracker:
    def __init__(self, threads: int = 4, weights: Path = WEIGHTS) -> None:
        import onnxruntime as ort

        if weights == WEIGHTS and weights_sha256(weights) != WEIGHTS_SHA256:
            raise RuntimeError(f"{weights.name}: unexpected SHA-256")
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, threads)
        opts.inter_op_num_threads = 1
        self._session: Any = ort.InferenceSession(
            str(weights), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._input = self._session.get_inputs()[0].name

    def heatmaps(self, groups: Arr) -> Arr:
        """(B, 3, H, W, 3) uint8 RGB → (B, 3, H, W) float32 in 0..1."""
        x = groups.astype(np.float32) * np.float32(1 / 255)
        x = x.transpose(0, 1, 4, 2, 3).reshape(len(groups), GROUP * 3, HEIGHT, WIDTH)
        out: Arr = self._session.run(None, {self._input: x})[0]
        return out

    def detect(self, frames: Arr) -> Detections:
        """Frames (N, H, W, 3) uint8 RGB, consecutive at the analysis rate. A last group
        shorter than three frames is padded with its last frame."""
        n = len(frames)
        pad = (-n) % GROUP
        if pad:
            frames = np.concatenate([frames, np.repeat(frames[-1:], pad, axis=0)])
        groups = frames.reshape(-1, GROUP, HEIGHT, WIDTH, 3)
        hms = self.heatmaps(groups).reshape(-1, HEIGHT, WIDTH)[:n]
        return peaks(hms)


def peaks(hms: Arr) -> Detections:
    n = len(hms)
    flat = hms.reshape(n, -1)
    at = flat.argmax(1)
    score = flat[np.arange(n), at].astype(np.float32)
    vis = (score >= THRESHOLD).astype(np.uint8)
    x = np.full(n, -1.0, dtype=np.float32)
    y = np.full(n, -1.0, dtype=np.float32)
    for i in np.flatnonzero(vis):
        r, c = divmod(int(at[i]), WIDTH)
        r0, c0 = max(0, r - WINDOW), max(0, c - WINDOW)
        win = hms[i, r0 : r + WINDOW + 1, c0 : c + WINDOW + 1] >= THRESHOLD
        rows, cols = np.nonzero(win)
        y[i] = r0 + rows.mean()
        x[i] = c0 + cols.mean()
    return Detections(vis=vis, x=x, y=y, score=score)
