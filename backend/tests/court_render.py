"""A synthetic hall for the court finder's tests: a pinhole camera behind the court, a
wooden floor with the volleyball court's lines, another sport's lines and the walls."""

from __future__ import annotations

import numpy as np

from nomal.core import court


def look_at(
    center: tuple[float, float, float], target: tuple[float, float, float]
) -> tuple[np.ndarray, np.ndarray]:
    c = np.array(center, float)
    f = np.array(target, float) - c
    f /= np.linalg.norm(f)
    r = np.cross(f, (0.0, 0.0, 1.0))
    r /= np.linalg.norm(r)
    down = np.cross(f, r)
    rot = np.stack([r, down, f])  # rows: camera x (right), y (down), z (forward)
    return rot, -rot @ c


def project(
    rot: np.ndarray, t: np.ndarray, focal: float, size: tuple[int, int], pts: np.ndarray
) -> np.ndarray:
    """Court points (N, 3) to pixels (N, 2); focal in image heights."""
    h, w = size
    cam = pts @ rot.T + t
    x = focal * cam[:, 0] / cam[:, 2]
    y = focal * cam[:, 1] / cam[:, 2]
    return np.stack([x * h + w / 2, y * h + h / 2], axis=1)


def _paint(img: np.ndarray, pts: np.ndarray, colour: tuple[int, int, int], r: int) -> None:
    h, w = img.shape[:2]
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            x = np.rint(pts[:, 0]).astype(int) + dx
            y = np.rint(pts[:, 1]).astype(int) + dy
            ok = (x >= 0) & (x < w) & (y >= 0) & (y < h)
            img[y[ok], x[ok]] = colour


def render(
    size: tuple[int, int] = (720, 1280),
    center: tuple[float, float, float] = (4.5, -6.0, 5.0),
    focal: float = 1.1,
    seed: int = 1,
) -> tuple[np.ndarray, dict[str, tuple[float, float]]]:
    """An RGB frame and the true landmark positions (normalised, inside the frame)."""
    h, w = size
    rng = np.random.default_rng(seed)
    rot, t = look_at(center, (4.5, 10.0, 0.0))
    img = np.empty((h, w, 3), np.uint8)
    img[:] = (205, 196, 180)  # walls
    # The floor: everything below the horizon, wood with some grain.
    grid = np.stack(np.meshgrid(np.arange(w), np.arange(h)), axis=2).reshape(-1, 2)
    xs = (grid[:, 0] - w / 2) / h / focal
    ys = (grid[:, 1] - h / 2) / h / focal
    dirs = np.stack([xs, ys, np.ones_like(xs)], axis=1) @ rot  # world directions
    floor = dirs[:, 2] < -1e-6
    img.reshape(-1, 3)[floor] = (176, 128, 74)
    noise = rng.normal(0, 4, (h, w, 1))
    img[:] = np.clip(img + noise, 0, 255).astype(np.uint8)

    def line(
        x1: float, y1: float, x2: float, y2: float, colour: tuple[int, int, int], r: int
    ) -> None:
        f = np.linspace(0, 1, 4000)
        pts = np.stack([x1 + f * (x2 - x1), y1 + f * (y2 - y1), np.zeros_like(f)], 1)
        cam = pts @ rot.T + t
        pts = pts[cam[:, 2] > 0.5]
        _paint(img, project(rot, t, focal, size, pts), colour, r)

    # Another sport's lines (a wider court, a centre circle's tangents, a key).
    for x in (-3.5, 12.5):
        line(x, -4, x, 26, (40, 40, 40), 1)
    for y in (-4.0, 4.5, 13.5, 22.0):
        line(-3.5, y, 12.5, y, (40, 40, 40), 1)
    line(2.0, -4, 2.0, 2.0, (200, 60, 50), 1)
    line(7.0, -4, 7.0, 2.0, (200, 60, 50), 1)
    # The volleyball court, white.
    for x1, y1, x2, y2 in court.LINES:
        line(x1, y1, x2, y2, (245, 245, 240), 1)
    # The walls' foot along the hall.
    for x in (-6.0, 15.0):
        line(x, -6, x, 40, (90, 70, 50), 2)
    truth: dict[str, tuple[float, float]] = {}
    for name, (x, y) in court.LANDMARKS.items():
        u, v = project(rot, t, focal, size, np.array([[x, y, 0.0]]))[0]
        if 0 <= u < w and 0 <= v < h:
            truth[name] = (u / w, v / h)
    return img, truth
