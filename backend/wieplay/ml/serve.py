"""Which end line the server stood at, per rally (PLAN §10.3, step 1), and the activity of a
video per second (for the gaps between rallies, PLAN §10.1). Worker side: needs numpy.

The camera stands behind one end line (PLAN §7.1), or at a corner behind it: the far court is a
small region of the picture around the net, where the ball spends most of its time (its centre:
the ball's median position), and the near court spreads over the rest. So the serve shows in
the ball's first track after a rally starts that moves like a ball in flight:

- **near** serve: the ball first shows far from that centre (low in the picture at the
  server's hands, at the picture's edge where the server stands, or high above them on a
  jump-serve toss) and flies towards it, getting smaller and slower: it moves away from the
  camera;
- **far** serve: the ball first shows near the centre and then moves out of it, towards the
  camera.

Distance and direction are measured from the centre in both axes (picture heights): a near jump
serve taken at the right edge of the picture shows at the far court's height, so its height
alone (rule 1, until 18.10.2026: distance above or below the band only) read it as far. A track
that starts slower than SLOW (a ball held, carried to the end line, rolled or bounced) is
passed over for the next one.

The rule (`p_near_v0`, version RULE) is a logistic function of those two numbers, its weights
fitted on the rallies whose server the club scoreboard's log implies (PLAN §10.10) and checked
on held-out games and halls. v1 is a classifier over the same features and the rule's answer,
trained on the serves the coaches tagged or called (their serving team and the set's sides),
used only when it beats the rule on held-out games (leave one game out, like the rally finder).

Both are evaluated for every half-second of the video ("a rally starting here"), so the API
can look up the serve of any rally, also one a coach moved or added: the **serve track**. The
**activity track** keeps per second the ball, the movement on court and along the sides (the
benches: a timeout), and the whistles.
"""

from __future__ import annotations

import hashlib
import io
import pickle
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from wieplay.ml import tracks
from wieplay.ml.features import GRID_COLS, HEIGHT, WIDTH, Arr, Signals, Tracks, ball_tracks

MODEL_FORMAT = 1
# The rule's version: 1 read the height alone (until 18.10.2026), 2 the distance and direction
# from the far court's centre. Kept with every serve track, so a training recomputes the tracks
# an older rule made.
RULE = 2
STEP_S = tracks.SERVE_STEP_MS / 1000
LEAD_S = 0.5  # the serve's ball track starts at most this long before the rally's start
WINDOW_S = 5.0  # ... and at most this long after it
EARLY_S = 0.6
LATE_S = (0.5, 1.2)
ASPECT = WIDTH / HEIGHT  # distances in picture heights, x scaled to them
# A track slower than this over its first EARLY_S (picture widths per second: 1.7 px a frame
# at 512 px and 30 fps) is a ball held, carried, rolled or bounced: the next one is the serve.
SLOW = 0.1
# The rule: logit P(near) = R_FIRST * r_first + R_SPEED * r_speed + R_BIAS, fitted on the
# 258 rallies of the two games with a trusted score log (scripts/ml/serve_eval.py); checked
# leaving one game out and on three other halls labelled by eye (PLAN §10.3).
R_FIRST = 8.6
R_SPEED = -4.3
R_BIAS = -3.6

FEATURES = [
    "y_first",  # where the ball first shows, relative to the band (0: in it; + lower)
    "y_late",  # where it is 0.5-1.2 s later
    "d_first",  # |y_first|
    "d_late",  # |y_late|
    "towards",  # d_first - d_late: > 0 when the ball flies towards the band
    "y_max",  # lowest point in the first 0.6 s
    "y_min",  # highest point in the first 0.6 s
    "speed",  # median speed in the first 0.6 s (picture widths per second)
    "seen",  # share of the first 0.6 s with the ball tracked
    "x_first",  # 0..1 across the picture
    "delay",  # seconds from the rally's start to the first track
    "near_motion",  # movement in the near third of the picture in the 1.5 s before
    "far_motion",  # ... in the far third
    "loud",  # loudest moment around the first track, over the video's median (dB)
    "r_first",  # distance from the far court's centre where the ball first shows (heights)
    "r_speed",  # how fast that distance changes in the first 0.6 s (heights/s; - towards it)
]


@dataclass
class Evidence:
    """Per-video context of the serve features."""

    band: float  # the ball's median height in the picture (0..1): the net and far court
    idx: Arr  # frames with the ball in a plausible track
    y: Arr  # ball height 0..1 per frame
    x: Arr
    speed: Arr
    near_motion: Arr  # per frame, relative to the video's median
    far_motion: Arr
    loud_t: Arr  # audio frame times
    loud: Arr  # dB over the median
    fps: float
    centre_x: float = 0.5  # the ball's median position across the picture (0..1)


