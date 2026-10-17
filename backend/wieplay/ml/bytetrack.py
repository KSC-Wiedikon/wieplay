"""People from frame to frame: a ByteTrack-style tracker (PLAN §10.5).

ByteTrack (Zhang et al., ECCV 2022; reference code MIT) associates every detection box,
not only the confident ones: first the confident boxes with all tracks, then the weak boxes
(a player half hidden at the net) with the tracks still unmatched, each by IoU against the
track's Kalman prediction. A track that finds no box is kept for `buffer` frames and can be
picked up again; a new track starts only from a confident box.

Written here (numpy + scipy's assignment) instead of taken from Ultralytics or supervision:
Ultralytics' tracker needs PyTorch (not in the worker image), supervision brings matplotlib
and more, and the algorithm is a page. Boxes are pixels (x1, y1, x2, y2); a track's id is an
int, unique for the tracker's lifetime (the job keeps one tracker per video and resets it
per rally window, so ids stay unique per video).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
from scipy.optimize import linear_sum_assignment

Arr = npt.NDArray[Any]

# Kalman filter on (cx, cy, aspect w/h, height) and their velocities, ByteTrack's weights.
STD_POS = 1.0 / 20
STD_VEL = 1.0 / 160


class KalmanXYAH:
    """Constant-velocity Kalman filter for a box (cx, cy, a, h)."""

    def __init__(self) -> None:
        self.motion = np.eye(8)
        for i in range(4):
            self.motion[i, 4 + i] = 1.0
        self.update_mat = np.eye(4, 8)

    def initiate(self, z: Arr) -> tuple[Arr, Arr]:
        mean = np.r_[z, np.zeros(4)]
        h = z[3]
        std = [
            2 * STD_POS * h, 2 * STD_POS * h, 1e-2, 2 * STD_POS * h,
            10 * STD_VEL * h, 10 * STD_VEL * h, 1e-5, 10 * STD_VEL * h,
        ]  # fmt: skip
        return mean, np.diag(np.square(std))

    def predict(self, mean: Arr, cov: Arr) -> tuple[Arr, Arr]:
        h = mean[3]
        std = [
            STD_POS * h, STD_POS * h, 1e-2, STD_POS * h,
            STD_VEL * h, STD_VEL * h, 1e-5, STD_VEL * h,
        ]  # fmt: skip
        mean = self.motion @ mean
        cov = self.motion @ cov @ self.motion.T + np.diag(np.square(std))
        return mean, cov

    def update(self, mean: Arr, cov: Arr, z: Arr) -> tuple[Arr, Arr]:
        h = mean[3]
        std = [STD_POS * h, STD_POS * h, 1e-1, STD_POS * h]
        proj_mean = self.update_mat @ mean
        proj_cov = self.update_mat @ cov @ self.update_mat.T + np.diag(np.square(std))
        gain = np.linalg.solve(proj_cov, (cov @ self.update_mat.T).T).T
        mean = mean + gain @ (z - proj_mean)
        cov = cov - gain @ proj_cov @ gain.T
        return mean, cov


def to_xyah(box: Arr) -> Arr:
    w, h = box[2] - box[0], box[3] - box[1]
    return np.array([box[0] + w / 2, box[1] + h / 2, w / max(h, 1e-6), h], dtype=np.float64)


def to_xyxy(xyah: Arr) -> Arr:
    cx, cy, a, h = xyah[:4]
    w = a * h
    return np.array([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], dtype=np.float64)


def iou_matrix(a: Arr, b: Arr) -> Arr:
    """IoU of every box in a (N, 4) with every box in b (M, 4)."""
    if len(a) == 0 or len(b) == 0:
        return np.zeros((len(a), len(b)))
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    union = area_a[:, None] + area_b[None, :] - inter
    out: Arr = inter / np.maximum(union, 1e-9)
    return out


def match(cost: Arr, threshold: float) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Minimum-cost assignment; pairs costing more than `threshold` stay unmatched."""
    rows, cols = cost.shape
    if rows == 0 or cols == 0:
        return [], list(range(rows)), list(range(cols))
    r, c = linear_sum_assignment(cost)
    pairs = [(int(i), int(j)) for i, j in zip(r, c, strict=True) if cost[i, j] <= threshold]
    used_r = {i for i, _ in pairs}
    used_c = {j for _, j in pairs}
    return (
        pairs,
        [i for i in range(rows) if i not in used_r],
        [j for j in range(cols) if j not in used_c],
    )


@dataclass(eq=False)  # identity: two tracks are never "equal"
class _Track:
    track_id: int
    mean: Arr
    cov: Arr
    score: float
    start: int
    last: int
    hits: int = 1
    confirmed: bool = False

    def box(self) -> Arr:
        return to_xyxy(self.mean)


