"""Court geometry (PLAN §10.6): the volleyball court model, homographies and the camera.

Pure Python, no numpy: the API image does without it, and a calibration is a handful of
points. The worker's court finder (wieplay/ml/court_detect.py) uses the same functions.

Coordinates:
    court   metres on the floor. x across the court (0 = the left side line seen from the
            camera, 9 = the right one), y along it (0 = the end line nearest the camera,
            9 = the centre line under the net, 18 = the far end line). z is up.
    image   the video frame normalised to 0..1 (u right, v down), whatever its pixel size;
            the ball track (wieplay/ml/trajectory.py) uses the same.

A homography maps the floor plane only. With the camera recovered from it (focal length,
position; principal point at the centre, square pixels), a point seen at a known height
(a reception at ~0.7 m) maps to the floor below it too (PLAN §10.7).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

Mat3 = tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]
Point = tuple[float, float]

WIDTH_M = 9.0
LENGTH_M = 18.0
NET_Y = 9.0
ATTACK_M = 3.0

# Landmarks a coach can click (and the court finder reports). The four corners are the
# usual ones; the centre line's ends (under the net, by the posts) and the attack lines'
# ends check the fit and stand in for corners that are out of the picture.
LANDMARKS: dict[str, Point] = {
    "near_left": (0.0, 0.0),
    "near_right": (WIDTH_M, 0.0),
    "far_right": (WIDTH_M, LENGTH_M),
    "far_left": (0.0, LENGTH_M),
    "net_left": (0.0, NET_Y),
    "net_right": (WIDTH_M, NET_Y),
    "attack_near_left": (0.0, NET_Y - ATTACK_M),
    "attack_near_right": (WIDTH_M, NET_Y - ATTACK_M),
    "attack_far_left": (0.0, NET_Y + ATTACK_M),
    "attack_far_right": (WIDTH_M, NET_Y + ATTACK_M),
}
CORNERS = ("near_left", "near_right", "far_right", "far_left")

# The painted lines, as segments (x1, y1, x2, y2) in metres.
LINES: tuple[tuple[float, float, float, float], ...] = (
    (0.0, 0.0, 0.0, LENGTH_M),  # left side line
    (WIDTH_M, 0.0, WIDTH_M, LENGTH_M),  # right side line
    (0.0, 0.0, WIDTH_M, 0.0),  # near end line
    (0.0, LENGTH_M, WIDTH_M, LENGTH_M),  # far end line
    (0.0, NET_Y, WIDTH_M, NET_Y),  # centre line
    (0.0, NET_Y - ATTACK_M, WIDTH_M, NET_Y - ATTACK_M),  # near attack line
    (0.0, NET_Y + ATTACK_M, WIDTH_M, NET_Y + ATTACK_M),  # far attack line
)

# The camera must sit above the floor and see it at a sane focal length (in image heights).
CAMERA_HEIGHT_M = (0.5, 40.0)
FOCAL = (0.25, 12.0)


class CourtError(ValueError):
    """Points that define no usable court (too few, three on one line, mirrored)."""


# --- 3x3 matrices --------------------------------------------------------------------------


def _mat(rows: Sequence[Sequence[float]]) -> Mat3:
    r0, r1, r2 = rows
    return (
        (float(r0[0]), float(r0[1]), float(r0[2])),
        (float(r1[0]), float(r1[1]), float(r1[2])),
        (float(r2[0]), float(r2[1]), float(r2[2])),
    )


def matmul(a: Mat3, b: Mat3) -> Mat3:
    return _mat([[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)])


def det3(m: Mat3) -> float:
    return (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )


def invert(m: Mat3) -> Mat3:
    d = det3(m)
    if abs(d) < 1e-300:
        raise CourtError("the homography cannot be inverted")
    adj = [
        [
            m[1][1] * m[2][2] - m[1][2] * m[2][1],
            m[0][2] * m[2][1] - m[0][1] * m[2][2],
            m[0][1] * m[1][2] - m[0][2] * m[1][1],
        ],
        [
            m[1][2] * m[2][0] - m[1][0] * m[2][2],
            m[0][0] * m[2][2] - m[0][2] * m[2][0],
            m[0][2] * m[1][0] - m[0][0] * m[1][2],
        ],
        [
            m[1][0] * m[2][1] - m[1][1] * m[2][0],
            m[0][1] * m[2][0] - m[0][0] * m[2][1],
            m[0][0] * m[1][1] - m[0][1] * m[1][0],
        ],
    ]
    return normalise(_mat([[v / d for v in row] for row in adj]))


def normalise(m: Mat3) -> Mat3:
    """Scale so the matrix has unit Frobenius norm and a positive last entry (a homography is
    defined up to scale; this makes stored ones comparable)."""
    norm = math.sqrt(sum(v * v for row in m for v in row)) or 1.0
    sign = -1.0 if m[2][2] < 0 else 1.0
    return _mat([[sign * v / norm for v in row] for row in m])


def apply(m: Mat3, p: Point) -> Point:
    x, y = p
    w = m[2][0] * x + m[2][1] * y + m[2][2]
    if abs(w) < 1e-12:
        raise CourtError("point maps to infinity")
    return (
        (m[0][0] * x + m[0][1] * y + m[0][2]) / w,
        (m[1][0] * x + m[1][1] * y + m[1][2]) / w,
    )


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting; CourtError when singular."""
    n = len(b)
    rows = [[*a[i], b[i]] for i in range(n)]
    scale = max((abs(v) for row in a for v in row), default=1.0) or 1.0
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(rows[r][col]))
        if abs(rows[pivot][col]) < 1e-10 * scale:
            raise CourtError("the points do not define a court (three on one line?)")
        rows[col], rows[pivot] = rows[pivot], rows[col]
        for r in range(col + 1, n):
            f = rows[r][col] / rows[col][col]
            if f:
                for c in range(col, n + 1):
                    rows[r][c] -= f * rows[col][c]
    out = [0.0] * n
    for r in range(n - 1, -1, -1):
        out[r] = (rows[r][n] - sum(rows[r][c] * out[c] for c in range(r + 1, n))) / rows[r][r]
    return out


