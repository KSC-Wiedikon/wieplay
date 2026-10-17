"""Players of one video (PLAN §10.5, §5.7): from boxes per frame to "players in this video".

    rally windows ──decode at 10 fps, court ROI──▶ YOLO person boxes (wieplay/ml/people.py)
        ──ByteTrack (wieplay/ml/bytetrack.py)──▶ tracks, masked to the court
        ──side of the net, team per rally──▶ ours / theirs
        ──appearance (colour, not faces) + time + shirt numbers──▶ clusters ≈ players
        ──▶ a contact sheet of back crops per cluster, a compact index for the API

Identity: a cluster is "a person in this video", nothing more. Which number (and so which
roster entry) it is, the coach says once in the gallery; the number reader only suggests.
Nothing here works across videos, and no faces are used: the appearance is the colour of
head (hair), shirt, shorts and legs regions, and the crops picked are of backs and shirts.

Coordinates: boxes are normalised to the full frame (0..1, x right, y down) like the ball
track and the court calibration (wieplay/core/court.py); court points in metres.
"""

from __future__ import annotations

import base64
import io
import math
import sys
import zlib
from array import array
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt

from wieplay.core import court

Arr = npt.NDArray[Any]

INDEX_SCHEMA = 1
NEAR, FAR = 0, 1
# The court mask (PLAN §5.7: detection only on the court and its surroundings, not the
# stands): side lines ± SIDE_MARGIN, end lines ± END_MARGIN (servers stand back there).
SIDE_MARGIN_M = 1.75
END_MARGIN_M = 5.0
# A track that stays beside the court (most of its feet past a side line) is a referee, a
# line judge or the bench, not a player: players leave the court only to chase a ball.
OFF_SIDE_M = 0.3
OFF_COURT_SHARE = 0.7
# Without a calibration: the floor below this share of the frame's height.
FALLBACK_TOP = 0.3
# Head room above the far end line in the ROI (a jumping blocker's hands): with the camera
# known, points at this height; else this share of the frame above the court's top.
HEAD_ROOM_M = 2.8
HEAD_ROOM_SHARE = 0.15

MIN_TRACK_DETECTIONS = 4  # 0.4 s at 10 fps
MIN_CROP_PX = 40  # box height for a crop or an appearance sample
OCCLUDED_IOU = 0.15  # a box overlapping another this much is no clean crop
CROPS_PER_TRACK = 4
DESCRIPTOR_EVERY = 3  # every third detection of a track feeds its appearance
CELL_W, CELL_H = 80, 160  # contact sheet cells
SHEET_COLS, SHEET_ROWS = 10, 8
CLUSTER_CROPS = 4

# Shirt numbers: a track's number counts when its votes reach this (sum of recogniser
# confidences) and make most of its votes.
NUMBER_MIN_VOTES = 1.2
NUMBER_SHARE = 0.6
# Clustering: average-linkage merges stop above this appearance distance (Hellinger,
# 0..2 over the four regions).
MERGE_DISTANCE = 0.55


# --- geometry -------------------------------------------------------------------------------


@dataclass
class Geometry:
    """How a point in the frame relates to the court for one video."""

    roi: tuple[float, float, float, float]  # normalised x0, y0, x1, y1 to decode
    to_court: court.Mat3 | None = None  # image (normalised) → court metres, floor
    camera: court.Camera | None = None
    split_v: float | None = None  # no calibration: foot v below this is the far side

    @property
    def calibrated(self) -> bool:
        return self.to_court is not None

    def foot(self, u: float, v: float) -> tuple[float, float] | None:
        if self.to_court is None:
            return None
        try:
            return court.apply(self.to_court, (u, v))
        except court.CourtError:
            return None

    def inside(self, point: tuple[float, float] | None) -> bool:
        if self.to_court is None:
            return True
        if point is None:
            return False
        x, y = point
        return (
            -SIDE_MARGIN_M <= x <= court.WIDTH_M + SIDE_MARGIN_M
            and -END_MARGIN_M <= y <= court.LENGTH_M + END_MARGIN_M
        )

    def side(self, point: tuple[float, float] | None, v: float) -> int:
        if point is not None:
            return NEAR if point[1] < court.NET_Y else FAR
        split = self.split_v if self.split_v is not None else 0.6
        return FAR if v < split else NEAR

    def as_dict(self) -> dict[str, Any]:
        return {
            "roi": [round(r, 4) for r in self.roi],
            "calibrated": self.calibrated,
            "camera": self.camera is not None,
            "split_v": round(self.split_v, 4) if self.split_v is not None else None,
        }


