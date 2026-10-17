"""Find the volleyball court in a hall seen for the first time (PLAN §10.6), version 2.

Multi-sport floors defeat a finder that takes every line: another sport's lines at the
right spacing fit the court by chance (v1, wieplay/ml/court_detect.py, on the Legends hall:
0.29). The court's lines share one colour, so this one looks at one colour at a time and
anchors the court's depth on the net:

    an empty hall (median frame) ─▶ line pixels, sorted by their colour against the floor
    around them (white, black, and the hues) ─▶ per colour: lines through the vanishing
    point (side lines) and lines across them (an end line, else an attack line)
    ─▶ two side lines and a line across fix the court up to one number, the camera's focal
    length (square pixels, principal point at the centre): every focal length gives a
    court (its depth: where the centre and far lines fall) ─▶ judged on that colour's
    line pixels along the whole model, the net's tapes above the centre line (2.43 m or
    2.24 m: the camera is known with the court), the ball's bounces (they land on the
    court) ─▶ the best court, its lines fitted to the pixels, and how sure

OpenCV and numpy, on the worker (the `court` job). Lens distortion is ignored: the court's
lines are fitted where they are straight enough (near the middle of the picture).
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from wieplay.core import court
from wieplay.ml import court_detect as v1
from wieplay.ml.court_detect import CourtGuess
from wieplay.ml.features import Arr

# Line pixels: v1's ridges, a lower bar for the faint far lines of a known colour.
STRONG = v1.RIDGE_MIN
WEAK = 9.0
RIDGE_D = (2, 4, 7)
DENSITY_BOX = 27
BACKGROUND_PX = 21  # median filter: the floor around a line
TOP_SKIP = 0.25  # line pixels in the top of the picture do not decide the colours
# Colours: paint of chroma (|a, b|) below this is white or black, else grouped by hue;
# taken from line pixels this far (Lab) from their floor, the middle of clear lines.
ACHROMATIC_PAINT = 14.0
CLEAR_CONTRAST = 30.0
SAME_DIR_DEG = 18.0  # a faint line pixel lies this close to the way to its paint
HUE_BINS = 36
SAME_HUE_DEG = 25.0  # a line pixel is of a hue within this of its peak
MIN_COLOUR_PX = 700
MAX_COLOURS = 7
# Hypotheses
SIDES = 7
ACROSS = 6
FOCALS = np.exp(np.linspace(math.log(0.4), math.log(3.5), 80))  # image heights: 130° to 28°
NEAR_YS = (0.0, 6.0)  # the line across: the near end line, else the near attack line
# Where a camera filming a match stands (court metres): behind or beside the near half.
CAMERA_X = (-12.0, 21.0)
CAMERA_Y = (-16.0, 9.5)
CAMERA_Z = (0.8, 12.0)
# Judging
NET_TOPS = (2.43, 2.24)  # men, women (m)
NET_BAND = 1.0  # the net's mesh is a metre high
W_NET = 0.5
W_EXPLAINED = 0.6
W_BOUNCE = 0.4
BOUNCE_MARGIN_M = 1.5
MIN_BOUNCES = 20
KEEP = 400  # best hypotheses by lines that are judged on the net and the bounces
AMBIGUOUS = 0.05  # another court scoring within this of the best: not sure
SAME_COURT = 0.03  # near corners and net ends closer than this (normalised): one court
FINALISTS = 6


@dataclass
class Colour:
    name: str  # white | black | hue<degrees>
    centre: tuple[float, float, float]  # the paint's L, a, b (OpenCV's 8-bit Lab, a b centred)
    mask: Arr  # bool line pixels of this colour
    pixels: int


@dataclass
class Fit:
    score: float
    lines: float  # share of the model's visible lines on this colour's pixels
    net: float
    bounces: float | None
    explains: float
    h: Arr  # court metres → pixels
    colour: str
    focal: float
    near_y: float
    family: str = ""
    stats: dict[str, Any] = field(default_factory=dict)


# --- colours ----------------------------------------------------------------------------------


def ridges(image: Arr) -> Arr:
    """v1's line strength, also for lines up to ~12 px wide (near a close camera)."""
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
    """v1's texture filter with a box for wider lines."""
    k = DENSITY_BOX
    pad = np.pad(mask.astype(np.int32), ((k // 2 + 1, k // 2), (k // 2 + 1, k // 2)))
    cum = pad.cumsum(0).cumsum(1)
    box = cum[k:, k:] - cum[:-k, k:] - cum[k:, :-k] + cum[:-k, :-k]
    out: Arr = mask & (box < v1.DENSITY_MAX * k * k)
    return out


def line_colours(image: Arr) -> tuple[list[Colour], Arr]:
    """The hall's line colours (each with its line pixels) and every line pixel.

    The colours themselves come from the middle of clear, wide lines (their pixels are the
    paint's colour): achromatic ones are white (brighter than the floor around them) or
    black (darker), tinted ones the peaks of their hues. A faint line's pixel is paint and
    floor mixed, so it lies between the floor (the neighbourhood's median) and the paint:
    it joins a colour when it lies towards that colour from its floor (within SAME_DIR_DEG,
    in Lab) and not beyond it. Measured against the floor alone, white and yellow both look
    yellow on a blue floor."""
    hh = image.shape[0]
    strength = ridges(image)
    strong = lines_only(strength >= STRONG)
    weak = lines_only(strength >= WEAK)
    weak[: int(TOP_SKIP * hh)] = False
    bgr = cv2.cvtColor(image, cv2.COLOR_RGB2BGR)
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32) - np.array(
        [0, 128, 128], np.float32
    )
    floor = cv2.cvtColor(cv2.medianBlur(bgr, BACKGROUND_PX), cv2.COLOR_BGR2LAB).astype(np.float32)
    floor -= np.array([0, 128, 128], np.float32)
    ys, xs = np.nonzero(strong)
    keep = ys > TOP_SKIP * hh
    paint = lab[ys[keep], xs[keep]]
    contrast = np.linalg.norm(paint - floor[ys[keep], xs[keep]], axis=1)
    clear = contrast >= CLEAR_CONTRAST
    paint, under = paint[clear], floor[ys[keep], xs[keep]][clear]
    centres: list[tuple[str, Arr]] = []
    chroma = np.hypot(paint[:, 1], paint[:, 2])
    grey = chroma < ACHROMATIC_PAINT
    for name, sel in (
        ("white", grey & (paint[:, 0] > under[:, 0])),
        ("black", grey & (paint[:, 0] < under[:, 0])),
    ):
        if sel.sum() >= MIN_COLOUR_PX // 4:
            centres.append((name, np.median(paint[sel], axis=0)))
    hues = np.degrees(np.arctan2(paint[:, 2], paint[:, 1])) % 360
    tinted = ~grey
    if tinted.any():
        counts, _ = np.histogram(hues[tinted], bins=HUE_BINS, range=(0, 360))
        smooth = sum(np.roll(counts, k) * wt for k, wt in ((-1, 0.25), (0, 0.5), (1, 0.25)))
        width = 360 / HUE_BINS
        peaks: list[float] = []
        for i in np.argsort(-smooth):
            if smooth[i] < MIN_COLOUR_PX / 8:
                break
            if smooth[i] < smooth[(i - 1) % HUE_BINS] or smooth[i] < smooth[(i + 1) % HUE_BINS]:
                continue  # not a peak
            peak = (i + 0.5) * width
            if any(_hue_gap(peak, q) < SAME_HUE_DEG for q in peaks):
                continue
            peaks.append(peak)
            sel = tinted & (_hue_gaps(hues, peak) <= SAME_HUE_DEG)
            centres.append((f"hue{round(peak)}", np.median(paint[sel], axis=0)))
    wy, wx = np.nonzero(weak)
    seen, below = lab[wy, wx], floor[wy, wx]
    delta = seen - below
    size = np.linalg.norm(delta, axis=1) + 1e-6
    out: list[Colour] = []
    for name, centre in centres:
        towards = centre[None, :] - below
        reach = np.linalg.norm(towards, axis=1) + 1e-6
        cos = np.einsum("ij,ij->i", delta, towards) / (size * reach)
        sel = (cos >= math.cos(math.radians(SAME_DIR_DEG))) & (size <= 1.3 * reach)
        n = int(sel.sum())
        if n < MIN_COLOUR_PX:
            continue
        mask = np.zeros(weak.shape, bool)
        mask[wy[sel], wx[sel]] = True
        out.append(Colour(name, (float(centre[0]), float(centre[1]), float(centre[2])), mask, n))
    out.sort(key=lambda c: -c.pixels)
    return out[:MAX_COLOURS], strong


def _hue_gaps(hues: Arr, peak: float) -> Arr:
    d = np.abs(hues - peak) % 360
    out: Arr = np.minimum(d, 360 - d)
    return out


def _hue_gap(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


# --- the court from two side lines and one line across ----------------------------------------


def family(
    left: Arr, right: Arr, across: Arr, near_y: float, size: tuple[int, int], focals: Arr
) -> list[tuple[float, Arr]]:
    """Court → pixels homographies with the side lines x = 0 (`left`) and x = 9 (`right`)
    and the line across at y = near_y, one per focal length (image heights).

    With V the side lines' vanishing point, A and B the line across's ends, and U the
    vanishing point of the lines across (on the line across, (w·A + d, w) in homogeneous
    centred pixels with d its direction: w = 0 when it is level with the horizon), the
    homography's columns are a·U, b·V and A - near_y·b·V. Square pixels and the principal
    point at the centre ask U·V = 0 under diag(1/f², 1/f², 1) (the two directions are
    perpendicular) and |a·U| = |b·V| (both are a metre); B fixes a."""
    hh, ww = size
    c = np.array([ww / 2, hh / 2])
    v = v1.intersect(left, right)
    a = v1.intersect(across, left)
    b = v1.intersect(across, right)
    if v is None or a is None or b is None:
        return []
    v, a, b = v - c, a - c, b - c
    span = float(np.linalg.norm(b - a))
    if span < 20:
        return []
    d = (b - a) / span
    dv = float(d @ v)
    av = float(a @ v)
    bd = float(b @ d)
    to_pixels = np.array([[1.0, 0.0, c[0]], [0.0, 1.0, c[1]], [0.0, 0.0, 1.0]])
    v_h = np.array([v[0], v[1], 1.0])
    out: list[tuple[float, Arr]] = []
    for f in focals:
        focal = float(f) * hh
        if abs(av + focal * focal) < 1e-9:
            continue
        w = -dv / (av + focal * focal)
        u3 = w * a + d
        den = float(u3 @ d) - w * bd
        if abs(den) < 1e-12:
            continue
        alpha = span / (court.WIDTH_M * den)
        u_h = alpha * np.array([u3[0], u3[1], w])
        omega = np.array([1 / focal**2, 1 / focal**2, 1.0])
        nu, nv = float(u_h @ (omega * u_h)), float(v_h @ (omega * v_h))
        if nu <= 0 or nv <= 0:
            continue
        beta = math.sqrt(nu / nv)
        a_h = np.array([a[0], a[1], 1.0]) - near_y * beta * v_h
        h = to_pixels @ np.stack([u_h, beta * v_h, a_h], axis=1)
        if abs(h[2, 2]) < 1e-12:
            continue
        out.append((float(f), h / h[2, 2]))
    return out


def in_hall(to_image: court.Mat3, aspect: float) -> bool:
    """A camera that could film a match in a hall saw the court so: above the floor, not
    too high, on the near side and not far behind or beside the court (a telephoto far
    away makes another sport's wider court look 9 m wide)."""
    camera = court.camera_from_homography(to_image, aspect)
    if camera is None:
        return False
    x, y, z = camera.center
    return (
        CAMERA_X[0] <= x <= CAMERA_X[1]
        and CAMERA_Y[0] <= y <= CAMERA_Y[1]
        and CAMERA_Z[0] <= z <= CAMERA_Z[1]
    )


def _bottom_x(line: Arr, hh: int) -> float:
    a, b, c = line
    return float(-(c + b * hh) / a) if abs(a) > 1e-9 else math.inf


# --- judging ----------------------------------------------------------------------------------


def project_above(h: Arr, size: tuple[int, int], pts: Arr) -> Arr | None:
    """Points above the court (n, 3: metres) in pixels, through the camera that sees the
    floor through h; NaN behind the camera. None without a plausible camera."""
    hh, ww = size
    to_image = court.normalise(v1._normalised(h, size))
    camera = court.camera_from_homography(to_image, ww / hh)
    if camera is None:
        return None
    k_r = np.linalg.inv(np.array(camera.rays))  # (X, Y, 1) ∝ K R (P - C)
    p = (np.asarray(pts, np.float64) - np.array(camera.center)) @ k_r.T
    with np.errstate(divide="ignore", invalid="ignore"):
        x, y = p[:, 0] / p[:, 2], p[:, 1] / p[:, 2]
    u = (x / camera.aspect + 0.5) * ww
    v = (y + 0.5) * hh
    out: Arr = np.stack([u, v], axis=1)
    out[p[:, 2] <= 0] = np.nan
    return out


def _net_points(top: float) -> Arr:
    xs = np.linspace(-0.3, court.WIDTH_M + 0.3, 30)
    return np.array([(x, court.NET_Y, z) for z in (top, top - NET_BAND) for x in xs])


NET_POINTS = {top: _net_points(top) for top in NET_TOPS}


def net_support(h: Arr, size: tuple[int, int], lines: Arr) -> tuple[float, float | None]:
    """Share of the net's two tapes (top at 2.43 or 2.24 m, bottom a metre lower, above the
    centre line) on line pixels, for the camera that sees the court through h; and the
    net's height that fits better."""
    hh, ww = size
    best: tuple[float, float | None] = (0.0, None)
    for top, pts in NET_POINTS.items():
        found = project_above(h, size, pts)
        if found is None:
            return best
        if np.isnan(found).any():
            continue
        px = np.rint(found).astype(np.int64)
        inside = (px[:, 0] >= 0) & (px[:, 0] < ww) & (px[:, 1] >= 0) & (px[:, 1] < hh)
        if inside.mean() < 0.5:
            continue
        share = float(lines[px[inside, 1], px[inside, 0]].sum() / len(px))
        if share > best[0]:
            best = (share, top)
    return best


def explained(h: Arr, size: tuple[int, int], mask: Arr) -> float:
    """Share of the colour's line pixels on the court (and a metre around it) that lie on
    the court's lines: the volleyball court explains its colour; another sport's court,
    or a stretch of the hall's other lines, leaves much of it unexplained."""
    outline = np.array([[-1.0, -1.0], [10.0, -1.0], [10.0, 19.0], [-1.0, 19.0]])
    homog = np.concatenate([outline, np.ones((4, 1))], axis=1) @ h.T
    if not np.all(homog[:, 2] * h[2, 2] > 0):
        return 0.0
    area = np.zeros(size, np.uint8)
    cv2.fillPoly(area, [np.rint(homog[:, :2] / homog[:, 2:3]).astype(np.int32)], 1)
    drawn = np.zeros(size, np.uint8)
    for x1, y1, x2, y2 in court.LINES:
        ends = np.array([[x1, y1, 1.0], [x2, y2, 1.0]]) @ h.T
        pts = np.rint(ends[:, :2] / ends[:, 2:3]).astype(np.int32)
        cv2.line(
            drawn,
            tuple(int(v) for v in pts[0]),
            tuple(int(v) for v in pts[1]),
            1,
            2 * v1.TOLERANCE_PX + 3,
        )
    inside = mask & (area > 0)
    total = int(inside.sum())
    if total == 0:
        return 0.0
    return float((inside & (drawn > 0)).sum() / total)


def bounce_share(h: Arr, size: tuple[int, int], bounces: Arr | None) -> float | None:
    """Share of the ball's bounces (normalised image points) that land on the court or
    just beside it."""
    if bounces is None or len(bounces) < MIN_BOUNCES:
        return None
    hh, ww = size
    try:
        to_court = np.linalg.inv(h)
    except np.linalg.LinAlgError:
        return None
    pts = np.asarray(bounces, np.float64) * np.array([ww, hh])
    homog = np.concatenate([pts, np.ones((len(pts), 1))], axis=1) @ to_court.T
    with np.errstate(divide="ignore", invalid="ignore"):
        xy = homog[:, :2] / homog[:, 2:3]
    m = BOUNCE_MARGIN_M
    inside = (
        (homog[:, 2] * h[2, 2] > 0)
        & (xy[:, 0] >= -m)
        & (xy[:, 0] <= court.WIDTH_M + m)
        & (xy[:, 1] >= -m)
        & (xy[:, 1] <= court.LENGTH_M + m)
    )
    return float(inside.mean())


def _combined(lines: float, net: float, bounces: float | None, explains: float) -> float:
    score = lines + W_NET * net + W_EXPLAINED * explains
    if bounces is not None:
        score += W_BOUNCE * (bounces - 0.5)
    return score


def _corners(h: Arr, size: tuple[int, int]) -> Arr:
    return v1._corners(h, size)


# --- the finder ---------------------------------------------------------------------------------


def _hypotheses(
    colour: Colour,
    vp: Arr | None,
    floor_top: float,
    size: tuple[int, int],
    rng: np.random.Generator,
    diag: float,
) -> list[tuple[float, float, float, Arr, int]]:
    """(line share, focal, near y, h, family) of every court this colour allows; courts of
    one family share their side lines and line across (they differ in depth only)."""
    hh, ww = size
    lines = v1.hough(colour.mask, rng, below=floor_top).peaks(3 * SIDES + 2 * ACROSS)
    if vp is not None:
        through = [
            abs(ln @ np.array([vp[0], vp[1], 1.0])) <= 2 * v1.VP_TOL * diag for ln, _ in lines
        ]
        sides = v1._distinct_directions([lv for lv, t in zip(lines, through, strict=True) if t])
        across = [lv for lv, t in zip(lines, through, strict=True) if not t]
    else:
        flat = [abs(ln[1]) > math.cos(math.radians(v1.FAMILY_DEG)) for ln, _ in lines]
        sides = [lv for lv, f in zip(lines, flat, strict=True) if not f]
        across = [lv for lv, f in zip(lines, flat, strict=True) if f]
    across = [lv for lv in across if abs(lv[0][1]) > 0.75][:ACROSS]  # within ~50° of level
    sides = sides[:SIDES]
    loose = v1.dilate(colour.mask, v1.TOLERANCE_PX)
    samples, which = v1._model_samples()
    min_cos = math.cos(math.radians(v1.SIDE_MIN_ANGLE_DEG))
    out: list[tuple[float, float, float, Arr, int]] = []
    fam = 0
    for (l1, _), (l2, _) in itertools.combinations(sides, 2):
        if abs(l1[0] * l2[0] + l1[1] * l2[1]) > min_cos:
            continue
        left, right = (l1, l2) if _bottom_x(l1, hh) < _bottom_x(l2, hh) else (l2, l1)
        for line, _ in across:
            fam += 1
            for near_y in NEAR_YS:
                for focal, h in family(left, right, line, near_y, size, FOCALS):
                    try:
                        to_image = court.normalise(v1._normalised(h, size))
                        if court.mirrored(to_image) or not in_hall(to_image, ww / hh):
                            continue
                    except court.CourtError:
                        continue
                    score, share = v1._score(h, loose, samples, which)
                    if math.isfinite(score):
                        out.append((share, focal, near_y, h, fam))
    return out


def detect(image: Arr, *, bounces: Arr | None = None, seed: int = 0) -> CourtGuess | None:
    """The court in an RGB frame (H, W, 3, uint8): the best fit over the hall's line colours,
    or None when nothing court-like shows. `bounces`: the ball's floor contacts (n, 2),
    normalised image coordinates, when the ball track is known."""
    hh, ww = image.shape[:2]
    size = (hh, ww)
    rng = np.random.default_rng(seed)
    colours, every = line_colours(image)
    if not colours:
        return None
    diag = math.hypot(hh, ww)
    tilted = [ln for ln, _ in v1.hough(every, rng).peaks(v1.VP_LINES) if abs(ln[1]) < v1.VP_MAX_SIN]
    vp = v1.vanishing_point(tilted, v1.VP_TOL * diag)
    floor_top = float(vp[1]) + 0.01 * hh if vp is not None else v1.TOP_SKIP * hh
    net_lines = v1.dilate(lines_only(ridges(image) >= WEAK), 1)
    fits: list[Fit] = []
    for colour in colours:
        found = _hypotheses(colour, vp, floor_top, size, rng, diag)
        found.sort(key=lambda f: -f[0])
        for share, focal, near_y, h, fam in found[:KEEP]:
            net, _ = net_support(h, size, net_lines)
            bounce = bounce_share(h, size, bounces)
            explains = explained(h, size, colour.mask)
            fits.append(
                Fit(
                    _combined(share, net, bounce, explains),
                    share,
                    net,
                    bounce,
                    explains,
                    h,
                    colour.name,
                    focal,
                    near_y,
                    f"{colour.name}/{fam}/{near_y:g}",
                )
            )
    if not fits:
        return None
    fits.sort(key=lambda f: -f.score)
    # The best readings (one per family, different courts), their lines fitted to the
    # pixels, judged again: a slightly wrong depth puts the net beside its tapes.
    masks = {c.name: c.mask for c in colours}
    finalists: list[Fit] = []
    for f in fits:
        if len(finalists) >= FINALISTS:
            break
        if any(f.family == g.family or _same_court(f.h, g.h, size) for g in finalists):
            continue
        finalists.append(_refit(f, masks[f.colour], size, net_lines, bounces))
    finalists.sort(key=lambda f: -f.score)
    best = finalists[0]
    rival = next((f for f in finalists[1:] if not _same_court(f.h, best.h, size)), None)
    ambiguous = rival is not None and rival.score >= best.score - AMBIGUOUS
    h = best.h
    tight = v1.dilate(masks[best.colour], 1)
    samples, which = v1._model_samples()
    _, support = v1._score(h, tight, samples, which)
    net, net_top = net_support(h, size, net_lines)
    bounce = bounce_share(h, size, bounces)
    to_image = court.normalise(v1._normalised(h, size))
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
    confidence = confidence_of(support, net, bounce, ambiguous)
    return CourtGuess(
        points=points,
        court_to_image=to_image,
        confidence=confidence,
        stats={
            "finder": "v2",
            "colour": best.colour,
            "support": round(support, 4),
            "net": round(net, 4),
            "net_top_m": net_top,
            "bounces_inside": round(bounce, 4) if bounce is not None else None,
            "explained": round(best.explains, 4),
            "ambiguous": ambiguous,
            "focal": round(best.focal, 3),
            "near_line_y": best.near_y,
            "vanishing_point": [round(float(v), 1) for v in vp] if vp is not None else None,
            "colours": [c.name for c in colours],
            "fits": len(fits),
            "score": round(best.score, 4),
            "rival": round(rival.score, 4) if rival is not None else None,
            "rival_colour": rival.colour if rival is not None else None,
        },
    )


def _same_court(a: Arr, b: Arr, size: tuple[int, int]) -> bool:
    """Two fits put the court's near half (its corners and the centre line's ends) within
    SAME_COURT of each other (normalised)."""
    pts = np.array(
        [court.LANDMARKS[n] for n in ("near_left", "near_right", "net_left", "net_right")]
    )
    pa = _through(a, pts, size)
    pb = _through(b, pts, size)
    d = np.abs(pa - pb)
    return bool(np.all(np.isfinite(d)) and d.max() <= SAME_COURT)


def _through(h: Arr, pts: Arr, size: tuple[int, int]) -> Arr:
    homog = np.concatenate([pts, np.ones((len(pts), 1))], axis=1) @ h.T
    with np.errstate(divide="ignore", invalid="ignore"):
        out: Arr = homog[:, :2] / homog[:, 2:3] / np.array([size[1], size[0]])
    return out


def _refit(fit: Fit, mask: Arr, size: tuple[int, int], net_lines: Arr, bounces: Arr | None) -> Fit:
    """The fit with each model line fitted to the colour's pixels near it, judged again
    (kept as it was when the fitted one is worse or no camera could see it)."""
    hh, ww = size
    loose = v1.dilate(mask, v1.TOLERANCE_PX)
    samples, which = v1._model_samples()
    try:
        h = v1._refine(v1._refine(fit.h, mask), mask)
        to_image = court.normalise(v1._normalised(h, size))
        if court.mirrored(to_image) or not in_hall(to_image, ww / hh):
            return fit
    except court.CourtError:
        return fit
    _, share = v1._score(h, loose, samples, which)
    net, _ = net_support(h, size, net_lines)
    bounce = bounce_share(h, size, bounces)
    explains = explained(h, size, mask)
    score = _combined(share, net, bounce, explains)
    if score < fit.score:
        return fit
    return Fit(
        score, share, net, bounce, explains, h, fit.colour, fit.focal, fit.near_y, fit.family
    )


def confidence_of(support: float, net: float, bounces: float | None, ambiguous: bool) -> float:
    """How sure: the court's colour along its lines, the net where the court says, the
    bounces on it; halved when another court explains the picture about as well."""
    parts = [(support, 0.55), (net, 0.25)]
    if bounces is not None:
        parts.append((bounces, 0.2))
    total = sum(w for _, w in parts)
    value = sum(v * w for v, w in parts) / total
    return round(value * (0.5 if ambiguous else 1.0), 4)