def _conditioning(points: Sequence[Point]) -> Mat3:
    """Hartley normalisation: centroid to the origin, mean distance sqrt(2)."""
    n = len(points)
    cx = sum(p[0] for p in points) / n
    cy = sum(p[1] for p in points) / n
    mean = sum(math.hypot(p[0] - cx, p[1] - cy) for p in points) / n
    s = math.sqrt(2) / mean if mean > 1e-12 else 1.0
    return ((s, 0.0, -s * cx), (0.0, s, -s * cy), (0.0, 0.0, 1.0))


def _collinear(a: Point, b: Point, c: Point, tol: float) -> bool:
    area = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    scale = max(math.hypot(b[0] - a[0], b[1] - a[1]), math.hypot(c[0] - a[0], c[1] - a[1]))
    return abs(area) <= tol * scale * scale


def check_spread(points: Sequence[Point], tol: float = 1e-3) -> None:
    """At least four points, and four of them with no three on one line."""
    if len(points) < 4:
        raise CourtError("at least four points are needed")
    n = len(points)
    for i in range(n):
        for j in range(i + 1, n):
            for k in range(j + 1, n):
                for m in range(k + 1, n):
                    quad = (points[i], points[j], points[k], points[m])
                    if not any(
                        _collinear(quad[a], quad[b], quad[c], tol)
                        for a, b, c in ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3))
                    ):
                        return
    raise CourtError("the points do not define a court (three on one line?)")


def fit_homography(src: Sequence[Point], dst: Sequence[Point]) -> Mat3:
    """The homography mapping src onto dst (DLT, least squares with four or more points).

    Both sets are normalised first (Hartley), then the eight unknowns of the normalised
    homography (h33 = 1: the centroid of the points maps to a finite point, which holds for
    any court seen by a camera) are solved from the normal equations."""
    if len(src) != len(dst):
        raise CourtError("as many image points as court points")
    check_spread(dst)
    check_spread(src)
    ts, td = _conditioning(src), _conditioning(dst)
    a: list[list[float]] = []
    b: list[float] = []
    for p, q in zip(src, dst, strict=True):
        x, y = apply(ts, p)
        xp, yp = apply(td, q)
        a.append([x, y, 1.0, 0.0, 0.0, 0.0, -x * xp, -y * xp])
        b.append(xp)
        a.append([0.0, 0.0, 0.0, x, y, 1.0, -x * yp, -y * yp])
        b.append(yp)
    ata = [[sum(row[i] * row[j] for row in a) for j in range(8)] for i in range(8)]
    atb = [sum(row[i] * v for row, v in zip(a, b, strict=True)) for i in range(8)]
    h = _solve(ata, atb)
    hn = _mat([h[0:3], h[3:6], [h[6], h[7], 1.0]])
    return normalise(matmul(invert(td), matmul(hn, ts)))


# --- calibrations --------------------------------------------------------------------------


