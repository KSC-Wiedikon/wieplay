"""Find the volleyball court in a hall seen for the first time (PLAN §10.6), version 3.

Version 2 judged one paint colour at a time and lost the court on real floors: it took its
line pixels with a ridge filter that the floor's grain and the lights' reflections pass, and
a texture filter that then dropped the thick near lines with everything around them. This
version finds painted lines as ridges of the image's curvature (a line is curved across,
flat along), fits a camera to them and lets the net decide between courts:

    an empty hall (median frame) ─▶ the centre lines of painted lines, any colour (multi-scale
    Hessian ridges, thinned) and their paint against the floor around them
    ─▶ straight pieces ─▶ the hall's long lines meet in a vanishing point: lines through it
       (side lines) and lines across ─▶ two side lines and two lines across, of one paint,
       given every pair of the court's cross lines (0, 6, 9, 12, 18 m): a court each
    ─▶ a camera that could film it (behind or beside the court, above the floor, a sane
       focal length) ─▶ judged on the picture: each of the court's seven lines on paint of
       one colour, no paint beyond its corners (a bigger court's lines run on)
    ─▶ the best few: a camera with lens distortion fitted to the court's paint, judged again,
       and the net: its two tapes (a metre apart) where this camera puts a net of 2.43 m
       (men) or 2.24 m (women) above the centre line; a court that wants the net elsewhere
       loses; the filming guide's camera (behind the end line, the whole court in the
       picture) is preferred
    ─▶ the best court, how sure (its score and its lead over the next different court)

Sure (`CourtGuess.sure`) means: a high score and a clear lead over every other court the
picture allows. On 21 halls of KSCW H3's games it was sure on 13, all right (PLAN §10.6,
docs/research/court-auto-2026-10.md); a hall whose volleyball court has no paint of its own,
a side view or an unusual camera stays unsure and goes to a coach.

OpenCV, numpy and SciPy's least squares, on the worker (the `court` job): about two seconds
for a 1280x720 picture.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from scipy.optimize import least_squares

from wieplay.core import court
from wieplay.ml import court_detect as v1
from wieplay.ml.court_detect import CourtGuess
from wieplay.ml.features import Arr

# --- line pixels ---------------------------------------------------------------------------------

TOP = 0.15  # the top of the picture (ceiling) has no floor lines
RIDGE_SCALES = (1.0, 1.8, 3.0)  # Gaussian sigma (px): lines ~2 to ~10 px wide at 1280x720
RIDGE_NOISE = 4.0  # a line pixel's curvature, in multiples of the picture's median
RIDGE_LOCAL = 2.0  # ... and of its neighbourhood's (a busy floor raises the bar there)
L_WEIGHT = 0.5  # lightness counts half in a paint's colour (the light varies over a hall)
PAINT_CONTRAST = 40.0  # contrast to the floor at which a pixel's colour is fully trusted
# v2's ridge filter, still what the hall's memory refines a carried court on (worker/court.py)
RIDGE_D = (2, 4, 7)
DENSITY_BOX = 27

# --- lines ---------------------------------------------------------------------------------------

SEGMENT_MIN_PX = 18
VP_TOL_DEG = 1.0  # a piece through the vanishing point points at it within this
LONG_MIN_DEG = 12.0  # pieces steeper than this vote for the vanishing point
LONG_APART_DEG = 0.35  # pieces through the vanishing point this far apart are other lines
LONG_MAX = 16
CROSS_MAX_DEG = 40.0  # lines across are within this of level
CROSS_MAX = 30
CROSS_PER_PAIR = 16
SAME_PAINT_DEG = 18.0  # paint directions further apart are other paints
CROSS_YS = (0.0, 6.0, 9.0, 12.0, 18.0)

# --- judging -------------------------------------------------------------------------------------

SAMPLE_M = 0.25
CHEAP_SAMPLE_M = 0.75
KEEP = 300  # courts judged in full
FINALISTS = 8  # different courts refined with a camera
MAX_STEP_PX = 30.0
LINE_WEIGHTS = np.array([1.5, 1.5, 1.0, 0.6, 0.7, 0.8, 0.7])  # sides, near end, far end, ...
EXTENSION_M = (0.7, 3.0)  # no court paint this far beyond the corners
# Where a camera that films a match can stand (court metres): anywhere around the near half.
CAMERA_X = (-14.0, 23.0)
CAMERA_Y = (-20.0, 9.5)
CAMERA_Z = (0.8, 14.0)
FOCAL = (0.3, 4.0)  # image heights
# The filming guide's camera: centred behind the end line, 2.5 to 3 m high, the court in view.
GUIDE_X = 4.0  # m off the court's middle before a penalty
GUIDE_Y = (-9.0, -1.5)
GUIDE_Z = 6.0
NET_TOPS = (2.43, 2.24)  # men, women (m)
NET_HEIGHTS = np.round(np.arange(1.6, 3.41, 0.02), 2)
NET_FIT_M = 0.13  # the net's tapes found within this of its height
NET_OFF_M = 0.16
NET_TAPE_GAP_M = 0.95  # top of the top tape to the top of the bottom tape
NEAR_SAME_PX = 15.0  # finalists whose landmarks in the picture are this close: one court

# --- how sure ------------------------------------------------------------------------------------

SURE_SCORE = 0.7
SURE_LEAD = 0.15  # over the next different court
SURE_PRIOR = 0.1  # the filming guide's camera penalty at most
SURE_CONFIDENCE = 0.8  # a sure court's confidence is at least this; an unsure one's below


@dataclass
class Picture:
    """The line evidence of one picture."""

    size: tuple[int, int]  # (height, width)
    mask: Arr  # bool: centre pixels of painted lines
    dist: Arr  # px to the nearest line pixel
    near_dir: Arr  # (H, W, 3): paint direction (Lab against the floor) of that pixel
    near_conf: Arr  # (H, W): how much its colour says (faint lines are mixed with the floor)
    tol: Arr  # (H, W): px a model line may miss its paint by (lens distortion grows outwards)
    bright: Arr  # bool: thin bright horizontal lines (the net's tapes)
    segments: Arr  # (n, 4) straight pieces x1 y1 x2 y2
    seg_len: Arr
    seg_dir: Arr  # (n, 3) paint direction
    seg_conf: Arr


@dataclass
class Line:
    line: Arr  # a, b, c with (a, b) a unit normal
    support: float  # px of pieces on it
    paint: Arr  # unit paint direction
    conf: float
    angle: float  # seen from the vanishing point (long lines), or its slope (cross lines)


# --- line pixels ---------------------------------------------------------------------------------


def lab(image: Arr) -> Arr:
    """OpenCV's 8-bit Lab of an RGB picture as floats, a and b centred on 0."""
    out: Arr = cv2.cvtColor(cv2.cvtColor(image, cv2.COLOR_RGB2BGR), cv2.COLOR_BGR2LAB).astype(
        np.float32
    ) - np.array([0, 128, 128], np.float32)
    return out


