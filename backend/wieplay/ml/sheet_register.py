"""Matching a photo of the Matchblatt to its layout (PLAN §10.3a, SS4; research note §5.3 step 1).

    photo ─▶ the paper (the bright region; a table, a folder or a sheet underneath is not)
          ─▶ thin dark lines, relative to the paper's own light (carbon copies are faint)
          ─▶ the page's turn: the angle at which those lines stand straightest, then each of
             the four quarter turns
          ─▶ scale and offset per axis: the printed rules' row and column profiles against the
             form's (wieplay/ml/sheet_form.py), a 1-D correlation
          ─▶ refined: the photo warped onto the form, every printed rule looked for near where
             it should be, a homography fitted to what was found (RANSAC), in rounds with a
             shrinking search; then once more per set block (a curl or a bend is local)

The result maps page millimetres to photo pixels: for the whole page, and for each set block it
found. A photo of a single set (the coach said which) is matched to that block alone.

numpy and OpenCV (the worker image); nothing leaves the worker.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np

from wieplay.ml import sheet_form as sf

WORK_SIDE = 1600  # the longer side the coarse search works at (px)
RES = 4.0  # px per mm of the warped form during the refinement
SAMPLE_MM = 2.0  # a rule is looked for every 2 mm along it
ROUNDS = (2.0, 1.4, 1.0, 0.8)  # search half-widths (mm) of the refinement rounds
BLOCK_ROUNDS = (1.4, 0.9, 0.7)
FOUND_MM = 0.5  # a rule point counts as found within this of its place (the score)
RIDGE_MIN = 0.06  # a line at least this much darker than the paper beside it
MIN_POINTS = 40
GOOD = 0.55  # a registration this good needs no second candidate


@dataclass
class Registration:
    """Where the form is in the photo. `page`: page mm → photo px; `blocks`: per set block
    found, its own refined mapping; `score`: the share of the form's rule points in view that
    were found where they belong (0-1); per block too."""

    page: np.ndarray
    blocks: dict[int, np.ndarray] = field(default_factory=dict)
    score: float = 0.0
    block_scores: dict[int, float] = field(default_factory=dict)
    turns: int = 0  # quarter turns (clockwise) from the photo to an upright page
    set_no: int | None = None
    width: int = 0
    height: int = 0

    def mapping(self, set_no: int) -> np.ndarray:
        return self.blocks.get(set_no, self.page)

    def to_photo(self, set_no: int, pts: np.ndarray) -> np.ndarray:
        """Page mm (N, 2) → photo px (N, 2), with the block's own mapping."""
        return apply(self.mapping(set_no), pts)

    def as_json(self) -> dict[str, Any]:
        """For `scoresheet_image.registration`: the mappings as nine numbers each, the photo's
        size they refer to, the scores."""

        def m(h: np.ndarray) -> list[float]:
            h = h / h[2, 2]
            return [round(float(v), 7) for v in h.reshape(-1)]

        return {
            "template": "matchblatt-sv",
            "units": "page_mm_to_photo_px",
            "width": self.width,
            "height": self.height,
            "page": m(self.page),
            "blocks": {str(k): m(v) for k, v in sorted(self.blocks.items())},
            "score": round(self.score, 4),
            "block_scores": {str(k): round(v, 4) for k, v in sorted(self.block_scores.items())},
            "turns": self.turns,
            "set_no": self.set_no,
        }

    @classmethod
    def from_json(cls, doc: dict[str, Any]) -> Registration:
        def h(v: list[float]) -> np.ndarray:
            return np.array(v, np.float64).reshape(3, 3)

        return cls(
            page=h(doc["page"]),
            blocks={int(k): h(v) for k, v in (doc.get("blocks") or {}).items()},
            score=float(doc.get("score", 0)),
            block_scores={int(k): float(v) for k, v in (doc.get("block_scores") or {}).items()},
            turns=int(doc.get("turns", 0)),
            set_no=doc.get("set_no"),
            width=int(doc.get("width", 0)),
            height=int(doc.get("height", 0)),
        )