@dataclass
class TrackerParams:
    high: float = 0.5  # a confident box
    low: float = 0.15  # weaker boxes take part in the second round only
    new_track: float = 0.55  # a new track starts from a box at least this confident
    match_cost: float = 0.8  # 1 - IoU; first round
    second_cost: float = 0.5  # second round (weak boxes)
    unconfirmed_cost: float = 0.7
    buffer: int = 10  # frames a lost track waits to be found again (1 s at 10 fps)

    def as_dict(self) -> dict[str, float | int]:
        return dict(self.__dict__)


@dataclass
class Assignment:
    """The tracker's answer for one frame: per input box, its track id (-1: none)."""

    ids: Arr  # int64 (n,)


@dataclass
class ByteTracker:
    params: TrackerParams = field(default_factory=TrackerParams)
    next_id: int = 1
    _kf: KalmanXYAH = field(default_factory=KalmanXYAH)
    _active: list[_Track] = field(default_factory=list)
    _lost: list[_Track] = field(default_factory=list)
    _frame: int = 0

    def reset(self) -> None:
        """A new stretch of video (a rally window): no track continues across it."""
        self._active, self._lost = [], []

    def update(self, boxes: Arr, scores: Arr, frame: int | None = None) -> Assignment:
        """Associate this frame's boxes (n, 4) with the tracks; returns a track id per box
        (-1: a weak box no track took, or a box too unsure to start one)."""
        p = self.params
        self._frame = self._frame + 1 if frame is None else frame
        boxes = np.asarray(boxes, dtype=np.float64).reshape(-1, 4)
        scores = np.asarray(scores, dtype=np.float64).reshape(-1)
        ids = np.full(len(boxes), -1, dtype=np.int64)
        keep = scores >= p.low
        high_idx = np.flatnonzero(scores >= p.high)
        low_idx = np.flatnonzero(keep & (scores < p.high))

        for t in self._active + self._lost:
            t.mean, t.cov = self._kf.predict(t.mean, t.cov)
        confirmed = [t for t in self._active if t.confirmed]
        unconfirmed = [t for t in self._active if not t.confirmed]
        pool = confirmed + self._lost

        # Round 1: confident boxes with confirmed and lost tracks.
        pool_boxes = np.array([t.box() for t in pool]).reshape(-1, 4)
        cost = 1 - iou_matrix(pool_boxes, boxes[high_idx])
        pairs, rest_tracks, rest_high = match(cost, p.match_cost)
        matched: list[_Track] = []
        for ti, di in pairs:
            self._hit(pool[ti], boxes[high_idx[di]], scores[high_idx[di]])
            ids[high_idx[di]] = pool[ti].track_id
            matched.append(pool[ti])

        # Round 2: weak boxes with the confirmed tracks still unmatched.
        confirmed_ids = {id(t) for t in confirmed}
        left = [pool[i] for i in rest_tracks if id(pool[i]) in confirmed_ids]
        left_boxes = np.array([t.box() for t in left]).reshape(-1, 4)
        cost = 1 - iou_matrix(left_boxes, boxes[low_idx])
        pairs, _, _ = match(cost, p.second_cost)
        for ti, di in pairs:
            self._hit(left[ti], boxes[low_idx[di]], scores[low_idx[di]])
            ids[low_idx[di]] = left[ti].track_id
            matched.append(left[ti])

        # Round 3: tracks seen once with the confident boxes left over.
        rest_idx = high_idx[rest_high]
        un_boxes = np.array([t.box() for t in unconfirmed]).reshape(-1, 4)
        cost = 1 - iou_matrix(un_boxes, boxes[rest_idx])
        pairs, _, still = match(cost, p.unconfirmed_cost)
        confirmed_now: list[_Track] = []
        for ti, di in pairs:
            t = unconfirmed[ti]
            self._hit(t, boxes[rest_idx[di]], scores[rest_idx[di]])
            t.confirmed = True
            ids[rest_idx[di]] = t.track_id
            confirmed_now.append(t)

        # New tracks from confident boxes nobody took.
        born: list[_Track] = []
        for di in rest_idx[still]:
            if scores[di] < p.new_track:
                continue
            mean, cov = self._kf.initiate(to_xyah(boxes[di]))
            born.append(
                _Track(self.next_id, mean, cov, float(scores[di]), self._frame, self._frame)
            )
            ids[di] = self.next_id  # a track seen once only is dropped later (too short)
            self.next_id += 1

        alive = {id(t) for t in matched + confirmed_now}
        self._active = [*matched, *confirmed_now, *born]
        lost = [t for t in confirmed + self._lost if id(t) not in alive]
        self._lost = [t for t in lost if self._frame - t.last <= p.buffer]
        return Assignment(ids)

    def _hit(self, t: _Track, box: Arr, score: float) -> None:
        t.mean, t.cov = self._kf.update(t.mean, t.cov, to_xyah(box))
        t.score = float(score)
        t.last = self._frame
        t.hits += 1
        t.confirmed = True
