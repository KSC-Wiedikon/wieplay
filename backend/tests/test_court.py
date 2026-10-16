"""Court geometry (nomal/core/court.py) and the court finder (nomal/ml/court_detect.py)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from nomal.core import court
from nomal.ml import court_detect
from tests.court_render import look_at, project, render

ASPECT = 16 / 9


def camera_points(
    center: tuple[float, float, float], focal: float, names: list[str] | None = None
) -> dict[str, court.Point]:
    rot, t = look_at(center, (4.5, 10.0, 0.0))
    names = names or list(court.LANDMARKS)
    pts = np.array([[*court.LANDMARKS[n], 0.0] for n in names])
    px = project(rot, t, focal, (720, 1280), pts)
    return {n: (float(p[0] / 1280), float(p[1] / 720)) for n, p in zip(names, px, strict=True)}


# --- geometry ------------------------------------------------------------------------------


def test_four_points_fit_exactly_and_round_trip() -> None:
    src = [(0.1, 0.9), (0.85, 0.95), (0.62, 0.4), (0.38, 0.38)]
    dst = [court.LANDMARKS[c] for c in court.CORNERS]
    h = court.fit_homography(src, dst)
    for p, q in zip(src, dst, strict=True):
        x, y = court.apply(h, p)
        assert math.hypot(x - q[0], y - q[1]) < 1e-9
    back = court.invert(h)
    for p, q in zip(src, dst, strict=True):
        u, v = court.apply(back, q)
        assert math.hypot(u - p[0], v - p[1]) < 1e-9


def test_more_points_are_a_least_squares_fit() -> None:
    truth = camera_points((4.5, -6.0, 5.0), 1.1)
    rng = np.random.default_rng(0)
    noisy = {
        k: (u + rng.normal(0, 0.0008), v + rng.normal(0, 0.0008)) for k, (u, v) in truth.items()
    }
    exact = court.solve(truth, ASPECT)
    assert exact.rms_m is not None and exact.rms_m < 1e-6
    fit = court.solve(noisy, ASPECT)
    assert fit.rms_m is not None and 0.0 < fit.rms_m < 0.2 and fit.max_m is not None
    assert set(fit.residuals) == set(court.LANDMARKS)
    # Four points always fit exactly: no error to report.
    four = court.solve({k: truth[k] for k in court.CORNERS}, ASPECT)
    assert four.rms_m is None and four.max_m is None


@pytest.mark.parametrize(
    ("center", "focal"),
    [((4.5, -7.0, 6.0), 1.2), ((3.0, -3.0, 3.0), 0.7), ((-6.0, 9.0, 5.0), 1.0)],
)
def test_the_camera_comes_back_from_the_homography(
    center: tuple[float, float, float], focal: float
) -> None:
    sol = court.solve(camera_points(center, focal, list(court.CORNERS)), ASPECT)
    cam = sol.camera
    assert cam is not None
    assert cam.focal == pytest.approx(focal, rel=1e-6)
    assert cam.center == pytest.approx(center, abs=1e-6)
    # A ball 0.8 m above (2, 12) maps back to (2, 12) at that height, not on the floor.
    rot, t = look_at(center, (4.5, 10.0, 0.0))
    u, v = project(rot, t, focal, (720, 1280), np.array([[2.0, 12.0, 0.8]]))[0]
    x, y = cam.to_court(u / 1280, v / 720, 0.8)
    assert (x, y) == pytest.approx((2.0, 12.0), abs=1e-6)
    fx, fy = court.apply(sol.image_to_court, (u / 1280, v / 720))
    assert math.hypot(fx - 2.0, fy - 12.0) > 0.5  # the floor mapping is off for a ball in the air
    projected = cam.project(2.0, 12.0, 0.8)
    assert projected == pytest.approx((u / 1280, v / 720), abs=1e-9)


def test_a_point_behind_the_camera_does_not_project() -> None:
    cam = court.solve(camera_points((4.5, -6.0, 5.0), 1.1, list(court.CORNERS)), ASPECT).camera
    assert cam is not None
    assert cam.project(4.5, -20.0, 1.0) is None


def test_mirrored_and_degenerate_points_are_refused() -> None:
    pts = camera_points((4.5, -6.0, 5.0), 1.1, list(court.CORNERS))
    swapped = {
        "near_left": pts["near_right"],
        "near_right": pts["near_left"],
        "far_right": pts["far_left"],
        "far_left": pts["far_right"],
    }
    with pytest.raises(court.CourtError, match="mirrored"):
        court.solve(swapped, ASPECT)
    with pytest.raises(court.CourtError, match="three on one line"):
        court.solve(
            {
                "near_left": pts["near_left"],
                "net_left": camera_points((4.5, -6.0, 5.0), 1.1, ["net_left"])["net_left"],
                "far_left": pts["far_left"],
                "attack_near_left": camera_points((4.5, -6.0, 5.0), 1.1, ["attack_near_left"])[
                    "attack_near_left"
                ],
            },
            ASPECT,
        )
    with pytest.raises(court.CourtError, match="at least four"):
        court.solve({k: pts[k] for k in court.CORNERS[:3]}, ASPECT)
    with pytest.raises(court.CourtError, match="unknown"):
        court.solve({**pts, "service_zone": (0.5, 0.5)}, ASPECT)


def test_corners_out_of_the_picture_can_be_replaced_by_other_landmarks() -> None:
    names = ["far_left", "far_right", "net_left", "net_right"]
    sol = court.solve(camera_points((4.5, -2.0, 4.0), 0.9, names), ASPECT)
    u, v = court.apply(sol.court_to_image, (0.0, 0.0))
    want = camera_points((4.5, -2.0, 4.0), 0.9, ["near_left"])["near_left"]
    assert (u, v) == pytest.approx(want, abs=1e-6)


def test_the_lines_project_into_the_picture() -> None:
    sol = court.solve(camera_points((4.5, -6.0, 5.0), 1.1), ASPECT)
    lines = court.project_lines(sol.court_to_image)
    assert len(lines) == len(court.LINES)
    near = lines[2]  # the near end line runs between the near corners
    truth = camera_points((4.5, -6.0, 5.0), 1.1, ["near_left", "near_right"])
    assert near[0] == pytest.approx(truth["near_left"], abs=1e-9)
    assert near[-1] == pytest.approx(truth["near_right"], abs=1e-9)


# --- the court finder -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("center", "focal"),
    [((4.5, -6.0, 5.0), 1.1), ((3.0, -4.0, 3.0), 0.8), ((5.5, -9.0, 7.0), 1.5)],
)
def test_the_finder_finds_a_rendered_court(
    center: tuple[float, float, float], focal: float
) -> None:
    image, truth = render(center=center, focal=focal)
    guess = court_detect.detect(image)
    assert guess is not None
    assert guess.confidence >= court_detect.CONFIDENT
    assert set(truth) <= set(guess.points) | {n for n, p in truth.items() if min(p) < 0.01}
    for name, (u, v) in truth.items():
        if name in guess.points:
            gu, gv = guess.points[name]
            assert abs(gu - u) * 1280 < 5 and abs(gv - v) * 720 < 5, name
    # What it found is a calibration the API accepts.
    sol = court.solve(guess.points, ASPECT)
    assert sol.camera is not None and sol.camera.center[2] == pytest.approx(center[2], abs=0.3)


def test_no_court_no_guess() -> None:
    rng = np.random.default_rng(5)
    noise = rng.integers(90, 110, (360, 640, 3), dtype=np.uint8)
    guess = court_detect.detect(noise)
    assert guess is None or guess.confidence < court_detect.CONFIDENT
