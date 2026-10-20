"""Suggested actions per rally (PLAN §10.4a): serve, reception, set, attack, block, dig and
freeball at the ball's contacts, with a side, sometimes a grade, and the ball's position for
the player attribution that follows (PLAN §10.5).

    signals ─▶ the ball's path (trajectory.clean) ─▶ contact candidates: sharp turns and
    changes of speed, tracks that start fast, direction changes while the tracker lost the
    ball ─▶ per candidate: which side of the net it is on (near: the camera's end) ─▶ per
    rally, the most plausible sequence under volleyball's rules (v0) or a classifier that
    learned from the coaches' tags (v1) ─▶ actions with times, sides, skills, grade hints

Worker side (numpy; v1 also scikit-learn). Sides are the camera's here (near, far); the API
turns them into us and them with the rally's sides (wieplay/core/actions.py).

**Which side.** The camera stands behind one end line at about net height (PLAN §7.1), so a
ball at net height looks the same on either side; what tells them apart:

- the far half of the court, up to 4 m, covers a small part of the picture (`View.far`): a
  contact outside it is on the near side;
- a flight's curvature: gravity bends the path of a far ball less (in the picture) than a
  near one; the depth that explains the bend of the flights before and after a contact;
- apparent speed: a far ball moves slower in the picture.

The camera is the coach's calibration when there is one (PLAN §10.6), else a nominal camera
of the filming standard (2.75 m high, 4 m behind the end line, its horizon at the ball's
median height).

**v0, the rules.** Per rally a small dynamic programme over the candidates: each one is
skipped (not a contact: a bounce on the floor, a ball thrown back, the tracker's noise) or
plays a role on a side: the serve (first, on the server's side), the 1st/2nd/3rd touch of a
side, a block (at the net, right after the other side's attack), or the floor (the rally is
over). A side has three touches, the third goes over; a block does not count. The cheapest
sequence (side evidence, the kind of contact, how long the ball flew) gives the skills: the
first touch after a serve is a reception, after an attack a dig; the second a set; the touch
that sends the ball over an attack (fast or downwards) or a freeball (else); a first touch
that goes straight over is an overpass (grade `/`). Grade hints come from who won the rally:
an ace (`#`) when the serve's side won without a touch of the receivers (or `=` on the
reception that touched it last), a kill (`#`) for the winner's last attack, `/` for an attack
blocked for a point, `=` for the loser's last attack; else no grade.

**v1, learned.** Two gradient-boosted classifiers over every candidate (where it is, how the
ball turned, how fast, the flights' depth, the side evidence, its place in the rally and v0's
answer): the skill or "no action", and the side. Labels: the coaches' tags of reviewed games,
matched to candidates within ±0.3 s. Scored on held-out games (split by match, PLAN §11) with
wieplay/ml/action_eval.py; used only when its mean action F1 beats v0's.
"""

from __future__ import annotations

import hashlib
import io
import itertools
import math
import pickle
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from wieplay.core import court
from wieplay.ml import action_eval
from wieplay.ml import trajectory as tr
from wieplay.ml.features import HEIGHT, WIDTH, Arr, Signals, ball_tracks

MODEL_FORMAT = 1
SKILLS = ("S", "R", "E", "A", "B", "D", "F")
NONE = "none"
CLASSES = (NONE, *SKILLS)
NEAR, FAR = "near", "far"

# Candidate kinds: the trajectory's contacts, then two of our own.
BOUNCE, LOW, HIGH = tr.BOUNCE, tr.LOW, tr.HIGH
START = 3  # a track that starts fast: the ball was just hit (often the serve)
GAP = 4  # the ball changed direction while the tracker had lost it
# Serves the tracker does not see (on the hand-labelled rallies of two matches it missed 17
# of 30): the time is estimated from what it does see.
TOSS = 5  # near: a ball rising fast from low in the picture is the toss; the hit follows
ARRIVE = 6  # far: the first flight seen at the net's height left the server a moment earlier
KIND_NAMES = ("bounce", "low", "high", "start", "gap", "toss", "arrive")
TOSS_HIT_S = 0.82  # from the toss's first frame to the hit (0.75-0.95 s on 10 near serves)
ARRIVE_S = 0.5  # from the far serve's hit to its first frame seen (0.25-0.8 s on 15)
TOSS_LOW = 0.08  # the toss starts this far below the net's foot in the picture (image heights)
TOSS_RISE = 0.06  # ... and rises at least this much in its first five frames
ARRIVE_BAND = (-0.12, 0.1)  # around the net tape's row

REF_FPS = 30.0  # speeds are given per frame at this rate
K = 2  # frames before and after a contact for its directions (as trajectory.TURN_K)
APEX_RATIO = 1.8  # up then down at about the same speed: the top of a flight, no contact
START_SPEED = 3.0  # px per frame: a track starting faster was just hit
MAX_GAP_FRAMES = 12  # a contact in a tracker gap only when the gap is shorter (0.4 s)
GAP_TURN_DEG = 50.0
MIN_DT_S = 0.12  # two contacts closer than this are one
LEAD_S = 1.0  # candidates from this long before a rally's start ...
TAIL_S = 0.3  # ... to this long after its end
G = 9.81
# Nominal camera (PLAN §7.1: centred behind the end line, 2.5-3 m high).
NOMINAL = {"x": 4.5, "y": -4.0, "z": 2.75, "focal": 0.62}
NET_TOP_M = 2.43
MAX_CONTACT_M = 3.6  # no hand reaches higher
STOP_RATIO = 0.2  # a fast ball that nearly stops: the top of a high ball, the net, a catch
DEAD_SPEED = (3.0, 5.0)  # a slow ball low in the picture, slow out too: rolling, handed on
FAR_TOP_M = 4.0  # contacts above this height are rare
NET_BAND = 0.07  # a block's ball is within this of the net tape's row (image heights)
# Floor point under the ball at the usual contact height of each skill (m).
CONTACT_HEIGHT = {"S": 2.6, "R": 0.8, "E": 2.4, "A": 2.9, "B": 2.8, "D": 0.7, "F": 1.5}


# --- the camera ---------------------------------------------------------------------------------


def nominal_camera(horizon: float, aspect: float = 16 / 9) -> court.Camera:
    """A camera of the filming standard whose horizon (points at its height) shows at image
    row `horizon` (the ball's median height is close to it)."""
    f = NOMINAL["focal"]
    pitch = math.atan(max(-0.4, min(0.4, (0.5 - horizon) / f)))  # down is positive
    s, c = math.sin(pitch), math.cos(pitch)
    rays = ((1 / f, 0.0, 0.0), (0.0, -s / f, c), (0.0, -c / f, -s))
    return court.Camera(
        center=(NOMINAL["x"], NOMINAL["y"], NOMINAL["z"]), focal=f, rays=rays, aspect=aspect
    )


def camera_from_dict(data: dict[str, Any]) -> court.Camera:
    rays = data["rays"]
    return court.Camera(
        center=(float(data["center"][0]), float(data["center"][1]), float(data["center"][2])),
        focal=float(data["focal"]),
        rays=court.from_lists(rays),
        aspect=float(data["aspect"]),
    )


def _hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o: tuple[float, float], a: tuple[float, float], b: tuple[float, float]) -> float:
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower: list[tuple[float, float]] = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: list[tuple[float, float]] = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


