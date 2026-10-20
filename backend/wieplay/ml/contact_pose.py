"""The player at the ball at a contact candidate (PLAN §10.4a): a pose model on a crop around
the ball says whether someone's hands are there, which the action finder adds to the
candidate's chance of being a contact.

    candidate (frame, ball) ─▶ the video's frame, a 640 x 640 crop around the ball (full
    resolution) ─▶ YOLO26s-pose (people and 17 keypoints) ─▶ the person whose hands or body
    are nearest the ball ─▶ three numbers ─▶ log-likelihood ratio of "a contact"

- **Model**: Ultralytics YOLO26s-pose (COCO keypoints, AGPL-3.0 like WiePlay), exported to
  ONNX in the worker image's throw-away stage (scripts/ml/player_weights.py), run with ONNX
  Runtime on the CPU (~0.1 s a crop on the Lenovo). No faces: keypoints and boxes only, kept
  as three numbers per candidate, never an image.
- **Numbers**: `d`, the distance from the ball to the nearest wrist (or the box, plus a
  margin) in person heights; `wr_up`, how far that person's highest wrist is above the
  shoulders (person heights; arms up for sets, attacks, blocks and serves); `near`, how many
  people stand within a person height of the ball (a contact draws players).
- **Weights**: a logistic fit on hand-labelled rallies of two matches filmed from behind the
  end line (docs/research/actions-2026-10.md): contact or not, area under the ROC curve
  0.76-0.81 trained on one match and tested on the other, against 0.65-0.68 for the ball's
  path alone. From behind, the arms are often hidden and the ball of a reception shows at
  the receiver's shoulders, so the pose says little about the skill; it is used for "a
  contact or not" only.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from wieplay.ml.people import file_sha256, nms

Arr = npt.NDArray[Any]

POSE_FILE = "yolo26s-pose.onnx"
POSE_NAME = "yolo26s-pose-coco"
CROP = 640  # px of the video's frame around the ball (full resolution, the model's input)
BELOW = 150  # the crop's centre this far below the ball: the player is under it
CONF = 0.2
KPT_CONF = 0.3
WRISTS, SHOULDERS = (9, 10), (5, 6)
# logit P(contact) = A + B_D * d + B_UP * wr_up + B_NEAR * near; BASE: the share of contacts
# among the candidates it was fitted on (the ratio is against that).
A, B_D, B_UP, B_NEAR = 0.33, -1.38, 2.12, 0.31
BASE = 0.49
MAX_D, MAX_NEAR = 2.0, 4
NO_WRIST_UP = -0.2


@dataclass(frozen=True)
class AtBall:
    d: float
    wr_up: float
    near: int

    def as_list(self) -> list[float]:
        return [round(self.d, 4), round(self.wr_up, 4), int(self.near)]

    @classmethod
    def of(cls, data: Sequence[float]) -> AtBall:
        return cls(float(data[0]), float(data[1]), int(data[2]))

    def llr(self) -> float:
        """Log-likelihood ratio of "a contact" against the base rate."""
        logit = A + B_D * min(self.d, MAX_D) + B_UP * self.wr_up + B_NEAR * min(self.near, MAX_NEAR)
        return logit - math.log(BASE / (1 - BASE))


def crop_origin(x: float, y: float, width: int, height: int) -> tuple[int, int]:
    """The top left corner of the crop around a ball at normalised (x, y)."""
    cx, cy = x * width, y * height + BELOW * height / 1080
    x0 = int(min(max(cx - CROP / 2, 0), max(width - CROP, 0)))
    y0 = int(min(max(cy - CROP / 2, 0), max(height - CROP, 0)))
    return x0, y0


Person = tuple[Arr, float, Arr]  # box x1 y1 x2 y2 (frame pixels), score, keypoints (17, 3)


def at_ball(people: Sequence[Person], bx: float, by: float) -> AtBall | None:
    """The numbers of the person nearest the ball at frame pixel (bx, by); None without
    people (the cue says nothing then)."""
    best: tuple[float, Person] | None = None
    for person in people:
        box, _, k = person
        x1, y1, x2, y2 = (float(v) for v in box)
        h = max(y2 - y1, 1.0)
        wrists = [k[j] for j in WRISTS if k[j][2] >= KPT_CONF]
        dw = min((math.hypot(bx - p[0], by - p[1]) for p in wrists), default=math.inf)
        db = math.hypot(max(x1 - bx, 0.0, bx - x2), max(y1 - by, 0.0, by - y2))
        d = min(dw, db + 0.15 * h) / h
        if best is None or d < best[0]:
            best = (d, person)
    if best is None:
        return None
    d, (box, _, k) = best
    h = max(float(box[3] - box[1]), 1.0)
    shoulders = [float(k[j][1]) for j in SHOULDERS if k[j][2] >= KPT_CONF]
    wrists = [float(k[j][1]) for j in WRISTS if k[j][2] >= KPT_CONF]
    wr_up = (np.mean(shoulders) - min(wrists)) / h if shoulders and wrists else NO_WRIST_UP
    near = sum(
        1
        for b, _, _ in people
        if math.hypot((b[0] + b[2]) / 2 - bx, (b[1] + b[3]) / 2 - by) < max(b[3] - b[1], 1.0)
    )
    return AtBall(d=float(d), wr_up=float(wr_up), near=int(near))


class PoseModel:
    """YOLO26s-pose with ONNX Runtime (the classic head: (56, anchors), NMS here)."""

    def __init__(self, path: Path, threads: int = 4) -> None:
        import onnxruntime as ort

        if not path.is_file():
            raise FileNotFoundError(f"pose weights missing: {path}")
        self.path = path
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, threads)
        opts.inter_op_num_threads = 1
        opts.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self._session = ort.InferenceSession(
            str(path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._input = self._session.get_inputs()[0].name

    @property
    def sha256(self) -> str:
        return file_sha256(self.path)

    def people(self, crop: Arr) -> list[Person]:
        """People in an RGB uint8 crop whose sides are multiples of 32 (crop pixels)."""
        x = crop.transpose(2, 0, 1)[None].astype(np.float32) * np.float32(1 / 255)
        out = self._session.run(None, {self._input: np.ascontiguousarray(x)})[0][0]
        return decode(out)


def decode(out: Arr, conf: float = CONF) -> list[Person]:
    """A YOLO pose output for one image (56, anchors: cx cy w h score, 17 x (x y conf))."""
    keep = out[4] >= conf
    rows = out[:, keep].T
    if not len(rows):
        return []
    boxes = np.stack(
        [rows[:, 0] - rows[:, 2] / 2, rows[:, 1] - rows[:, 3] / 2,
         rows[:, 0] + rows[:, 2] / 2, rows[:, 1] + rows[:, 3] / 2], axis=1,
    )  # fmt: skip
    return [
        (boxes[i], float(rows[i, 4]), rows[i, 5:].reshape(17, 3)) for i in nms(boxes, rows[:, 4])
    ]


def in_frame(people: Sequence[Person], x0: int, y0: int) -> list[Person]:
    """Crop pixels → frame pixels."""
    out = []
    for box, score, k in people:
        k = k.copy()
        k[:, 0] += x0
        k[:, 1] += y0
        out.append((box + np.array([x0, y0, x0, y0], dtype=box.dtype), score, k))
    return out
