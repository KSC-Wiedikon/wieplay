"""Suggested actions per rally (PLAN §10.4a): serve, reception, set, attack, block, dig and
freeball at the ball's contacts, with a side, sometimes a grade, and the ball's position for
the player attribution that follows (PLAN §10.5).

    signals ─▶ the ball's path (trajectory.clean) ─▶ contact candidates: sharp turns and
    changes of speed, tracks that start fast, direction changes while the tracker lost the
    ball ─▶ per candidate: which side of the net it is on (near: the camera's end) ─▶ per
    rally, the most plausible sequence under volleyball's rules (v0) or a classifier that
    learned from the coaches' tags (v1) ─▶ actions with times, sides, skills, grade hints

Worker side (numpy; v1 also scikit-learn). Sides are the camera's here (near, far); the API
turns them into us and them with the rally's sides (nomal/core/actions.py).

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
nomal/ml/action_eval.py; used only when its mean action F1 beats v0's.
"""

from __future__ import annotations

import hashlib
import io
import itertools
import math
import pickle
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from nomal.core import court
from nomal.ml import action_eval
from nomal.ml import trajectory as tr
from nomal.ml.features import HEIGHT, WIDTH, Arr, Signals, ball_tracks

MODEL_FORMAT = 1
SKILLS = ("S", "R", "E", "A", "B", "D", "F")
NONE = "none"
CLASSES = (NONE, *SKILLS)
NEAR, FAR = "near", "far"