def geometry(calibration: dict[str, Any] | None, aspect: float) -> Geometry:
    """The ROI and the court mapping from a calibration (the API's `homography` image →
    court and `camera`), or the floor area below the stands without one."""
    if not calibration:
        return Geometry(roi=(0.0, FALLBACK_TOP, 1.0, 1.0))
    to_court = court.from_lists(calibration["homography"])
    to_image = court.invert(to_court)
    cam = None
    if calibration.get("camera"):
        c = calibration["camera"]
        cam = court.Camera(
            center=(float(c["center"][0]), float(c["center"][1]), float(c["center"][2])),
            focal=float(c["focal"]),
            rays=court.from_lists(c["rays"]),
            aspect=float(c.get("aspect") or aspect),
        )
    # The mask's area, sampled every metre (points behind the camera skipped: the camera
    # often stands inside the margin behind the near end line), clipped to the frame.
    xs = np.linspace(-SIDE_MARGIN_M, court.WIDTH_M + SIDE_MARGIN_M, 14)
    ys = np.linspace(-END_MARGIN_M, court.LENGTH_M + END_MARGIN_M, 29)
    pts: list[tuple[float, float]] = []
    for x in xs:
        for y in ys:
            for z in (0.0, HEAD_ROOM_M) if cam is not None else (0.0,):
                p = (
                    cam.project(float(x), float(y), z)
                    if cam is not None
                    else _project(to_image, float(x), float(y))
                )
                if p is not None and -0.5 <= p[0] <= 1.5 and -0.5 <= p[1] <= 1.5:
                    pts.append((min(max(p[0], 0.0), 1.0), min(max(p[1], 0.0), 1.0)))
    if not pts:
        return Geometry(roi=(0.0, FALLBACK_TOP, 1.0, 1.0), to_court=to_court, camera=cam)
    u0 = min(p[0] for p in pts)
    u1 = max(p[0] for p in pts)
    v0 = min(p[1] for p in pts)
    v1 = max(p[1] for p in pts)
    if cam is None:
        v0 -= HEAD_ROOM_SHARE
    pad = 0.02
    roi = (
        max(0.0, u0 - pad), max(0.0, v0 - pad), min(1.0, u1 + pad), min(1.0, v1 + pad)
    )  # fmt: skip
    if roi[2] - roi[0] < 0.2 or roi[3] - roi[1] < 0.2:
        roi = (0.0, FALLBACK_TOP, 1.0, 1.0)
    return Geometry(roi=roi, to_court=to_court, camera=cam)


class Setups:
    """The court geometry at a time of the video: the camera's setup then (a camera bumped
    during the match has several, PLAN §10.6); without setups, one geometry for all."""

    def __init__(
        self,
        setups: Sequence[dict[str, Any]],
        calibration: dict[str, Any] | None,
        aspect: float,
    ) -> None:
        self.setups = sorted(setups, key=lambda s: int(s["from_ms"]))
        self.aspect = aspect
        self._single = geometry(calibration, aspect) if not self.setups else None
        self._cache: dict[int, Geometry] = {}

    def at(self, t_ms: int) -> Geometry:
        if self._single is not None:
            return self._single
        seg = self.setups[0] if t_ms < int(self.setups[0]["from_ms"]) else self.setups[-1]
        for s in self.setups:
            if int(s["from_ms"]) <= t_ms and (s.get("to_ms") is None or t_ms < int(s["to_ms"])):
                seg = s
                break
        key = int(seg["from_ms"])
        if key not in self._cache:
            calib = (
                {"homography": seg["homography"], "camera": seg.get("camera")}
                if seg.get("homography")
                else None
            )
            self._cache[key] = geometry(calib, self.aspect)
        return self._cache[key]

    def as_dict(self) -> dict[str, Any]:
        if self._single is not None:
            return self._single.as_dict()
        return {
            "setups": [
                {"from_ms": int(s["from_ms"]), **self.at(int(s["from_ms"])).as_dict()}
                for s in self.setups
            ]
        }


