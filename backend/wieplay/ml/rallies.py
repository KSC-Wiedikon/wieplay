"""Rally finders (PLAN §10.1): v0 rules, v1 learned from the coaches' rallies.

- **v0** needs no training: a rally is sustained ball activity in plausible tracks (with
  movement on court), gaps of a few seconds bridged, split and ended at referee whistles
  when the video has them, 3-90 s long. Works without sound (friendlies, silent videos).
- **v1** is a per-step classifier ("is this half-second in a rally?") over the features of
  `features.step_features` plus v0's own answer, trained on every reviewed game, then
  smoothed into rallies with hysteresis. It is only used when it beats v0 on held-out games
  (leave one game out): boundary F1 at ±1 s, PLAN §10.1.
"""

from __future__ import annotations

import hashlib
import io
import pickle
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from wieplay.ml import metrics
from wieplay.ml.features import STEP_S, Arr, Signals, StepFeatures, rolling, step_features

MODEL_FORMAT = 1
PER_S = round(1 / STEP_S)


@dataclass(frozen=True)
class Proposal:
    start_ms: int
    end_ms: int
    confidence: float

    @property
    def span(self) -> tuple[int, int]:
        return (self.start_ms, self.end_ms)


# --- shared helpers ------------------------------------------------------------------------


def runs_of(mask: Arr) -> list[tuple[int, int]]:
    """[start, end) step ranges where mask is true."""
    edges = np.diff(np.concatenate([[0], mask.astype(np.int8), [0]]))
    return list(
        zip(np.flatnonzero(edges == 1).tolist(), np.flatnonzero(edges == -1).tolist(), strict=True)
    )