# Candidate kinds: the trajectory's contacts, then two of our own.
BOUNCE, LOW, HIGH = tr.BOUNCE, tr.LOW, tr.HIGH
START = 3  # a track that starts fast: the ball was just hit (often the serve)
GAP = 4  # the ball changed direction while the tracker had lost it
KIND_NAMES = ("bounce", "low", "high", "start", "gap")

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
        return cls(
            camera=camera,
            calibrated=calibrated,
            far=_hull(corners),
            v_net=tape[1] if tape else 0.45,
            v_net_floor=foot[1] if foot else 0.58,
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
        for c in out:
            c.in_far = view.in_far(c.x, c.y)
            c.p_near = side_evidence(c, view)
    return out


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


# Roles of a used candidate: S serve, 1-3 the touch of a side, B block, END the floor.
ROLES = ("S", "1", "2", "3", "B", "END")
INF = 1e9
ROLE_KIND = {  # cost of a role for a kind of candidate
    "S": {BOUNCE: 1.5, LOW: 1.0, HIGH: 0.0, START: 0.0, GAP: 0.4},
    "1": {BOUNCE: 0.1, LOW: 0.0, HIGH: 0.5, START: 0.9, GAP: 0.3},
    "2": {BOUNCE: 0.8, LOW: 0.3, HIGH: 0.0, START: 0.8, GAP: 0.3},
    "3": {BOUNCE: 1.0, LOW: 0.7, HIGH: 0.0, START: 0.5, GAP: 0.3},
    "B": {BOUNCE: 1.5, LOW: 1.2, HIGH: 0.0, START: 0.3, GAP: 0.3},
    "END": {BOUNCE: 0.0, LOW: 1.0, HIGH: 2.5, START: INF, GAP: INF},
}
SIDE_WEIGHT = 0.8  # the side evidence is soft: the sequence decides the close ones
TOSS_COST = 1.2  # a track starting upwards at the server is the toss, the hit comes later
END_WINDOW_S = 2.5  # the floor bounce that ends a rally is in its last seconds
PRE_SERVE_SKIP = 0.3  # candidates in the toss window before the serve skip cheaply
TOSS_WINDOW_S = 2.5  # ... the first seconds of the rally
SERVE_LATE_S = 3.0  # a serve later than this into the rally costs SERVE_LATE per second
SERVE_LATE = 1.0
USE_BONUS = 0.7  # rallies are made of contacts: using a plausible candidate is preferred
DEAD_TAIL_S = 0.5  # the rally finder closes a rally about a second after the ball is dead:
FADE_S = 1.5  # ... the bonus fades out over this long before that


def contact_prior(c: Candidate) -> float:
    """How likely a candidate is a contact at all, before the rally's sequence is known."""
    if c.kind == HIGH:
        ratio = (c.speed_out + 0.5) / (c.speed_in + 0.5)
        q = 0.35 + c.turn / 250 + 0.2 * abs(math.log(ratio))
    elif c.kind == LOW:
        q = 0.6 + c.turn / 600
    elif c.kind == BOUNCE:
        q = 0.5
    elif c.kind == START:
        q = 0.45
    else:
        q = 0.4 + (0.1 if c.turn >= 90 else 0.0)
    return min(0.9, max(0.2, q))


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
    n = len(cands)
    if n == 0:
        return []
    skip = [-math.log(1 - contact_prior(c)) for c in cands]
    pre = [0.0]
    for cost_skip in skip:
        pre.append(pre[-1] + cost_skip)
    t = [c.t(fps) for c in cands]
    ns = len(STATES)
    cost = [[INF] * ns for _ in range(n)]
    back: list[list[tuple[int, int] | None]] = [[None] * ns for _ in range(n)]
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
    t0 = t[0] if start_s is None else start_s
    for i in range(n):
        for k, (s, r) in enumerate(STATES):
            e = emit[i][k]
            if e >= INF:
                continue
            if r == "S":
                # The serve: the first contact, early in the rally; what comes before it in
                # the first seconds is the toss or the ball handed to the server.
                late = max(0.0, t[i] - t0 - SERVE_LATE_S) * SERVE_LATE
                before = sum(
                    skip[j] * (PRE_SERVE_SKIP if t[j] < t0 + TOSS_WINDOW_S else 1.0)
                    for j in range(i)
                )
                cost[i][k] = before + e + late
                continue
            best, arg = INF, None
            for j in range(i - 1, -1, -1):
                gap_cost = pre[i] - pre[j + 1]
                if gap_cost >= INF or gap_cost > 12:
                    break
                for m in range(ns):
                    if cost[j][m] >= INF:
                        continue
                    late_seen = STATES[m][1] == "S" and cands[j].kind == START
                    tc = _trans(STATES[m], (s, r), t[i] - t[j], late_seen)
                    if tc >= INF:
                        continue
                    total = cost[j][m] + gap_cost + tc
                    if total < best:
                        best, arg = total, (j, m)
            if arg is not None:
                cost[i][k] = best + e
                back[i][k] = arg
    # The end: the remaining candidates skipped (nothing after the floor).
    best_total, end = INF, None
    for i in range(n):
        for k, (_, r) in enumerate(STATES):
            if cost[i][k] >= INF:
                continue
            total = cost[i][k] + (0.0 if r == "END" else pre[n] - pre[i + 1])
            if total < best_total:
                best_total, end = total, (i, k)
    if end is None:
        return []
    path: list[tuple[int, int]] = []
    node: tuple[int, int] | None = end
    while node is not None:
        path.append(node)
        node = back[node[0]][node[1]]
    path.reverse()
    out = []
    for i, k in path:
        side, role = STATES[k]
        # How much the side matters: the cost of the same role on the other side.
        other = STATES.index((_other(side), role))
        margin = emit[i][other] - emit[i][k]
        p_side = 1.0 / (1.0 + math.exp(-margin)) if margin < 50 else 1.0
        out.append((i, side, role, p_side))
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


def label(
    rally: RallyIn,
    cands: Sequence[Candidate],
    fps: float,
    view: View,
) -> list[Action]:
    """v0: the actions of one rally (its candidates in time order)."""
    path = decode(cands, fps, view, rally.serve_near, rally.start_ms / 1000, rally.end_ms / 1000)
    used = [(cands[i], side, role, p) for i, side, role, p in path if role != "END"]
    actions: list[Action] = []
    prev_code: str | None = None
    prev_side: str | None = None
    for n, (c, side, role, p_side) in enumerate(used):
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
        confidence = round(min(0.99, max(0.05, 0.55 * p_side + 0.45 * _strength(c))), 3)
        actions.append(
            Action(
                rally_id=rally.id, frame=c.frame, code=code, side=side, grade=grade,
                confidence=confidence, x=c.x, y=c.y, gap=c.kind == GAP, role=role,
            )
        )  # fmt: skip
        prev_code, prev_side = code, side
    grade_hints(actions, rally, fps)
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
    labeller: Labeller = model.label if model is not None else label
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
    from nomal.ml.rallies import folds

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