def evidence(sig: Signals, trk: Tracks | None = None) -> Evidence:
    trk = trk or ball_tracks(sig)
    idx = np.flatnonzero(trk.in_track)
    y = sig.ball_y.astype(np.float32) / HEIGHT
    x = sig.ball_x.astype(np.float32) / WIDTH
    band = float(np.median(y[idx])) if len(idx) else 0.45
    centre_x = float(np.median(x[idx])) if len(idx) else 0.5
    motion = sig.motion.astype(np.float32)
    if motion.size:
        near = motion[:, -1, :].mean(1)
        far = motion[:, 0, :].mean(1)
        near = near / max(float(np.median(near)), 1e-6)
        far = far / max(float(np.median(far)), 1e-6)
    else:
        near = far = np.ones(sig.frames, dtype=np.float32)
    if sig.has_audio:
        level = sig.all_db.astype(np.float32)
        loud = level - float(np.median(level))
        loud_t = np.arange(len(level)) * float(sig.meta.get("audio_hop_s", 512 / 22050))
    else:
        loud = loud_t = np.zeros(0, dtype=np.float32)
    return Evidence(
        band=band,
        idx=idx,
        y=y,
        x=x,
        speed=trk.speed,
        near_motion=near,
        far_motion=far,
        loud_t=loud_t,
        loud=loud,
        fps=sig.fps,
        centre_x=centre_x,
    )


def _first_track_features(ev: Evidence, f0: int) -> list[float]:
    """The features of a serve whose ball track starts at frame f0 (all but `delay`)."""
    idx, fps = ev.idx, ev.fps
    k = int(np.searchsorted(idx, f0))
    first = idx[k : k + 3]
    early = idx[(idx >= f0) & (idx < f0 + EARLY_S * fps)]
    late = idx[(idx >= f0 + LATE_S[0] * fps) & (idx < f0 + LATE_S[1] * fps)]
    y_first = float(np.median(ev.y[first])) - ev.band
    y_late = float(np.median(ev.y[late])) - ev.band if len(late) else np.nan
    sp = ev.speed[early]
    sp = sp[~np.isnan(sp)]
    lo = max(0, f0 - round(1.5 * fps))
    near_m = float(ev.near_motion[lo:f0].mean()) if f0 > lo else np.nan
    far_m = float(ev.far_motion[lo:f0].mean()) if f0 > lo else np.nan
    if len(ev.loud):
        t0 = f0 / fps
        a, b = np.searchsorted(ev.loud_t, [t0 - 0.3, t0 + 0.6])
        loud = float(ev.loud[a:b].max()) if b > a else np.nan
    else:
        loud = np.nan
    d_first = abs(y_first)
    d_late = abs(y_late) if not np.isnan(y_late) else np.nan
    r = _radius(ev, early)
    r_first = float(np.median(_radius(ev, first)))
    r_speed = float(np.polyfit(early / fps, r, 1)[0]) if len(early) >= 3 else np.nan
    return [
        y_first,
        y_late,
        d_first,
        d_late,
        d_first - d_late if not np.isnan(d_late) else np.nan,
        float(ev.y[early].max()) - ev.band,
        float(ev.y[early].min()) - ev.band,
        float(np.median(sp)) if len(sp) else np.nan,
        len(early) / max(EARLY_S * fps, 1.0),
        float(np.median(ev.x[first])),
        np.nan,  # delay: per start
        near_m,
        far_m,
        loud,
        r_first,
        r_speed,
    ]


def _radius(ev: Evidence, frames: Arr) -> Arr:
    """Distance of the ball from the far court's centre (picture heights)."""
    out: Arr = np.hypot((ev.x[frames] - ev.centre_x) * ASPECT, ev.y[frames] - ev.band)
    return out


def _serve_start(ev: Evidence, k: int, last_frame: float) -> int | None:
    """The frame the serve's ball track starts at: the first tracked frame from ev.idx[k] on
    whose first EARLY_S moves at least SLOW, else the first tracked frame (a slow ball is still
    some evidence); None when nothing is tracked up to `last_frame`."""
    idx, fps = ev.idx, ev.fps
    first: int | None = None
    while k < len(idx) and idx[k] <= last_frame:
        f0 = int(idx[k])
        first = f0 if first is None else first
        end = f0 + EARLY_S * fps
        early = idx[k : int(np.searchsorted(idx, end))]
        sp = ev.speed[early]
        sp = sp[~np.isnan(sp)]
        if len(sp) and float(np.median(sp)) >= SLOW:
            return f0
        k = int(np.searchsorted(idx, end))
    return first