def merge_gaps(runs: Sequence[tuple[int, int]], max_gap: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for a, b in runs:
        if out and a - out[-1][1] <= max_gap:
            out[-1] = (out[-1][0], b)
        else:
            out.append((a, b))
    return out


def to_ms(step: float) -> int:
    return round(step * STEP_S * 1000)


def labels_for(n_steps: int, rallies_ms: Sequence[tuple[int, int]]) -> Arr:
    """1 for each step whose centre lies inside a rally."""
    y = np.zeros(n_steps, dtype=np.int8)
    centre = (np.arange(n_steps) + 0.5) * STEP_S * 1000
    for s, e in rallies_ms:
        a, b = np.searchsorted(centre, s), np.searchsorted(centre, e)
        y[a:b] = 1
    return y


# --- v0: rules -----------------------------------------------------------------------------


@dataclass(frozen=True)
class V0Params:
    on: float = 0.15  # share of frames in a plausible ball track (2 s mean) that is play
    on_low: float = 0.06  # ... or this much, with players moving more than usual
    motion: float = 1.25
    edge: float = 0.1  # a half-second with this share of tracked frames has the ball in play
    gap_s: float = 3.0  # ball lost for a moment (high balls, occlusion)
    min_s: float = 3.0
    # Without a referee, short bursts are mostly balls thrown back to the server; the
    # whistle-anchored rallies of a league match are rarely shorter (spike data, 16.10.2026).
    min_s_ball_only: float = 4.5
    max_s: float = 90.0
    min_activity: float = 0.15  # mean tracked share over the rally
    # The coach's rally starts at the toss (the tagging page opens it 2 s before the serve
    # tag) and ends about when the referee whistles (1.5 s after the last tag).
    lead_s: float = 1.0
    tail_s: float = 1.0
    # A referee: enough whistles per minute and at least this many serve whistles.
    whistle_rate: float = 0.8
    min_serves: int = 10
    whistle_s: float = 0.3  # shorter tones are shoe squeaks
    pitch_hz: float = 350.0  # our referee's whistles lie this close to their median pitch
    quiet_s: float = 3.0  # a serve whistle follows a dead ball; an end whistle a live one
    quiet: float = 0.1
    serve_window_s: float = 10.0  # the serve follows its whistle within 8 s
    # Play without a serve whistle (a missed one) counts when it is long and clear, and does
    # not start right after an end whistle (players throwing the ball back).
    unanchored_s: float = 4.0
    unanchored_activity: float = 0.3
    after_end_s: float = 4.0

    def as_dict(self) -> dict[str, float]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class _Piece:
    first: int  # first step with the ball in play
    end: float  # end, in steps
    by_whistle: bool  # ended by the referee's whistle
    anchored: bool  # began after a serve whistle (or no referee in this video)


def find_v0(
    sig: Signals, feats: StepFeatures | None = None, p: V0Params | None = None
) -> list[Proposal]:
    """Rallies from the rules (module docstring). Candidate stretches of play come from the
    smoothed ball activity, their edges from the raw per-step activity. With a referee,
    every rally hangs on a serve whistle and ends at the next whistle of the same pitch."""
    p = p or V0Params()
    f = feats or step_features(sig)
    n = f.n_steps
    trk = f.col("ball_trk")
    act = rolling(trk, 2 * PER_S)
    motion = np.nan_to_num(f.col("motion_c2"), nan=1.0)
    on = (act >= p.on) | ((act >= p.on_low) & (motion >= p.motion))
    raw = trk >= p.edge
    runs = merge_gaps(runs_of(on), round(p.gap_s * PER_S))
    pieces = _with_referee(sig, f, trk, raw, runs, p)
    min_s = p.min_s
    if pieces is None:
        pieces = _from_activity(raw, runs, p)
        min_s = p.min_s_ball_only

    out: list[Proposal] = []
    for piece in pieces:
        s = max(0.0, piece.first - p.lead_s * PER_S)
        e = min(float(n), piece.end)
        length = (e - s) * STEP_S
        if length < min_s or length > p.max_s:
            continue
        a, b = int(s), max(int(s) + 1, int(np.ceil(e)))
        activity = float(np.nanmean(trk[a:b]))
        if activity < p.min_activity:
            continue
        strength = float(np.clip(activity / 0.5, 0, 1))
        confidence = 0.2 + 0.4 * strength + 0.2 * piece.by_whistle + 0.2 * piece.anchored
        out.append(Proposal(to_ms(s), to_ms(e), round(min(confidence, 1.0), 3)))
    return out


def _active_edges(raw: Arr, a: float, b: float) -> tuple[int, int] | None:
    """First and last+1 step in [a, b) where the ball is in play."""
    lo, hi = max(0, int(a)), min(len(raw), int(np.ceil(b)))
    idx = np.flatnonzero(raw[lo:hi])
    return (lo + int(idx[0]), lo + int(idx[-1]) + 1) if len(idx) else None


def _from_activity(raw: Arr, runs: list[tuple[int, int]], p: V0Params) -> list[_Piece]:
    """No referee (friendlies, silent videos): every clear stretch of play."""
    out = []
    for a, b in runs:
        edges = _active_edges(raw, a, b)
        if edges is not None:
            out.append(_Piece(edges[0], edges[1] + p.tail_s * PER_S, False, True))
    return out


def _with_referee(
    sig: Signals,
    f: StepFeatures,
    trk: Arr,
    raw: Arr,
    runs: list[tuple[int, int]],
    p: V0Params,
) -> list[_Piece] | None:
    """Serve whistle → the ball comes into play within seconds → the next whistle of the
    referee's pitch ends it (else the play stops). None when the video has no referee."""
    w = sig.whistles.reshape(-1, 5)
    if f.whistle_rate < p.whistle_rate or len(w) == 0:
        return None
    n = len(trk)
    starts = w[:, 0].astype(np.float64) / STEP_S
    ends = (w[:, 0] + w[:, 1]).astype(np.float64) / STEP_S
    long_enough = w[:, 1] >= p.whistle_s

    def mean_trk(a: float, b: float) -> float:
        lo, hi = max(0, int(a)), min(n, int(np.ceil(b)))
        return float(trk[lo:hi].mean()) if hi > lo else 0.0

    quiet_before = np.array([mean_trk(s - p.quiet_s * PER_S, s) < p.quiet for s in starts])
    window = p.serve_window_s * PER_S
    play_after = np.array([_active_edges(raw, e, e + window) is not None for e in ends])
    serve_like = long_enough & quiet_before & play_after
    if serve_like.sum() < p.min_serves:
        return None
    # Our referee's whistles: near the pitch of the serve whistles (other courts' referees
    # in a tournament hall, squeaks and the bench whistle elsewhere).
    pitch = float(np.median(w[serve_like, 2]))
    ours = long_enough & (np.abs(w[:, 2] - pitch) <= p.pitch_hz)
    serves = np.flatnonzero(serve_like & ours)

    pieces: list[_Piece] = []
    for i, k in enumerate(serves):
        stop = starts[serves[i + 1]] if i + 1 < len(serves) else float(n)
        edges = _active_edges(raw, ends[k], min(ends[k] + window, stop))
        if edges is None:
            continue
        first = edges[0]
        closing = np.flatnonzero(ours & (starts > first + 2 * PER_S) & (starts < stop))
        if len(closing):
            pieces.append(_Piece(first, float(starts[closing[0]]), True, True))
            continue
        # No end whistle heard: the end of the stretch of play it starts.
        run = next(((a, b) for a, b in runs if a <= first < b), (first, first + 1))
        play = _active_edges(raw, first, min(run[1], stop))
        end = (play[1] if play else first + 1) + p.tail_s * PER_S
        pieces.append(_Piece(first, min(end, stop), False, True))

    # Clear play without a serve whistle (a whistle we did not hear), between the first and
    # the last serve, not right after a rally ended, not overlapping one.
    if len(serves):
        lo, hi = starts[serves[0]], ends[serves[-1]] + 120 * PER_S
        taken = [(q.first - p.lead_s * PER_S, q.end) for q in pieces]
        for a, b in runs:
            edges = _active_edges(raw, a, b)
            if edges is None or not lo <= edges[0] <= hi:
                continue
            first, last = edges
            if any(min(last, e) > max(first, s) for s, e in taken):
                continue
            if any(0 <= first - q.end <= p.after_end_s * PER_S for q in pieces):
                continue
            if (last - first) * STEP_S < p.unanchored_s:
                continue
            if mean_trk(first, last) < p.unanchored_activity:
                continue
            pieces.append(_Piece(first, last + p.tail_s * PER_S, False, False))
    return sorted(pieces, key=lambda q: q.first)


# --- v1: learned ---------------------------------------------------------------------------


@dataclass(frozen=True)
class V1Params:
    on: float = 0.5
    off: float = 0.35
    smooth_steps: int = 3
    gap_s: float = 1.0
    min_s: float = 2.5
    max_s: float = 120.0
    max_iter: int = 250
    learning_rate: float = 0.08
    max_leaf_nodes: int = 31
    min_samples_leaf: int = 40
    l2: float = 1.0

    def as_dict(self) -> dict[str, float]:
        return dict(self.__dict__)


def design(sig: Signals, feats: StepFeatures | None = None) -> tuple[Arr, list[str]]:
    """The classifier's input: the step features plus v0's answer for each step."""
    f = feats or step_features(sig)
    v0 = labels_for(f.n_steps, [p.span for p in find_v0(sig, f)])
    return np.column_stack([f.X, v0.astype(np.float32)]), [*f.names, "v0"]


def segments_from_prob(prob: Arr, p: V1Params | None = None) -> list[Proposal]:
    p = p or V1Params()
    smooth = rolling(prob, p.smooth_steps) if p.smooth_steps > 1 else prob
    state, out, start = False, [], 0
    on = np.zeros(len(smooth), dtype=bool)
    for k, value in enumerate(np.nan_to_num(smooth, nan=0.0)):
        if not state and value >= p.on:
            state, start = True, k
        elif state and value < p.off:
            state = False
            on[start:k] = True
    if state:
        on[start:] = True
    runs = merge_gaps(runs_of(on), round(p.gap_s * PER_S))
    for a, b in runs:
        length = (b - a) * STEP_S
        if length < p.min_s or length > p.max_s:
            continue
        out.append(Proposal(to_ms(a), to_ms(b), round(float(np.mean(prob[a:b])), 3)))
    return out


@dataclass
class TrainVideo:
    video_id: str
    signals: Signals
    rallies: list[tuple[int, int]]  # the coach's, ms
    _cache: dict[str, Any] = field(default_factory=dict)

    def data(self) -> tuple[Arr, list[str], Arr, StepFeatures]:
        if "X" not in self._cache:
            feats = step_features(self.signals)
            X, names = design(self.signals, feats)
            self._cache.update(X=X, names=names, feats=feats)
        X, feats = self._cache["X"], self._cache["feats"]
        return X, self._cache["names"], labels_for(len(X), self.rallies), feats


@dataclass
class RallyModel:
    """The v1 classifier and what it needs to be used again."""

    classifier: Any
    names: list[str]
    params: V1Params
    trained_on: list[str]
    sklearn: str

    def predict(self, sig: Signals) -> list[Proposal]:
        X, names = design(sig)
        if names != self.names:
            raise ValueError("the features changed since this model was trained")
        prob = self.classifier.predict_proba(X)[:, 1]
        return segments_from_prob(prob, self.params)

    def dumps(self) -> bytes:
        buf = io.BytesIO()
        pickle.dump(
            {
                "format": MODEL_FORMAT,
                "classifier": self.classifier,
                "names": self.names,
                "params": self.params.as_dict(),
                "trained_on": self.trained_on,
                "sklearn": self.sklearn,
            },
            buf,
            protocol=5,
        )
        return buf.getvalue()

    @classmethod
    def load(cls, path: Path, sha256: str) -> RallyModel:
        """Only a file whose SHA-256 the API recorded when the training job wrote it: pickle
        runs code, so nothing else is ever unpickled."""
        import sklearn

        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ValueError(f"{path.name}: SHA-256 does not match the model run")
        obj = pickle.loads(data)  # noqa: S301 - our own file, checked against the recorded hash
        if obj.get("format") != MODEL_FORMAT:
            raise ValueError("unknown rally model format")
        if obj["sklearn"] != sklearn.__version__:
            raise ValueError(
                f"trained with scikit-learn {obj['sklearn']}, have {sklearn.__version__}"
            )
        return cls(
            classifier=obj["classifier"],
            names=list(obj["names"]),
            params=V1Params(**obj["params"]),
            trained_on=list(obj["trained_on"]),
            sklearn=obj["sklearn"],
        )


def fit(videos: Sequence[TrainVideo], p: V1Params | None = None, threads: int = 4) -> RallyModel:
    import sklearn
    from sklearn.ensemble import HistGradientBoostingClassifier
    from threadpoolctl import threadpool_limits

    p = p or V1Params()
    Xs, ys, ws = [], [], []
    names: list[str] = []
    for v in videos:
        X, names, y, _ = v.data()
        Xs.append(X)
        ys.append(y)
        ws.append(np.full(len(y), 1.0 / max(len(y), 1)))  # each game counts the same
    X = np.concatenate(Xs)
    # A column that is empty in every game (no sound in any of them) cannot be binned.
    X[:, np.isnan(X).all(axis=0)] = 0.0
    y = np.concatenate(ys)
    w = np.concatenate(ws) * len(X) / len(videos)
    clf = HistGradientBoostingClassifier(
        max_iter=p.max_iter,
        learning_rate=p.learning_rate,
        max_leaf_nodes=p.max_leaf_nodes,
        min_samples_leaf=p.min_samples_leaf,
        l2_regularization=p.l2,
        early_stopping=False,
        random_state=0,
    )
    with threadpool_limits(threads):
        clf.fit(X, y, sample_weight=w)
    return RallyModel(clf, names, p, [v.video_id for v in videos], sklearn.__version__)


def folds(n: int, k_max: int = 8) -> list[list[int]]:
    """Held-out groups of videos: leave one out up to k_max videos, else k_max folds."""
    k = min(n, k_max)
    return [list(range(i, n, k)) for i in range(k)]


def cross_validate(
    videos: Sequence[TrainVideo],
    p: V1Params | None = None,
    threads: int = 4,
    progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> dict[str, Any]:
    """v0 and v1 on held-out games (split by match, PLAN §11). Returns per-video metrics,
    the summaries, the held-out v1 proposals and whether v1 is better. Stops early (without
    v1) when `cancel` is set."""
    p = p or V1Params()
    per_video: dict[str, dict[str, Any]] = {}
    held_out: dict[str, list[Proposal]] = {}
    for v in videos:
        _, _, _, feats = v.data()
        found = find_v0(v.signals, feats)
        per_video[v.video_id] = {"v0": metrics.evaluate([q.span for q in found], v.rallies)}
    if len(videos) >= 2:
        groups = folds(len(videos))
        for n, fold in enumerate(groups):
            if cancel is not None and cancel.is_set():
                break
            if progress is not None:
                progress(n / len(groups))
            train = [v for i, v in enumerate(videos) if i not in fold]
            model = fit(train, p, threads)
            for i in fold:
                v = videos[i]
                X, _, _, _ = v.data()
                prob = model.classifier.predict_proba(X)[:, 1]
                held_out[v.video_id] = segments_from_prob(prob, p)
                per_video[v.video_id]["v1"] = metrics.evaluate(
                    [q.span for q in held_out[v.video_id]], v.rallies
                )
    summary = {
        "v0": metrics.summarise([m["v0"] for m in per_video.values()]),
        "v1": metrics.summarise([m["v1"] for m in per_video.values() if "v1" in m]),
    }
    better = len(videos) >= 2 and summary["v1"].get("boundary_f1", 0.0) > summary["v0"].get(
        "boundary_f1", 0.0
    )
    return {
        "videos": per_video,
        "summary": summary,
        "v1_better": bool(better),
        "held_out": held_out,
    }