class NotFound(Exception):
    """No Matchblatt grid in the photo."""


def apply(h: np.ndarray, pts: np.ndarray) -> np.ndarray:
    p = np.asarray(pts, np.float64).reshape(-1, 1, 2)
    return cv2.perspectiveTransform(p, h).reshape(-1, 2)


# --- the paper and its lines ----------------------------------------------------------------------


def _odd(n: float) -> int:
    return max(3, int(n) | 1)


def light(gray: np.ndarray, k: int) -> np.ndarray:
    """The paper's own brightness under the ink (a closing removes thin dark marks)."""
    bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))
    return cv2.GaussianBlur(bg.astype(np.float32), (0, 0), k / 3)


def paper_mask(gray: np.ndarray) -> np.ndarray:
    """The paper: the bright, large region (uint8 0/1), shadows on it included. A darker table
    or folder is not."""
    k = _odd(min(gray.shape) / 40)
    bg = np.clip(light(gray, k), 0, 255).astype(np.uint8)
    t, _ = cv2.threshold(bg, 0, 1, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    m = (bg > 0.75 * t).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(m)
    if n <= 1:
        return np.ones_like(gray, np.uint8)
    big = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    out = (labels == big).astype(np.uint8)
    out = cv2.morphologyEx(out, cv2.MORPH_CLOSE, np.ones((3 * k, 3 * k), np.uint8))
    return cv2.erode(out, np.ones((k, k), np.uint8))


def ridges(
    gray: np.ndarray, d: int = 2, mask: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Thin dark horizontal and vertical lines: how much darker a pixel is than the paper d px
    above and below it (or left and right), relative to the paper's light. 0 elsewhere."""
    g = gray.astype(np.float32)
    bg = np.maximum(light(gray, _odd(8 * d + 1)), 8.0)
    up = np.roll(g, d, axis=0)
    down = np.roll(g, -d, axis=0)
    left = np.roll(g, d, axis=1)
    right = np.roll(g, -d, axis=1)
    rh = np.clip((np.minimum(up, down) - g) / bg, 0, 1)
    rv = np.clip((np.minimum(left, right) - g) / bg, 0, 1)
    if mask is not None:
        rh *= mask
        rv *= mask
    return rh, rv


def _dark(gray: np.ndarray) -> np.ndarray:
    """Thin dark marks in any direction (a black-hat), relative to the paper: for the skew."""
    bh = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, np.ones((5, 5), np.uint8)).astype(np.float32)
    return np.asarray(bh / np.maximum(light(gray, _odd(min(gray.shape) / 60)), 8.0), np.float32)


def _level(ridge: np.ndarray, mask: np.ndarray) -> float:
    """What counts as a line: a share of the strongest lines on the paper (a faint or dim photo
    has weaker ones), within sane bounds."""
    on = ridge[mask > 0]
    if on.size == 0:
        return RIDGE_MIN
    return float(np.clip(0.35 * np.percentile(on, 99.5), 0.02, RIDGE_MIN))


def _long(binary: np.ndarray, run: int, horizontal: bool) -> np.ndarray:
    """The long lines of a binary picture in one direction. Widened across first, so a line a
    few degrees off (perspective: the sheet's lines converge) still runs through."""
    across = np.ones((3, 1) if horizontal else (1, 3), np.uint8)
    k = (max(5, run), 1) if horizontal else (1, max(5, run))
    wide = cv2.dilate(binary, across)
    return cv2.morphologyEx(wide, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, k))


# --- coarse: turn, angle, scale, offset -----------------------------------------------------------