def features_at(ev: Evidence, starts_s: Sequence[float] | Arr) -> Arr:
    """The serve features of rallies starting at these times (s); a row of NaN where no ball
    track starts within the window."""
    starts = np.asarray(starts_s, dtype=np.float64)
    out = np.full((len(starts), len(FEATURES)), np.nan, dtype=np.float32)
    if len(ev.idx) == 0 or len(starts) == 0:
        return out
    fps = ev.fps
    k = np.searchsorted(ev.idx, np.floor((starts - LEAD_S) * fps).astype(np.int64))
    cache: dict[int, list[float]] = {}
    delay_col = FEATURES.index("delay")
    for row, (t, kk) in enumerate(zip(starts, k, strict=True)):
        f0 = _serve_start(ev, int(kk), (t + WINDOW_S) * fps)
        if f0 is None:
            continue
        if f0 not in cache:
            cache[f0] = _first_track_features(ev, f0)
        out[row] = cache[f0]
        out[row, delay_col] = f0 / fps - t
    return out


def p_near_v0(X: Arr, weights: tuple[float, float, float] | None = None) -> Arr:
    """The rule (version RULE): far from the far court's centre at first and flying towards
    it is a near serve; near it and flying out of it, a far one. `weights` (r_first, r_speed,
    bias) are for fitting and measuring it (scripts/ml/serve_eval.py)."""
    a, b, c = weights or (R_FIRST, R_SPEED, R_BIAS)
    col = {name: i for i, name in enumerate(FEATURES)}
    r0 = X[:, col["r_first"]].astype(np.float64)
    rv = np.nan_to_num(X[:, col["r_speed"]].astype(np.float64), nan=0.0)
    z = a * np.nan_to_num(r0) + b * np.clip(rv, -3.0, 3.0) + c
    p = 1.0 / (1.0 + np.exp(-z))
    # A track that starts late is less likely the serve's.
    delay = np.nan_to_num(X[:, col["delay"]].astype(np.float64), nan=0.0)
    trust = np.clip(1.0 - (delay - 3.0) / 3.0, 0.3, 1.0)
    p = 0.5 + (p - 0.5) * trust
    return np.where(np.isnan(r0), np.nan, p)


# --- v1 -----------------------------------------------------------------------------------------


def design(X: Arr) -> Arr:
    return np.column_stack([X, p_near_v0(X)])


@dataclass
class ServeModel:
    classifier: Any
    trained_on: list[str]
    sklearn: str

    def p_near(self, X: Arr) -> Arr:
        D = design(X)
        ok = ~np.isnan(X[:, FEATURES.index("r_first")])
        out = np.full(len(X), np.nan)
        if ok.any():
            out[ok] = self.classifier.predict_proba(D[ok])[:, 1]
        return out

    def dumps(self) -> bytes:
        buf = io.BytesIO()
        pickle.dump(
            {
                "format": MODEL_FORMAT,
                "kind": "serve",
                "classifier": self.classifier,
                "features": FEATURES,
                "trained_on": self.trained_on,
                "sklearn": self.sklearn,
            },
            buf,
            protocol=5,
        )
        return buf.getvalue()

    @classmethod
    def load(cls, path: Path, sha256: str) -> ServeModel:
        """Only a file whose SHA-256 the API recorded (pickle runs code)."""
        import sklearn

        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ValueError(f"{path.name}: SHA-256 does not match the model run")
        obj = pickle.loads(data)  # noqa: S301 - our own file, checked against the recorded hash
        if obj.get("format") != MODEL_FORMAT or obj.get("kind") != "serve":
            raise ValueError("unknown serve model format")
        if obj["features"] != FEATURES:
            raise ValueError("the serve features changed since this model was trained")
        if obj["sklearn"] != sklearn.__version__:
            raise ValueError(
                f"trained with scikit-learn {obj['sklearn']}, have {sklearn.__version__}"
            )
        return cls(obj["classifier"], list(obj["trained_on"]), obj["sklearn"])


@dataclass
class ServeVideo:
    """A reviewed game: its signals' evidence and the coach's serves (start ms, near?)."""

    video_id: str
    ev: Evidence
    serves: list[tuple[int, bool]]

    def data(self) -> tuple[Arr, Arr]:
        X = features_at(self.ev, [s / 1000 for s, _ in self.serves])
        y = np.array([near for _, near in self.serves], dtype=np.int8)
        ok = ~np.isnan(X[:, FEATURES.index("r_first")])
        return X[ok], y[ok]


