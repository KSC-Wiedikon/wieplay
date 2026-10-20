"""The action jobs on the worker (wieplay/worker/actions.py): the actions of one video from its
signals on the NAS, and the training on reviewed games; their results pass the API's
validation (wieplay/core/actions.py)."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from tests.test_actions import path
from tests.test_trajectory import signals
from tests.test_worker_analyse import (
    FakeApi,
    context,
    worker_settings,  # noqa: F401 - a fixture
)
from wieplay.core.actions import ActionsResult, TrainResult
from wieplay.ml import actions as ac
from wieplay.ml.features import Signals
from wieplay.worker.config import WorkerSettings
from wieplay.worker.handlers import BACKGROUND_KINDS, HANDLERS


def store_signals(root: Path, name: str, sig: Signals) -> dict[str, Any]:
    target = root / "videos" / name / "signals-test.npz"
    target.parent.mkdir(parents=True, exist_ok=True)
    sig.save(target)
    return {
        "artifact_id": "a",
        "nas_path": str(target.relative_to(root)),
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }


RALLY = "00000000-0000-0000-0000-00000000000{}"


def rallies(n: int, length_s: float) -> list[dict[str, Any]]:
    return [
        {"id": RALLY.format(k), "start_ms": round(k * length_s * 1000) + 500,
         "end_ms": round((k + 1) * length_s * 1000), "serve_near": 0.9, "won_near": None}
        for k in range(n)
    ]  # fmt: skip


def job(kind: str, **params: Any) -> dict[str, Any]:
    return {"id": f"{kind}-0000-0000", "kind": kind, "video_id": "v", "params": params}


def test_the_actions_of_a_video(worker_settings: WorkerSettings) -> None:  # noqa: F811
    assert "actions" in BACKGROUND_KINDS and "train_actions" in BACKGROUND_KINDS
    root = worker_settings.analysis_root
    assert root is not None
    x, y, vis = path()
    features = store_signals(root, "a", signals(x, y, vis))
    inputs = {
        "features": features,
        "fps": 60.0,
        "aspect": 16 / 9,
        "camera": None,
        "rallies": rallies(1, 9.5),
        "model": None,
    }
    ctx = context(worker_settings, FakeApi(inputs), job("actions"))
    out = HANDLERS["actions"](ctx)
    result = ActionsResult.model_validate(out)
    assert result.finder == "v0" and result.model_run_id is None
    assert result.actions and result.actions[0].code == "S"
    first = result.actions[0]
    # Analysed at 30 fps, filmed at 60: the time is adjusted, the frame the video's.
    assert first.time_precision == "adjusted" and first.frame == round(first.t_ms * 60 / 1000)
    assert first.court_x is None  # no calibration
    assert out["stats"]["candidates"] >= len(result.actions)
    assert any(m == "labelling the rallies" for _, m in ctx.seen)  # type: ignore[attr-defined]


def labelled_game(root: Path, name: str, seed: int) -> dict[str, Any]:
    """Signals of a ball flying between contacts and the coach's tags at every candidate
    (so the training has labels to match)."""
    rng = np.random.default_rng(seed)
    x, y, vis = path()
    x = np.where(vis, x + rng.normal(0, 0.3, len(x)), x)
    sig = signals(x, y, vis)
    va = ac.prepare(sig, None)
    rally = rallies(1, 9.5)
    cands = ac.in_rally(va.candidates, ac.RallyIn(**rally[0]), sig.fps)
    codes = ["S", "R", "E", "A", "D"]
    events = [
        [round(c.frame / sig.fps * 1000), codes[k % len(codes)], ("near", "far")[k % 2],
         RALLY.format(0)]
        for k, c in enumerate(cands)
    ]  # fmt: skip
    return {
        "video_id": name,
        "features": store_signals(root, name, sig),
        "fps": 30.0,
        "aspect": 16 / 9,
        "camera": None,
        "rallies": rally,
        "events": events,
    }


def test_the_training_on_reviewed_games(worker_settings: WorkerSettings) -> None:  # noqa: F811
    root = worker_settings.analysis_root
    assert root is not None
    games = [labelled_game(root, f"g{k}", k) for k in range(3)]
    ctx = context(
        worker_settings,
        FakeApi({"model": None, "train": games, "skipped": []}),
        job("train_actions", out_dir="models/actions"),
    )
    out = HANDLERS["train_actions"](ctx)
    result = TrainResult.model_validate(out)
    summary = result.evaluation["summary"]
    assert summary["games"] == 3 and summary["contact_recall"] == 1.0
    assert set(result.evaluation["videos"]) == {"g0", "g1", "g2"}
    assert [d["video_id"] for d in result.dataset] == ["g0", "g1", "g2"]
    assert result.artifact is not None
    assert result.artifact.nas_path.startswith("models/actions/actions-v1-")
    stored = root / result.artifact.nas_path
    assert hashlib.sha256(stored.read_bytes()).hexdigest() == result.artifact.sha256

    # The model in use: the actions job labels with it.
    model = {"model_run_id": "11111111-1111-1111-1111-111111111111",
             "nas_path": result.artifact.nas_path, "sha256": result.artifact.sha256}  # fmt: skip
    inputs = {**games[0], "model": model}
    out = HANDLERS["actions"](context(worker_settings, FakeApi(inputs), job("actions")))
    labelled = ActionsResult.model_validate(out)
    assert labelled.finder == "v1" and str(labelled.model_run_id) == model["model_run_id"]
    # A model file that does not match its hash is not used: the rules take over.
    inputs = {**games[0], "model": {**model, "sha256": "0" * 64}}
    out = HANDLERS["actions"](context(worker_settings, FakeApi(inputs), job("actions")))
    assert out["finder"] == "v0" and out["model_run_id"] is None


# --- the player at the ball (wieplay/ml/contact_pose.py) ---------------------------------------


def test_without_pose_weights_the_finder_goes_on_with_the_ball(
    worker_settings: WorkerSettings,  # noqa: F811
) -> None:
    root = worker_settings.analysis_root
    assert root is not None
    x, y, vis = path()
    inputs = {
        "features": store_signals(root, "np", signals(x, y, vis)),
        "fps": 60.0, "aspect": 16 / 9, "camera": None, "rallies": rallies(1, 9.5), "model": None,
        "parts": [{"path": "nowhere.mp4", "offset_ms": 0, "duration_ms": 9000}],
    }  # fmt: skip
    settings = worker_settings.model_copy(update={"players_weights": root / "no-weights"})
    out = HANDLERS["actions"](context(settings, FakeApi(inputs), job("actions")))
    assert out["stats"]["pose"] == "no weights"
    assert ActionsResult.model_validate(out).actions


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_the_pose_cue_reads_the_frames_once_and_keeps_them(
    worker_settings: WorkerSettings,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from wieplay.ml import contact_pose as cp
    from wieplay.worker import actions as wa

    root, raw = worker_settings.analysis_root, worker_settings.raw_root
    assert root is not None
    raw.mkdir(parents=True)
    clip = raw / "cam.mp4"
    args = ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30",
            "-t", "11", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)]  # fmt: skip
    subprocess.run(args, check=True)  # noqa: S603 - a generated test clip, ffmpeg from PATH
    weights = root / "weights"
    weights.mkdir(parents=True)
    (weights / cp.POSE_FILE).write_bytes(b"not a model")
    calls: list[tuple[int, int]] = []

    class FakePose:
        def __init__(self, path: Path, threads: int = 4) -> None:
            self.path = path

        @property
        def sha256(self) -> str:
            return "f" * 64

        def people(self, crop: np.ndarray) -> list[cp.Person]:
            calls.append(crop.shape[:2])
            k = np.zeros((17, 3), np.float32)
            k[:, :2] = 320
            k[:, 2] = 0.9
            return [(np.array([300, 200, 340, 400], np.float32), 0.9, k)]

    monkeypatch.setattr(cp, "PoseModel", FakePose)
    x, y, vis = path()
    inputs = {
        "features": store_signals(root, "p", signals(x, y, vis)),
        "fps": 30.0, "aspect": 16 / 9, "camera": None, "rallies": rallies(1, 9.5), "model": None,
        "parts": [{"path": str(clip), "offset_ms": 0, "duration_ms": 11_000}],
    }  # fmt: skip
    settings = worker_settings.model_copy(update={"players_weights": weights})
    out = HANDLERS["actions"](context(settings, FakeApi(inputs), job("actions")))
    stats = out["stats"]
    assert stats["pose"] == cp.POSE_NAME and stats["pose_candidates"] > 0
    assert stats["pose_new"] == len(calls) > 0 and all(c == (cp.CROP, cp.CROP) for c in calls)
    assert wa._cache_path(root, inputs["features"]).is_file()
    # Again: every frame comes from the cache next to the signals.
    calls.clear()
    out = HANDLERS["actions"](context(settings, FakeApi(inputs), job("actions")))
    assert out["stats"]["pose_new"] == 0 and calls == []
    assert out["stats"]["pose_seen"] == stats["pose_seen"]