def _inside(poly: Sequence[tuple[float, float]], u: float, v: float) -> bool:
    inside = False
    n = len(poly)
    for i in range(n):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % n]
        if (y1 > v) != (y2 > v) and u < x1 + (v - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


@dataclass
class View:
    """What the picture shows of the court."""

    camera: court.Camera
    calibrated: bool
    far: list[tuple[float, float]]  # the far half up to 4 m (+1.5 m around), in the image
    v_net: float  # image row of the net tape's middle
    v_net_floor: float  # image row of the centre line's middle
    # The highest a contact shows in the picture: a jump serve at 3.6 m, a metre behind the
    # near end line. A ball above it is in the air (the top of a high ball, the ceiling).
    v_top: float = 0.2

    @classmethod
    def of(cls, camera: court.Camera, calibrated: bool) -> View:
        corners = []
        for x in (-1.5, court.WIDTH_M + 1.5):
            for y in (court.NET_Y, court.LENGTH_M + 1.5):
                for z in (0.0, FAR_TOP_M):
                    p = camera.project(x, y, z)
                    if p is not None:
                        corners.append(p)
        tape = camera.project(court.WIDTH_M / 2, court.NET_Y, NET_TOP_M)
        foot = camera.project(court.WIDTH_M / 2, court.NET_Y, 0.0)
        top = camera.project(court.WIDTH_M / 2, -1.0, MAX_CONTACT_M)
        return cls(
            camera=camera,
            calibrated=calibrated,
            far=_hull(corners),
            v_net=tape[1] if tape else 0.45,
            v_net_floor=foot[1] if foot else 0.58,
            v_top=max(0.05, top[1]) if top else 0.2,
        )

    def in_far(self, u: float, v: float) -> bool:
        return len(self.far) >= 3 and _inside(self.far, u, v)

    def floor_point(self, u: float, v: float, height: float) -> tuple[float, float] | None:
        """Where on the floor a ball seen at (u, v) at `height` metres is, or None when the
        view cannot say (a ray at about the camera's height)."""
        try:
            x, y = self.camera.to_court(u, v, height)
        except court.CourtError:
            return None
        if not (-6.0 <= x <= court.WIDTH_M + 6 and -8.0 <= y <= court.LENGTH_M + 8):
            return None
        return x, y


def make_view(sig: Signals, camera: dict[str, Any] | None, aspect: float = 16 / 9) -> View:
    if camera:
        try:
            return View.of(camera_from_dict(camera), True)
        except (KeyError, TypeError, ValueError, court.CourtError):
            pass
    trk = ball_tracks(sig)
    ys = sig.ball_y[trk.in_track] / HEIGHT
    horizon = float(np.median(ys)) if len(ys) else 0.43
    return View.of(nominal_camera(horizon, aspect), False)


# --- candidates ---------------------------------------------------------------------------------


@dataclass
class Candidate:
    """A moment the ball may have been played."""

    frame: int  # analysis frame
    x: float  # normalised image position
    y: float
    kind: int
    turn: float  # degrees
    speed_in: float  # px per frame at REF_FPS
    speed_out: float
    vx_in: float = 0.0  # px per frame at REF_FPS, image y down
    vy_in: float = 0.0
    vx_out: float = 0.0
    vy_out: float = 0.0
    depth_in: float = math.nan  # metres from the camera that explain the flights' bend
    depth_out: float = math.nan
    p_near: float = 0.5
    in_far: bool = False
    # The player at the ball (wieplay/ml/contact_pose.py): the log-likelihood ratio of "a
    # contact" from how near a person's hands are, arms raised, people around; None unseen.
    pose: float | None = None

    def t(self, fps: float) -> float:
        return self.frame / fps


def _vel(px: Arr, py: Arr, a: int, b: int) -> tuple[float, float] | None:
    if a < 0 or b >= len(px) or b <= a or math.isnan(px[a]) or math.isnan(px[b]):
        return None
    return (float(px[b] - px[a]) / (b - a), float(py[b] - py[a]) / (b - a))


def _angle(a: tuple[float, float], b: tuple[float, float]) -> float:
    na, nb = math.hypot(*a), math.hypot(*b)
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    cos = (a[0] * b[0] + a[1] * b[1]) / (na * nb)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def _flight_depth(track: tr.Track, f0: int, f1: int, focal: float) -> float:
    """The distance (m) at which gravity bends the picture's path between two frames as much
    as it does: the vertical acceleration in the picture is about focal * g / depth."""
    frames = np.arange(f0 + 1, f1)
    if len(frames) < 8:
        return math.nan
    frames = frames[~track.filled[frames] & ~np.isnan(track.y[frames])]
    if len(frames) < 8:
        return math.nan
    t = (frames - f0) / track.fps
    v = track.y[frames]
    design = np.stack([np.ones_like(t), t, t * t], axis=1)
    coef, *_ = np.linalg.lstsq(design, v, rcond=None)
    resid = float(np.sqrt(np.mean((design @ coef - v) ** 2)))
    acc = 2.0 * float(coef[2])
    if acc <= 0.05 or resid > 0.004:
        return math.nan
    return float(min(60.0, focal * G / acc))


def candidates(track: tr.Track, view: View | None = None) -> list[Candidate]:
    """Every moment of the video the ball may have been played, in time order."""
    fps = track.fps or REF_FPS
    scale = fps / REF_FPS  # px per analysis frame → per frame at 30 fps
    px, py = track.x * WIDTH, track.y * HEIGHT
    focal = view.camera.focal if view is not None else NOMINAL["focal"]
    out: list[Candidate] = []
    spans = track.spans()
    cuts: dict[int, list[int]] = {}
    for found in track.contacts:
        cuts.setdefault(int(track.track_id[found.frame]), []).append(found.frame)
    # Turns and changes of speed inside a track.
    for tc in track.contacts:
        f = tc.frame
        vin = _vel(px, py, f - K, f) or (0.0, 0.0)
        vout = _vel(px, py, f, f + K) or (0.0, 0.0)
        s_in, s_out = tc.speed_in / scale, tc.speed_out / scale
        ratio = (s_out + 0.5) / (s_in + 0.5)
        if vin[1] < -0.3 and vout[1] > 0.3 and 1 / APEX_RATIO < ratio < APEX_RATIO:
            continue  # the top of a flight
        out.append(
            Candidate(
                frame=f, x=tc.x, y=tc.y, kind=tc.kind, turn=tc.turn_deg,
                speed_in=s_in, speed_out=s_out,
                vx_in=vin[0] / scale, vy_in=vin[1] / scale,
                vx_out=vout[0] / scale, vy_out=vout[1] / scale,
            )
        )  # fmt: skip
    # Tracks that start fast, and direction changes while the tracker lost the ball.
    for i, (a, b) in enumerate(spans):
        if b - a < 5:
            continue
        v_start = _vel(px, py, a, a + 3)
        if v_start is None:
            continue
        prev = spans[i - 1] if i > 0 and (a - spans[i - 1][1]) <= MAX_GAP_FRAMES else None
        if prev is not None and prev[1] - prev[0] >= 3:
            v_end = _vel(px, py, prev[1] - 3, prev[1])
            if v_end is None:
                continue
            s_end, s_start = math.hypot(*v_end), math.hypot(*v_start)
            turn = _angle(v_end, v_start)
            ratio = (s_start + 0.5) / (s_end + 0.5)
            if (turn >= GAP_TURN_DEG and min(s_end, s_start) >= 1.0) or not (
                1 / tr.SPEED_RATIO < ratio < tr.SPEED_RATIO
            ):
                f = (prev[1] + a) // 2
                out.append(
                    Candidate(
                        frame=f,
                        x=float((track.x[prev[1]] + track.x[a]) / 2),
                        y=float((track.y[prev[1]] + track.y[a]) / 2),
                        kind=GAP, turn=turn,
                        speed_in=s_end / scale, speed_out=s_start / scale,
                        vx_in=v_end[0] / scale, vy_in=v_end[1] / scale,
                        vx_out=v_start[0] / scale, vy_out=v_start[1] / scale,
                    )
                )  # fmt: skip
            continue
        s_start = math.hypot(*v_start) / scale
        if s_start >= START_SPEED:
            out.append(
                Candidate(
                    frame=max(0, a - 1), x=float(track.x[a]), y=float(track.y[a]), kind=START,
                    turn=0.0, speed_in=0.0, speed_out=s_start,
                    vx_out=v_start[0] / scale, vy_out=v_start[1] / scale,
                )
            )  # fmt: skip
    out.sort(key=lambda c: c.frame)
    # The depth of the flights on either side of each candidate (same track only).
    for c in out:
        tid = int(track.track_id[min(c.frame, track.frames - 1)])
        if tid < 0 or c.kind == GAP:
            continue
        a, b = spans[tid] if tid < len(spans) else (c.frame, c.frame)
        edges = sorted({a, b, *cuts.get(tid, [])})
        before = max((e for e in edges if e < c.frame), default=None)
        after = min((e for e in edges if e > c.frame), default=None)
        if before is not None:
            c.depth_in = _flight_depth(track, before, c.frame, focal)
        if after is not None:
            c.depth_out = _flight_depth(track, c.frame, after, focal)
    if view is not None:
        out = [c for c in out if plausible(c, view)]
        for c in out:
            c.in_far = view.in_far(c.x, c.y)
            c.p_near = side_evidence(c, view)
        out.extend(serve_candidates(track, view))
        out.sort(key=lambda c: c.frame)
    return out


def serve_candidates(track: tr.Track, view: View) -> list[Candidate]:
    """Where a serve the tracker did not see probably was: near, a toss (a flight starting
    low in the picture and rising fast) and TOSS_HIT_S later the hit; far, the first frame
    of a flight at the net's height and ARRIVE_S before it the hit. Only the serve's role
    can use them (ROLE_KIND), the rally's start decides which one."""
    fps = track.fps or REF_FPS
    out: list[Candidate] = []
    for a, b in track.spans():
        if b - a < 5 or math.isnan(track.y[a]) or math.isnan(track.y[a + 5]):
            continue
        x, y = float(track.x[a]), float(track.y[a])
        if y > view.v_net_floor + TOSS_LOW and track.y[a + 5] - y < -TOSS_RISE:
            f = min(a + round(TOSS_HIT_S * fps), track.frames - 1)
            fx = float(track.x[f]) if not math.isnan(track.x[f]) else x
            fy = (
                float(track.y[f])
                if not math.isnan(track.y[f])
                else float(np.nanmin(track.y[a : b + 1]))
            )
            out.append(
                Candidate(frame=f, x=fx, y=fy, kind=TOSS, turn=0.0, speed_in=0.0, speed_out=0.0,
                          p_near=0.97)
            )  # fmt: skip
        elif ARRIVE_BAND[0] <= y - view.v_net <= ARRIVE_BAND[1] and abs(x - 0.5) < 0.35:
            f = max(0, a - round(ARRIVE_S * fps))
            out.append(
                Candidate(frame=f, x=x, y=y, kind=ARRIVE, turn=0.0, speed_in=0.0, speed_out=0.0,
                          p_near=0.05, in_far=True)
            )  # fmt: skip
    return out


def plausible(c: Candidate, view: View) -> bool:
    """Whether a candidate can be a player's contact at all (the eyeball check on the first
    real game found these four kinds of false ones):

    - above any hand (the top of a high ball, the ceiling);
    - a fast rising ball that nearly stops: the top of a steep high ball (the tracker's turn
      is the deceleration), or a ball stopped by the net or caught;
    - a ball low in the picture that comes in slowly and leaves slowly: rolling on the floor,
      handed to the server, picked up after the point."""
    if c.y < view.v_top - 0.02:
        return False
    if c.speed_in > 8.0 and c.speed_out < STOP_RATIO * c.speed_in:
        return False
    if c.vy_in < -1.0 and c.turn < 30 and c.speed_out < 0.45 * c.speed_in:
        return False  # a rising ball slowing down without turning: the top of its flight
    low = c.y > view.v_net_floor + 0.01
    return not (low and c.speed_in < DEAD_SPEED[0] and c.speed_out < DEAD_SPEED[1])


def _depth_split(view: View) -> float:
    """Metres from the camera to the net (along the court): nearer flights are near side."""
    cam = view.camera.center
    return max(4.0, court.NET_Y - cam[1])


def side_evidence(c: Candidate, view: View) -> float:
    """P(the contact is on the near side) from where it is, the flights' bend and speed."""
    if not c.in_far:
        # Outside what the far half can show: near, unless high above everything.
        return 0.93 if c.y > view.v_net - 0.25 else 0.75
    logit = 0.0
    # Below the net's foot in the picture: only the near court is there.
    if c.y > view.v_net_floor + 0.01:
        logit += 2.0
    split = _depth_split(view)
    depths = [d for d in (c.depth_in, c.depth_out) if not math.isnan(d)]
    if depths:
        d = min(depths) if c.kind in (LOW, BOUNCE) else float(np.mean(depths))
        logit += 2.2 * math.tanh(1.6 * math.log(split / max(d, 0.5)))
    speed = max(c.speed_in, c.speed_out)
    logit += 1.0 * math.tanh(1.2 * math.log(max(speed, 0.3) / 5.0))
    return 1.0 / (1.0 + math.exp(-logit))


# --- rallies and the rules (v0) -------------------------------------------------------------------


@dataclass
class RallyIn:
    """What the API knows of a rally, in the camera's terms."""

    id: str
    start_ms: int
    end_ms: int
    serve_near: float | None = None  # P(the server stood at the near end line)
    won_near: float | None = None  # P(the near side won the rally)


@dataclass
class Action:
    rally_id: str
    frame: int  # analysis frame
    code: str
    side: str  # near | far
    grade: str | None
    confidence: float
    x: float
    y: float
    gap: bool  # the time is the middle of a tracker gap (less precise)
    court: tuple[float, float] | None = None
    role: str = ""  # v0's role: S 1 2 3 B
    # The posterior of this (skill, side) at this contact, and the likeliest other answer
    # there ("" when there is none: another skill or side, or no contact at all).
    posterior: float | None = None
    alternative: str = ""
    sure: bool = False  # right without a look (is_sure): accepted in bulk by the coach


# Roles of a used candidate: S serve, 1-3 the touch of a side, B block, END the floor.
ROLES = ("S", "1", "2", "3", "B", "END")
INF = 1e9
ROLE_KIND = {  # cost of a role for a kind of candidate (the serve estimates: serves only)
    "S": {BOUNCE: 2.5, LOW: 2.0, HIGH: 0.0, START: 0.0, GAP: 0.4, TOSS: 0.0, ARRIVE: 0.3},
    "1": {BOUNCE: 0.1, LOW: 0.0, HIGH: 0.5, START: 0.9, GAP: 0.3, TOSS: INF, ARRIVE: INF},
    "2": {BOUNCE: 0.8, LOW: 0.3, HIGH: 0.0, START: 0.8, GAP: 0.3, TOSS: INF, ARRIVE: INF},
    "3": {BOUNCE: 1.0, LOW: 0.7, HIGH: 0.0, START: 0.5, GAP: 0.3, TOSS: INF, ARRIVE: INF},
    "B": {BOUNCE: 1.5, LOW: 1.2, HIGH: 0.0, START: 0.3, GAP: 0.3, TOSS: INF, ARRIVE: INF},
    "END": {BOUNCE: 0.0, LOW: 1.0, HIGH: 2.5, START: INF, GAP: INF, TOSS: INF, ARRIVE: INF},
}
SIDE_WEIGHT = 0.8  # the side evidence is soft: the sequence decides the close ones
TOSS_COST = 1.2  # a track starting upwards at the server is the toss, the hit comes later
END_WINDOW_S = 2.5  # the floor bounce that ends a rally is in its last seconds
PRE_SERVE_SKIP = 0.3  # candidates in the toss window before the serve skip cheaply
TOSS_WINDOW_S = 2.5  # ... the first seconds of the rally
SERVE_LATE_S = 3.0  # a serve later than this into the rally costs SERVE_LATE per second
SERVE_LATE = 2.0
UNSEEN_SERVE = 1.6  # the rally starts without a serve the tracker saw (or the rally is a part)
USE_BONUS = 0.7  # rallies are made of contacts: using a plausible candidate is preferred
DEAD_TAIL_S = 0.5  # the rally finder closes a rally about a second after the ball is dead:
FADE_S = 1.5  # ... the bonus fades out over this long before that


def contact_prior(c: Candidate) -> float:
    """How likely a candidate is a contact at all, before the rally's sequence is known."""
    if c.kind == HIGH:
        ratio = (c.speed_out + 0.5) / (c.speed_in + 0.5)
        q = 0.35 + c.turn / 250 + 0.2 * abs(math.log(ratio))
        if c.turn < 60 and max(c.speed_in, c.speed_out) < 4.5:
            q = 0.25  # a slow ball turning a little: mostly a far flight's wobble
    elif c.kind == LOW:
        q = 0.6 + c.turn / 600
    elif c.kind == BOUNCE:
        q = 0.5
    elif c.kind == START:
        q = 0.45
    elif c.kind in (TOSS, ARRIVE):
        q = 0.4
    else:
        q = 0.4 + (0.1 if c.turn >= 90 else 0.0)
    q = min(0.9, max(0.2, q))
    if c.pose is not None:
        logit = math.log(q / (1 - q)) + POSE_WEIGHT * c.pose
        q = 1 / (1 + math.exp(-logit))
    return min(0.95, max(0.05, q))


POSE_WEIGHT = 1.0


CROSS_COST = {"S": 0.0, "1": 1.2, "2": 0.6, "3": 0.0, "B": 0.0}
BLOCK_COST = {"1": 2.0, "2": 1.0, "3": 0.4}


def _clip(p: float) -> float:
    return min(0.95, max(0.05, p))


def _side_cost(p_near: float, side: str) -> float:
    p = _clip(p_near)
    return -math.log(p if side == NEAR else 1 - p)


def _emit(
    c: Candidate,
    side: str,
    role: str,
    view: View,
    serve_near: float | None,
    left_s: float | None = None,
) -> float:
    """Cost of using a candidate in a role on a side (the skip costs -log(1 - prior)).
    `left_s`: how long the rally goes on after the candidate; in its last moments the ball is
    mostly dead (on the floor, thrown back), so a contact there gets no bonus."""
    cost = ROLE_KIND[role].get(c.kind, 1.0)
    if cost >= INF:
        return INF
    if role == "END":
        # The floor: not an action, the prior is about contacts.
        return cost + 0.5 * SIDE_WEIGHT * _side_cost(c.p_near, side)
    bonus = USE_BONUS
    if left_s is not None:
        bonus *= min(1.0, max(0.0, (left_s - DEAD_TAIL_S) / FADE_S))
    cost -= math.log(contact_prior(c)) + bonus
    if role == "S":
        # The server's side (from the scoring) and where the candidate is must both agree.
        if serve_near is not None:
            cost += _side_cost(serve_near, side)
        cost += SIDE_WEIGHT * _side_cost(c.p_near, side)
        if c.kind == START and c.vy_out < -2.0:
            cost += TOSS_COST
    else:
        cost += SIDE_WEIGHT * _side_cost(c.p_near, side)
    if role == "B" and abs(c.y - view.v_net) > NET_BAND:
        cost += 2.0
    return cost


def _other(side: str) -> str:
    return FAR if side == NEAR else NEAR


SERVE_FLIGHT_S = 0.7  # the serve takes at least this long to reach the receivers
SAME_SIDE_S = 0.4  # between two touches of one side
CROSS_S = 0.25  # between the touch that sends the ball over and the next one
MAX_FLIGHT_S = 3.0  # no flight is longer: after a longer gap the tracker missed contacts
UNSEEN_COST = 1.5


def _trans(a: tuple[str, str], b: tuple[str, str], dt: float, seen_late: bool = False) -> float:
    """Cost of going from a used candidate's (side, role) to the next one's. `seen_late`:
    the first candidate was the ball already in flight (its track started fast), so the
    serve's flight is partly unseen. After a long flight the tracker missed contacts: any
    side and touch may follow, at a cost."""
    (sa, ra), (sb, rb) = a, b
    if ra == "END" or rb == "S":
        return INF
    if rb == "END":
        return 0.3 if dt >= 0.15 else INF
    if dt < MIN_DT_S and not (rb == "B" and dt >= 0.05):
        return INF
    if dt > MAX_FLIGHT_S:
        if rb == "B":
            return INF
        return UNSEEN_COST + (1.0 if ra == "S" else 0.0)
    if ra == "S":
        flight = 0.15 if seen_late else SERVE_FLIGHT_S
        return 0.0 if (sb != sa and rb == "1" and dt >= flight) else INF
    if ra == "B":
        # After a block the ball is back on the attackers' side or stays with the blockers.
        if rb == "1":
            return 0.0 if sb != sa else 0.3
        return INF
    k = int(ra)
    if sb == sa:
        if rb in ("2", "3") and int(rb) == k + 1 and dt >= SAME_SIDE_S:
            return 0.0
        return INF
    if rb == "1" and dt >= CROSS_S:
        return CROSS_COST[ra]
    if rb == "B" and dt <= 0.6:
        return BLOCK_COST[ra]
    return INF


STATES = [(s, r) for r in ROLES for s in (NEAR, FAR)]
MAX_GAP_COST = 12.0  # skipping more than this between two used candidates is not considered


@dataclass
class Lattice:
    """The costs of one rally's sequences of roles (the rules, v0): a used candidate's
    emission per state, the cost of starting a sequence at it, of skipping candidates, and
    of the transitions. The cheapest path is v0's answer; the paths' weights at a
    temperature give its posteriors."""

    n: int
    t: list[float]
    emit: list[list[float]]
    start: list[list[float]]  # INF when a sequence cannot start there
    pre: list[float]  # pre[i]: the cost of skipping candidates 0..i-1
    kinds: list[int]

    def edge(self, j: int, m: int, i: int, k: int) -> float:
        """From candidate j in state m to candidate i in state k (skipping those between)."""
        gap = self.pre[i] - self.pre[j + 1]
        late_seen = STATES[m][1] == "S" and self.kinds[j] == START
        tc = _trans(STATES[m], STATES[k], self.t[i] - self.t[j], late_seen)
        return INF if tc >= INF else gap + tc

    def end(self, i: int, k: int) -> float:
        """The candidates after i skipped (nothing after the floor)."""
        return 0.0 if STATES[k][1] == "END" else self.pre[self.n] - self.pre[i + 1]

    def gap_ok(self, j: int, i: int) -> bool:
        return self.pre[i] - self.pre[j + 1] <= MAX_GAP_COST


SERVE_PICK_COST = 3.0  # a serve elsewhere than at the picked candidate (serve_pick)
SERVE_WINDOW_S = (-1.5, 2.5)  # around the rally's start: where its serve is


def serve_pick(
    cands: Sequence[Candidate], fps: float, serve_near: float | None, start_s: float | None
) -> int | None:
    """The serve's candidate when the server's side is known: near, the first toss around
    the rally's start; far, the first flight seen at the net (else a track starting fast).
    On the hand-labelled rallies the rules' own choice was a later contact half the time."""
    if serve_near is None or start_s is None or max(serve_near, 1 - serve_near) < 0.75:
        return None
    lo, hi = start_s + SERVE_WINDOW_S[0], start_s + SERVE_WINDOW_S[1]
    window = [i for i, c in enumerate(cands) if lo <= c.t(fps) <= hi]
    kinds = (TOSS,) if serve_near >= 0.5 else (ARRIVE, START)
    for kind in kinds:
        found = [i for i in window if cands[i].kind == kind]
        if found:
            return found[0]
    return None


def lattice(
    cands: Sequence[Candidate],
    fps: float,
    view: View,
    serve_near: float | None,
    start_s: float | None = None,
    end_s: float | None = None,
) -> Lattice:
    n = len(cands)
    picked = serve_pick(cands, fps, serve_near, start_s)
    skip = [-math.log(1 - contact_prior(c)) for c in cands]
    pre = [0.0]
    for cost_skip in skip:
        pre.append(pre[-1] + cost_skip)
    t = [c.t(fps) for c in cands]
    emit = [
        [
            _emit(c, s, r, view, serve_near, None if end_s is None else end_s - c.t(fps))
            for (s, r) in STATES
        ]
        for c in cands
    ]
    if end_s is not None:
        # The floor ends a rally: only in its last seconds (the finder closes it ~1 s later).
        for i in range(n):
            if t[i] < end_s - END_WINDOW_S:
                for k, (_, r) in enumerate(STATES):
                    if r == "END":
                        emit[i][k] = INF
    t0 = (t[0] if n else 0.0) if start_s is None else start_s
    start = [[INF] * len(STATES) for _ in range(n)]
    for i in range(n):
        for k, (s, r) in enumerate(STATES):
            if r == "S":
                # The serve: the first contact, early in the rally; what comes before it in
                # the first seconds is the toss or the ball handed to the server.
                late = max(0.0, t[i] - t0 - SERVE_LATE_S) * SERVE_LATE
                before = sum(
                    skip[j] * (PRE_SERVE_SKIP if t[j] < t0 + TOSS_WINDOW_S else 1.0)
                    for j in range(i)
                )
                if picked is not None and i != picked:
                    late += SERVE_PICK_COST
                start[i][k] = before + late
            elif r in ("1", "2", "3"):
                # The serve was not seen: the sequence starts with a touch of a side (the
                # receivers', when the scoring knows who served).
                cost = pre[i] + UNSEEN_SERVE + (0.0 if r == "1" else 0.5)
                if r == "1" and serve_near is not None:
                    cost += 0.5 * _side_cost(1 - serve_near, s)
                start[i][k] = cost
    return Lattice(n=n, t=t, emit=emit, start=start, pre=pre, kinds=[c.kind for c in cands])


def decode(
    cands: Sequence[Candidate],
    fps: float,
    view: View,
    serve_near: float | None,
    start_s: float | None = None,
    end_s: float | None = None,
) -> list[tuple[int, str, str, float]]:
    """The cheapest sequence of roles: (candidate index, side, role, side probability) of
    the candidates used, in time order."""
    if not cands:
        return []
    lat = lattice(cands, fps, view, serve_near, start_s, end_s)
    return _side_margins(lat, viterbi(lat))


def viterbi(lat: Lattice) -> list[tuple[int, int]]:
    """The cheapest path: (candidate index, state index) in time order."""
    n, ns = lat.n, len(STATES)
    cost = [[INF] * ns for _ in range(n)]
    back: list[list[tuple[int, int] | None]] = [[None] * ns for _ in range(n)]
    for i in range(n):
        for k, (_, r) in enumerate(STATES):
            e = lat.emit[i][k]
            if e >= INF:
                continue
            best, arg = lat.start[i][k], None
            if r != "S":
                for j in range(i - 1, -1, -1):
                    if not lat.gap_ok(j, i):
                        break
                    for m in range(ns):
                        if cost[j][m] >= INF:
                            continue
                        ec = lat.edge(j, m, i, k)
                        if ec >= INF:
                            continue
                        if cost[j][m] + ec < best:
                            best, arg = cost[j][m] + ec, (j, m)
            if best < INF:
                cost[i][k] = best + e
                back[i][k] = arg
    best_total, end = INF, None
    for i in range(n):
        for k in range(ns):
            if cost[i][k] >= INF:
                continue
            total = cost[i][k] + lat.end(i, k)
            if total < best_total:
                best_total, end = total, (i, k)
    path: list[tuple[int, int]] = []
    node = end
    while node is not None:
        path.append(node)
        node = back[node[0]][node[1]]
    path.reverse()
    return path


def _side_margins(lat: Lattice, path: list[tuple[int, int]]) -> list[tuple[int, str, str, float]]:
    out = []
    for i, k in path:
        side, role = STATES[k]
        # How much the side matters: the cost of the same role on the other side.
        other = STATES.index((_other(side), role))
        margin = lat.emit[i][other] - lat.emit[i][k]
        p_side = 1.0 / (1.0 + math.exp(-margin)) if margin < 50 else 1.0
        out.append((i, side, role, p_side))
    return out


def _lse(values: list[float]) -> float:
    top = max(values)
    if top == -math.inf:
        return -math.inf
    return top + math.log(sum(math.exp(v - top) for v in values))


def sample_paths(
    lat: Lattice, temperature: float, samples: int, seed: int = 0
) -> list[list[tuple[int, int]]]:
    """Paths drawn from the lattice's weights exp(-cost / temperature) (forward filtering,
    backward sampling): how sure the cheapest path is shows in how often its parts recur."""
    import random

    n, ns = lat.n, len(STATES)
    if n == 0:
        return []
    T = temperature
    neg = -math.inf
    fwd = [[neg] * ns for _ in range(n)]
    for i in range(n):
        for k, (_, r) in enumerate(STATES):
            e = lat.emit[i][k]
            if e >= INF:
                continue
            terms = [-lat.start[i][k] / T] if lat.start[i][k] < INF else []
            if r != "S":
                for j in range(i - 1, -1, -1):
                    if not lat.gap_ok(j, i):
                        break
                    for m in range(ns):
                        if fwd[j][m] == neg:
                            continue
                        ec = lat.edge(j, m, i, k)
                        if ec < INF:
                            terms.append(fwd[j][m] - ec / T)
            if terms:
                fwd[i][k] = _lse(terms) - e / T
    ends = [
        (fwd[i][k] - lat.end(i, k) / T, i, k)
        for i in range(n)
        for k in range(ns)
        if fwd[i][k] > neg
    ]
    if not ends:
        return []
    rng = random.Random(seed)  # noqa: S311 - sampling, not secrets

    def draw(options: list[tuple[float, Any]]) -> Any:
        top = max(w for w, _ in options)
        weights = [math.exp(w - top) for w, _ in options]
        return rng.choices([o for _, o in options], weights=weights)[0]

    out = []
    for _ in range(samples):
        node: tuple[int, int] | None = draw([(w, (i, k)) for w, i, k in ends])
        path: list[tuple[int, int]] = []
        while node is not None:
            path.append(node)
            i, k = node
            r = STATES[k][1]
            opts: list[tuple[float, Any]] = []
            if lat.start[i][k] < INF:
                opts.append((-lat.start[i][k] / T, None))
            if r != "S":
                for j in range(i - 1, -1, -1):
                    if not lat.gap_ok(j, i):
                        break
                    for m in range(ns):
                        if fwd[j][m] == neg:
                            continue
                        ec = lat.edge(j, m, i, k)
                        if ec < INF:
                            opts.append((fwd[j][m] - ec / T, (j, m)))
            node = draw(opts) if opts else None
        path.reverse()
        out.append(path)
    return out


def is_attack(c: Candidate) -> bool:
    """The ball sent over is an attack unless it was lobbed: slow and upwards (a freeball)."""
    return not (c.vy_out < -1.5 and c.speed_out < 6.0)


def _strength(c: Candidate) -> float:
    if c.kind == START:
        return 0.6
    if c.kind == GAP:
        return 0.5
    ratio = (c.speed_out + 0.5) / (c.speed_in + 0.5)
    return min(1.0, 0.35 + c.turn / 180 + 0.3 * abs(math.log(ratio)))


def skills_of(used: Sequence[tuple[Candidate, str, str]]) -> list[tuple[str, str | None]]:
    """The skill (and an overpass's grade) of each used candidate of a sequence of roles,
    (candidate, side, role) in time order: the first touch after a serve a reception, after
    anything else a dig; the second a set; the one that goes over an attack unless lobbed."""
    out: list[tuple[str, str | None]] = []
    prev_code: str | None = None
    prev_side: str | None = None
    for n, (c, side, role) in enumerate(used):
        nxt = used[n + 1] if n + 1 < len(used) else None
        crosses = nxt is not None and nxt[1] != side and nxt[2] in ("1", "B")
        if role == "S":
            code = "S"
        elif role == "B":
            code = "B"
        elif role == "1":
            code = "R" if prev_code == "S" and prev_side != side else "D"
        elif role == "2":
            code = "E"
        else:
            code = "A"
        grade = None
        if crosses and role in ("2", "3"):
            code = "A" if is_attack(c) else "F"
        elif crosses and role == "1":
            if is_attack(c) and c.speed_out >= 6.0:
                code = "A"
            else:
                grade = "/"  # an overpass
        if role == "3" and not crosses and not is_attack(c):
            code = "F"
        out.append((code, grade))
        prev_code, prev_side = code, side
    return out


def label(
    rally: RallyIn,
    cands: Sequence[Candidate],
    fps: float,
    view: View,
) -> list[Action]:
    """v0: the actions of one rally (its candidates in time order)."""
    path = decode(cands, fps, view, rally.serve_near, rally.start_ms / 1000, rally.end_ms / 1000)
    used = [(cands[i], side, role, p) for i, side, role, p in path if role != "END"]
    codes = skills_of([(c, side, role) for c, side, role, _ in used])
    actions: list[Action] = []
    for (c, side, role, p_side), (code, grade) in zip(used, codes, strict=True):
        confidence = round(min(0.99, max(0.05, 0.55 * p_side + 0.45 * _strength(c))), 3)
        actions.append(
            Action(
                rally_id=rally.id, frame=c.frame, code=code, side=side, grade=grade,
                confidence=confidence, x=c.x, y=c.y, gap=c.kind == GAP, role=role,
            )
        )  # fmt: skip
    grade_hints(actions, rally, fps)
    return actions


TEMPERATURE = 2.0  # the rules' costs are not calibrated log-odds: flattened like the scoring's
SAMPLES = 400


def label_posterior(
    rally: RallyIn,
    cands: Sequence[Candidate],
    fps: float,
    view: View,
    temperature: float = TEMPERATURE,
    samples: int = SAMPLES,
) -> list[Action]:
    """v0 with posteriors: the cheapest sequence's actions, each with how often sequences
    drawn from the rules' weights put the same skill and side at the same contact."""
    lat = lattice(cands, fps, view, rally.serve_near, rally.start_ms / 1000, rally.end_ms / 1000)
    best = viterbi(lat)
    if not best:
        return []
    seed = int(hashlib.sha256(rally.id.encode()).hexdigest()[:8], 16)
    seen: dict[int, Counter[tuple[str, str]]] = {}
    for path in sample_paths(lat, temperature, samples, seed):
        used = [(i, STATES[k]) for i, k in path if STATES[k][1] != "END"]
        codes = skills_of([(cands[i], side, role) for i, (side, role) in used])
        for (i, (side, _)), (code, _) in zip(used, codes, strict=True):
            seen.setdefault(i, Counter())[(code, side)] += 1
    actions = label(rally, cands, fps, view) if lat.n else []
    by_frame = {c.frame: i for i, c in enumerate(cands)}
    for a in actions:
        found = seen.get(by_frame[a.frame], Counter())
        p = found[(a.code, a.side)] / samples if samples else 0.0
        a.posterior = round(p, 3)
        a.confidence = round(min(0.99, max(0.01, p)), 3)
        rest = [(n, key) for key, n in found.items() if key != (a.code, a.side)]
        unused = samples - sum(found.values())
        if rest and max(rest)[0] >= unused:
            a.alternative = "{}:{}".format(*max(rest)[1])
        elif unused > 0:
            a.alternative = "none"
    return actions


SURE_P = 0.9  # a sure action: its posterior at least this ...
SURE_SERVE_SIDE = 0.95  # ... a serve: from a toss, the server's side known this well
SURE_POSE = 1.0  # ... any other: someone's hands at the ball (contact_pose log-likelihood ratio)
SURE_SKILLS = frozenset({"S"})


def is_sure(a: Action, c: Candidate, rally: RallyIn) -> bool:
    """Whether an action is right without a look (PLAN §10.4a), for "Accept all sure actions".
    Set on hand-labelled rallies so that none of the sure ones was wrong
    (docs/research/actions-2026-10.md)."""
    if a.posterior is None or a.posterior < SURE_P or a.code not in SURE_SKILLS:
        return False
    if a.code == "S":
        side = rally.serve_near
        return c.kind == TOSS and side is not None and max(side, 1 - side) >= SURE_SERVE_SIDE
    return c.pose is not None and c.pose >= SURE_POSE


def label_sure(rally: RallyIn, cands: Sequence[Candidate], fps: float, view: View) -> list[Action]:
    """v0 with posteriors and the "sure" flag."""
    actions = label_posterior(rally, cands, fps, view)
    by_frame = {c.frame: c for c in cands}
    for a in actions:
        a.sure = is_sure(a, by_frame[a.frame], rally)
    return actions


SURE_WINNER = 0.8


def grade_hints(actions: list[Action], rally: RallyIn, fps: float) -> None:
    """Grades that follow from who won the rally (module docstring); left empty when the
    winner is not sure or the end of the rally does not say."""
    if not actions or rally.won_near is None:
        return
    if max(rally.won_near, 1 - rally.won_near) < SURE_WINNER:
        return
    winner = NEAR if rally.won_near >= 0.5 else FAR
    end_s = rally.end_ms / 1000
    last = actions[-1]
    after_last = end_s - last.frame / fps
    if len(actions) == 1 and last.code == "S":
        if after_last <= 4.5:
            last.grade = "#" if last.side == winner else "="
        return
    if last.side == winner:
        if last.code == "A":
            last.grade = "#"
        elif last.code == "B":
            last.grade = "#"
            before = actions[-2] if len(actions) >= 2 else None
            if before is not None and before.code == "A" and before.side != winner:
                before.grade = "/"
        return
    # The loser touched last.
    if last.code == "A" and after_last <= 3.0:
        last.grade = "="
    elif last.code == "R" and len(actions) >= 2 and actions[-2].code == "S":
        actions[-2].grade = "#"
        last.grade = "="
    elif last.code == "D" and len(actions) >= 2:
        before = actions[-2]
        if before.code == "A" and before.side == winner:
            before.grade = "#"


def in_rally(cands: Sequence[Candidate], rally: RallyIn, fps: float) -> list[Candidate]:
    a = (rally.start_ms / 1000 - LEAD_S) * fps
    b = (rally.end_ms / 1000 + TAIL_S) * fps
    return [c for c in cands if a <= c.frame <= b]


def place(actions: list[Action], view: View) -> None:
    """The floor point under the ball at contact, with a calibrated camera only."""
    if not view.calibrated:
        return
    for a in actions:
        found = view.floor_point(a.x, a.y, CONTACT_HEIGHT.get(a.code, 1.5))
        if found is None:
            continue
        x, y = found
        if a.code == "B":
            y = court.NET_Y + (-0.3 if a.side == NEAR else 0.3)
        if (a.side == NEAR) != (y < court.NET_Y):
            continue  # the view and the sequence disagree: no position rather than a wrong one
        a.court = (round(x, 2), round(y, 2))


# --- features and the learned model (v1) -------------------------------------------------------

V0_ROLES = ("S", "1", "2", "3", "B", "END", "skip")
FEATURES = [
    "x", "y", *(f"kind_{k}" for k in KIND_NAMES), "turn", "log_speed_in", "log_speed_out",
    "vx_in", "vy_in", "vx_out", "vy_out", "depth_in", "depth_out", "p_near", "in_far",
    "dy_net", "dy_net_floor", "dt_prev", "dt_next", "t_from_start", "t_to_end",
    "t_from_serve", "index", "candidates", "serve_near",
    *(f"v0_{r}" for r in V0_ROLES), "v0_near", "v0_crosses",
]  # fmt: skip


def rally_features(
    rally: RallyIn, cands: Sequence[Candidate], fps: float, view: View
) -> tuple[Arr, list[tuple[str, str]]]:
    """One row per candidate of a rally, and v0's (role, side) for each."""
    path = decode(cands, fps, view, rally.serve_near, rally.start_ms / 1000, rally.end_ms / 1000)
    by_index = {i: (side, role) for i, side, role, _ in path}
    serve_t = next((cands[i].t(fps) for i, _, role, _ in path if role == "S"), math.nan)
    used = [i for i, _, role, _ in path if role != "END"]
    crossing: set[int] = set()
    for a, b in itertools.pairwise(used):
        if by_index[a][0] != by_index[b][0]:
            crossing.add(a)
    rows = []
    v0 = []
    n = len(cands)
    for i, c in enumerate(cands):
        side, role = by_index.get(i, ("", "skip"))
        t = c.t(fps)
        row = [
            c.x, c.y, *(float(c.kind == k) for k in range(len(KIND_NAMES))), c.turn,
            math.log(c.speed_in + 0.5), math.log(c.speed_out + 0.5),
            c.vx_in, c.vy_in, c.vx_out, c.vy_out, c.depth_in, c.depth_out, c.p_near,
            float(c.in_far), c.y - view.v_net, c.y - view.v_net_floor,
            t - cands[i - 1].t(fps) if i > 0 else math.nan,
            cands[i + 1].t(fps) - t if i + 1 < n else math.nan,
            t - rally.start_ms / 1000, rally.end_ms / 1000 - t, t - serve_t, float(i), float(n),
            rally.serve_near if rally.serve_near is not None else math.nan,
            *(float(role == r) for r in V0_ROLES),
            float(side == NEAR) if side else math.nan, float(i in crossing),
        ]  # fmt: skip
        rows.append(row)
        v0.append((role, side))
    return np.asarray(rows, dtype=np.float64).reshape(-1, len(FEATURES)), v0


@dataclass
class ActionModel:
    skill: Any  # classifier over CLASSES
    side: Any  # classifier: 1 near, 0 far
    trained_on: list[str]
    sklearn: str

    def label(
        self, rally: RallyIn, cands: Sequence[Candidate], fps: float, view: View
    ) -> list[Action]:
        if not cands:
            return []
        X, _ = rally_features(rally, cands, fps, view)
        p_skill = self.skill.predict_proba(X)
        classes = [CLASSES[int(k)] for k in self.skill.classes_]
        p_near = (
            self.side.predict_proba(X)[:, list(self.side.classes_).index(1)]
            if len(self.side.classes_) == 2
            else np.full(len(X), float(self.side.classes_[0]))
        )
        actions: list[Action] = []
        last_t = -INF
        for i, c in enumerate(cands):
            k = int(np.argmax(p_skill[i]))
            code = classes[k]
            if code == NONE or c.t(fps) - last_t < MIN_DT_S:
                continue
            side = NEAR if p_near[i] >= 0.5 else FAR
            p_side = float(max(p_near[i], 1 - p_near[i]))
            actions.append(
                Action(
                    rally_id=rally.id, frame=c.frame, code=code, side=side, grade=None,
                    confidence=round(float(p_skill[i][k]) * p_side, 3), x=c.x, y=c.y,
                    gap=c.kind == GAP,
                )
            )  # fmt: skip
            last_t = c.t(fps)
        for n, a in enumerate(actions):  # an overpass, as v0 calls it
            nxt = actions[n + 1] if n + 1 < len(actions) else None
            if a.code in ("R", "D") and nxt is not None and nxt.side != a.side:
                a.grade = "/"
        grade_hints(actions, rally, fps)
        return actions

    def dumps(self) -> bytes:
        buf = io.BytesIO()
        pickle.dump(
            {
                "format": MODEL_FORMAT,
                "kind": "actions",
                "skill": self.skill,
                "side": self.side,
                "features": FEATURES,
                "classes": list(CLASSES),
                "trained_on": self.trained_on,
                "sklearn": self.sklearn,
            },
            buf,
            protocol=5,
        )
        return buf.getvalue()

    @classmethod
    def load(cls, path: Path, sha256: str) -> ActionModel:
        """Only a file whose SHA-256 the API recorded (pickle runs code)."""
        import sklearn

        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ValueError(f"{path.name}: SHA-256 does not match the model run")
        obj = pickle.loads(data)  # noqa: S301 - our own file, checked against the recorded hash
        if obj.get("format") != MODEL_FORMAT or obj.get("kind") != "actions":
            raise ValueError("unknown action model format")
        if obj["features"] != FEATURES or obj["classes"] != list(CLASSES):
            raise ValueError("the action features changed since this model was trained")
        if obj["sklearn"] != sklearn.__version__:
            raise ValueError(
                f"trained with scikit-learn {obj['sklearn']}, have {sklearn.__version__}"
            )
        return cls(obj["skill"], obj["side"], list(obj["trained_on"]), obj["sklearn"])


# --- one video ----------------------------------------------------------------------------------


Labeller = Callable[[RallyIn, Sequence[Candidate], float, View], list[Action]]


@dataclass
class VideoActions:
    """Everything a video's actions came from (for the job's result and the training)."""

    fps: float
    view: View
    candidates: list[Candidate]
    actions: list[Action] = field(default_factory=list)
    per_rally: dict[str, list[Candidate]] = field(default_factory=dict)


def prepare(sig: Signals, camera: dict[str, Any] | None, aspect: float = 16 / 9) -> VideoActions:
    view = make_view(sig, camera, aspect)
    track = tr.clean(sig)
    return VideoActions(fps=sig.fps, view=view, candidates=candidates(track, view))


def propose(
    va: VideoActions, rallies: Sequence[RallyIn], model: ActionModel | None = None
) -> list[Action]:
    """The actions of every rally, with v1 when a model is given, else v0."""
    labeller: Labeller = model.label if model is not None else label_sure
    out: list[Action] = []
    for rally in sorted(rallies, key=lambda r: r.start_ms):
        cands = in_rally(va.candidates, rally, va.fps)
        va.per_rally[rally.id] = cands
        found = labeller(rally, cands, va.fps, va.view)
        place(found, va.view)
        out.extend(found)
    # A candidate in two overlapping rallies is played once (the earlier rally's).
    seen: set[int] = set()
    unique = []
    for a in out:
        if a.frame in seen:
            continue
        seen.add(a.frame)
        unique.append(a)
    va.actions = unique
    return unique


# --- training --------------------------------------------------------------------------------


@dataclass
class TrainVideo:
    """A reviewed game: its candidates per rally and the coaches' tags (near/far sides)."""

    video_id: str
    va: VideoActions
    rallies: list[RallyIn]
    # (t_ms, code, side near|far, rally id) of the rallies the coaches tagged
    events: list[tuple[int, str, str, str]]

    def tagged(self) -> list[RallyIn]:
        ids = {e[3] for e in self.events}
        return [r for r in self.rallies if r.id in ids]

    def truth(self) -> list[action_eval.Action]:
        return [(t, code, side) for t, code, side, _ in self.events]

    def data(self) -> tuple[Arr, Arr, Arr, Arr]:
        """Rows of every candidate in the tagged rallies; the skill class (index into
        CLASSES) of each; for the matched ones the side (1 near); the mask of those."""
        Xs, ys, sides, mask = [], [], [], []
        by_rally: dict[str, list[tuple[int, str, str]]] = {}
        for t, code, side, rid in self.events:
            by_rally.setdefault(rid, []).append((t, code, side))
        for rally in self.tagged():
            cands = in_rally(self.va.candidates, rally, self.va.fps)
            if not cands:
                continue
            X, _ = rally_features(rally, cands, self.va.fps, self.va.view)
            truth = by_rally.get(rally.id, [])
            pairs = action_eval.match(
                [round(c.t(self.va.fps) * 1000) for c in cands], [t for t, _, _ in truth]
            )
            matched = dict(pairs)
            for i in range(len(cands)):
                j = matched.get(i)
                if j is None:
                    ys.append(0)
                    sides.append(0)
                    mask.append(False)
                else:
                    ys.append(CLASSES.index(truth[j][1]) if truth[j][1] in CLASSES else 0)
                    sides.append(int(truth[j][2] == NEAR))
                    mask.append(True)
            Xs.append(X)
        if not Xs:
            empty = np.zeros((0, len(FEATURES)))
            return empty, np.zeros(0, int), np.zeros(0, int), np.zeros(0, bool)
        return np.concatenate(Xs), np.array(ys), np.array(sides), np.array(mask, dtype=bool)


def fit(videos: Sequence[TrainVideo], threads: int = 4) -> ActionModel | None:
    import sklearn
    from sklearn.ensemble import HistGradientBoostingClassifier
    from threadpoolctl import threadpool_limits

    Xs, ys, ss, ms, ws = [], [], [], [], []
    for v in videos:
        X, y, s, m = v.data()
        if len(y) == 0:
            continue
        Xs.append(X)
        ys.append(y)
        ss.append(s)
        ms.append(m)
        ws.append(np.full(len(y), 1.0 / len(y)))  # each game weighs the same
    if not Xs:
        return None
    X, y = np.concatenate(Xs), np.concatenate(ys)
    s, m, w = np.concatenate(ss), np.concatenate(ms), np.concatenate(ws) * len(ys)
    if len(set(y.tolist())) < 2 or m.sum() < 2 or len(set(s[m].tolist())) < 2:
        return None
    X = X.copy()
    X[:, np.isnan(X).all(axis=0)] = 0.0

    def classifier() -> Any:
        return HistGradientBoostingClassifier(
            max_iter=200, learning_rate=0.06, max_leaf_nodes=15, min_samples_leaf=10,
            l2_regularization=1.0, early_stopping=False, random_state=0,
        )  # fmt: skip

    with threadpool_limits(threads):
        skill = classifier().fit(X, y, sample_weight=w)
        side = classifier().fit(X[m], s[m], sample_weight=w[m])
    return ActionModel(skill, side, [v.video_id for v in videos], sklearn.__version__)


def _held_out_score(v: TrainVideo, model: ActionModel | None) -> dict[str, Any]:
    pred: list[action_eval.Action] = []
    for rally in v.tagged():
        cands = in_rally(v.va.candidates, rally, v.va.fps)
        found = (
            model.label(rally, cands, v.va.fps, v.va.view)
            if model
            else label(rally, cands, v.va.fps, v.va.view)
        )
        pred.extend((round(a.frame / v.va.fps * 1000), a.code, a.side) for a in found)
    return action_eval.evaluate(pred, v.truth())


def contact_recall(v: TrainVideo) -> float | None:
    """Share of the coaches' tags with a candidate within ±0.3 s."""
    cands = [round(c.t(v.va.fps) * 1000) for c in v.va.candidates]
    return action_eval.contact_recall(cands, [t for t, _, _, _ in v.events])


def cross_validate(
    videos: Sequence[TrainVideo],
    threads: int = 4,
    progress: Callable[[float], None] | None = None,
) -> dict[str, Any]:
    """v0 and v1 on held-out games (leave one game out, at most 8 folds). v1 is better when
    its mean action F1 beats v0's."""
    from wieplay.ml.rallies import folds

    per_video: dict[str, dict[str, Any]] = {}
    for v in videos:
        per_video[v.video_id] = {
            "v0": _held_out_score(v, None),
            "contact_recall": contact_recall(v),
        }
    labelled = [v for v in videos if v.events]
    if len(labelled) >= 2:
        groups = folds(len(labelled))
        for n, fold in enumerate(groups):
            if progress is not None:
                progress(n / len(groups))
            model = fit([v for i, v in enumerate(labelled) if i not in fold], threads)
            if model is None:
                continue
            for i in fold:
                per_video[labelled[i].video_id]["v1"] = _held_out_score(labelled[i], model)
    recalls = [m["contact_recall"] for m in per_video.values() if m["contact_recall"] is not None]
    summary: dict[str, Any] = {
        "v0": action_eval.summarise(m["v0"] for m in per_video.values()),
        "v1": action_eval.summarise(m["v1"] for m in per_video.values() if "v1" in m),
        "contact_recall": round(sum(recalls) / len(recalls), 4) if recalls else None,
        "games": len(labelled),
    }
    v0_f1 = summary["v0"].get("action_f1")
    v1_f1 = summary["v1"].get("action_f1")
    better = v0_f1 is not None and v1_f1 is not None and v1_f1 > v0_f1
    return {"videos": per_video, "summary": summary, "v1_better": bool(better)}


def counts(actions: Sequence[Action]) -> dict[str, int]:
    out = dict.fromkeys(SKILLS, 0)
    for a in actions:
        out[a.code] = out.get(a.code, 0) + 1
    return out