def _rotation(angle_deg: float, w: int, h: int) -> tuple[np.ndarray, int, int]:
    """A rotation about the centre that keeps the whole picture, and the new size."""
    a = math.radians(angle_deg)
    c, s = abs(math.cos(a)), abs(math.sin(a))
    nw, nh = int(w * c + h * s + 0.5), int(w * s + h * c + 0.5)
    m = cv2.getRotationMatrix2D((w / 2, h / 2), angle_deg, 1.0)
    m[0, 2] += (nw - w) / 2
    m[1, 2] += (nh - h) / 2
    return np.vstack([m, [0, 0, 1]]), nw, nh


def _skew(binary: np.ndarray) -> float:
    """The angle (degrees, within ±45) that makes the lines horizontal and vertical."""
    small = binary
    scale = 700 / max(binary.shape)
    if scale < 1:
        small = (
            cv2.resize(binary, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) > 0
        ).astype(np.uint8)
    h, w = small.shape

    def energy(a: float) -> float:
        m, nw, nh = _rotation(a, w, h)
        r = cv2.warpAffine(small, m[:2], (nw, nh), flags=cv2.INTER_NEAREST)
        rows = r.sum(axis=1).astype(np.float64)
        cols = r.sum(axis=0).astype(np.float64)
        return float((rows**2).sum() + (cols**2).sum())

    best_a = max(np.arange(-45, 45, 1.5), key=lambda a: energy(float(a)))
    fine = np.arange(best_a - 1.5, best_a + 1.5, 0.25)
    return float(max(fine, key=lambda a: energy(float(a))))


def _template_profile(
    segments: list[sf.Segment], axis: int, scale: float, size: int, sigma: float
) -> np.ndarray:
    """The form's rules as a 1-D profile along y (axis 0: horizontal rules) or x (axis 1), in
    px at `scale`, starting at 0 mm."""
    prof = np.zeros(size, np.float64)
    for x0, y0, x1, y1 in segments:
        if axis == 0 and abs(y1 - y0) < 1e-6:
            k = round(y0 * scale)
            if 0 <= k < size:
                prof[k] += abs(x1 - x0)
        elif axis == 1 and abs(x1 - x0) < 1e-6:
            k = round(x0 * scale)
            if 0 <= k < size:
                prof[k] += abs(y1 - y0)
    return cv2.GaussianBlur(prof.reshape(-1, 1), (1, 0), sigma).reshape(-1)


def _axis_peaks(
    photo_prof: np.ndarray,
    segments: list[sf.Segment],
    axis: int,
    scales: np.ndarray,
    per_scale: int = 2,
) -> list[list[tuple[float, float]]]:
    """Per scale (px per mm) the best offsets (px) of the form along one axis against the
    photo's rule profile: [(normalised correlation, offset), ...]."""
    p = cv2.GaussianBlur(photo_prof.reshape(-1, 1), (1, 0), 1.5).reshape(-1)
    p = p - p.mean()
    pn = np.sqrt((p**2).sum()) + 1e-9
    out: list[list[tuple[float, float]]] = []
    for s in scales:
        size = int((sf.PAGE_W if axis == 1 else sf.PAGE_H) * s) + 4
        t = _template_profile(segments, axis, s, size, max(1.0, 0.35 * s))
        t = t - t.mean()
        tn = np.sqrt((t**2).sum()) + 1e-9
        # The form's 0 mm lands at an offset in the photo (negative: cut off by a crop).
        padded = np.pad(p, (size, size))
        n = len(padded) + size
        nfft = 1 << (n - 1).bit_length()
        corr = np.fft.irfft(np.fft.rfft(padded, nfft) * np.conj(np.fft.rfft(t, nfft)), nfft)
        corr = corr[: len(padded) - size + 1] / (pn * tn)
        best: list[tuple[float, float]] = []
        for _ in range(per_scale):
            k = int(np.argmax(corr))
            best.append((float(corr[k]), float(k - size)))
            corr[max(0, k - 4) : k + 5] = -1
        out.append(best)
    return out


