"""Where one picture of a hall lies in another (PLAN §10.6): the hall's memory and a camera
that moved during a match.

    two frames ─features (ORB: same view, fast; SIFT: another day, another tripod spot)─▶
    matches (ratio test) ─RANSAC homography─▶ src pixels → dst pixels, with its inliers and
    how much of the picture they span

A homography relates two pictures exactly for a camera that only turned (a bump on the
tripod) and for one plane seen from anywhere (the floor). Matches on the floor are what
counts for the court, so the reference side can be masked to the floor around its court.

When the camera stood somewhere else, the floor's lines look too different for local
features. Both floors are then turned into a top view (the reference by its calibration,
the new one by a guess, at first the reference's own) and matched there, where the
difference left is small; the guess is corrected and the match repeated.

OpenCV, on the worker only (the `court` job).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

import cv2
import numpy as np
import numpy.typing as npt

Arr = npt.NDArray[Any]
Kind = Literal["orb", "sift"]

RATIO = 0.8  # Lowe's ratio test
THRESHOLD_PX = 3.0  # RANSAC: an inlier lands within this of its match
MIN_INLIERS = 15
# Top view of the floor: court metres around the court, PX pixels a metre.
TOP_PX = 25.0
TOP_X = (-5.0, 14.0)
TOP_Y = (-4.0, 22.0)
TOP_ROUNDS = 3


@dataclass
class Features:
    points: Arr  # (n, 2) float32, pixels
    descriptors: Arr | None
    kind: Kind
    size: tuple[int, int]  # (height, width) of the picture


@dataclass
class Registration:
    h: Arr  # 3x3: src pixels → dst pixels
    inliers: int
    matches: int
    spread: float  # area of the inliers' hull over the dst picture's area
    error_px: float  # median distance of the inliers to where the homography puts them


def gray(image: Arr) -> Arr:
    if image.ndim == 2:
        return image
    out: Arr = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    return out


def features(image: Arr, kind: Kind = "orb", n: int = 3000, mask: Arr | None = None) -> Features:
    """Keypoints and descriptors of an RGB (or grey) picture; `mask` (uint8, non-zero where
    to look) limits them to a region."""
    g = gray(image)
    detector: Any = cv2.ORB.create(nfeatures=n) if kind == "orb" else cv2.SIFT.create(nfeatures=n)
    keypoints, descriptors = detector.detectAndCompute(g, mask)
    points = np.array([k.pt for k in keypoints], dtype=np.float32).reshape(-1, 2)
    return Features(points, descriptors, kind, (int(g.shape[0]), int(g.shape[1])))


def _hull_area(points: Arr) -> float:
    if len(points) < 3:
        return 0.0
    hull = cv2.convexHull(points.astype(np.float32))
    return float(cv2.contourArea(hull))


def register(
    src: Features,
    dst: Features,
    *,
    threshold_px: float = THRESHOLD_PX,
    min_inliers: int = MIN_INLIERS,
) -> Registration | None:
    """The homography taking src's picture onto dst's, or None without enough agreement."""
    if (
        src.descriptors is None
        or dst.descriptors is None
        or len(src.points) < min_inliers
        or len(dst.points) < min_inliers
    ):
        return None
    norm = cv2.NORM_HAMMING if src.kind == "orb" else cv2.NORM_L2
    pairs = cv2.BFMatcher(norm).knnMatch(src.descriptors, dst.descriptors, k=2)
    good = [p[0] for p in pairs if len(p) == 2 and p[0].distance < RATIO * p[1].distance]
    if len(good) < min_inliers:
        return None
    a = src.points[[m.queryIdx for m in good]]
    b = dst.points[[m.trainIdx for m in good]]
    h, mask = cv2.findHomography(
        a, b, cv2.USAC_MAGSAC, threshold_px, maxIters=5000, confidence=0.999
    )
    if h is None or mask is None:
        return None
    keep = mask.ravel().astype(bool)
    n = int(keep.sum())
    if n < min_inliers or not np.all(np.isfinite(h)):
        return None
    moved = apply(h, a[keep])
    error = float(np.median(np.linalg.norm(moved - b[keep], axis=1)))
    area = float(dst.size[0] * dst.size[1])
    return Registration(
        h=h / h[2, 2], inliers=n, matches=len(good), spread=_hull_area(b[keep]) / area,
        error_px=error,
    )  # fmt: skip


def apply(h: Arr, points: Arr) -> Arr:
    """Points (n, 2) through a homography; points at infinity come out as inf."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    homog = np.concatenate([pts, np.ones((len(pts), 1))], axis=1) @ np.asarray(h).T
    with np.errstate(divide="ignore", invalid="ignore"):
        out: Arr = homog[:, :2] / homog[:, 2:3]
    return out