def fit(videos: Sequence[ServeVideo], threads: int = 4) -> ServeModel | None:
    import sklearn
    from sklearn.ensemble import HistGradientBoostingClassifier
    from threadpoolctl import threadpool_limits

    Xs, ys, ws = [], [], []
    for v in videos:
        X, y = v.data()
        if len(y) == 0:
            continue
        Xs.append(design(X))
        ys.append(y)
        ws.append(np.full(len(y), 1.0 / len(y)))  # each game counts the same
    if not Xs:
        return None
    X, y = np.concatenate(Xs), np.concatenate(ys)
    if len(set(y.tolist())) < 2:
        return None
    # A column that is empty in every game (no sound in any of them) cannot be binned.
    X[:, np.isnan(X).all(axis=0)] = 0.0
    clf = HistGradientBoostingClassifier(
        max_iter=150,
        learning_rate=0.08,
        max_leaf_nodes=15,
        min_samples_leaf=20,
        l2_regularization=1.0,
        early_stopping=False,
        random_state=0,
    )
    with threadpool_limits(threads):
        clf.fit(X, y, sample_weight=np.concatenate(ws) * len(X) / len(Xs))
    return ServeModel(clf, [v.video_id for v in videos], sklearn.__version__)


def _accuracy(p: Arr, y: Arr) -> dict[str, Any]:
    ok = ~np.isnan(p)
    if not ok.any():
        return {"rallies": len(y), "with_evidence": 0, "accuracy": None}
    right = (p[ok] >= 0.5) == (y[ok] == 1)
    return {
        "rallies": len(y),
        "with_evidence": int(ok.sum()),
        "accuracy": round(float(right.mean()), 4),
    }


def cross_validate(videos: Sequence[ServeVideo], threads: int = 4) -> dict[str, Any]:
    """v0 and v1 on held-out games. v1 is better when its mean accuracy beats v0's."""
    per_video: dict[str, dict[str, Any]] = {}
    for v in videos:
        X, y = v.data()
        per_video[v.video_id] = {"v0": _accuracy(p_near_v0(X), y)}
    labelled = [v for v in videos if len(v.serves) > 0]
    if len(labelled) >= 2:
        for v in labelled:
            model = fit([o for o in labelled if o is not v], threads)
            if model is None:
                continue
            X, y = v.data()
            per_video[v.video_id]["v1"] = _accuracy(model.p_near(X), y)

    def mean(key: str) -> float | None:
        vals = [m[key]["accuracy"] for m in per_video.values() if key in m]
        vals = [a for a in vals if a is not None]
        return round(sum(vals) / len(vals), 4) if vals else None

    summary = {"v0": mean("v0"), "v1": mean("v1"), "games": len(labelled)}
    better = (
        summary["v1"] is not None and summary["v0"] is not None and (summary["v1"] > summary["v0"])
    )
    return {"videos": per_video, "summary": summary, "v1_better": bool(better)}


# --- tracks -------------------------------------------------------------------------------------


def serve_track(sig: Signals, model: ServeModel | None = None, ev: Evidence | None = None) -> bytes:
    """P(near serve) for a rally starting at every half-second, one byte each (255: none)."""
    ev = ev or evidence(sig)
    n = max(1, int(np.ceil(sig.duration_s / STEP_S)))
    X = features_at(ev, np.arange(n) * STEP_S)
    p = model.p_near(X) if model is not None else p_near_v0(X)
    return tracks.quantise(p.tolist())


def activity_track(sig: Signals, trk: Tracks | None = None) -> dict[str, bytes]:
    """Per second: the share of frames with the ball in play, the movement in the middle of the
    court and along its sides (relative to the video's median, x32), whistles starting."""
    trk = trk or ball_tracks(sig)
    fps = sig.fps
    n = max(1, int(np.ceil(sig.duration_s)))
    sec = (np.arange(sig.frames) / fps).astype(np.int64)
    sec = np.minimum(sec, n - 1)
    count = np.bincount(sec, minlength=n).astype(np.float64)
    count[count == 0] = 1.0

    def per_second(values: Arr) -> Arr:
        out: Arr = np.bincount(sec, weights=values.astype(np.float64), minlength=n) / count
        return out

    ball = per_second(trk.in_track.astype(np.float64))
    motion = sig.motion.astype(np.float32)
    if motion.size:
        middle = motion[:, :, 1 : GRID_COLS - 1].mean((1, 2))
        sides = motion[:, :, [0, GRID_COLS - 1]].mean((1, 2))
        court = per_second(middle / max(float(np.median(middle)), 1e-6))
        side = per_second(sides / max(float(np.median(sides)), 1e-6))
    else:
        court = side = np.full(n, np.nan)
    whistles = np.zeros(n)
    for t in sig.whistles.reshape(-1, 5)[:, 0]:
        k = int(t)
        if 0 <= k < n:
            whistles[k] += 1
    return {
        "ball": tracks.quantise(ball.tolist()),
        "court": tracks.quantise((court / tracks.MOTION_SCALE).tolist()),
        "side": tracks.quantise((side / tracks.MOTION_SCALE).tolist()),
        "whistle": bytes(int(min(254, w)) for w in whistles),
    }
