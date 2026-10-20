"""The player at the ball (wieplay/ml/contact_pose.py) and what it does to the action finder
(wieplay/ml/actions.py): the pose model's output decoded, the person nearest the ball, the
log-likelihood ratio of a contact; the serve estimates (a toss, a far flight's first frame);
posteriors from the rules' weights and the "sure" flag."""

from __future__ import annotations

import math

import numpy as np

from tests.test_actions import FPS, VIEW, cand, rally
from wieplay.ml import actions as ac
from wieplay.ml import contact_pose as cp
from wieplay.ml import trajectory as tr


def person(x1: float, y1: float, x2: float, y2: float, wrists_y: float | None = None) -> cp.Person:
    k = np.zeros((17, 3), np.float32)
    k[:, 2] = 0.9
    h = y2 - y1
    k[5] = (x1 + 0.2 * (x2 - x1), y1 + 0.2 * h, 0.9)  # shoulders
    k[6] = (x2 - 0.2 * (x2 - x1), y1 + 0.2 * h, 0.9)
    wy = y1 + 0.5 * h if wrists_y is None else wrists_y
    k[9] = (x1, wy, 0.9)
    k[10] = (x2, wy, 0.9)
    return (np.array([x1, y1, x2, y2], np.float32), 0.9, k)


def test_the_person_nearest_the_ball_and_the_contact_ratio() -> None:
    far_away = person(0, 0, 50, 150)
    setter = person(500, 300, 560, 480, wrists_y=290)  # hands up, at the ball
    found = cp.at_ball([far_away, setter], 530, 288)
    assert found is not None
    assert found.d < 0.2 and found.wr_up > 0.2 and found.near == 1
    nobody = cp.at_ball([far_away], 530, 288)
    assert nobody is not None and nobody.d > 2
    assert found.llr() > 0.5 > 0 > nobody.llr()
    assert cp.at_ball([], 1, 1) is None
    assert cp.AtBall.of(found.as_list()) == cp.AtBall(round(found.d, 4), round(found.wr_up, 4), 1)


def test_the_pose_output_is_decoded_and_moved_into_the_frame() -> None:
    out = np.zeros((56, 3), np.float32)
    out[:5, 0] = (100, 100, 40, 120, 0.9)  # cx cy w h score
    out[:5, 1] = (102, 101, 40, 118, 0.8)  # the same person again: suppressed
    out[:5, 2] = (400, 300, 30, 90, 0.1)  # too unsure
    out[5:, 0] = np.tile([100, 60, 0.9], 17)
    people = cp.decode(out)
    assert len(people) == 1
    [(box, score, k)] = cp.in_frame(people, 10, 20)
    assert box.tolist() == [90.0, 60.0, 130.0, 180.0] and math.isclose(score, 0.9, rel_tol=1e-6)
    assert k[0].tolist()[:2] == [110.0, 80.0]


def test_the_crop_stays_inside_the_frame() -> None:
    assert cp.crop_origin(0.0, 0.0, 1920, 1080) == (0, 0)
    assert cp.crop_origin(1.0, 1.0, 1920, 1080) == (1280, 440)
    assert cp.crop_origin(0.5, 0.4, 1280, 720) == (320, 68)


def test_the_cue_raises_or_lowers_a_contact_prior() -> None:
    c = cand(1.0, "near", kind=ac.LOW)
    plain = ac.contact_prior(c)
    c.pose = 2.0
    assert ac.contact_prior(c) > plain
    c.pose = -2.0
    assert ac.contact_prior(c) < plain


def track_of(ys: list[float], start: int, total: int) -> tr.Track:
    x = np.full(total, np.nan)
    y = np.full(total, np.nan)
    ids = np.full(total, -1, np.int32)
    y[start : start + len(ys)] = ys
    x[start : start + len(ys)] = 0.5
    ids[start : start + len(ys)] = 0
    return tr.Track(fps=FPS, x=x, y=y, track_id=ids, filled=np.zeros(total, bool))


def test_serve_estimates_from_a_toss_and_from_a_far_flight() -> None:
    # A ball rising fast from low in the picture: the toss; the hit about 0.8 s later.
    toss = track_of([0.9 - 0.03 * i for i in range(20)], start=30, total=200)
    [found] = ac.serve_candidates(toss, VIEW)
    assert found.kind == ac.TOSS and found.frame == 30 + round(ac.TOSS_HIT_S * FPS)
    assert found.p_near > 0.9
    # A flight first seen at the net's height: the far serve left the server ~0.5 s before.
    far = track_of([VIEW.v_net + 0.002 * i for i in range(20)], start=60, total=200)
    [found] = ac.serve_candidates(far, VIEW)
    assert found.kind == ac.ARRIVE and found.frame == 60 - round(ac.ARRIVE_S * FPS)
    assert found.p_near < 0.1
    # Only the serve may use them.
    assert ac.ROLE_KIND["1"][ac.TOSS] >= ac.INF and ac.ROLE_KIND["S"][ac.TOSS] == 0.0


def test_the_serve_is_the_first_toss_when_the_server_is_known() -> None:
    early = cand(0.5, "near", kind=ac.TOSS)
    late = cand(2.0, "near", kind=ac.TOSS)
    cands = [early, late, cand(3.5, "far", kind=ac.LOW)]
    assert ac.serve_pick(cands, FPS, 0.95, 0.0) == 0
    assert ac.serve_pick(cands, FPS, 0.5, 0.0) is None  # the server is not known
    assert ac.serve_pick(cands, FPS, 0.05, 0.0) is None  # far: no far estimate here


def test_posteriors_and_the_sure_flag() -> None:
    r = rally(serve="near", sure=0.97)
    cands = [
        cand(0.6, "near", kind=ac.TOSS),
        cand(2.0, "far", kind=ac.LOW),
        cand(3.0, "far"),
        cand(3.9, "far", s_out=12.0, vy_out=4.0),
    ]
    first = ac.label_posterior(r, cands, FPS, VIEW)
    again = ac.label_posterior(r, cands, FPS, VIEW)
    assert [a.posterior for a in first] == [a.posterior for a in again]  # seeded by the rally
    assert [a.code for a in first][:2] == ["S", "R"]
    assert all(a.posterior is not None and 0 <= a.posterior <= 1 for a in first)
    assert all(
        a.alternative == "" or a.alternative == "none" or ":" in a.alternative for a in first
    )
    sure = ac.label_sure(r, cands, FPS, VIEW)
    serve = sure[0]
    assert serve.code == "S" and serve.sure == (
        serve.posterior is not None and serve.posterior >= ac.SURE_P
    )
    assert not any(a.sure for a in sure[1:] if a.code not in ac.SURE_SKILLS)
    # Without a known server the serve is never sure.
    assert not any(a.sure for a in ac.label_sure(rally(serve="near", sure=0.5), cands, FPS, VIEW))