def probe_grid(size: tuple[int, int], top: float = 0.3, n: tuple[int, int] = (6, 4)) -> Arr:
    """Points spread over the picture below `top` (share of its height): the floor, where
    the court is, in a view from behind the end line."""
    h, w = size
    xs = np.linspace(0.08 * w, 0.92 * w, n[0])
    ys = np.linspace(top * h, 0.95 * h, n[1])
    gx, gy = np.meshgrid(xs, ys)
    out: Arr = np.stack([gx.ravel(), gy.ravel()], axis=1)
    return out


def shift_px(h: Arr, probe: Arr) -> float:
    """How far the homography moves the probe points (the 90th percentile, pixels)."""
    moved = apply(h, probe)
    d = np.linalg.norm(moved - probe, axis=1)
    d = np.where(np.isfinite(d), d, 1e9)
    return float(np.percentile(d, 90))


# --- the court through another picture -----------------------------------------------------


def floor_mask(court_to_image_px: Arr, size: tuple[int, int], margin_m: float = 4.0) -> Arr:
    """uint8 mask of the floor around the court (margin_m metres beyond its lines), in front
    of the camera."""
    h, w = size
    mask = np.zeros((h, w), np.uint8)
    xs = np.linspace(-margin_m, 9.0 + margin_m, 40)
    ys = np.linspace(-margin_m, 18.0 + margin_m, 80)
    outline = np.concatenate(
        [
            np.stack([xs, np.full_like(xs, ys[0])], 1),
            np.stack([np.full_like(ys, xs[-1]), ys], 1),
            np.stack([xs[::-1], np.full_like(xs, ys[-1])], 1),
            np.stack([np.full_like(ys, xs[0]), ys[::-1]], 1),
        ]
    )
    homog = np.concatenate([outline, np.ones((len(outline), 1))], 1) @ court_to_image_px.T
    front = homog[:, 2] > 1e-6
    if front.sum() < 3:
        return mask
    pts = homog[front, :2] / homog[front, 2:3]
    pts = np.clip(pts, -4 * w, 5 * w)
    cv2.fillPoly(mask, [np.rint(pts).astype(np.int32)], 255)
    return mask


def _top_transform() -> Arr:
    """Court metres → top-view pixels (y up, so the near end line is at the bottom)."""
    return np.array(
        [[TOP_PX, 0.0, -TOP_X[0] * TOP_PX], [0.0, -TOP_PX, TOP_Y[1] * TOP_PX], [0.0, 0.0, 1.0]]
    )


def top_view(image: Arr, court_to_image_px: Arr) -> Arr:
    """The floor seen from above (TOP_PX pixels a metre) through a court → image homography."""
    t = _top_transform()
    size = (int((TOP_X[1] - TOP_X[0]) * TOP_PX), int((TOP_Y[1] - TOP_Y[0]) * TOP_PX))
    image_to_top = t @ np.linalg.inv(court_to_image_px)
    out: Arr = cv2.warpPerspective(image, image_to_top, size, flags=cv2.INTER_AREA)
    return out


@dataclass
class Transfer:
    court_to_image: Arr  # 3x3, court metres → the new picture's pixels
    how: Literal["direct", "top"]
    registration: Registration


def transfers(
    ref_image: Arr,
    ref_court_to_image: Arr,
    image: Arr,
    *,
    image_features: Features | None = None,
    min_inliers: int = 30,
) -> list[Transfer]:
    """The court of a reference picture (calibrated) in a new picture of the same hall,
    every way that works: the two pictures' floors matched directly, the whole pictures
    matched (the camera at about the same spot: more to match than the floor), and when
    neither does, their top views. The caller judges them on the new picture's lines."""
    size = (int(image.shape[0]), int(image.shape[1]))
    ref_size = (int(ref_image.shape[0]), int(ref_image.shape[1]))
    new_f = image_features or features(image, "sift", 6000)
    out: list[Transfer] = []
    for mask in (floor_mask(ref_court_to_image, ref_size), None):
        ref_f = features(ref_image, "sift", 4000, mask)
        found = register(ref_f, new_f, threshold_px=4.0, min_inliers=min_inliers)
        if found is not None and found.spread >= 0.04:
            h = found.h @ ref_court_to_image
            if _sane(h, size):
                out.append(Transfer(h, "direct", found))
    if out:
        return out
    top = _from_above(ref_image, ref_court_to_image, image)
    return [top] if top is not None else []


