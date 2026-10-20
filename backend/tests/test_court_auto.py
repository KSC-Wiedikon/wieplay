"""The court set automatically (PLAN §10.6): pictures of a hall matched to each other (the
hall's memory), a camera that moves during a match (setups), and the court finder for a
hall seen for the first time, on rendered halls with known geometry."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from tests.court_render import court_to_image, render
from wieplay.ml import court_find, court_segments
from wieplay.ml import court_register as reg
from wieplay.worker import court as job

SIZE = (720, 1280)
HALL = {
    "court_colour": (235, 200, 40),
    "floor": (40, 70, 140),
    "others": ((240, 240, 240), (20, 20, 20), (200, 60, 50)),
}


def corners_px(h: np.ndarray) -> np.ndarray:
    return reg.apply(h, np.array([[0.0, 0.0], [9.0, 0.0], [9.0, 18.0], [0.0, 18.0]]))


def warp(img: np.ndarray, h: np.ndarray) -> np.ndarray:
    out: np.ndarray = cv2.warpPerspective(img, h, (SIZE[1], SIZE[0]), borderMode=cv2.BORDER_REFLECT)
    return out


def shifted(dx: float, dy: float, turn_deg: float = 0.0) -> np.ndarray:
    """A small camera move: the picture shifted and turned about its centre."""
    c, s = np.cos(np.radians(turn_deg)), np.sin(np.radians(turn_deg))
    cx, cy = SIZE[1] / 2, SIZE[0] / 2
    to_centre = np.array([[1.0, 0.0, -cx], [0.0, 1.0, -cy], [0.0, 0.0, 1.0]])
    rot = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    back = np.array([[1.0, 0.0, cx + dx], [0.0, 1.0, cy + dy], [0.0, 0.0, 1.0]])
    out: np.ndarray = back @ rot @ to_centre
    return out


# --- registration ---------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["orb", "sift"])
def test_a_known_homography_comes_back_from_a_warped_picture(kind: str) -> None:
    img, _ = render(patches=150, **HALL)
    known = np.array([[1.02, 0.03, -14.0], [-0.02, 0.99, 9.0], [2e-5, -1e-5, 1.0]])
    found = reg.register(reg.features(img, kind), reg.features(warp(img, known), kind))  # type: ignore[arg-type]
    assert found is not None and found.inliers >= 50
    probe = reg.probe_grid((SIZE[0], SIZE[1]))
    err = np.linalg.norm(reg.apply(found.h, probe) - reg.apply(known, probe), axis=1)
    assert err.max() < 1.5


def test_unrelated_pictures_do_not_match() -> None:
    a, _ = render(patches=150, **HALL)
    rng = np.random.default_rng(5)
    noise = rng.integers(0, 255, a.shape, dtype=np.uint8)
    assert reg.register(reg.features(a, "orb"), reg.features(noise, "orb")) is None


def test_the_hall_memory_carries_a_court_to_another_tripod_spot() -> None:
    """The reference video was calibrated; the new one was filmed from 0.6 m further left,
    0.4 m lower: the floor's marks match, the court comes over within a couple of pixels."""
    ref, _ = render(patches=200, **HALL)
    ref_h = court_to_image()
    new_center = (3.9, -6.2, 4.6)
    new, _ = render(center=new_center, patches=200, **HALL)
    found = reg.transfer(ref, ref_h, new)
    assert found is not None
    truth = court_to_image(center=new_center)
    assert np.abs(corners_px(found.court_to_image) - corners_px(truth)).max() < 3.0


def test_the_job_judges_hall_memory_on_the_new_picture() -> None:
    ref, _ = render(patches=200, **HALL)
    new_center = (5.0, -5.5, 5.2)
    new, _ = render(center=new_center, patches=200, **HALL)
    reference = {
        "calibration_id": "c1",
        "video_id": "v1",
        "court_to_image": reg.to_normalised(court_to_image(), SIZE).tolist(),
    }
    [found] = job.from_hall(new, [(reference, ref)])
    assert found["confidence"] >= 0.6 and found["reference"] == {
        "calibration_id": "c1",
        "video_id": "v1",
    }
    truth = court_to_image(center=new_center)
    assert np.abs(corners_px(found["h"]) - corners_px(truth)).max() < 3.0
    # Another hall (no marks in common): nothing comes over.
    other, _ = render(center=new_center, patches=0, seed=9, court_colour=(250, 250, 250))
    assert job.from_hall(other, [(reference, ref)]) == []


# --- the camera's setups -------------------------------------------------------------------


def sequence(moves: list[np.ndarray]) -> tuple[list[int], list[reg.Features | None]]:
    img, _ = render(patches=200, **HALL)
    times = [30_000 * k + 15_000 for k in range(len(moves))]
    return times, [reg.features(warp(img, m), "orb") for m in moves]