def _project(to_image: court.Mat3, x: float, y: float) -> tuple[float, float] | None:
    w = to_image[2][0] * x + to_image[2][1] * y + to_image[2][2]
    if w <= 1e-9:
        return None
    return court.apply(to_image, (x, y))


def roi_pixels(
    roi: tuple[float, float, float, float], width: int, height: int
) -> tuple[int, int, int, int]:
    """The ROI as an even pixel crop (x, y, w, h) for ffmpeg."""
    x0 = int(roi[0] * width) // 2 * 2
    y0 = int(roi[1] * height) // 2 * 2
    x1 = min(width, -(-math.ceil(roi[2] * width) // 2) * 2)
    y1 = min(height, -(-math.ceil(roi[3] * height) // 2) * 2)
    return x0, y0, max(2, x1 - x0), max(2, y1 - y0)


def split_from_feet(vs: Sequence[float]) -> float | None:
    """No calibration: the foot height between the far and the near team (two means)."""
    if len(vs) < 20:
        return None
    v = np.sort(np.asarray(vs, dtype=np.float64))
    lo, hi = float(np.percentile(v, 25)), float(np.percentile(v, 75))
    for _ in range(20):
        mid = (lo + hi) / 2
        a, b = v[v < mid], v[v >= mid]
        if not len(a) or not len(b):
            break
        lo, hi = float(a.mean()), float(b.mean())
    return (lo + hi) / 2


# --- appearance ----------------------------------------------------------------------------

REGIONS = ((0.0, 0.15), (0.15, 0.5), (0.5, 0.7), (0.7, 1.0))  # head, shirt, shorts, legs
HUE_BINS = 8
GREYS = 3
BINS = HUE_BINS + GREYS


def _hsv(rgb: Arr) -> tuple[Arr, Arr, Arr]:
    x = rgb.astype(np.float32) / 255
    r, g, b = x[..., 0], x[..., 1], x[..., 2]
    mx = x.max(axis=-1)
    mn = x.min(axis=-1)
    d = mx - mn
    s = np.where(mx > 0, d / np.maximum(mx, 1e-6), 0)
    safe = np.maximum(d, 1e-6)
    h = np.where(
        mx == r, ((g - b) / safe) % 6, np.where(mx == g, (b - r) / safe + 2, (r - g) / safe + 4)
    )
    return h / 6, s, mx


def descriptor(crop: Arr) -> Arr:
    """Colour of a person's regions (head, shirt, shorts, legs): per region a histogram of
    hue for coloured pixels and three greys, square-rooted (Hellinger) and concatenated.
    The middle 60 % of the width only (less floor and neighbours)."""
    from wieplay.ml.people import resize

    w = crop.shape[1]
    x0, x1 = int(w * 0.2), max(int(w * 0.8), int(w * 0.2) + 1)
    small = resize(np.ascontiguousarray(crop[:, x0:x1]), 12, 48)
    hue, sat, val = _hsv(small)
    out = []
    for top, bottom in REGIONS:
        a, b = int(top * 48), max(int(bottom * 48), int(top * 48) + 1)
        hh, ss, vv = hue[a:b].ravel(), sat[a:b].ravel(), val[a:b].ravel()
        coloured = (ss > 0.25) & (vv > 0.2)
        hist = np.zeros(BINS, np.float32)
        np.add.at(hist, np.minimum((hh[coloured] * HUE_BINS).astype(int), HUE_BINS - 1), 1)
        grey = vv[~coloured]
        np.add.at(hist, HUE_BINS + np.minimum((grey * GREYS).astype(int), GREYS - 1), 1)
        hist /= max(float(hist.sum()), 1.0)
        out.append(np.sqrt(hist))
    return np.concatenate(out)


def distance(a: Arr, b: Arr) -> float:
    return float(np.linalg.norm(a - b))


def sharpness(grey: Arr) -> float:
    """Variance of the Laplacian: blurred (moving) crops score low."""
    g = grey.astype(np.float32)
    lap = (
        -4 * g[1:-1, 1:-1] + g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:]
    )  # fmt: skip
    return float(lap.var()) if lap.size else 0.0


# --- collecting while the frames stream by --------------------------------------------------


@dataclass
class Crop:
    quality: float
    frame: int
    jpeg: bytes  # the box at source resolution (capped), JPEG


@dataclass
class TrackData:
    track_id: int
    frames: list[int] = field(default_factory=list)
    sides: list[int] = field(default_factory=list)
    feet: list[tuple[float, float]] = field(default_factory=list)  # court metres
    foot_v: list[float] = field(default_factory=list)
    heights_px: list[float] = field(default_factory=list)
    descriptors: list[Arr] = field(default_factory=list)
    crops: list[Crop] = field(default_factory=list)
    window: int = -1
    votes: dict[int, float] = field(default_factory=dict)
    reads: int = 0


def encode_jpeg(img: Arr, quality: int = 90) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.fromarray(img).save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def decode_jpeg(data: bytes) -> Arr:
    from PIL import Image

    out: Arr = np.asarray(Image.open(io.BytesIO(data)).convert("RGB"))
    return out


class Collector:
    """Per detection: its row for the index; per track: feet, sides, appearance samples
    and its best crops. Fed one analysed frame at a time."""

    def __init__(
        self, geom: Geometry, frame_w: int, frame_h: int, roi_px: tuple[int, int, int, int]
    ) -> None:
        self.geom = geom
        self.frame_w, self.frame_h = frame_w, frame_h
        self.roi_px = roi_px
        self.times: list[int] = []  # ms of each analysed frame
        self.windows: list[int] = []  # the rally window of each analysed frame
        self.rows: list[tuple[int, int, float, float, float, float]] = []  # frame, track, box
        self.tracks: dict[int, TrackData] = {}
        self.detections = 0
        self.masked = 0
        # Foot heights seen without a calibration (for the split between the two teams).
        self.split_v: float | None = None

    def view(self, geom: Geometry, roi_px: tuple[int, int, int, int]) -> None:
        """The camera's setup for the next frames (a camera bumped during the match has
        several, PLAN §10.6): its court mapping and the ROI the frames are cropped to."""
        self.geom, self.roi_px = geom, roi_px

    def add(self, t_ms: int, window: int, roi_img: Arr, boxes: Arr, scores: Arr, ids: Arr) -> int:
        """One analysed frame: boxes in ROI pixels with their track ids. Returns the frame
        index."""
        frame = len(self.times)
        self.times.append(int(t_ms))
        self.windows.append(window)
        rx, ry, _, _ = self.roi_px
        fw, fh = self.frame_w, self.frame_h
        n = len(boxes)
        self.detections += n
        if n == 0:
            return frame
        from wieplay.ml.bytetrack import iou_matrix

        iou = iou_matrix(boxes, boxes)
        np.fill_diagonal(iou, 0)
        crowded = iou.max(axis=1) if n > 1 else np.zeros(n)
        for i in range(n):
            tid = int(ids[i])
            if tid < 0:
                continue
            x1, y1, x2, y2 = (float(v) for v in boxes[i])
            u1, v1 = (x1 + rx) / fw, (y1 + ry) / fh
            u2, v2 = (x2 + rx) / fw, (y2 + ry) / fh
            foot = self.geom.foot((u1 + u2) / 2, v2)
            if not self.geom.inside(foot):
                self.masked += 1
                continue
            tr = self.tracks.get(tid)
            if tr is None:
                tr = self.tracks[tid] = TrackData(tid, window=window)
            tr.frames.append(frame)
            # Uncalibrated: the side is decided at the end, from the feet (finish_tracks).
            tr.sides.append(self.geom.side(foot, v2) if self.geom.calibrated else -1)
            tr.foot_v.append(v2)
            if foot is not None:
                tr.feet.append(foot)
            h_px = y2 - y1
            tr.heights_px.append(h_px)
            self.rows.append((frame, tid, u1, v1, u2, v2))
            if h_px < MIN_CROP_PX or crowded[i] > OCCLUDED_IOU:
                continue
            take_desc = len(tr.frames) % DESCRIPTOR_EVERY == 1
            worst = min((c.quality for c in tr.crops), default=0.0)
            if not take_desc and len(tr.crops) >= CROPS_PER_TRACK and h_px < worst:
                continue
            crop = roi_img[int(y1) : math.ceil(y2), int(x1) : math.ceil(x2)]
            if crop.shape[0] < 8 or crop.shape[1] < 4:
                continue
            if take_desc:
                tr.descriptors.append(descriptor(crop))
            grey = crop[..., 1]
            quality = h_px * (1 + math.log1p(sharpness(grey)) / 4)
            if len(tr.crops) < CROPS_PER_TRACK or quality > worst:
                tr.crops.append(Crop(quality, frame, encode_jpeg(np.ascontiguousarray(crop))))
                tr.crops.sort(key=lambda c: -c.quality)
                del tr.crops[CROPS_PER_TRACK:]
        return frame


# --- tracks, teams, players -----------------------------------------------------------------


@dataclass
class Track:
    track_id: int
    first: int  # analysed frame
    last: int
    detections: int
    side: int  # NEAR | FAR, by majority
    window: int
    team: str = "us"  # us | them
    descriptor: Arr | None = None
    number: int | None = None
    number_conf: float = 0.0
    votes: dict[int, float] = field(default_factory=dict)
    height_px: float = 0.0
    foot: tuple[float, float] | None = None  # median, court metres
    crops: list[Crop] = field(default_factory=list)
    referee: bool = False


def finish_tracks(col: Collector) -> list[Track]:
    """The tracks long enough to count, each with its side (by majority; without a
    calibration by its median foot height against the split between the two teams)."""
    unsure = [
        v
        for tr in col.tracks.values()
        for v, s in list(zip(tr.foot_v, tr.sides, strict=True))[:: max(1, len(tr.foot_v) // 8)]
        if s < 0
    ]
    if unsure and col.split_v is None:
        col.split_v = split_from_feet(unsure)
    fallback = Geometry(roi=(0.0, 0.0, 1.0, 1.0), split_v=col.split_v)
    out = []
    for tr in col.tracks.values():
        if len(tr.frames) < MIN_TRACK_DETECTIONS:
            continue
        known = [s for s in tr.sides if s >= 0]
        if known:
            near = sum(1 for s in known if s == NEAR)
            side = NEAR if near * 2 >= len(known) else FAR
        else:
            side = fallback.side(None, float(np.median(tr.foot_v)))
        foot = None
        if tr.feet:
            feet = np.asarray(tr.feet)
            foot = (float(np.median(feet[:, 0])), float(np.median(feet[:, 1])))
        desc = np.median(np.stack(tr.descriptors), axis=0) if tr.descriptors else None
        referee = False
        if tr.feet:
            xs = np.asarray(tr.feet)[:, 0]
            off = (xs < -OFF_SIDE_M) | (xs > court.WIDTH_M + OFF_SIDE_M)
            referee = float(off.mean()) >= OFF_COURT_SHARE
        out.append(
            Track(
                track_id=tr.track_id,
                first=tr.frames[0],
                last=tr.frames[-1],
                detections=len(tr.frames),
                side=side,
                window=tr.window,
                descriptor=desc,
                height_px=float(np.median(tr.heights_px)),
                foot=foot,
                crops=list(tr.crops),
                referee=referee,
            )
        )
    out.sort(key=lambda t: t.track_id)
    return out


def read_numbers(tracks: Sequence[Track], reader: Any, per_track: int = CROPS_PER_TRACK) -> int:
    """Shirt numbers from each track's best crops (torso part); returns the crops read."""
    done = 0
    for t in tracks:
        if t.referee:
            continue
        for c in t.crops[:per_track]:
            img = decode_jpeg(c.jpeg)
            h = img.shape[0]
            torso = np.ascontiguousarray(img[int(0.1 * h) : max(int(0.6 * h), 1)])
            number, conf = reader.read(torso)
            done += 1
            if number is not None:
                t.votes[number] = t.votes.get(number, 0.0) + conf
        settle_number(t)
    return done


def settle_number(t: Track) -> None:
    if not t.votes:
        t.number, t.number_conf = None, 0.0
        return
    number, score = max(t.votes.items(), key=lambda kv: kv[1])
    total = sum(t.votes.values())
    if score >= NUMBER_MIN_VOTES and score / total >= NUMBER_SHARE:
        t.number, t.number_conf = number, round(min(1.0, score / (score + 1.0)), 3)
    else:
        t.number, t.number_conf = None, 0.0


def assign_teams(tracks: Sequence[Track], near_team: Sequence[str | None]) -> dict[str, Any]:
    """Our team or theirs for each track: the side of the net it played on, and which team
    was on that side in its rally window (`near_team[window]`: "us", "them" or None, from
    the serve and the sets). Windows with no answer take the team whose shirts look like
    the side's (kit colours learnt from the known windows, else from all windows, with
    "near in the first window = us" as the start: the coach can swap)."""
    groups: dict[tuple[int, int], list[Track]] = defaultdict(list)
    for t in tracks:
        if not t.referee:
            groups[(t.window, t.side)].append(t)
    windows = sorted({w for w, _ in groups})

    def mean_desc(items: list[Track]) -> Arr | None:
        ds = [t.descriptor for t in items if t.descriptor is not None]
        return np.mean(np.stack(ds), axis=0) if ds else None

    # near_is_us per window: True / False / None
    known: dict[int, bool] = {}
    for w in windows:
        team = near_team[w] if 0 <= w < len(near_team) else None
        if team in ("us", "them"):
            known[w] = team == "us"
    guess: dict[int, bool] = dict(known)
    unknown = [w for w in windows if w not in known]
    if unknown:
        state = dict(known)
        # The kits are learnt from the known windows; with none, from the first window
        # (near = us) and then from every window as it is decided.
        if not known:
            state[windows[0]] = True
        for _ in range(8):
            decided = list(state)
            us_d = [
                d
                for w in decided
                if (d := mean_desc(groups[(w, NEAR if state[w] else FAR)])) is not None
            ]
            them_d = [
                d
                for w in decided
                if (d := mean_desc(groups[(w, FAR if state[w] else NEAR)])) is not None
            ]
            if not us_d or not them_d:
                break
            us_p, them_p = np.mean(np.stack(us_d), axis=0), np.mean(np.stack(them_d), axis=0)
            changed = False
            for w in unknown:
                if not known and w == windows[0]:
                    continue  # the anchor
                near, far = mean_desc(groups[(w, NEAR)]), mean_desc(groups[(w, FAR)])
                keep = swap = 0.0
                if near is not None:
                    keep += distance(near, us_p)
                    swap += distance(near, them_p)
                if far is not None:
                    keep += distance(far, them_p)
                    swap += distance(far, us_p)
                value = keep <= swap
                if state.get(w) != value:
                    state[w], changed = value, True
            if not changed or known:
                break  # with known windows the kits are fixed: one pass decides
        guess = state
    for t in tracks:
        near_us = guess.get(t.window, True)
        t.team = "us" if (t.side == NEAR) == near_us else "them"
    return {
        "windows": len(windows),
        "known_windows": len(known),
        "kit_guessed_windows": len(unknown),
    }


@dataclass
class Cluster:
    key: int
    team: str
    tracks: list[Track]
    number: int | None = None  # the number reader's suggestion
    number_conf: float = 0.0
    referee: bool = False

    @property
    def detections(self) -> int:
        return sum(t.detections for t in self.tracks)


def _overlap(a: Track, b: Track) -> bool:
    return a.first <= b.last and b.first <= a.last


def cluster_tracks(
    tracks: Sequence[Track], merge_distance: float = MERGE_DISTANCE
) -> list[Cluster]:
    """Tracks → players, per team. Must link: the same confident shirt number. Cannot link:
    tracks seen at the same time (two people), different confident numbers. Then average
    linkage on appearance, closest admissible pair first, until the closest is farther than
    `merge_distance`. Referee-like tracks form their own cluster (marked)."""
    clusters: list[Cluster] = []
    referees = [t for t in tracks if t.referee]
    for team in ("us", "them"):
        items = [t for t in tracks if t.team == team and not t.referee]
        if not items:
            continue
        groups: list[list[Track]] = []
        by_number: dict[int, list[Track]] = defaultdict(list)
        rest: list[Track] = []
        for t in items:
            (by_number[t.number] if t.number is not None else rest).append(t)
        for number in sorted(by_number):
            group: list[Track] = []
            for t in sorted(by_number[number], key=lambda x: -x.number_conf):
                if any(_overlap(t, o) for o in group):
                    t.number, t.number_conf = None, 0.0  # a misread: two at once
                    rest.append(t)
                else:
                    group.append(t)
            groups.append(group)
        groups.extend([t] for t in rest)
        groups = _agglomerate(groups, merge_distance)
        for g in groups:
            clusters.append(Cluster(key=0, team=team, tracks=sorted(g, key=lambda t: t.first)))
    if referees:
        clusters.append(Cluster(key=0, team="them", tracks=referees, referee=True))
    for c in clusters:
        _cluster_number(c)
    clusters.sort(key=lambda c: (c.referee, c.team != "us", -c.detections))
    for i, c in enumerate(clusters):
        c.key = i + 1
    return clusters


def _numbers(group: list[Track]) -> set[int]:
    return {t.number for t in group if t.number is not None}


def _agglomerate(groups: list[list[Track]], threshold: float) -> list[list[Track]]:
    n = len(groups)
    if n < 2:
        return groups
    dim = next((len(t.descriptor) for g in groups for t in g if t.descriptor is not None), 0)
    if dim == 0:
        return groups
    desc = np.zeros((n, dim))
    has = np.zeros(n, dtype=bool)
    size = np.zeros(n)
    for i, g in enumerate(groups):
        ds = [t.descriptor for t in g if t.descriptor is not None]
        if ds:
            desc[i] = np.mean(np.stack(ds), axis=0)
            has[i] = True
            size[i] = len(ds)
    diff = desc[:, None, :] - desc[None, :, :]
    dist = np.sqrt((diff * diff).sum(-1))
    spans = [(min(t.first for t in g), max(t.last for t in g)) for g in groups]
    blocked = np.zeros((n, n), dtype=bool)
    for i in range(n):
        for j in range(i + 1, n):
            if (
                spans[i][0] <= spans[j][1]
                and spans[j][0] <= spans[i][1]
                and any(_overlap(a, b) for a in groups[i] for b in groups[j])
            ):
                blocked[i, j] = blocked[j, i] = True
            else:
                ni, nj = _numbers(groups[i]), _numbers(groups[j])
                if ni and nj and ni != nj:
                    blocked[i, j] = blocked[j, i] = True
    alive = has.copy()
    inf = np.inf
    work = np.where(blocked | ~alive[:, None] | ~alive[None, :], inf, dist)
    np.fill_diagonal(work, inf)
    members = [list(g) for g in groups]
    while True:
        k = int(np.argmin(work))
        i, j = divmod(k, n)
        if not np.isfinite(work[i, j]) or work[i, j] > threshold:
            break
        # Merge j into i (average linkage, Lance-Williams).
        si, sj = size[i], size[j]
        dist[i] = (si * dist[i] + sj * dist[j]) / (si + sj)
        dist[:, i] = dist[i]
        size[i] = si + sj
        blocked[i] |= blocked[j]
        blocked[:, i] = blocked[i]
        members[i].extend(members[j])
        members[j] = []
        alive[j] = False
        work[j, :] = inf
        work[:, j] = inf
        row = np.where(blocked[i] | ~alive, inf, dist[i])
        row[i] = inf
        work[i, :] = row
        work[:, i] = row
    return [m for m in members if m]


def _cluster_number(c: Cluster) -> None:
    votes: dict[int, float] = defaultdict(float)
    for t in c.tracks:
        for number, v in t.votes.items():
            votes[number] += v
    if not votes:
        return
    number, score = max(votes.items(), key=lambda kv: kv[1])
    total = sum(votes.values())
    if score >= NUMBER_MIN_VOTES and score / total >= NUMBER_SHARE:
        c.number = number
        c.number_conf = round(min(1.0, (score / total) * score / (score + 2.0)), 3)


# --- outputs --------------------------------------------------------------------------------


def _cell(img: Arr) -> Arr:
    """A crop fitted into a sheet cell (kept aspect, dark padding)."""
    from wieplay.ml.people import resize

    h, w = img.shape[:2]
    s = min(CELL_W / w, CELL_H / h)
    nw, nh = max(1, round(w * s)), max(1, round(h * s))
    out = np.full((CELL_H, CELL_W, 3), 24, np.uint8)
    x0, y0 = (CELL_W - nw) // 2, CELL_H - nh
    out[y0 : y0 + nh, x0 : x0 + nw] = resize(img, nw, nh)
    return out


@dataclass
class Sheets:
    pages: list[bytes]  # JPEG contact sheets
    cluster_crops: dict[int, list[list[int]]]  # cluster key → [[page, cell], ...]
    track_crops: dict[int, list[int]]  # track id → [page, cell]


def contact_sheets(clusters: Sequence[Cluster]) -> Sheets:
    """The crops the gallery shows: up to four per cluster (largest and sharpest, those
    whose number reads as the cluster's first, from different tracks), then one per track
    (for splitting a cluster)."""
    cells: list[Arr] = []
    cluster_crops: dict[int, list[list[int]]] = {}
    track_crops: dict[int, list[int]] = {}
    per_page = SHEET_COLS * SHEET_ROWS

    def place(img: Arr) -> list[int]:
        cells.append(_cell(img))
        k = len(cells) - 1
        return [k // per_page, k % per_page]

    for c in clusters:
        candidates = []
        for t in c.tracks:
            for rank, crop in enumerate(t.crops):
                bonus = 1.5 if c.number is not None and t.number == c.number else 1.0
                candidates.append((crop.quality * bonus / (1 + rank), t.track_id, crop))
        candidates.sort(key=lambda x: -x[0])
        chosen: list[Crop] = []
        used_tracks: set[int] = set()
        for _, tid, crop in candidates:
            if tid in used_tracks and len(used_tracks) < len(c.tracks):
                continue
            chosen.append(crop)
            used_tracks.add(tid)
            if len(chosen) >= CLUSTER_CROPS:
                break
        cluster_crops[c.key] = [place(decode_jpeg(crop.jpeg)) for crop in chosen]
    for c in clusters:
        for t in c.tracks:
            if t.crops:
                track_crops[t.track_id] = place(decode_jpeg(t.crops[0].jpeg))
    pages: list[bytes] = []
    for p in range(0, len(cells), per_page):
        chunk = cells[p : p + per_page]
        rows = -(-len(chunk) // SHEET_COLS)
        sheet = np.full((rows * CELL_H, SHEET_COLS * CELL_W, 3), 24, np.uint8)
        for k, img in enumerate(chunk):
            r, col = divmod(k, SHEET_COLS)
            sheet[r * CELL_H : (r + 1) * CELL_H, col * CELL_W : (col + 1) * CELL_W] = img
        pages.append(encode_jpeg(sheet, quality=85))
    return Sheets(pages, cluster_crops, track_crops)


def _pack(values: Sequence[int] | Arr, code: str) -> str:
    a = array(code, (int(v) for v in values))
    if sys.byteorder != "little":
        a.byteswap()
    return base64.b64encode(zlib.compress(a.tobytes(), 6)).decode()


def encode_index(col: Collector, tracks: Sequence[Track]) -> dict[str, Any]:
    """The compact index the API attributes actions with (schema 1, JSON):

        {"schema": 1, "frames": n, "t_ms": <uint32 per analysed frame>,
         "start": <uint32 first row of each frame, n + 1>, "track": <uint32 per row>,
         "box": <uint16 x1 y1 x2 y2 per row, 1/65535 of the frame>}

    Arrays are little-endian, zlib, base64. Only rows of the tracks kept (players' tracks,
    not referees')."""
    keep = {t.track_id for t in tracks if not t.referee}
    rows = sorted((r for r in col.rows if r[1] in keep), key=lambda r: (r[0], r[1]))
    n = len(col.times)
    start = np.zeros(n + 1, dtype=np.int64)
    for r in rows:
        start[r[0] + 1] += 1
    start = np.cumsum(start)
    boxes = np.array([r[2:] for r in rows], dtype=np.float64).reshape(-1, 4)
    q = np.rint(np.clip(boxes, 0, 1) * 65535).astype(np.int64).ravel()
    return {
        "schema": INDEX_SCHEMA,
        "frames": n,
        "rows": len(rows),
        "t_ms": _pack(col.times, "I"),
        "start": _pack(start, "I"),
        "track": _pack([r[1] for r in rows], "I"),
        "box": _pack(q, "H"),
    }


def track_rows(
    tracks: Sequence[Track], times: Sequence[int], sheets: Sheets
) -> dict[int, dict[str, Any]]:
    """Per track what the API keeps with its cluster (for the gallery and its split)."""
    out = {}
    for t in tracks:
        out[t.track_id] = {
            "id": t.track_id,
            "t0": int(times[t.first]) if t.first < len(times) else 0,
            "t1": int(times[t.last]) if t.last < len(times) else 0,
            "n": t.detections,
            "side": "near" if t.side == NEAR else "far",
            "number": t.number,
            "number_conf": t.number_conf,
            "crop": sheets.track_crops.get(t.track_id),
            "h": round(t.height_px, 1),
            "foot": [round(t.foot[0], 2), round(t.foot[1], 2)] if t.foot else None,
        }
    return out
