"""Find the volleyball court's lines in a frame (PLAN §10.6, "later: automatic line
detection"), so a coach confirms the corners instead of clicking them.

    a few frames ─median─▶ an empty hall (players move, lines stay) ─▶ thin bright or dark
    lines of any colour (textures such as the net's mesh dropped) ─▶ the vanishing point of
    the hall's long lines: the floor is below it ─▶ pairs of side lines through it ─▶ the
    lines across the wedge between them ─▶ the court's spacing (0, 6, 9, 12, 18 m) found
    along both side lines (cross ratios survive any view) ─▶ fits a real camera could see,
    judged on the line pixels along the whole model ─▶ landmarks in normalised coordinates

Pure numpy, on the worker (the `trajectory` job). Multi-sport floors are hard: many
parallel lines, some at spacings that fit the model by chance; the confidence is lowered
when two different courts explain the picture about as well. Lens distortion is ignored
(PLAN §10.6: later), so very wide lenses fit worse.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from nomal.core import court
from nomal.ml.features import Arr

# Line pixels: a pixel that differs from the pixels D px to both sides across the line.
RIDGE_D = (2, 4)
RIDGE_MIN = 22.0  # grey levels, in the channel where it differs most
DENSITY_BOX = 15  # px: where most pixels around are line pixels, it is a texture
DENSITY_MAX = 0.4
MAX_RIDGE_PIXELS = 120_000
# Hough
THETA_BINS = 360  # 0.5°
RHO_STEP = 2.0
PEAK_MIN_VOTES = 50
NMS_THETA = 3  # bins
NMS_RHO = 4  # bins
VP_LINES = 40
VP_MAX_SIN = 0.985  # lines steeper than ~10° from horizontal vote for the vanishing point
VP_TOL = 0.015  # of the image diagonal
VP_MIN_LINES = 5
TOP_SKIP = 0.2  # without a vanishing point: the top of the picture does not vote
FAMILY_DEG = 35.0  # without a vanishing point: within this of horizontal, a cross line
# Side lines
SIDE_CANDIDATES = 8
SIDE_APART_DEG = 2.0  # lines through one point closer than this are one bundle
SIDE_MIN_ANGLE_DEG = 10.0  # two side lines a court apart meet at a clear angle
# Cross lines, between two side lines
CROSS_Y = (0.0, 6.0, 9.0, 12.0, 18.0)
CROSS_CANDIDATES = 16
CROSS_STEP_PX = 2.0
CROSS_MAX_SLOPE = 0.7  # within ~35° of horizontal
CROSS_SAMPLES = 64
CROSS_MIN_SUPPORT = 0.45
CROSS_NMS = 3  # steps
MATCH_PX = 3.0  # a model cross line lands on a candidate within this
MATCH_SHARE = 0.012  # plus this share of its distance from where the side lines meet
CROSS_KEEP = 40  # best line matchings per pair of side lines, judged on the pixels
MIN_CROSS = 3
# Judging a fit
SAMPLE_M = 0.25
MISS_WEIGHT = 1.5  # a pixel of visible model line on no line pixel costs this much
TOLERANCE_PX = 2
MAX_STEP_PX = 40.0  # a sample stands for at most this much line (parts near the camera)
MIN_VISIBLE = 1.5  # visible model lines, in image widths
CAMERA_Z = (1.0, 25.0)  # metres above the floor
AMBIGUOUS = 0.92  # a different court scoring this close: not sure
DIFFERENT = 0.04  # corners further apart than this (normalised) make a different court
# Pre-fill the dialog ("auto") at or above this confidence.
CONFIDENT = 0.6


@dataclass
class CourtGuess:
    points: dict[str, tuple[float, float]]  # landmark → (u, v), those inside the frame
    court_to_image: court.Mat3
    confidence: float  # share of the visible model lines on line pixels; halved if ambiguous
    stats: dict[str, Any] = field(default_factory=dict)


# --- line pixels ---------------------------------------------------------------------------


def ridges(image: Arr) -> Arr:
    """Line strength per pixel (grey levels), bright or dark lines in any colour."""
    img = image.astype(np.float32)
    h, w = img.shape[:2]
    best = np.zeros((h, w), dtype=np.float32)
    for d in RIDGE_D:
        for axis in (1, 0):  # across x (a line running up and down), across y
            mid_s: list[slice] = [slice(None), slice(None)]
            lo_s: list[slice] = [slice(None), slice(None)]
            hi_s: list[slice] = [slice(None), slice(None)]
            mid_s[axis], lo_s[axis], hi_s[axis] = slice(d, -d), slice(0, -2 * d), slice(2 * d, None)
            mid, a, b = img[tuple(mid_s)], img[tuple(lo_s)], img[tuple(hi_s)]
            r = np.maximum(np.minimum(mid - a, mid - b), np.minimum(a - mid, b - mid)).max(axis=2)
            target = best[tuple(mid_s)]
            np.maximum(target, r, out=target)
    return best


def lines_only(mask: Arr) -> Arr:
    """Drops textures (the net's mesh, crowds, clutter): where most pixels in a box are line
    pixels they are not lines (a line a few pixels wide fills a fraction of the box)."""
    k = DENSITY_BOX
    pad = np.pad(mask.astype(np.int32), ((k // 2 + 1, k // 2), (k // 2 + 1, k // 2)))
    cum = pad.cumsum(0).cumsum(1)
    box = cum[k:, k:] - cum[:-k, k:] - cum[k:, :-k] + cum[:-k, :-k]
    out: Arr = mask & (box < DENSITY_MAX * k * k)
    return out


def dilate(mask: Arr, r: int) -> Arr:
    h, w = mask.shape
    out = mask.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dx or dy:
                src = mask[max(0, -dy) : h - max(0, dy), max(0, -dx) : w - max(0, dx)]
                out[max(0, dy) : h - max(0, -dy), max(0, dx) : w - max(0, -dx)] |= src
    return out


# --- lines ---------------------------------------------------------------------------------


@dataclass
class _Hough:
    acc: Arr  # (THETA_BINS, n_rho) votes
    thetas: Arr
    diag: float

    def peaks(self, n: int, rows: Arr | None = None) -> list[tuple[Arr, int]]:
        """The n strongest lines (a, b, c), a x + b y + c = 0 with (a, b) a unit normal."""
        n_rho = self.acc.shape[1]
        work = (self.acc if rows is None else np.where(rows[:, None], self.acc, 0)).copy()
        found: list[tuple[Arr, int]] = []
        while len(found) < n:
            t, r = divmod(int(work.argmax()), n_rho)
            votes = int(work[t, r])
            if votes < PEAK_MIN_VOTES:
                break
            theta, rho = float(self.thetas[t]), r * RHO_STEP - self.diag
            found.append((np.array([math.cos(theta), math.sin(theta), -rho]), votes))
            for off in range(-NMS_THETA, NMS_THETA + 1):
                row, rr = t + off, r
                if not 0 <= row < THETA_BINS:  # theta wraps around: the same line, rho negated
                    row, rr = row % THETA_BINS, n_rho - 1 - r
                work[row, max(0, rr - NMS_RHO) : rr + NMS_RHO + 1] = 0
        return found


def hough(mask: Arr, rng: np.random.Generator, below: float = -1.0) -> _Hough:
    """Line votes of the mask's pixels below image row `below`."""
    ys, xs = np.nonzero(mask)
    keep = ys > below
    xs, ys = xs[keep], ys[keep]
    if len(xs) > MAX_RIDGE_PIXELS:
        pick = rng.choice(len(xs), MAX_RIDGE_PIXELS, replace=False)
        xs, ys = xs[pick], ys[pick]
    h, w = mask.shape
    diag = math.hypot(h, w)
    thetas = np.arange(THETA_BINS) * (np.pi / THETA_BINS)
    n_rho = int(2 * diag / RHO_STEP) + 1
    acc = np.zeros(THETA_BINS * n_rho, dtype=np.int64)
    cos, sin = np.cos(thetas).astype(np.float32), np.sin(thetas).astype(np.float32)
    for start in range(0, len(xs), 20_000):
        x = xs[start : start + 20_000, None].astype(np.float32)
        y = ys[start : start + 20_000, None].astype(np.float32)
        rho = np.rint((x * cos + y * sin + diag) / RHO_STEP).astype(np.int64)
        idx = np.arange(THETA_BINS)[None, :] * n_rho + rho
        acc += np.bincount(idx.ravel(), minlength=len(acc))
    return _Hough(acc.reshape(THETA_BINS, n_rho), thetas, diag)


def intersect(l1: Arr, l2: Arr) -> Arr | None:
    p = np.cross(l1, l2)
    if abs(p[2]) < 1e-9:
        return None
    out: Arr = p[:2] / p[2]
    return out


def vanishing_point(lines: list[Arr], tol: float) -> Arr | None:
    """The point most lines pass through (within tol px): where the court's long lines,
    the walls and the ceiling meet in a view along the hall. None without a clear one."""
    best: tuple[Arr, int] | None = None
    for l1, l2 in itertools.combinations(lines, 2):
        p = intersect(l1, l2)
        if p is None or not np.all(np.abs(p) < 1e5):
            continue
        count = sum(1 for ln in lines if abs(ln[0] * p[0] + ln[1] * p[1] + ln[2]) <= tol)
        if best is None or count > best[1]:
            best = (p, count)
    return best[0] if best is not None and best[1] >= VP_MIN_LINES else None


def _distinct_directions(lines: list[tuple[Arr, int]]) -> list[tuple[Arr, int]]:
    """Lines through one point differ only in direction: the strongest of each bundle (a
    wall's edge shows as several lines a pixel apart)."""
    kept: list[tuple[Arr, int]] = []
    angles: list[float] = []
    apart = math.radians(SIDE_APART_DEG)
    for line, votes in lines:
        angle = math.atan2(line[1], line[0]) % math.pi
        if all(min(abs(angle - a), math.pi - abs(angle - a)) > apart for a in angles):
            kept.append((line, votes))
            angles.append(angle)
    return kept


def _cross_candidates(left: Arr, right: Arr, mask: Arr, below: float) -> list[Arr]:
    """Lines across the wedge between two side lines, found on the pixels inside it: every
    segment from a point of one side line to a point of the other, scored by the share of
    its length on line pixels; the local best ones. (The court's far lines are short and
    lose against the hall's long lines in a Hough transform of the whole floor.)"""
    hh, ww = mask.shape

    def visible(line: Arr) -> Arr | None:
        a, b, c = line
        if abs(a) < 1e-9:
            return None
        ys = np.arange(max(0.0, below), hh, CROSS_STEP_PX)
        xs = -(c + b * ys) / a
        ok = (xs >= 0) & (xs < ww)
        return np.stack([xs[ok], ys[ok]], axis=1) if ok.sum() >= 4 else None

    pl, pr = visible(left), visible(right)
    if pl is None or pr is None:
        return []
    grid_i, grid_j = np.meshgrid(np.arange(len(pl)), np.arange(len(pr)), indexing="ij")
    i: Arr = grid_i.ravel()
    j: Arr = grid_j.ravel()
    a, b = pl[i], pr[j]
    d = b - a
    flat = np.abs(d[:, 1]) <= CROSS_MAX_SLOPE * np.abs(d[:, 0])
    a, b, i, j = a[flat], b[flat], i[flat], j[flat]
    if not len(a):
        return []
    f = np.linspace(0.0, 1.0, CROSS_SAMPLES)
    px = np.rint(a[:, None, 0] + f[None] * (b[:, None, 0] - a[:, None, 0])).astype(np.int64)
    py = np.rint(a[:, None, 1] + f[None] * (b[:, None, 1] - a[:, None, 1])).astype(np.int64)
    inside = (px >= 0) & (px < ww) & (py >= 0) & (py < hh)
    on = inside & mask[np.clip(py, 0, hh - 1), np.clip(px, 0, ww - 1)]
    grid = np.zeros((len(pl), len(pr)))
    grid[i, j] = on.mean(1)
    found: list[Arr] = []
    while len(found) < CROSS_CANDIDATES:
        ii, jj = divmod(int(grid.argmax()), len(pr))
        if grid[ii, jj] < CROSS_MIN_SUPPORT:
            break
        line = np.cross([*pl[ii], 1.0], [*pr[jj], 1.0])
        found.append(line / np.linalg.norm(line[:2]))
        n = CROSS_NMS
        grid[max(0, ii - n) : ii + n + 1, max(0, jj - n) : jj + n + 1] = 0
    return found


@dataclass
class _Matching:
    count: int
    pts_left: list[Arr]  # where each matched model cross line meets the side lines
    pts_right: list[Arr]
    ys: list[float]


def _match_cross(left: Arr, right: Arr, cross: list[Arr], size: tuple[int, int]) -> list[_Matching]:
    """Which cross lines are the court's end, attack and centre lines between two side
    lines: the court's spacing must show along both side lines as a 1D projective map
    (cross ratios hold in any view). Every three candidates against every three model
    lines; the other model lines must land on candidates too, on both side lines."""
    hh, ww = size
    origin = intersect(left, right)
    pts_l: list[Arr] = []
    pts_r: list[Arr] = []
    for line in cross:
        pl, pr = intersect(left, line), intersect(right, line)
        if pl is None or pr is None:
            continue
        if all(-0.5 * ww <= p[0] <= 1.5 * ww and -0.5 * hh <= p[1] <= 1.5 * hh for p in (pl, pr)):
            pts_l.append(pl)
            pts_r.append(pr)
    n = len(pts_l)
    if n < MIN_CROSS:
        return []
    finite = origin is not None and bool(np.all(np.abs(origin) < 1e6))
    base = origin if finite and origin is not None else np.array([ww / 2, -hh])

    def along(line: Arr, pts: list[Arr]) -> Arr:
        d = np.array([line[1], -line[0]])
        t = np.array([(p - base) @ d for p in pts])
        out: Arr = t if np.median(t) >= 0 else -t  # positive: away from where the lines meet
        return out

    tl, tr = along(left, pts_l), along(right, pts_r)
    keep = (tl > 0) & (tr > 0) if finite else np.ones(n, dtype=bool)
    idx = np.flatnonzero(keep)
    if len(idx) < MIN_CROSS:
        return []
    order = idx[np.argsort(-tl[idx])]  # nearest (furthest from the meeting point) first
    ys = np.array(CROSS_Y)
    combos = [
        (c, m)
        for c in itertools.combinations(order, 3)
        for m in itertools.combinations(range(5), 3)
    ]
    cand = np.array([c for c, _ in combos])
    mod = ys[np.array([m for _, m in combos])]
    ok = np.ones(len(combos), dtype=bool)
    sides: list[Arr] = []
    for t in (tl, tr):
        # t = (a y + b) / (c y + 1)  ⇔  a y + b - c y t = t
        tv = t[cand]
        a_mat = np.stack([mod, np.ones_like(mod), -mod * tv], axis=2)
        good = np.abs(np.linalg.det(a_mat)) > 1e-9
        coef = np.zeros((len(combos), 3))
        coef[good] = np.linalg.solve(a_mat[good], tv[good][..., None])[..., 0]
        den = coef[:, 2:3] * ys[None, :] + 1
        good &= np.all(den > 0.05, axis=1)  # no pole inside the court: it is in front
        pred = (coef[:, 0:1] * ys[None, :] + coef[:, 1:2]) / np.where(den > 0.05, den, 1)
        diff = np.abs(pred[:, :, None] - t[None, None, :])
        diff[:, :, ~keep] = np.inf
        nearest = diff.argmin(axis=2)
        dist = np.take_along_axis(diff, nearest[..., None], 2)[..., 0]
        sides.append(np.where(dist <= MATCH_PX + MATCH_SHARE * np.abs(pred), nearest, -1))
        ok &= good
    # Found: the same candidate on both side lines; no candidate stands for two model lines.
    inl = np.where((sides[0] == sides[1]) & (sides[0] >= 0) & ok[:, None], sides[0], -1)
    for col in range(1, 5):
        dup = (inl[:, col : col + 1] == inl[:, :col]).any(axis=1) & (inl[:, col] >= 0)
        inl[dup, col] = -1
    count = (inl >= 0).sum(1)
    out: list[_Matching] = []
    seen: set[tuple[int, ...]] = set()
    for k in np.argsort(-count, kind="stable"):
        if count[k] < MIN_CROSS or len(out) >= CROSS_KEEP:
            break
        key = tuple(int(v) for v in inl[k])
        if key in seen:
            continue
        seen.add(key)
        cols = [c for c in range(5) if inl[k, c] >= 0]
        out.append(
            _Matching(
                count=len(cols),
                pts_left=[pts_l[inl[k, c]] for c in cols],
                pts_right=[pts_r[inl[k, c]] for c in cols],
                ys=[float(ys[c]) for c in cols],
            )
        )
    return out


# --- judging fits ----------------------------------------------------------------------------


def _model_samples() -> tuple[Arr, Arr]:
    """Points along the model's lines (court metres) and the line each belongs to."""
    pts: list[tuple[float, float]] = []
    which: list[int] = []
    for i, (x1, y1, x2, y2) in enumerate(court.LINES):
        n = max(2, round(math.hypot(x2 - x1, y2 - y1) / SAMPLE_M) + 1)
        for f in np.linspace(0, 1, n):
            pts.append((x1 + f * (x2 - x1), y1 + f * (y2 - y1)))
            which.append(i)
    return np.array(pts, dtype=np.float64), np.array(which)


def _project(h: Arr, pts: Arr) -> tuple[Arr, Arr, Arr]:
    homog = np.concatenate([pts, np.ones((len(pts), 1))], axis=1).T
    out = h @ homog
    w = out[2]
    with np.errstate(divide="ignore", invalid="ignore"):
        return out[0] / w, out[1] / w, w


def _score(h: Arr, mask: Arr, samples: Arr, which: Arr) -> tuple[float, float]:
    """(Pixels of model line on line pixels, minus MISS_WEIGHT times those on none) in the
    image, so a court shrunk to a few pixels explains nothing; and the share on line pixels."""
    hh, ww = mask.shape
    u, v, depth = _project(h, samples)
    ur, vr = np.rint(u), np.rint(v)
    visible = (depth * depth[0] > 0) & (ur >= 0) & (ur < ww) & (vr >= 0) & (vr < hh)
    same = which[1:] == which[:-1]
    step = np.where(same, np.hypot(np.diff(u), np.diff(v)), 0.0)
    length = np.concatenate([step, [0.0]])
    length[1:] = np.where(same, length[1:], step)  # a line's last sample
    length = np.minimum(np.nan_to_num(length), MAX_STEP_PX) * visible
    ui = np.where(visible, ur, 0).astype(np.int64)
    vi = np.where(visible, vr, 0).astype(np.int64)
    hit = visible & mask[vi, ui]
    on, total = float((length * hit).sum()), float(length.sum())
    if total < MIN_VISIBLE * ww:
        return -math.inf, 0.0
    return on - MISS_WEIGHT * (total - on), on / total


def _plausible(to_image: court.Mat3, aspect: float) -> bool:
    """A camera above the floor, on the near side of the net, could see the court so."""
    camera = court.camera_from_homography(to_image, aspect)
    return (
        camera is not None
        and CAMERA_Z[0] <= camera.center[2] <= CAMERA_Z[1]
        and camera.center[1] <= court.NET_Y + 0.5
    )


def _fit(src: list[tuple[float, float]], dst: list[tuple[float, float]]) -> Arr:
    """Least-squares court → image homography in pixels (CourtError when degenerate)."""
    to_image = np.array(court.invert(court.fit_homography(src, dst)))
    out: Arr = to_image / to_image[2, 2]
    return out


def _refine(h: Arr, mask: Arr) -> Arr:
    """Fit each model line to the line pixels along its projection, intersect the fits at
    the landmarks and solve again over every landmark found."""
    hh, ww = mask.shape
    ys, xs = np.nonzero(mask)
    pts = np.stack([xs, ys], axis=1).astype(np.float64)
    fitted: dict[int, Arr] = {}
    for idx, (x1, y1, x2, y2) in enumerate(court.LINES):
        f = np.linspace(0, 1, 60)
        u, v, depth = _project(h, np.stack([x1 + f * (x2 - x1), y1 + f * (y2 - y1)], 1))
        ok = (depth > 0) & (u > -ww) & (u < 2 * ww) & (v > -hh) & (v < 2 * hh)
        if ok.sum() < 2:
            continue
        a, b = np.array([u[ok][0], v[ok][0]]), np.array([u[ok][-1], v[ok][-1]])
        length = float(np.linalg.norm(b - a))
        if length < 20:
            continue
        d = (b - a) / length
        rel = pts - a
        along, off = rel @ d, rel @ np.array([-d[1], d[0]])
        near = (np.abs(off) <= 3 * TOLERANCE_PX) & (along >= 0) & (along <= length)
        if near.sum() < 40:
            continue
        sel = pts[near]
        centre = sel.mean(0)
        direction = np.linalg.svd(sel - centre, full_matrices=False)[2][0]
        normal = np.array([-direction[1], direction[0]])
        fitted[idx] = np.array([normal[0], normal[1], -normal @ centre])
    side_of = {0.0: 0, court.WIDTH_M: 1}
    cross_of = {0.0: 2, 18.0: 3, 9.0: 4, 6.0: 5, 12.0: 6}
    src: list[tuple[float, float]] = []
    dst: list[tuple[float, float]] = []
    for x, y in court.LANDMARKS.values():
        la, lb = fitted.get(side_of[x]), fitted.get(cross_of[y])
        p = intersect(la, lb) if la is not None and lb is not None else None
        if p is not None:
            src.append((float(p[0]), float(p[1])))
            dst.append((x, y))
    return _fit(src, dst) if len(src) >= 4 else h


def _corners(h: Arr, size: tuple[int, int]) -> Arr:
    u, v, _ = _project(h, np.array([court.LANDMARKS[c] for c in court.CORNERS]))
    return np.stack([u / size[1], v / size[0]], axis=1)


def _normalised(h: Arr, size: tuple[int, int]) -> court.Mat3:
    return court.from_lists((np.diag([1 / size[1], 1 / size[0], 1.0]) @ h).tolist())


def detect(image: Arr, seed: int = 0) -> CourtGuess | None:
    """The court in an RGB frame (H, W, 3, uint8), or None when nothing court-like shows."""
    hh, ww = image.shape[:2]
    rng = np.random.default_rng(seed)
    raw = lines_only(ridges(image) >= RIDGE_MIN)
    diag = math.hypot(hh, ww)
    # The floor is below the horizon: through the vanishing point of the hall's long lines.
    tilted = [ln for ln, _ in hough(raw, rng).peaks(VP_LINES) if abs(ln[1]) < VP_MAX_SIN]
    vp = vanishing_point(tilted, VP_TOL * diag)
    if vp is not None:
        floor_top = float(vp[1]) + 0.01 * hh
        lines = hough(raw, rng, below=floor_top).peaks(3 * SIDE_CANDIDATES + CROSS_CANDIDATES)
        through = [abs(ln[0] * vp[0] + ln[1] * vp[1] + ln[2]) <= VP_TOL * diag for ln, _ in lines]
        fam_a = _distinct_directions([lv for lv, t in zip(lines, through, strict=True) if t])
        fam_b = [lv for lv, t in zip(lines, through, strict=True) if not t]
    else:
        floor_top = TOP_SKIP * hh
        votes = hough(raw, rng, below=floor_top)
        flat_rows = np.abs(np.degrees(votes.thetas) - 90.0) <= FAMILY_DEG
        fam_b = votes.peaks(CROSS_CANDIDATES, flat_rows)
        fam_a = votes.peaks(SIDE_CANDIDATES, ~flat_rows)
    mask = dilate(raw, TOLERANCE_PX)
    tight = dilate(raw, 1)
    samples, which = _model_samples()
    fits: list[tuple[float, Arr, int]] = []
    min_cos = math.cos(math.radians(SIDE_MIN_ANGLE_DEG))
    # Behind the court the side lines meet at the vanishing point; from the side the cross
    # lines do. Both ways are tried; the court's spacing decides.
    for side, cross in ((fam_a, fam_b), (fam_b, fam_a)):
        fallback = [ln for ln, _ in cross[:CROSS_CANDIDATES]]
        for (l1, _), (l2, _) in itertools.combinations(side[:SIDE_CANDIDATES], 2):
            if abs(l1[0] * l2[0] + l1[1] * l2[1]) > min_cos:
                continue
            between = _cross_candidates(l1, l2, tight, floor_top) or fallback
            for m in _match_cross(l1, l2, between, (hh, ww)):
                for x1, x2 in ((0.0, court.WIDTH_M), (court.WIDTH_M, 0.0)):
                    src = [(float(p[0]), float(p[1])) for p in (*m.pts_left, *m.pts_right)]
                    dst = [(x1, y) for y in m.ys] + [(x2, y) for y in m.ys]
                    try:
                        h = _fit(src, dst)
                        to_image = _normalised(h, (hh, ww))
                        if court.mirrored(to_image) or not _plausible(to_image, ww / hh):
                            continue
                    except court.CourtError:
                        continue
                    score, _ = _score(h, mask, samples, which)
                    if math.isfinite(score):
                        fits.append((score, h, m.count))
    if not fits:
        return None
    fits.sort(key=lambda f: -f[0])
    best_score, h, count = fits[0]
    corners = _corners(h, (hh, ww))
    rival = next(
        (
            s
            for s, other, _ in fits[1:]
            if np.abs(_corners(other, (hh, ww)) - corners).max() > DIFFERENT
        ),
        None,
    )
    ambiguous = rival is not None and best_score > 0 and rival >= AMBIGUOUS * best_score
    _, support = _score(h, tight, samples, which)
    try:
        refined = _refine(_refine(h, raw), raw)
        _, support_refined = _score(refined, tight, samples, which)
        if support_refined > support:
            h, support = refined, support_refined
    except court.CourtError:
        pass
    to_image = court.normalise(_normalised(h, (hh, ww)))
    points: dict[str, tuple[float, float]] = {}
    for name, (x, y) in court.LANDMARKS.items():
        try:
            u, v = court.apply(to_image, (x, y))
        except court.CourtError:
            continue
        if 0.0 <= u <= 1.0 and 0.0 <= v <= 1.0:
            points[name] = (round(u, 5), round(v, 5))
    try:
        court.check_spread([court.LANDMARKS[n] for n in points])
    except court.CourtError:
        return None
    return CourtGuess(
        points=points,
        court_to_image=to_image,
        confidence=round(support * (0.5 if ambiguous else 1.0), 4),
        stats={
            "support": round(support, 4),
            "ambiguous": ambiguous,
            "vanishing_point": [round(float(v), 1) for v in vp] if vp is not None else None,
            "cross_lines": count,
            "fits": len(fits),
            "score_px": round(best_score, 1),
            "rival_px": round(rival, 1) if rival is not None else None,
        },
    )