def _fit_2d(
    hor: np.ndarray,
    ver: np.ndarray,
    segments: list[sf.Segment],
    ext: sf.Rect,
) -> tuple[float, np.ndarray]:
    """The form's scale and offset in an upright picture: candidates from the row and column
    profiles at every scale, the x and y scales within 15 % of each other, judged by how many of
    the form's rule points land on a rule (2-D)."""
    h, w = hor.shape
    rows = hor.sum(axis=1).astype(np.float64)
    cols = ver.sum(axis=0).astype(np.float64)
    lo = max(w, h) / (ext.w * 4.0)
    hi = max(w, h) / (ext.w * 0.7)
    scales = np.exp(np.linspace(math.log(lo), math.log(hi), 110))
    step = math.log(hi / lo) / 109
    reach = math.ceil(math.log(1.15) / step)
    xs = _axis_peaks(cols, segments, 1, scales)
    ys = _axis_peaks(rows, segments, 0, scales)
    pts, horiz = _samples(segments, 4.0)
    hb = np.asarray(cv2.GaussianBlur(hor.astype(np.float32), (0, 0), 1.2), np.float32)
    vb = np.asarray(cv2.GaussianBlur(ver.astype(np.float32), (0, 0), 1.2), np.float32)
    hb /= max(float(hb.max()), 1e-6)
    vb /= max(float(vb.max()), 1e-6)
    # Only the likelier x candidates are judged in 2-D (the profile's correlation).
    ranked = sorted(((c, i, o) for i, peaks in enumerate(xs) for c, o in peaks), reverse=True)
    best = (-1.0, np.eye(3))
    for _, i, ox in ranked[:60]:
        sx = scales[i]
        u = np.round(pts[:, 0] * sx + ox).astype(int)
        inside_x = (u >= 0) & (u < w)
        for j in range(max(0, i - reach), min(len(scales), i + reach + 1)):
            sy = scales[j]
            for _, oy in ys[j]:
                v = np.round(pts[:, 1] * sy + oy).astype(int)
                inside = inside_x & (v >= 0) & (v < h)
                if inside.sum() < 0.3 * len(pts):
                    continue
                ui, vi, hz = u[inside], v[inside], horiz[inside]
                score = float(np.where(hz, hb[vi, ui], vb[vi, ui]).sum()) / len(pts)
                if score > best[0]:
                    best = (score, np.array([[sx, 0, ox], [0, sy, oy], [0, 0, 1]], np.float64))
    return best


def coarse(gray: np.ndarray, set_no: int | None = None) -> list[tuple[float, np.ndarray, int]]:
    """Candidate mappings page mm → photo px, best first: (score, homography, quarter turns)."""
    h0, w0 = gray.shape
    f = min(1.0, WORK_SIDE / max(h0, w0))
    small = cv2.resize(gray, None, fx=f, fy=f, interpolation=cv2.INTER_AREA) if f < 1 else gray
    mask = paper_mask(small)
    dark = _dark(small) * mask
    angle = _skew((dark > 0.12).astype(np.uint8))
    segments = sf.lines(set_no)
    ext = sf.extent(set_no)
    out: list[tuple[float, np.ndarray, int]] = []
    h, w = small.shape
    for turns in range(4):
        m, nw, nh = _rotation(angle - 90 * turns, w, h)
        r = cv2.warpAffine(small, m[:2], (nw, nh), flags=cv2.INTER_LINEAR, borderValue=0)
        rm = cv2.warpAffine(mask, m[:2], (nw, nh), flags=cv2.INTER_NEAREST, borderValue=0)
        rh, rv = ridges(r, 2, rm)
        run = int(min(nw, nh) * 0.025)
        hor = _long((rh > _level(rh, rm)).astype(np.uint8), run, True)
        ver = _long((rv > _level(rv, rm)).astype(np.uint8), run, False)
        score, a = _fit_2d(hor, ver, segments, ext)
        if score < 0:
            continue
        to_photo = np.diag([1 / f, 1 / f, 1]) @ np.linalg.inv(m) @ a
        out.append((score, to_photo, turns))
    out.sort(key=lambda t: -t[0])
    return out