def line_centres(image: Arr) -> Arr:
    """The centre pixels of painted lines, bright or dark, of any colour: where the picture
    is strongly curved across a line and flat along it (the Hessian's eigenvalues, on the
    Lab channels, at the scale that fits the line's width), thinned to one pixel across."""
    hh, ww = image.shape[:2]
    lab_img = lab(image)
    channels = [lab_img[..., 0] * 0.6, lab_img[..., 1], lab_img[..., 2]]
    best = np.zeros((hh, ww), np.float32)
    nx = np.zeros((hh, ww), np.float32)
    ny = np.zeros((hh, ww), np.float32)
    for s in RIDGE_SCALES:
        for channel in channels:
            g = cv2.GaussianBlur(channel, (0, 0), s)
            dxx = cv2.Sobel(g, cv2.CV_32F, 2, 0, ksize=3)
            dyy = cv2.Sobel(g, cv2.CV_32F, 0, 2, ksize=3)
            dxy = cv2.Sobel(g, cv2.CV_32F, 1, 1, ksize=3)
            disc = np.sqrt((dxx - dyy) ** 2 + 4 * dxy**2)
            l1 = (dxx + dyy + disc) / 2
            l2 = (dxx + dyy - disc) / 2
            first = np.abs(l1) > np.abs(l2)
            big = np.where(first, l1, l2)
            small = np.where(first, l2, l1)
            r = np.maximum((s * s) * (np.abs(big) - np.abs(small)), 0)
            better = r > best
            vx, vy = dxy, big - dxx  # the eigenvector across the line
            n = np.sqrt(vx * vx + vy * vy) + 1e-9
            best = np.where(better, r, best)
            nx = np.where(better, vx / n, nx)
            ny = np.where(better, vy / n, ny)
    best[: int(TOP * hh)] = 0
    # Thin: a maximum across the line (its normal quantised to four directions).
    q = np.rint(((np.degrees(np.arctan2(ny, nx)) + 180) % 180) / 45).astype(int) % 4
    thin = np.zeros((hh, ww), bool)
    for k, (dx, dy) in enumerate(((1, 0), (1, 1), (0, 1), (-1, 1))):
        shift = np.array([[1, 0, -dx], [0, 1, -dy]], np.float32)
        fwd = cv2.warpAffine(best, shift, (ww, hh), flags=cv2.INTER_NEAREST)
        shift = np.array([[1, 0, dx], [0, 1, dy]], np.float32)
        back = cv2.warpAffine(best, shift, (ww, hh), flags=cv2.INTER_NEAREST)
        thin |= (q == k) & (best >= fwd) & (best >= back)
    noise = float(np.median(best[int(TOP * hh) :])) + 1e-3
    local = cv2.medianBlur(np.clip(best, 0, 255).astype(np.uint8), 31).astype(np.float32)
    out: Arr = thin & (best > RIDGE_NOISE * noise) & (best > RIDGE_LOCAL * local)
    return out