def transfer(
    ref_image: Arr,
    ref_court_to_image: Arr,
    image: Arr,
    *,
    image_features: Features | None = None,
    min_inliers: int = 30,
) -> Transfer | None:
    """The transfer with the most agreement (see `transfers`), or None."""
    found = transfers(
        ref_image, ref_court_to_image, image, image_features=image_features, min_inliers=min_inliers
    )
    return max(found, key=lambda t: t.registration.inliers) if found else None


def _from_above(ref_image: Arr, ref_court_to_image: Arr, image: Arr) -> Transfer | None:
    """Another tripod spot: the floors seen from above matched, the new one through a guess
    (at first the reference's court), corrected and matched again."""
    size = (int(image.shape[0]), int(image.shape[1]))
    t = _top_transform()
    ref_top = features(top_view(ref_image, ref_court_to_image), "sift", 4000)
    guess = ref_court_to_image
    found: Registration | None = None
    for _ in range(TOP_ROUNDS):
        top = top_view(image, guess)
        if not np.any(top):
            return None
        reg = register(ref_top, features(top, "sift", 4000), threshold_px=4.0, min_inliers=20)
        if reg is None:
            return None
        found = reg
        # ref top → new top (through the guess): court → new image is guess · T⁻¹ · reg · T.
        guess = guess @ np.linalg.inv(t) @ reg.h @ t
        guess = guess / guess[2, 2]
        if shift_px(reg.h, probe_grid((top.shape[0], top.shape[1]), 0.0)) < 1.0:
            break
    if found is None or not _sane(guess, size):
        return None
    return Transfer(guess, "top", found)


def _sane(court_to_image_px: Arr, size: tuple[int, int]) -> bool:
    """The court's corners are in front of the camera and not absurdly far off the picture."""
    h, w = size
    corners = np.array([[0.0, 0.0], [9.0, 0.0], [9.0, 18.0], [0.0, 18.0]])
    homog = np.concatenate([corners, np.ones((4, 1))], 1) @ court_to_image_px.T
    if not np.all(homog[:, 2] > 0):
        return False
    pts = homog[:, :2] / homog[:, 2:3]
    return bool(
        np.all(np.abs(pts[:, 0] - w / 2) < 3 * w) and np.all(np.abs(pts[:, 1] - h / 2) < 3 * h)
    )


def to_normalised(h_px: Arr, size: tuple[int, int]) -> Arr:
    """A court → pixels homography as court → normalised (0..1) picture coordinates."""
    out: Arr = np.diag([1.0 / size[1], 1.0 / size[0], 1.0]) @ h_px
    return out / out[2, 2] if abs(out[2, 2]) > 1e-12 else out


def to_pixels(h_norm: Arr, size: tuple[int, int]) -> Arr:
    out: Arr = np.diag([float(size[1]), float(size[0]), 1.0]) @ np.asarray(h_norm, float)
    return out / out[2, 2] if abs(out[2, 2]) > 1e-12 else out


def image_to_image_normalised(h_px: Arr, src: tuple[int, int], dst: tuple[int, int]) -> Arr:
    """A pixels → pixels homography between two pictures as normalised → normalised."""
    out: Arr = (
        np.diag([1.0 / dst[1], 1.0 / dst[0], 1.0])
        @ h_px
        @ np.diag([float(src[1]), float(src[0]), 1.0])
    )
    return out / out[2, 2] if abs(out[2, 2]) > 1e-12 else out


def corner_distance_px(a: Arr, b: Arr) -> float:
    """Largest distance between the court corners of two court → pixels homographies."""
    corners = np.array([[0.0, 0.0], [9.0, 0.0], [9.0, 18.0], [0.0, 18.0]])
    d = np.linalg.norm(apply(a, corners) - apply(b, corners), axis=1)
    return float(np.max(np.where(np.isfinite(d), d, math.inf)))