# --- fine: snapping the rules ---------------------------------------------------------------------


def _samples(segments: list[sf.Segment], step: float = SAMPLE_MM) -> tuple[np.ndarray, np.ndarray]:
    """Points along the rules (mm), every `step` mm, and whether each lies on a horizontal
    rule."""
    pts: list[tuple[float, float]] = []
    horiz: list[bool] = []
    for x0, y0, x1, y1 in segments:
        length = math.hypot(x1 - x0, y1 - y0)
        n = max(2, int(length / step))
        is_h = abs(y1 - y0) < 1e-6
        for k in range(n):
            t = (k + 0.5) / n
            pts.append((x0 + t * (x1 - x0), y0 + t * (y1 - y0)))
            horiz.append(is_h)
    return np.array(pts, np.float64), np.array(horiz, bool)


def warp_to_form(
    gray: np.ndarray, h_px: np.ndarray, box: sf.Rect, res: float, margin: float = 0.0
) -> tuple[np.ndarray, np.ndarray]:
    """The photo resampled onto the form around `box` (mm) at `res` px per mm; and the mapping
    from that picture's px to the photo's."""
    x0, y0 = box.x - margin, box.y - margin
    w = round((box.w + 2 * margin) * res)
    h = round((box.h + 2 * margin) * res)
    to_photo = h_px @ np.array([[1 / res, 0, x0], [0, 1 / res, y0], [0, 0, 1]], np.float64)
    out = cv2.warpPerspective(
        gray,
        to_photo,
        (w, h),
        flags=cv2.WARP_INVERSE_MAP | cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_REPLICATE,
    )
    return out, to_photo