def test_a_bump_splits_the_match_into_two_setups_at_the_right_moment() -> None:
    still, bumped = np.eye(3), shifted(18.0, -12.0, 0.6)
    moves = [still] * 8 + [bumped] * 8
    times, feats = sequence(moves)
    img, _ = render(patches=200, **HALL)

    def grab(t_ms: int) -> reg.Features:  # the bump happened at 3:58
        return reg.features(warp(img, bumped if t_ms >= 238_000 else still), "orb")

    setups = court_segments.find(times, feats, 480_000, grab=grab)
    assert [s.start for s in setups] == ["start", "bump"]
    assert abs(setups[1].from_ms - 238_000) <= 1_000
    assert setups[1].shift_px > 15
    # Each setup's picture maps onto the anchor's: the bump comes back as the homography.
    a, b = setups
    assert a.to_anchor is not None and b.to_anchor is not None
    between = np.linalg.inv(a.to_anchor) @ b.to_anchor  # setup 2's picture → setup 1's
    probe = reg.probe_grid((SIZE[0], SIZE[1]))
    err = reg.apply(between, probe) - reg.apply(np.linalg.inv(bumped), probe)
    assert np.abs(err).max() < 1.5


def test_a_slow_drift_gives_setups_without_a_bump_and_an_odd_frame_none() -> None:
    moves = [shifted(0.0, 0.6 * k) for k in range(24)]  # 14 px over 12 minutes
    moves[5] = shifted(40.0, 40.0)  # someone in front of the lens, for one frame
    times, feats = sequence(moves)
    setups = court_segments.find(times, feats, 720_000, min_setup_ms=60_000)
    assert len(setups) >= 2
    assert all(s.start in ("start", "drift") for s in setups)
    assert setups[0].from_ms == 0 and setups[-1].to_ms == 720_000


def test_a_still_camera_is_one_setup_and_short_ones_merge() -> None:
    times, feats = sequence([np.eye(3)] * 10)
    [only] = court_segments.find(times, feats, 300_000)
    assert (only.from_ms, only.to_ms, only.start) == (0, 300_000, "start")
    # A camera put up during the first minute: its frames do not make a setup.
    moves = [shifted(30.0, 0.0), *[np.eye(3)] * 10]
    times, feats = sequence(moves)
    [only] = court_segments.find(times, feats, 330_000)
    assert only.from_ms == 0 and only.to_ms == 330_000


# --- the finder for a new hall ------------------------------------------------------------


def max_error_px(guess: court_find.CourtGuess, truth: dict[str, tuple[float, float]]) -> float:
    return max(
        float(np.hypot((guess.points[k][0] - u) * SIZE[1], (guess.points[k][1] - v) * SIZE[0]))
        for k, (u, v) in truth.items()
        if k in guess.points
    )


@pytest.mark.parametrize(
    "view",
    [
        {"center": (4.5, -6.0, 5.0), "focal": 1.1},
        {"center": (3.0, -5.0, 6.0), "focal": 1.3},
        {"center": (6.0, -4.0, 3.5), "focal": 0.8},
        {"center": (4.5, -4.0, 2.8), "focal": 0.7},  # the filming guide's camera, a wide lens
    ],
)
def test_the_finder_is_sure_of_the_court_on_a_multi_sport_floor(view: dict[str, object]) -> None:
    """Yellow volleyball lines among white, black and red ones (one sport's at the court's
    own spacing), the net above the centre line: the volleyball court, sure."""
    img, truth = render(net=True, **HALL, **view)  # type: ignore[arg-type]
    guess = court_find.detect(img)
    assert guess is not None
    assert guess.sure and guess.confidence >= court_find.SURE_CONFIDENCE
    assert guess.stats["finder"] == "v3" and abs(guess.stats["net_height_m"] - 2.43) <= 0.06
    assert max_error_px(guess, truth) < 6.0


def test_the_net_must_stand_where_the_court_puts_it() -> None:
    """The finder looks for the net's two tapes above the centre line: found at the men's
    height on a men's court; told to expect the women's net, the net it sees is elsewhere and
    the court is not sure."""
    img, truth = render(net=True, **HALL)
    pic = court_find.picture(img)
    found, _ = court_find.finalists(pic)
    best = found[0]
    assert best.cam is not None and not best.net_off and best.net >= 0.8
    lm = court_find.landmarks_of(best.cam)
    for name, (u, v) in truth.items():
        assert np.hypot(lm[name][0] - u * SIZE[1], lm[name][1] - v * SIZE[0]) < 6.0
    women = court_find.detect(img, net_tops=(2.24,))
    assert women is None or not women.sure