@dataclass(frozen=True)
class Camera:
    """A pinhole camera recovered from the floor homography. Image coordinates for it are
    centred and in image heights: X = (u - 0.5) * aspect, Y = v - 0.5."""

    center: tuple[float, float, float]  # metres, court coordinates
    focal: float  # in image heights
    rays: Mat3  # (X, Y, 1) → direction of the viewing ray in court coordinates
    aspect: float

    def to_court(self, u: float, v: float, height: float = 0.0) -> Point:
        """Where the ray through image point (u, v) is at `height` metres."""
        r = self.rays
        x, y = (u - 0.5) * self.aspect, v - 0.5
        d = [r[i][0] * x + r[i][1] * y + r[i][2] for i in range(3)]
        if d[2] >= -1e-9 and self.center[2] > height:
            raise CourtError("the ray does not come down to that height")
        s = (height - self.center[2]) / d[2]
        return (self.center[0] + s * d[0], self.center[1] + s * d[1])

    def project(self, x: float, y: float, z: float) -> Point | None:
        """Where a point above the court (metres) shows in the image; None behind the camera."""
        k_r = invert(self.rays)  # (X, Y, 1) ∝ K R (P - C)
        d = (x - self.center[0], y - self.center[1], z - self.center[2])
        p = [sum(k_r[i][j] * d[j] for j in range(3)) for i in range(3)]
        if abs(p[2]) < 1e-12:
            return None
        X, Y = p[0] / p[2], p[1] / p[2]
        # The point is in front when the ray through (X, Y) points towards it.
        r = [self.rays[i][0] * X + self.rays[i][1] * Y + self.rays[i][2] for i in range(3)]
        if sum(r[i] * d[i] for i in range(3)) <= 0:
            return None
        return (X / self.aspect + 0.5, Y + 0.5)

    def as_dict(self) -> dict[str, object]:
        return {
            "center": [round(c, 3) for c in self.center],
            "focal": round(self.focal, 5),
            "rays": [[round(v, 9) for v in row] for row in self.rays],
            "aspect": round(self.aspect, 6),
        }


def camera_from_homography(court_to_image: Mat3, aspect: float) -> Camera | None:
    """Focal length and pose from a floor homography (Zhang's constraints with the principal
    point at the centre and square pixels). None when the view says nothing about the focal
    length (e.g. a camera looking straight down) or the result is implausible."""
    if aspect <= 0:
        return None
    # To centred image-height units: X = (u - 0.5) * aspect, Y = v - 0.5.
    centre = ((aspect, 0.0, -0.5 * aspect), (0.0, 1.0, -0.5), (0.0, 0.0, 1.0))
    g = matmul(centre, court_to_image)
    h1 = [g[0][0], g[1][0], g[2][0]]
    h2 = [g[0][1], g[1][1], g[2][1]]
    h3 = [g[0][2], g[1][2], g[2][2]]
    # r1 ⟂ r2 and |r1| = |r2| with K = diag(f, f, 1): a_i / f² + b_i = 0.
    a1 = h1[0] * h2[0] + h1[1] * h2[1]
    b1 = h1[2] * h2[2]
    a2 = h1[0] ** 2 + h1[1] ** 2 - h2[0] ** 2 - h2[1] ** 2
    b2 = h1[2] ** 2 - h2[2] ** 2
    den = a1 * a1 + a2 * a2
    if den < 1e-18:
        return None
    w = -(a1 * b1 + a2 * b2) / den  # 1 / f²
    if w <= 0:
        return None
    f = 1 / math.sqrt(w)
    if not FOCAL[0] <= f <= FOCAL[1]:
        return None
    k_inv = ((1 / f, 0.0, 0.0), (0.0, 1 / f, 0.0), (0.0, 0.0, 1.0))
    c1 = [h1[0] / f, h1[1] / f, h1[2]]
    c2 = [h2[0] / f, h2[1] / f, h2[2]]
    c3 = [h3[0] / f, h3[1] / f, h3[2]]
    n1, n2 = math.sqrt(sum(v * v for v in c1)), math.sqrt(sum(v * v for v in c2))
    lam = 2 / (n1 + n2)
    # The court lies in front of the camera: its centre has a positive depth.
    if lam * (c1[2] * WIDTH_M / 2 + c2[2] * LENGTH_M / 2 + c3[2]) < 0:
        lam = -lam
    r1 = [lam * v for v in c1]
    r2 = [lam * v for v in c2]
    t = [lam * v for v in c3]
    # Make r1, r2 exactly orthonormal (Gram-Schmidt), then r3 = r1 x r2.
    n = math.sqrt(sum(v * v for v in r1))
    r1 = [v / n for v in r1]
    dot = sum(p * q for p, q in zip(r1, r2, strict=True))
    r2 = [q - dot * p for p, q in zip(r1, r2, strict=True)]
    n = math.sqrt(sum(v * v for v in r2))
    r2 = [v / n for v in r2]
    r3 = [
        r1[1] * r2[2] - r1[2] * r2[1],
        r1[2] * r2[0] - r1[0] * r2[2],
        r1[0] * r2[1] - r1[1] * r2[0],
    ]
    # Rotation (court → camera) has columns r1 r2 r3; its transpose takes camera to court.
    rt = ((r1[0], r1[1], r1[2]), (r2[0], r2[1], r2[2]), (r3[0], r3[1], r3[2]))
    center = tuple(-sum(rt[i][j] * t[j] for j in range(3)) for i in range(3))
    if not CAMERA_HEIGHT_M[0] <= center[2] <= CAMERA_HEIGHT_M[1]:
        return None
    return Camera(
        center=(center[0], center[1], center[2]),
        focal=f,
        rays=matmul(rt, k_inv),
        aspect=aspect,
    )