def _snap(
    gray: np.ndarray,
    h_px: np.ndarray,
    pts: np.ndarray,
    horiz: np.ndarray,
    search: float,
    box: sf.Rect,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Warp the photo onto the form around `box`, find each rule point's line within ±search
    mm across it. Returns the points found (mm), where they are in the photo (px) and how far
    they moved (mm)."""
    margin = search + 2
    warped, _ = warp_to_form(gray, h_px, box, RES, margin)
    x0, y0 = box.x - margin, box.y - margin
    hgt, w = warped.shape
    rh, rv = ridges(warped, 2)
    along = int(1.5 * RES) | 1
    rh = cv2.blur(rh, (along, 1))
    rv = cv2.blur(rv, (1, along))
    r = round(search * RES)
    found_mm: list[tuple[float, float]] = []
    moved: list[float] = []
    keep: list[int] = []
    for i, ((px, py), is_h) in enumerate(zip(pts, horiz, strict=True)):
        cx = round((px - x0) * RES)
        cy = round((py - y0) * RES)
        if is_h:
            if not (0 <= cx < w and r <= cy < hgt - r):
                continue
            prof = rh[cy - r : cy + r + 1, cx]
        else:
            if not (r <= cx < w - r and 0 <= cy < hgt):
                continue
            prof = rv[cy, cx - r : cx + r + 1]
        k = int(np.argmax(prof))
        if prof[k] < RIDGE_MIN:
            continue
        # A wide search needs a clear peak: another as strong elsewhere makes it ambiguous.
        others = np.concatenate([prof[: max(0, k - 3)], prof[k + 4 :]])
        if search > 1.5 and others.size and others.max() >= prof[k] * 0.9:
            continue
        # Sub-pixel: a parabola through the peak and its neighbours.
        off = 0.0
        if 0 < k < len(prof) - 1:
            a, b, c = prof[k - 1], prof[k], prof[k + 1]
            den = a - 2 * b + c
            if den < 0:
                off = float(np.clip(0.5 * (a - c) / den, -0.5, 0.5))
        d = (k + off - r) / RES
        keep.append(i)
        moved.append(d)
        found_mm.append((px, py + d) if is_h else (px + d, py))
    if not keep:
        return np.zeros((0, 2)), np.zeros((0, 2)), np.zeros(0)
    idx = np.array(keep, int)
    return pts[idx], apply(h_px, np.array(found_mm, np.float64)), np.abs(np.array(moved))


def _refine(
    gray: np.ndarray,
    h_px: np.ndarray,
    segments: list[sf.Segment],
    box: sf.Rect,
    rounds: tuple[float, ...],
) -> tuple[np.ndarray, float, int]:
    """Snap and refit; returns the mapping, the share of rule points in view found, how many."""
    pts, horiz = _samples(segments)
    current = h_px
    for search in rounds:
        src, dst, _ = _snap(gray, current, pts, horiz, search, box)
        if len(src) < MIN_POINTS:
            break
        thr = max(1.0, 0.5 * px_per_mm(current, box))  # half a millimetre in the photo
        fit, mask = cv2.findHomography(src, dst, cv2.RANSAC, thr, maxIters=4000)
        if fit is None or mask is None or int(mask.sum()) < MIN_POINTS:
            break
        current = fit
    src, _, moved = _snap(gray, current, pts, horiz, FOUND_MM * 2, box)
    found = int((moved <= FOUND_MM).sum()) if len(src) else 0
    return current, found / max(_visible(current, pts, gray.shape), 1), found


def _visible(h_px: np.ndarray, pts: np.ndarray, shape: tuple[int, ...]) -> int:
    p = apply(h_px, pts)
    inside = (p[:, 0] >= 0) & (p[:, 1] >= 0) & (p[:, 0] < shape[1]) & (p[:, 1] < shape[0])
    return int(inside.sum())


def px_per_mm(h_px: np.ndarray, box: sf.Rect) -> float:
    """The photo's resolution at the middle of `box`."""
    c = np.array(
        [[box.x + box.w / 2, box.y + box.h / 2], [box.x + box.w / 2 + 10, box.y + box.h / 2]]
    )
    p = apply(h_px, c)
    return float(np.hypot(*(p[1] - p[0])) / 10)


def render_rules(segments: list[sf.Segment], box: sf.Rect, res: float, margin: float) -> np.ndarray:
    """The form's rules as a picture (1 on the lines), around `box` at `res` px per mm."""
    x0, y0 = box.x - margin, box.y - margin
    w = round((box.w + 2 * margin) * res)
    h = round((box.h + 2 * margin) * res)
    img = np.zeros((h, w), np.float32)
    for a, b, c, d in segments:
        p0 = (round((a - x0) * res), round((b - y0) * res))
        p1 = (round((c - x0) * res), round((d - y0) * res))
        cv2.line(img, p0, p1, 1.0, 1)
    return img


def _ecc(
    gray: np.ndarray,
    h_px: np.ndarray,
    segments: list[sf.Segment],
    box: sf.Rect,
    sigma: float,
    res: float = 3.0,
) -> np.ndarray | None:
    """Refine the mapping by matching the form's rules, blurred, to the photo's printed lines,
    blurred alike (enhanced correlation, a homography), in the form's frame: the photo is first
    warped onto the form with the current mapping, so its lines stand upright and long ones can
    be told from handwriting. A wide blur (`sigma` mm) cannot lock onto the neighbouring row of
    boxes."""
    margin = 8.0
    warped, to_photo = warp_to_form(gray, h_px, box, res, margin)
    mask = paper_mask(warped)
    rh, rv = ridges(warped, 2, mask)
    run = int(2.5 * res)
    hor = _long((rh > _level(rh, mask)).astype(np.uint8), run, True)
    ver = _long((rv > _level(rv, mask)).astype(np.uint8), run, False)
    k = max(0.6, sigma * res)
    found = np.asarray(cv2.GaussianBlur(np.maximum(hor, ver).astype(np.float32), (0, 0), k))
    template = np.asarray(cv2.GaussianBlur(render_rules(segments, box, res, margin), (0, 0), k))
    warp = np.eye(3, dtype=np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 50, 1e-5)
    everywhere = np.ones(found.shape, np.uint8)
    try:
        _, fitted = cv2.findTransformECC(
            template, found, warp, cv2.MOTION_HOMOGRAPHY, criteria, everywhere, 1
        )
    except cv2.error:
        return None
    warp = np.asarray(fitted, np.float32)
    to_t = np.array(
        [[res, 0, -(box.x - margin) * res], [0, res, -(box.y - margin) * res], [0, 0, 1]]
    )
    out = to_photo @ warp.astype(np.float64) @ to_t
    out = out / out[2, 2]
    # A step of more than a few blurs is not a refinement but a slip (a part of the form the
    # photo doesn't show, a homography the rules can't pin down): keep what we had.
    corners = np.array(box.corners())
    moved = np.hypot(*(apply(out, corners) - apply(h_px, corners)).T).max()
    limit_mm = max(3 * sigma, 0.06 * math.hypot(box.w, box.h))
    if moved > limit_mm * px_per_mm(h_px, box):
        return None
    return np.asarray(out, np.float64)


ONE_SET_SLIPS = ((0.0, 0.0), (0.0, -4.65), (0.0, 4.65))  # mm: a service row's pitch


def register(image: np.ndarray, set_no: int | None = None) -> Registration:
    """Find the form in a photo (BGR or grey). Raises NotFound when nothing fits."""
    gray = image if image.ndim == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    segments = sf.lines(set_no)
    box = sf.extent(set_no)
    best: tuple[float, np.ndarray, int] | None = None
    # One set block alone repeats itself (its service rows are alike), so its coarse fit may be
    # a row off: the rows above and below are tried too, the best fit wins.
    slips = ONE_SET_SLIPS if set_no else ((0.0, 0.0),)
    for _, coarse_h, turns in coarse(gray, set_no)[:2]:
        for dx, dy in slips:
            h0 = coarse_h @ np.array([[1.0, 0.0, dx], [0.0, 1.0, dy], [0.0, 0.0, 1.0]])
            for sigma, res in ((2.5, 2.5), (1.2, 3.0), (0.6, 3.0)):
                better = _ecc(gray, h0, segments, box, sigma, res)
                if better is not None:
                    h0 = better
            h1, score, n = _refine(gray, h0, segments, box, ROUNDS)
            if n >= MIN_POINTS and (best is None or score > best[0]):
                best = (score, h1, turns)
        if best is not None and best[0] >= GOOD:
            break  # the likeliest turn fits: the other is not tried
    if best is None or best[0] < 0.2:
        raise NotFound("no Matchblatt grid found in the photo")
    score, h_page, turns = best
    reg = Registration(
        h_page, score=score, turns=turns, set_no=set_no, width=gray.shape[1], height=gray.shape[0]
    )
    for no in [set_no] if set_no else sorted(sf.BLOCKS):
        b = sf.BLOCKS[no]
        corners = apply(h_page, np.array(b.rect.corners()))
        w, h = gray.shape[1], gray.shape[0]
        inside = (
            (corners[:, 0] > -0.05 * w)
            & (corners[:, 0] < 1.05 * w)
            & (corners[:, 1] > -0.05 * h)
            & (corners[:, 1] < 1.05 * h)
        )
        if inside.sum() < 2:
            continue
        hb, bscore, n = _refine(gray, h_page, sf.lines(no), b.rect, BLOCK_ROUNDS)
        if n >= MIN_POINTS and bscore >= 0.3:
            reg.blocks[no] = hb
            reg.block_scores[no] = bscore
    return reg