def ridges(image: Arr) -> Arr:
    """v2's line strength (grey levels): a pixel that differs from the pixels a few px to
    both sides. The hall's memory still refines a carried court on it."""
    img = image.astype(np.float32)
    h, w = img.shape[:2]
    best = np.zeros((h, w), dtype=np.float32)
    for d in RIDGE_D:
        for axis in (1, 0):
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
    """v2's texture filter: drops pixels where most pixels around are line pixels."""
    k = DENSITY_BOX
    pad = np.pad(mask.astype(np.int32), ((k // 2 + 1, k // 2), (k // 2 + 1, k // 2)))
    cum = pad.cumsum(0).cumsum(1)
    box = cum[k:, k:] - cum[:-k, k:] - cum[k:, :-k] + cum[:-k, :-k]
    out: Arr = mask & (box < v1.DENSITY_MAX * k * k)
    return out


def bright_lines(image: Arr) -> Arr:
    """Thin bright horizontal lines: the net's tapes against the wall or the floor behind."""
    g = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY).astype(np.float32)
    best = np.zeros_like(g)
    for d in (1, 2, 3):
        best = np.maximum(best, np.minimum(g - np.roll(g, d, 0), g - np.roll(g, -d, 0)))
    out: Arr = best > 10
    return out


def picture(image: Arr) -> Picture:
    """The line evidence of an RGB picture (H, W, 3, uint8)."""
    hh, ww = image.shape[:2]
    mask = line_centres(image)
    delta = lab(cv2.GaussianBlur(image, (0, 0), 0.8)) - lab(cv2.medianBlur(image, 31))
    delta[..., 0] *= L_WEIGHT
    mag = np.linalg.norm(delta, axis=2)
    paint = delta / np.maximum(mag, 1e-6)[..., None]
    conf = np.clip(mag / PAINT_CONTRAST, 0, 1)
    dist, labels = cv2.distanceTransformWithLabels(
        (~mask).astype(np.uint8), cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL
    )
    ys, xs = np.nonzero(mask)
    near_y = np.zeros(int(labels.max()) + 1, np.int32)
    near_x = np.zeros(int(labels.max()) + 1, np.int32)
    near_y[labels[ys, xs]] = ys
    near_x[labels[ys, xs]] = xs
    ny, nx = near_y[labels], near_x[labels]
    yy, xx = np.mgrid[0:hh, 0:ww]
    r2 = (((xx - ww / 2) ** 2 + (yy - hh / 2) ** 2) / (ww / 2) ** 2).astype(np.float32)
    pic = Picture(
        size=(hh, ww),
        mask=mask,
        dist=dist,
        near_dir=paint[ny, nx],
        near_conf=conf[ny, nx],
        tol=2.0 + 5.0 * r2,
        bright=bright_lines(image),
        segments=np.zeros((0, 4)),
        seg_len=np.zeros(0),
        seg_dir=np.zeros((0, 3)),
        seg_conf=np.zeros(0),
    )
    _segments(pic)
    return pic


def _segments(pic: Picture) -> None:
    """Straight pieces of the line pixels, each with its paint."""
    raw = cv2.HoughLinesP(
        pic.mask.astype(np.uint8) * 255,
        1,
        np.pi / 720,
        threshold=20,
        minLineLength=SEGMENT_MIN_PX,
        maxLineGap=3,
    )
    segs: list[tuple[float, float, float, float]] = []
    lens: list[float] = []
    dirs: list[Arr] = []
    confs: list[float] = []
    for x1, y1, x2, y2 in raw[:, 0, :].astype(float) if raw is not None else []:
        length = math.hypot(x2 - x1, y2 - y1)
        t = np.linspace(0.1, 0.9, max(4, int(length / 2)))
        xs = np.rint(x1 + t * (x2 - x1)).astype(int)
        ys = np.rint(y1 + t * (y2 - y1)).astype(int)
        on = pic.dist[ys, xs] <= 1.5
        if on.mean() < 0.6:
            continue
        c = pic.near_conf[ys[on], xs[on]]
        v = (pic.near_dir[ys[on], xs[on]] * c[:, None]).sum(0)
        segs.append((x1, y1, x2, y2))
        lens.append(length)
        dirs.append(v / (np.linalg.norm(v) + 1e-9))
        confs.append(float(np.median(c)))
    pic.segments = np.array(segs, float).reshape(-1, 4)
    pic.seg_len = np.array(lens, float)
    pic.seg_dir = np.array(dirs, float).reshape(-1, 3)
    pic.seg_conf = np.array(confs, float)


# --- lines ---------------------------------------------------------------------------------------


def _lines_of(segs: Arr) -> Arr:
    p1 = np.c_[segs[:, 0:2], np.ones(len(segs))]
    p2 = np.c_[segs[:, 2:4], np.ones(len(segs))]
    lines: Arr = np.cross(p1, p2)
    return lines / np.linalg.norm(lines[:, :2], axis=1, keepdims=True)


def vanishing_point(pic: Picture) -> tuple[Arr | None, Arr]:
    """Where the hall's long lines meet (the side lines, the walls' feet), and the pieces on
    lines through it; None without one."""
    s, length = pic.segments, pic.seg_len
    if not len(s):
        return None, np.zeros(0, bool)
    ang = np.degrees(np.arctan2(s[:, 3] - s[:, 1], s[:, 2] - s[:, 0])) % 180
    steep = (np.abs(ang - 90) < 90 - LONG_MIN_DEG) & (length >= 25)
    idx = np.flatnonzero(steep)
    if len(idx) < 4:
        return None, steep
    lines = _lines_of(s)
    mids = (s[:, 0:2] + s[:, 2:4]) / 2
    dirs = (s[:, 2:4] - s[:, 0:2]) / length[:, None]
    cos_tol = math.cos(math.radians(VP_TOL_DEG))
    hh, ww = pic.size
    best: tuple[Arr | None, float] = (None, -1.0)
    for i, j in itertools.combinations(idx[np.argsort(-length[idx])][:70], 2):
        p = np.cross(lines[i], lines[j])
        if abs(p[2]) < 1e-9:
            continue
        p = p[:2] / p[2]
        if not (-2 * ww < p[0] < 3 * ww and -4 * hh < p[1] < 0.75 * hh):
            continue
        to = p[None, :] - mids[idx]
        to /= np.linalg.norm(to, axis=1, keepdims=True) + 1e-9
        score = float((length[idx] * (np.abs((to * dirs[idx]).sum(1)) > cos_tol)).sum())
        if score > best[1]:
            best = (p, score)
    vp = best[0]
    if vp is None:
        return None, steep
    for _ in range(2):
        to = vp[None, :] - mids
        to /= np.linalg.norm(to, axis=1, keepdims=True) + 1e-9
        through = (np.abs((to * dirs).sum(1)) > cos_tol) & steep
        if through.sum() >= 3:
            a, w = lines[through], length[through]
            vp = np.linalg.lstsq(a[:, :2] * w[:, None], -(a[:, 2] * w), rcond=None)[0]
    to = vp[None, :] - mids
    to /= np.linalg.norm(to, axis=1, keepdims=True) + 1e-9
    through = (np.abs((to * dirs).sum(1)) > math.cos(math.radians(1.5 * VP_TOL_DEG))) & steep
    return vp, through


def _paint_of(pic: Picture, idx: Arr) -> tuple[Arr, float]:
    w = pic.seg_len[idx] * pic.seg_conf[idx]
    v = (pic.seg_dir[idx] * w[:, None]).sum(0)
    return v / (np.linalg.norm(v) + 1e-9), float(
        np.average(pic.seg_conf[idx], weights=pic.seg_len[idx])
    )


def long_lines(pic: Picture, vp: Arr, through: Arr) -> list[Line]:
    """Lines through the vanishing point: its pieces grouped by their direction from it."""
    idx_all = np.flatnonzero(through)
    if not len(idx_all):
        return []
    s, length = pic.segments[idx_all], pic.seg_len[idx_all]
    mids = (s[:, 0:2] + s[:, 2:4]) / 2
    ang = np.degrees(np.arctan2(mids[:, 1] - vp[1], mids[:, 0] - vp[0]))
    groups: list[list[int]] = []
    for k in np.argsort(ang):
        if groups and ang[k] - ang[groups[-1][-1]] < LONG_APART_DEG:
            groups[-1].append(int(k))
        else:
            groups.append([int(k)])
    out: list[Line] = []
    for g in groups:
        ga = np.array(g)
        if length[ga].sum() < 40:
            continue
        a = float(np.average(ang[ga], weights=length[ga]))
        n = np.array([-math.sin(math.radians(a)), math.cos(math.radians(a))])
        paint, conf = _paint_of(pic, idx_all[ga])
        out.append(Line(np.array([n[0], n[1], -n @ vp]), float(length[ga].sum()), paint, conf, a))
    out.sort(key=lambda ln: -ln.support)
    return out[:LONG_MAX]


def cross_lines(pic: Picture, vp: Arr | None) -> list[Line]:
    """Lines within CROSS_MAX_DEG of level, not through the vanishing point: collinear
    pieces joined, the strongest first."""
    s, length = pic.segments, pic.seg_len
    ang = (np.degrees(np.arctan2(s[:, 3] - s[:, 1], s[:, 2] - s[:, 0])) + 90) % 180 - 90
    idx = np.flatnonzero(np.abs(ang) < CROSS_MAX_DEG)
    lines = _lines_of(s) if len(s) else np.zeros((0, 3))
    used = np.zeros(len(s), bool)
    out: list[Line] = []
    for i in idx[np.argsort(-length[idx])]:
        if used[i]:
            continue
        e1 = np.abs(np.c_[s[idx, 0:2], np.ones(len(idx))] @ lines[i])
        e2 = np.abs(np.c_[s[idx, 2:4], np.ones(len(idx))] @ lines[i])
        members = idx[(e1 < 3) & (e2 < 3) & ~used[idx]]
        used[members] = True
        if length[members].sum() < 30:
            continue
        pts = np.r_[s[members, 0:2], s[members, 2:4]]
        w = np.r_[length[members], length[members]]
        c = np.average(pts, axis=0, weights=w)
        d = np.linalg.eigh(((pts - c).T * w) @ (pts - c))[1][:, -1]
        n = np.array([-d[1], d[0]])
        line = np.array([n[0], n[1], -n @ c])
        if vp is not None and abs(line @ np.array([vp[0], vp[1], 1.0])) < 0.015 * pic.size[1]:
            continue
        paint, conf = _paint_of(pic, members)
        out.append(
            Line(
                line,
                float(length[members].sum()),
                paint,
                conf,
                math.degrees(math.atan2(d[1], d[0])),
            )
        )
    out.sort(key=lambda ln: -ln.support)
    return out[:CROSS_MAX]


def same_paint(a: Line, b: Line) -> bool:
    """False only when both lines show their colour clearly and the colours differ."""
    angle = math.degrees(math.acos(float(np.clip(a.paint @ b.paint, -1, 1))))
    return not (min(a.conf, b.conf) > 0.5 and angle > 1.6 * SAME_PAINT_DEG)


# --- courts --------------------------------------------------------------------------------------


def hypotheses(pic: Picture, longs: list[Line], crosses: list[Line]) -> tuple[Arr, list[Any]]:
    """Court metres → pixels homographies (N, 3, 3): every pair of side lines of one paint,
    every pair of lines across them of that paint, as every pair of the court's cross lines."""
    hh, ww = pic.size
    out: list[Arr] = []
    meta: list[tuple[float, float]] = []
    pairs_y = list(itertools.combinations(CROSS_YS, 2))
    for a, b in itertools.combinations(longs, 2):
        if abs(a.angle - b.angle) < 5 or not same_paint(a, b):
            continue
        left, right = (a, b) if a.angle > b.angle else (b, a)  # seen from the vanishing point
        cands = [c for c in crosses if same_paint(c, left) and same_paint(c, right)]
        for c1, c2 in itertools.combinations(cands[:CROSS_PER_PAIR], 2):
            pts = []
            for c in (c1, c2):
                for side in (left, right):
                    p = np.cross(c.line, side.line)
                    pts.append(p[:2] / p[2] if abs(p[2]) > 1e-9 else np.array([1e9, 1e9]))
            quad = np.array(pts)
            if np.any(np.abs(quad[:, 0] - ww / 2) > 2.5 * ww) or np.any(
                np.abs(quad[:, 1] - hh / 2) > 2.5 * hh
            ):
                continue
            if quad[0:2, 1].mean() < quad[2:4, 1].mean():  # the near line is the lower one
                quad = quad[[2, 3, 0, 1]]
            for ya, yb in pairs_y:
                src = np.array([(0, ya), (9, ya), (0, yb), (9, yb)], np.float32)
                out.append(np.asarray(cv2.getPerspectiveTransform(src, quad.astype(np.float32))))
                meta.append((ya, yb))
    if not out:
        return np.zeros((0, 3, 3)), []
    return np.array(out), meta


def project_many(hs: Arr, pts: Arr) -> tuple[Arr, Arr]:
    """Homographies (N, 3, 3) applied to points (S, 2): pixels (N, S, 2) and depth (N, S)."""
    q = np.einsum("nij,sj->nsi", hs, np.c_[pts, np.ones(len(pts))])
    with np.errstate(divide="ignore", invalid="ignore"):
        return q[..., :2] / q[..., 2:3], q[..., 2]


def cameras(hs: Arr, size: tuple[int, int]) -> tuple[Arr, Arr]:
    """The camera centre (N, 3, court metres) and focal length (N, image heights) of each
    court → pixels homography (principal point at the centre, square pixels; court.py's
    camera_from_homography, vectorised); NaN where no camera fits."""
    hh, ww = size
    k_inv = np.array([[1 / hh, 0, -ww / 2 / hh], [0, 1 / hh, -0.5], [0, 0, 1]])
    g = k_inv[None] @ hs
    h1, h2, h3 = g[:, :, 0], g[:, :, 1], g[:, :, 2]
    a1 = h1[:, 0] * h2[:, 0] + h1[:, 1] * h2[:, 1]
    b1 = h1[:, 2] * h2[:, 2]
    a2 = h1[:, 0] ** 2 + h1[:, 1] ** 2 - h2[:, 0] ** 2 - h2[:, 1] ** 2
    b2 = h1[:, 2] ** 2 - h2[:, 2] ** 2
    with np.errstate(divide="ignore", invalid="ignore"):
        w = -(a1 * b1 + a2 * b2) / (a1 * a1 + a2 * a2)
        f = 1 / np.sqrt(w)
        c1 = np.c_[h1[:, 0] / f, h1[:, 1] / f, h1[:, 2]]
        c2 = np.c_[h2[:, 0] / f, h2[:, 1] / f, h2[:, 2]]
        c3 = np.c_[h3[:, 0] / f, h3[:, 1] / f, h3[:, 2]]
        lam = 2 / (np.linalg.norm(c1, axis=1) + np.linalg.norm(c2, axis=1))
        depth = c1[:, 2] * court.WIDTH_M / 2 + c2[:, 2] * court.LENGTH_M / 2 + c3[:, 2]
        lam = np.where(lam * depth < 0, -lam, lam)
        r1 = lam[:, None] * c1
        r2 = lam[:, None] * c2
        t = lam[:, None] * c3
        r1 /= np.linalg.norm(r1, axis=1, keepdims=True)
        r2 = r2 - (r1 * r2).sum(1, keepdims=True) * r1
        r2 /= np.linalg.norm(r2, axis=1, keepdims=True)
        rot = np.stack([r1, r2, np.cross(r1, r2)], axis=2)
        centre: Arr = -np.einsum("nji,nj->ni", rot, t)
    bad = ~(w > 0) | ~np.isfinite(centre).all(1)
    f = np.where(bad, np.nan, f)
    centre[bad] = np.nan
    return centre, f


def plausible(hs: Arr, size: tuple[int, int]) -> Arr:
    """bool (N,): a camera that films a match could see the court so."""
    c, f = cameras(hs, size)
    with np.errstate(invalid="ignore"):
        out: Arr = (
            (c[:, 0] >= CAMERA_X[0])
            & (c[:, 0] <= CAMERA_X[1])
            & (c[:, 1] >= CAMERA_Y[0])
            & (c[:, 1] <= CAMERA_Y[1])
            & (c[:, 2] >= CAMERA_Z[0])
            & (c[:, 2] <= CAMERA_Z[1])
            & (f >= FOCAL[0])
            & (f <= FOCAL[1])
        )
    return out


def _samples(step: float) -> tuple[Arr, Arr]:
    pts: list[tuple[float, float]] = []
    which: list[int] = []
    for i, (x1, y1, x2, y2) in enumerate(court.LINES):
        n = max(2, round(math.hypot(x2 - x1, y2 - y1) / step) + 1)
        for t in np.linspace(0, 1, n):
            pts.append((x1 + t * (x2 - x1), y1 + t * (y2 - y1)))
            which.append(i)
    return np.array(pts), np.array(which)


def _extensions(step: float = SAMPLE_M) -> Arr:
    lo, hi = EXTENSION_M
    along = np.r_[np.arange(-hi, -lo, step), court.LENGTH_M + np.arange(lo, hi, step)]
    across = np.r_[np.arange(-hi, -lo, step), court.WIDTH_M + np.arange(lo, hi, step)]
    out = [(x, y) for x in (0.0, court.WIDTH_M) for y in along]
    out += [(x, y) for y in (0.0, court.LENGTH_M) for x in across]
    return np.array(out)


MODEL, MODEL_LINE = _samples(SAMPLE_M)
CHEAP, CHEAP_LINE = _samples(CHEAP_SAMPLE_M)
EXTENSIONS = _extensions()
MODEL_3D = np.c_[MODEL, np.zeros(len(MODEL))]


def _hits(pic: Picture, p: Arr, ahead: Arr) -> tuple[Arr, Arr, Arr, Arr]:
    hh, ww = pic.size
    u, v = np.rint(p[..., 0]), np.rint(p[..., 1])
    vis = ahead & (u >= 0) & (u <= ww - 1) & (v >= TOP * hh) & (v <= hh - 1)
    ui = np.where(vis, u, 0).astype(np.int64)
    vi = np.where(vis, v, 0).astype(np.int64)
    return vis, vis & (pic.dist[vi, ui] <= pic.tol[vi, ui]), ui, vi


def _step_px(p: Arr, which: Arr, vis: Arr) -> Arr:
    """The picture length each sample stands for (px, capped near the camera)."""
    step = np.hypot(*np.moveaxis(np.diff(p, axis=-2), -1, 0))
    step = np.where((which[1:] == which[:-1])[None], step, 0.0)
    step = np.concatenate([step, np.zeros((*step.shape[:-1], 1))], axis=-1)
    out: Arr = np.minimum(np.nan_to_num(step), MAX_STEP_PX) * vis
    return out


def cheap_scores(pic: Picture, hs: Arr) -> Arr:
    """Share of the courts' visible lines near line pixels (any paint), for many courts."""
    p, depth = project_many(hs, CHEAP)
    vis, hit, _, _ = _hits(pic, p, depth * depth[:, :1] > 0)
    length = _step_px(p, CHEAP_LINE, vis)
    total = length.sum(1)
    with np.errstate(invalid="ignore", divide="ignore"):
        share = np.where(total > 0.8 * pic.size[1], (length * hit).sum(1) / total, 0.0)
    out: Arr = share * np.minimum(1, total / (2 * pic.size[1]))
    return out


@dataclass
class Judged:
    """A court judged on a picture."""

    lines: Arr  # per model line, its share on the court's paint (NaN: not in the picture)
    colour: float  # how much the court's lines agree in paint
    extension: float  # court paint beyond its corners
    paint: Arr  # the court's paint direction


def _agreement(dirs: Arr, paint: Arr) -> Arr:
    """How much paint directions (n, 3) agree with the court's (1: the same, 0: another)."""
    angle = np.degrees(np.arccos(np.clip(dirs @ paint, -1, 1)))
    out: Arr = np.exp(-((angle / SAME_PAINT_DEG) ** 2) / 2)
    return out


def judge(pic: Picture, h: Arr) -> Judged:
    """Each of the court's lines on paint of one colour (the court's: its lines' confident
    pixels), and paint of that colour where a volleyball court has none."""
    p, depth = project_many(h[None], MODEL)
    vis, hit, ui, vi = (a[0] for a in _hits(pic, p, depth * depth[:, :1] > 0))
    length = _step_px(p, MODEL_LINE, vis[None])[0]
    dirs = pic.near_dir[vi, ui]
    conf = pic.near_conf[vi, ui]
    v = (dirs * (conf * hit * length)[:, None]).sum(0)
    paint = v / (np.linalg.norm(v) + 1e-9)
    agree = _agreement(dirs, paint)
    good = hit * (conf * agree + (1 - conf) * 0.6)
    per = np.full(len(court.LINES), np.nan)
    for i in range(len(court.LINES)):
        m = i == MODEL_LINE
        total = length[m].sum()
        if total > 10:
            per[i] = (good[m] * length[m]).sum() / total
    pe, de = project_many(h[None], EXTENSIONS)
    vis_e, hit_e, ue, ve = (a[0] for a in _hits(pic, pe, de * depth[:, :1] > 0))
    if vis_e.sum() > 5:
        ag = _agreement(pic.near_dir[ve, ue], paint)
        extension = float((hit_e * pic.near_conf[ve, ue] * ag)[vis_e].mean())
    else:
        extension = 0.0
    w = conf * hit * length
    colour = float((w * agree).sum() / max(1e-6, w.sum()))
    return Judged(per, colour, extension, paint)


def combine(lines: Arr, colour: float, extension: float, net: float = 0.0) -> float:
    """One number for a court: its lines on paint (a missing line costs most), the paint's
    agreement, no paint beyond its corners, the net where it says."""
    vis = ~np.isnan(lines)
    if vis.sum() < 4:
        return -9.0
    w = LINE_WEIGHTS[vis]
    on = float((w * np.log(0.15 + lines[vis])).sum() / w.sum())
    return on + 0.04 * float(vis.sum()) - extension + 0.3 * colour + 0.6 * net


# --- the camera, with lens distortion ------------------------------------------------------------


@dataclass
class Camera:
    """A pinhole camera (principal point at the centre, square pixels) with one radial
    distortion term: rotation (Rodrigues), centre (court metres), focal length (px)."""

    rvec: Arr
    centre: Arr
    focal: float
    k1: float
    size: tuple[int, int]

    def params(self) -> Arr:
        out: Arr = np.r_[self.rvec, self.centre, math.log(self.focal), self.k1]
        return out

    @classmethod
    def of(cls, p: Arr, size: tuple[int, int]) -> Camera:
        rvec, centre = np.asarray(p[0:3], float), np.asarray(p[3:6], float)
        return cls(rvec, centre, math.exp(p[6]), float(p[7]), size)

    def project(self, pts: Arr) -> Arr:
        """Court points (N, 3, metres) → pixels (N, 2); NaN behind the camera."""
        rot = cv2.Rodrigues(self.rvec)[0]
        cam = (np.asarray(pts, float) - self.centre) @ rot.T
        z = cam[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            x, y = cam[:, 0] / z, cam[:, 1] / z
        hh, ww = self.size
        d = 1 + self.k1 * (x * x + y * y) * (self.focal / hh) ** 2
        out: Arr = np.c_[ww / 2 + self.focal * x * d, hh / 2 + self.focal * y * d]
        out[~(z > 0)] = np.nan
        return out


def camera_of(h: Arr, size: tuple[int, int]) -> Camera | None:
    """The distortion-free camera that sees the floor as h does (least squares on a grid of
    the court: h itself need not be exactly a camera's)."""
    hh, ww = size
    try:
        to_image = court.normalise(v1._normalised(h, size))
    except court.CourtError:
        return None
    cam = court.camera_from_homography(to_image, ww / hh)
    if cam is None:
        return None
    rt = np.array(cam.rays) @ np.diag([cam.focal, cam.focal, 1.0])
    first = Camera(cv2.Rodrigues(rt.T)[0].ravel(), np.array(cam.center), cam.focal * hh, 0.0, size)
    gx, gy = np.meshgrid(np.linspace(0, court.WIDTH_M, 4), np.linspace(0, court.LENGTH_M, 7))
    grid = np.c_[gx.ravel(), gy.ravel()]
    target = project_many(h[None], grid)[0][0]
    grid3 = np.c_[grid, np.zeros(len(grid))]

    def residuals(q: Arr) -> Arr:
        r = (Camera.of(np.r_[q, 0.0], size).project(grid3) - target).ravel()
        out: Arr = np.where(np.isfinite(r), r, 100.0)
        return out

    fit = least_squares(residuals, first.params()[:7], max_nfev=200)
    return Camera.of(np.r_[fit.x, 0.0], size)


def paint_mask(pic: Picture, paint: Arr) -> Arr:
    """Line pixels of the court's paint (or too faint to tell)."""
    angle = np.degrees(np.arccos(np.clip(pic.near_dir @ paint, -1, 1)))
    out: Arr = (pic.dist <= 0.5) & ((angle < 1.6 * SAME_PAINT_DEG) | (pic.near_conf < 0.35))
    return out


def refine(
    pic: Picture, cam: Camera, paint: Arr, radii: tuple[float, ...] = (10, 6, 4, 3)
) -> Camera | None:
    """The camera (and its lens) fitted to the court's paint: each model line's samples
    pulled to the nearest paint pixel within a shrinking radius (robust least squares)."""
    m = paint_mask(pic, paint)
    ys, xs = np.nonzero(m)
    if len(ys) < 50:
        return None
    dist, labels = cv2.distanceTransformWithLabels(
        (~m).astype(np.uint8), cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL
    )
    nearest = np.zeros((int(labels.max()) + 1, 2))
    nearest[labels[ys, xs]] = np.c_[xs, ys]
    hh, ww = pic.size
    p = cam.params()
    for r in radii:
        pts = Camera.of(p, pic.size).project(MODEL_3D)
        ok = (
            np.all(np.isfinite(pts), 1)
            & (pts[:, 0] >= 0)
            & (pts[:, 0] < ww - 1)
            & (pts[:, 1] >= TOP * hh)
            & (pts[:, 1] < hh - 1)
        )
        ui = np.rint(pts[ok, 0]).astype(int)
        vi = np.rint(pts[ok, 1]).astype(int)
        near = dist[vi, ui] <= r
        idx = np.flatnonzero(ok)[near]
        if len(idx) < 30:
            return None
        target = nearest[labels[vi[near], ui[near]]]
        model = MODEL_3D[idx]

        def residuals(q: Arr, model: Arr = model, target: Arr = target) -> Arr:
            rr = (Camera.of(q, pic.size).project(model) - target).ravel()
            out: Arr = np.r_[np.where(np.isfinite(rr), rr, 50.0), 20.0 * q[7]]  # k1 pulled to 0
            return out

        p = least_squares(residuals, p, loss="huber", f_scale=1.5, max_nfev=80).x
    out = Camera.of(p, pic.size)
    return out if -0.5 < out.k1 < 0.2 else None


def judge_camera(pic: Picture, cam: Camera, paint: Arr) -> Arr:
    """Per model line, its share on the court's paint (2 px), seen through the camera."""
    hh, ww = pic.size
    p = cam.project(MODEL_3D)
    ok = (
        np.all(np.isfinite(p), 1)
        & (p[:, 0] >= 0)
        & (p[:, 0] <= ww - 1)
        & (p[:, 1] >= TOP * hh)
        & (p[:, 1] <= hh - 1)
    )
    ui = np.clip(np.rint(np.where(ok, p[:, 0], 0)), 0, ww - 1).astype(int)
    vi = np.clip(np.rint(np.where(ok, p[:, 1], 0)), 0, hh - 1).astype(int)
    near = cv2.dilate(paint_mask(pic, paint).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0
    hit = ok & near[vi, ui]
    length = _step_px(p[None], MODEL_LINE, ok[None])[0]
    per = np.full(len(court.LINES), np.nan)
    for i in range(len(court.LINES)):
        m = i == MODEL_LINE
        total = length[m].sum()
        if total > 10:
            per[i] = (hit[m] * length[m]).sum() / total
    return per


def net_support(pic: Picture, cam: Camera) -> Arr:
    """Per height of NET_HEIGHTS: the share of the net's two tapes (the top one at that
    height above the centre line, the bottom one NET_TAPE_GAP_M lower; the weaker of the two)
    on thin bright lines, seen through the camera."""
    hh, ww = pic.size
    xs = np.linspace(0.3, court.WIDTH_M - 0.3, 50)
    out = np.zeros(len(NET_HEIGHTS))
    for k, h in enumerate(NET_HEIGHTS):
        shares = []
        for z in (h, h - NET_TAPE_GAP_M):
            p = cam.project(np.c_[xs, np.full(len(xs), court.NET_Y), np.full(len(xs), z)])
            if not np.all(np.isfinite(p)):
                shares.append(0.0)
                continue
            ui = np.rint(p[:, 0]).astype(int)
            vi = np.rint(p[:, 1]).astype(int)
            ok = (ui >= 0) & (ui < ww) & (vi >= 1) & (vi < hh - 1)
            if ok.mean() < 0.6:
                shares.append(0.0)
                continue
            u, v = ui[ok], vi[ok]
            on = pic.bright[v, u] | pic.bright[v - 1, u] | pic.bright[v + 1, u]
            shares.append(float(on.sum()) / len(xs))
        out[k] = min(shares)
    return out


def guide_penalty(cam: Camera, landmarks: dict[str, Arr]) -> tuple[float, float]:
    """The filming guide's camera: centred behind the end line, not too high (the camera's
    part), and the whole court in the picture (a court whose corners are out of it is less
    likely: the frame's part)."""
    x, y, z = (float(v) for v in cam.centre)
    hh, ww = cam.size
    camera = (
        0.04 * max(0.0, GUIDE_Y[0] - y)
        + 0.06 * max(0.0, y - GUIDE_Y[1])
        + 0.05 * max(0.0, z - GUIDE_Z)
        + 0.04 * max(0.0, abs(x - court.WIDTH_M / 2) - GUIDE_X)
    )
    frame = 0.0
    corners = (("near_left", 0.06), ("near_right", 0.06), ("far_left", 0.1), ("far_right", 0.1))
    for name, weight in corners:
        u, v = landmarks[name]
        if not (np.isfinite(u) and -5 <= u <= ww + 5 and 0 <= v <= hh + 5):
            frame += weight
    return camera, frame


def landmarks_of(cam: Camera) -> dict[str, Arr]:
    names = list(court.LANDMARKS)
    pts = cam.project(np.array([(*court.LANDMARKS[n], 0.0) for n in names]))
    return {n: pts[k] for k, n in enumerate(names)}


# --- the finder ----------------------------------------------------------------------------------


@dataclass
class Finalist:
    h: Arr
    meta: tuple[float, float]
    judged: Judged
    score: float = 0.0
    cam: Camera | None = None
    lines: Arr = field(default_factory=lambda: np.full(7, np.nan))
    net: float = 0.0
    net_height: float = 0.0
    net_best: float = 0.0
    net_off: bool = False
    penalty: float = 0.0  # the filming guide's, camera and frame
    camera_penalty: float = 0.0


def _same_court(a: Arr, b: Arr, size: tuple[int, int], tol: float = 0.03) -> bool:
    pts = np.array(
        [court.LANDMARKS[n] for n in ("near_left", "near_right", "net_left", "net_right")]
    )
    pa, pb = project_many(a[None], pts)[0][0], project_many(b[None], pts)[0][0]
    d = np.abs(pa - pb) / np.array([size[1], size[0]])
    return bool(np.all(np.isfinite(d)) and d.max() <= tol)


def _apart_px(a: dict[str, Arr], b: dict[str, Arr], size: tuple[int, int]) -> float:
    """How far apart two courts are in the picture: their landmarks that show in both (px);
    a corner far out of the frame moves a lot for a court that hardly moves in it."""
    hh, ww = size

    def inside(p: Arr) -> bool:
        return bool(np.all(np.isfinite(p))) and -0.05 * ww <= p[0] <= 1.05 * ww and 0 <= p[1] <= hh

    d = [float(np.hypot(*(a[n] - b[n]))) for n in a if inside(a[n]) and inside(b[n])]
    return max(d) if d else math.inf


def finalists(
    pic: Picture, net_tops: tuple[float, ...] = NET_TOPS
) -> tuple[list[Finalist], dict[str, Any]]:
    """The best different courts of a picture, refined and judged, best first."""
    vp, through = vanishing_point(pic)
    if vp is None:
        return [], {"vanishing_point": None}
    longs = long_lines(pic, vp, through)
    crosses = cross_lines(pic, vp)
    hs, meta = hypotheses(pic, longs, crosses)
    info: dict[str, Any] = {
        "vanishing_point": [round(float(v), 1) for v in vp],
        "long_lines": len(longs),
        "cross_lines": len(crosses),
        "courts": len(hs),
    }
    if not len(hs):
        return [], info
    ok = plausible(hs, pic.size)
    hs = hs[ok]
    meta = [m for m, k in zip(meta, ok, strict=True) if k]
    info["plausible"] = len(hs)
    if not len(hs):
        return [], info
    cheap = np.concatenate([cheap_scores(pic, hs[i : i + 4000]) for i in range(0, len(hs), 4000)])
    judged: list[Finalist] = []
    for k in np.argsort(-cheap)[:KEEP]:
        try:
            if court.mirrored(court.normalise(v1._normalised(hs[k], pic.size))):
                continue
        except court.CourtError:
            continue
        j = judge(pic, hs[k])
        judged.append(Finalist(hs[k], meta[k], j, combine(j.lines, j.colour, j.extension)))
    judged.sort(key=lambda f: -f.score)
    chosen: list[Finalist] = []
    for f in judged:
        if len(chosen) >= FINALISTS:
            break
        if not any(_same_court(f.h, g.h, pic.size) for g in chosen):
            chosen.append(f)
    out: list[Finalist] = []
    for f in chosen:
        first = camera_of(f.h, pic.size)
        if first is None:
            continue
        cam = refine(pic, first, f.judged.paint) or first
        f.cam = cam
        f.lines = judge_camera(pic, cam, f.judged.paint)
        support = net_support(pic, cam)
        near = np.zeros(len(NET_HEIGHTS), bool)
        for top in net_tops:
            near |= np.abs(NET_HEIGHTS - top) <= NET_FIT_M
        f.net = float(support[near].max())
        best = int(np.argmax(support))
        f.net_height, f.net_best = float(NET_HEIGHTS[best]), float(support[best])
        # The net's tapes are clearly elsewhere than this court wants them.
        f.net_off = (
            f.net_best >= 0.6
            and min(abs(f.net_height - t) for t in net_tops) > NET_OFF_M
            and f.net_best > f.net + 0.2
        )
        f.camera_penalty, frame_penalty = guide_penalty(cam, landmarks_of(cam))
        f.penalty = f.camera_penalty + frame_penalty
        f.score = (
            combine(f.lines, f.judged.colour, f.judged.extension, f.net)
            - (0.3 if f.net_off else 0.0)
            - f.penalty
        )
        out.append(f)
    out.sort(key=lambda f: -f.score)
    return out, info


def detect(
    image: Arr,
    *,
    bounces: Arr | None = None,
    net_tops: tuple[float, ...] = NET_TOPS,
    seed: int = 0,
) -> CourtGuess | None:
    """The court in an RGB frame (H, W, 3, uint8), or None when nothing court-like shows.
    `bounces`: the ball's floor contacts (n, 2, normalised image points), only reported;
    `net_tops`: the net's height(s) to expect (men 2.43 m, women 2.24 m)."""
    del seed  # deterministic
    pic = picture(image)
    found, info = finalists(pic, net_tops)
    if not found:
        return None
    best = found[0]
    assert best.cam is not None
    lm = landmarks_of(best.cam)
    rival = next(
        (
            f
            for f in found[1:]
            if f.cam is not None and _apart_px(landmarks_of(f.cam), lm, pic.size) > NEAR_SAME_PX
        ),
        None,
    )
    hh, ww = pic.size
    points: dict[str, tuple[float, float]] = {}
    for name, (u, v) in lm.items():
        if np.isfinite(u) and np.isfinite(v) and 0 <= u <= ww and 0 <= v <= hh:
            points[name] = (round(float(u) / ww, 5), round(float(v) / hh, 5))
    try:
        court.check_spread([court.LANDMARKS[n] for n in points])
        solution = court.solve(points, ww / hh)
    except court.CourtError:
        return None
    lead = best.score - (rival.score if rival is not None else -1.0)
    sure = (
        best.score >= SURE_SCORE
        and lead >= SURE_LEAD
        and not best.net_off
        and best.camera_penalty <= SURE_PRIOR
    )
    return CourtGuess(
        points=points,
        court_to_image=solution.court_to_image,
        confidence=confidence_of(best.score, lead, sure),
        stats={
            "finder": "v3",
            "score": round(best.score, 4),
            "rival": round(rival.score, 4) if rival is not None else None,
            "lead": round(lead, 4),
            "lines": [None if math.isnan(v) else round(float(v), 3) for v in best.lines],
            "colour": round(best.judged.colour, 3),
            "extension": round(best.judged.extension, 3),
            "net": round(best.net, 3),
            "net_height_m": round(best.net_height, 2),
            "net_best": round(best.net_best, 3),
            "net_off": best.net_off,
            "guide_penalty": round(best.penalty, 3),
            "k1": round(best.cam.k1, 4),
            "camera": [round(float(v), 2) for v in best.cam.centre],
            "focal": round(best.cam.focal / hh, 3),
            "cross_lines": list(best.meta),
            "bounces_inside": _bounces_inside(solution, bounces),
            **info,
        },
        sure=sure,
    )


def confidence_of(score: float, lead: float, sure: bool) -> float:
    """0..1, SURE_CONFIDENCE and above exactly when sure: the score, lowered when another
    court comes close."""
    closeness = min(1.0, max(0.0, lead) / SURE_LEAD)
    base = min(1.0, max(0.0, (score - 0.1) / 0.8)) * (0.6 + 0.4 * closeness)
    if sure:
        above = min(1.0, (score - SURE_SCORE) / 0.4)
        return round(SURE_CONFIDENCE + (1 - SURE_CONFIDENCE) * above, 4)
    return round(min(SURE_CONFIDENCE - 0.01, base * SURE_CONFIDENCE), 4)


def _bounces_inside(solution: court.Solution, bounces: Arr | None) -> float | None:
    """Share of the ball's floor contacts on the court or within 1.5 m of it (noisy: the ball
    tracker takes a hand or a wall for the floor too); for the record."""
    if bounces is None or len(bounces) < 20:
        return None
    inside = 0
    for u, v in np.asarray(bounces, float):
        try:
            x, y = court.apply(solution.image_to_court, (float(u), float(v)))
        except court.CourtError:
            continue
        if -1.5 <= x <= court.WIDTH_M + 1.5 and -1.5 <= y <= court.LENGTH_M + 1.5:
            inside += 1
    return round(inside / len(bounces), 4)