@dataclass(frozen=True)
class Solution:
    """A calibration computed from clicked (or detected) landmarks."""

    image_to_court: Mat3
    court_to_image: Mat3
    camera: Camera | None
    # Per landmark: how far its image point lands from where it belongs on the court, in
    # metres (least squares over all points; zero for every point with only four).
    residuals: dict[str, float]

    @property
    def rms_m(self) -> float | None:
        if len(self.residuals) <= 4:
            return None
        return math.sqrt(sum(r * r for r in self.residuals.values()) / len(self.residuals))

    @property
    def max_m(self) -> float | None:
        if len(self.residuals) <= 4:
            return None
        return max(self.residuals.values())


def mirrored(court_to_image: Mat3) -> bool:
    """A real camera above the floor never mirrors it: with v pointing down, the image of
    the court turns the other way round than the court itself. Points clicked left for
    right (or near for far) give a mirrored fit."""
    for x, y in ((WIDTH_M / 2, NET_Y), (1.0, 1.0), (WIDTH_M - 1, LENGTH_M - 1)):
        u0, v0 = apply(court_to_image, (x, y))
        u1, v1 = apply(court_to_image, (x + 0.1, y))
        u2, v2 = apply(court_to_image, (x, y + 0.1))
        if (u1 - u0) * (v2 - v0) - (v1 - v0) * (u2 - u0) > 0:
            return True
    return False


def solve(points: dict[str, Point], aspect: float) -> Solution:
    """The calibration from landmark → image point (normalised). Raises CourtError for
    unknown landmarks, too few or degenerate points, or a mirrored court."""
    unknown = sorted(set(points) - set(LANDMARKS))
    if unknown:
        raise CourtError(f"unknown landmarks: {', '.join(unknown)}")
    names = list(points)
    image = [points[n] for n in names]
    court = [LANDMARKS[n] for n in names]
    to_court = fit_homography(image, court)
    to_image = invert(to_court)
    if mirrored(to_image):
        raise CourtError("the court comes out mirrored: left and right (or near and far) swapped?")
    residuals: dict[str, float] = {}
    for name, p, q in zip(names, image, court, strict=True):
        x, y = apply(to_court, p)
        residuals[name] = math.hypot(x - q[0], y - q[1])
    return Solution(
        image_to_court=to_court,
        court_to_image=to_image,
        camera=camera_from_homography(to_image, aspect),
        residuals=residuals,
    )


def project_lines(court_to_image: Mat3, steps: int = 12) -> list[list[Point]]:
    """The court's lines in the image, as polylines (straight under a homography, but a
    line partly behind the camera would not be: sampled, the parts in front kept)."""
    out: list[list[Point]] = []
    for x1, y1, x2, y2 in LINES:
        line: list[Point] = []
        for i in range(steps + 1):
            f = i / steps
            p = (x1 + f * (x2 - x1), y1 + f * (y2 - y1))
            w = court_to_image[2][0] * p[0] + court_to_image[2][1] * p[1] + court_to_image[2][2]
            if w <= 1e-9:
                continue
            line.append(apply(court_to_image, p))
        if len(line) >= 2:
            out.append(line)
    return out


def as_lists(m: Mat3) -> list[list[float]]:
    return [[round(v, 12) for v in row] for row in m]


def from_lists(rows: Sequence[Sequence[float]]) -> Mat3:
    return _mat(rows)