def test_a_picture_without_a_court_finds_none_or_is_unsure() -> None:
    rng = np.random.default_rng(3)
    noise = rng.integers(0, 255, (*SIZE, 3), dtype=np.uint8)
    guess = court_find.detect(noise)
    assert guess is None or not guess.sure
    # A floor of another sport only (no volleyball court, no net): never sure.
    img, _ = render(**{**HALL, "court_colour": HALL["floor"]})  # type: ignore[arg-type]
    guess = court_find.detect(img)
    assert guess is None or not guess.sure


def test_the_finder_reports_the_bounces_and_fits_the_lens() -> None:
    img, truth = render(net=True, **HALL)
    on = np.array([truth["near_left"], truth["far_right"], truth["net_left"]] * 10)
    on = (on + np.array(truth["near_right"])) / 2  # inside the court
    off = np.tile([[0.02, 0.05]], (30, 1))  # up in the wall
    with_on = court_find.detect(img, bounces=on)
    with_off = court_find.detect(img, bounces=off)
    assert with_on is not None and with_off is not None
    assert with_on.stats["bounces_inside"] == 1.0 and with_off.stats["bounces_inside"] == 0.0
    assert abs(with_on.stats["k1"]) < 0.05  # a pinhole render: no distortion found
    plain = court_find.camera_of(court_to_image(), SIZE)
    assert plain is not None
    lens = court_find.Camera(plain.rvec, plain.centre, plain.focal, -0.2, SIZE)
    corners3 = np.array([[4.5, 9.0, 0.0], [0.0, 0.0, 0.0]])
    p, q = lens.project(corners3), plain.project(corners3)
    centre = np.array([SIZE[1] / 2, SIZE[0] / 2])
    # Barrel distortion pulls points towards the centre, the more the further out.
    assert np.linalg.norm(p[1] - centre) < np.linalg.norm(q[1] - centre)


def test_the_cameras_of_many_courts_are_the_courts_cameras() -> None:
    for center, focal in (((4.5, -6.0, 5.0), 1.1), ((6.5, -3.0, 3.0), 0.7)):
        h = court_to_image(center=center, focal=focal, target=(5.0, 10.0, 0.0))
        c, f = court_find.cameras(h[None], SIZE)
        assert np.allclose(c[0], center, atol=1e-6) and f[0] == pytest.approx(focal)
        assert court_find.plausible(h[None], SIZE)[0]
    above = court_to_image(center=(4.5, 9.0, 30.0), focal=1.0, target=(4.5, 9.5, 0.0))
    assert not court_find.plausible(above[None], SIZE)[0]  # a camera on the ceiling


# --- the job's choice ------------------------------------------------------------------------


def _guess(h: np.ndarray, sure: bool, confidence: float) -> court_find.CourtGuess:
    to_image = reg.to_normalised(h, SIZE)
    return court_find.CourtGuess(
        points={},
        court_to_image=tuple(tuple(float(v) for v in row) for row in to_image),  # type: ignore[arg-type]
        confidence=confidence,
        stats={"finder": "v3"},
        sure=sure,
    )


def _hall(h: np.ndarray, confidence: float) -> dict[str, object]:
    return {"h": h, "confidence": confidence, "reference": {"video_id": "v"}, "stats": {}}


def test_the_job_takes_a_sure_court_and_lets_a_coach_decide_a_conflict() -> None:
    h = court_to_image()
    other = court_to_image(center=(3.0, -6.0, 5.0))  # another court in the picture
    # The finder alone: its verdict.
    best = job.best_court([], _guess(h, True, 0.9), SIZE)
    assert best is not None and best["method"] == "auto" and best["sure"]
    # The hall's memory sure and the finder agreeing: the memory, sure.
    best = job.best_court([_hall(h, 0.9)], _guess(h, True, 0.9), SIZE)
    assert best is not None and best["method"] == "auto-hall" and best["sure"]
    assert best["confidence"] >= court_find.SURE_CONFIDENCE
    # Both sure of different courts: unsure, a coach decides.
    best = job.best_court([_hall(other, 0.9)], _guess(h, True, 0.9), SIZE)
    assert best is not None and not best["sure"] and best["stats"]["conflict"]
    assert best["confidence"] < court_find.SURE_CONFIDENCE
    # The finder sure, the memory not: the finder's.
    best = job.best_court([_hall(other, 0.65)], _guess(h, True, 0.85), SIZE)
    assert best is not None and best["method"] == "auto" and best["sure"]
    # Neither sure: the memory unless the finder is clearly surer, unsure either way.
    best = job.best_court([_hall(other, 0.65)], _guess(h, False, 0.5), SIZE)
    assert best is not None and best["method"] == "auto-hall" and not best["sure"]
