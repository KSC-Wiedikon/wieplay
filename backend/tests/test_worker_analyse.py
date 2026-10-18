"""The analysis jobs on the worker, for real: ffmpeg decodes a generated clip (a moving dot,
two whistles), VballNet-Fast runs in ONNX Runtime, the whistle detector listens, and the
result goes through the API. Skipped without ffmpeg."""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.analysis_helpers import league_rallies, synthetic_signals, to_ms
from tests.fakes import FakeStore
from tests.media_helpers import (
    H3,
    MEDIA_CAPS,
    THUMBS,
    claim,
    game_id,
    transcode_result,
    upload_all,
    worker_client,
)
from wieplay.core.analysis import TrainResult
from wieplay.ml import rallies
from wieplay.ml.features import Signals
from wieplay.worker import ffmpeg
from wieplay.worker.client import ClaimedJob
from wieplay.worker.config import WorkerSettings
from wieplay.worker.handlers import HANDLERS, Context

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
ANALYSE_CAPS = ["analyse", "nas"]


def make_clip(path: Path, seconds: int = 8) -> None:
    """60 fps (analysed at 30), a white dot flying over a green floor, whistles at 2 and 5 s
    over a little noise."""
    subprocess.run(
        [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"color=c=0x507050:s=640x360:r=60:d={seconds}",
            "-f", "lavfi", "-i", f"color=c=white:s=10x10:r=60:d={seconds}",
            "-f", "lavfi", "-i",
            "aevalsrc='0.02*(random(0)-0.5)+0.3*sin(2*PI*3800*t)*(between(t,2,2.5)+between(t,5,5.5))'"
            f":s=48000:d={seconds}",
            "-filter_complex", "[0][1]overlay=x='40+mod(t*150,560)':y='80+60*sin(t*4)'[v]",
            "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-preset", "ultrafast",
            "-pix_fmt", "yuv420p", "-c:a", "aac", str(path),
        ],
        check=True,
    )  # fmt: skip


class ApiThroughTestClient:
    """What a handler needs of the worker's API client, answered by the real API."""

    def __init__(self, w: TestClient) -> None:
        self.w = w

    def inputs(self, job_id: str) -> dict[str, Any]:
        r = self.w.post(f"/api/worker/jobs/{job_id}/inputs", json={})
        assert r.status_code == 200, r.text
        body: dict[str, Any] = r.json()
        return body


class FakeApi:
    def __init__(self, inputs: dict[str, Any]) -> None:
        self._inputs = inputs

    def inputs(self, job_id: str) -> dict[str, Any]:
        return self._inputs


def context(
    settings: WorkerSettings, api: Any, job: dict[str, Any], cancel: bool = False
) -> Context:
    seen: list[tuple[float, str]] = []
    event = threading.Event()
    if cancel:
        event.set()
    claimed = ClaimedJob(
        id=job["id"], kind=job["kind"], video_id=job.get("video_id"), params=job["params"],
        attempt=1, checkpoint=None,
    )  # fmt: skip
    ctx = Context(settings, api, claimed, event, lambda p, m: seen.append((p, m)))
    ctx.seen = seen  # type: ignore[attr-defined]
    return ctx


@pytest.fixture
def worker_settings(tmp_path: Path) -> WorkerSettings:
    return WorkerSettings(
        api_url="http://unused",
        token="x",
        work_dir=tmp_path / "work",
        raw_root=tmp_path / "raw",
        analysis_root=tmp_path / "analysis",
        analysis_hwaccel="vaapi",  # falls back to the CPU where there is no iGPU
        analysis_threads=2,
    )


def ready_with_real_file(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, settings: WorkerSettings
) -> dict[str, Any]:
    """A video whose ingest put a generated clip into the raw folder."""
    coach = club["coach"]
    created = coach.post(
        f"/api/teams/{H3}/videos",
        json={"kind": "match", "game_id": game_id(H3, 1),
              "files": [{"name": "C0001.MP4", "size": 1000, "type": "video/mp4"}]},
    )  # fmt: skip
    video = upload_all(coach, store, created.json())
    media = worker_client(app, club["admin"], MEDIA_CAPS)
    [ingest] = claim(media, MEDIA_CAPS)
    folder = settings.raw_root / ingest["params"]["raw_subdir"]
    folder.mkdir(parents=True)
    clip = folder / "01_C0001.MP4"
    make_clip(clip)
    probe = ffmpeg.probe(clip)
    [f] = ingest["params"]["files"]
    result = {
        "files": [{"file_id": f["id"], "sha256": "a" * 64, "nas_path": str(clip),
                   "duration_ms": probe.duration_ms, "probe": {}}],
        "duration_ms": probe.duration_ms, "width": 1920, "height": 1080, "fps_num": 60,
        "fps_den": 1, "has_audio": True, "capture": {},
    }  # fmt: skip
    assert media.post(f"/api/worker/jobs/{ingest['id']}/complete", json=result).status_code == 204
    while jobs := claim(media, MEDIA_CAPS):
        job = jobs[0]
        body = transcode_result(job) if job["kind"] == "transcode" else THUMBS
        assert media.post(f"/api/worker/jobs/{job['id']}/complete", json=body).status_code == 204
    return video


def test_analyse_end_to_end_on_a_generated_clip(
    app: FastAPI,
    club: dict[str, TestClient],
    store: FakeStore,
    worker_settings: WorkerSettings,
) -> None:
    video = ready_with_real_file(app, club, store, worker_settings)
    w = worker_client(app, club["admin"], ANALYSE_CAPS)
    [job] = w.post(
        "/api/worker/claim", json={"caps": ANALYSE_CAPS, "wait": False, "kinds": ["analyse"]}
    ).json()["jobs"]
    ctx = context(worker_settings, ApiThroughTestClient(w), job)
    result = HANDLERS["analyse"](ctx)
    assert w.post(f"/api/worker/jobs/{job['id']}/complete", json=result).status_code == 204

    path = worker_settings.analysis_root / result["artifact"]["nas_path"]  # type: ignore[operator]
    assert path.is_file() and path.parent.name.endswith(video["id"][:8])
    assert hashlib.sha256(path.read_bytes()).hexdigest() == result["artifact"]["sha256"]
    sig = Signals.load(path)
    assert sig.fps == 30.0 and 235 <= sig.frames <= 245  # 8 s at 30 of the 60 fps
    assert sig.motion.shape == (sig.frames, 3, 4) and float(sig.motion.max()) > 0
    assert [round(float(t)) for t in sig.whistles[:, 0]] == [2, 5]
    assert (
        result["timing"]["hwaccel"] in {"vaapi", "none"}
        and result["timing"]["frames"] == sig.frames
    )
    assert result["rallies"]["finder"] == "v0"
    seen = ctx.seen  # type: ignore[attr-defined]
    assert seen[-1][1] == "finding rallies" and any(m == "finding the ball" for _, m in seen)

    state = club["coach"].get(f"/api/videos/{video['id']}/analysis").json()
    assert state["status"] == "done"
    assert state["model_run"]["metrics"]["signals"]["whistles"] == 2
    assert state["model_run"]["params"]["analysis_fps"] == 30.0
    # The series the API scores with came along, and the video was scored.
    assert result["tracks"]["serve"]["finder"] == "v0"
    assert set(result["tracks"]["activity"]["channels"]) == {"ball", "court", "side", "whistle"}
    assert state["model_run"]["metrics"]["tracks"] == ["serve", "activity"]
    assert state["scoring"]["serve_finder"] == "v0"


def test_analyse_stops_when_cancelled(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, worker_settings: WorkerSettings
) -> None:
    ready_with_real_file(app, club, store, worker_settings)
    w = worker_client(app, club["admin"], ANALYSE_CAPS)
    [job] = w.post("/api/worker/claim", json={"caps": ANALYSE_CAPS, "wait": False}).json()["jobs"]
    with pytest.raises(ffmpeg.Cancelled):
        HANDLERS["analyse"](context(worker_settings, ApiThroughTestClient(w), job, cancel=True))


def test_train_rallies_handler(worker_settings: WorkerSettings) -> None:
    root = worker_settings.analysis_root
    assert root is not None
    refs, labels = {}, {}
    ids = [str(uuid.UUID(int=k + 1)) for k in range(3)]
    for k in range(3):
        true, duration = league_rallies(20, seed=50 + k)
        sig = synthetic_signals(true, duration, seed=60 + k, whistles=k != 1, audio=k != 1)
        path = root / "videos" / f"v{k}" / "signals.npz"
        path.parent.mkdir(parents=True)
        sig.save(path)
        refs[k] = {"nas_path": str(path.relative_to(root)),
                   "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}  # fmt: skip
        labels[k] = [list(r) for r in to_ms(true)]
    inputs: dict[str, Any] = {
        "rally_model": None,
        "serve_model": None,
        "train": [
            {"video_id": ids[k], "features": refs[k], "rallies": labels[k],
             "serves": [[s, int(n % 2 == 0)] for n, (s, _) in enumerate(labels[k])],
             "tracks": []}
            for k in (0, 1)
        ],
        "others": [{"video_id": ids[2], "features": refs[2], "tracks": ["activity"]}],
    }  # fmt: skip
    # A rejected proposal in the first game (dead time after its first rally); the second
    # comes from an API that does not send them yet.
    first_end = labels[0][0][1]
    inputs["train"][0]["rejected"] = [[first_end + 4_000, first_end + 8_000]]
    job = {"id": "0123456789abcdef", "kind": "train_rallies",
           "params": {"out_dir": "models/rallies"}}  # fmt: skip
    result = HANDLERS["train_rallies"](context(worker_settings, FakeApi(inputs), job))
    TrainResult.model_validate(result)  # what the API accepts
    assert set(result["evaluation"]["videos"]) == {ids[0], ids[1]}
    assert result["evaluation"]["summary"]["v1"]["videos"] == 2
    assert [d["video_id"] for d in result["dataset"]] == ids[:2]
    assert [d["rejected"] for d in result["dataset"]] == [1, 0]
    assert result["timing"]["hard_negatives"] == 1
    assert result["model"]["params"]["v1"]["hard_negative"] == rallies.V1Params().hard_negative
    model_path = root / result["artifact"]["nas_path"]
    model = rallies.RallyModel.load(model_path, result["artifact"]["sha256"])
    assert model.trained_on == ids[:2]
    if result["accepted"]:
        assert result["predictions"][ids[2]]["finder"] == "v1"
        assert len(result["predictions"][ids[2]]["proposals"]) >= 15
    else:
        assert result["predictions"] == {}
    # The serve model was scored on held-out games; every game without series gets them.
    assert set(result["serve"]["evaluation"]["videos"]) == {ids[0], ids[1]}
    assert set(result["tracks"][ids[0]]) == {"serve", "activity"}
    assert set(result["tracks"][ids[2]]) == {"serve"}  # it had its activity already
    # A training never reads outside the analysis root.
    inputs["train"][0]["features"] = {"nas_path": "../../etc/passwd", "sha256": "0" * 64}
    escaped = HANDLERS["train_rallies"](context(worker_settings, FakeApi(inputs), job))
    assert [d["video_id"] for d in escaped["dataset"]] == [ids[1]]


def test_the_analysis_runs_on_a_background_lane(tmp_path: Path) -> None:
    from wieplay.worker.runner import Worker

    settings = WorkerSettings(
        api_url="http://unused", token="x", caps=["ingest", "transcode", "nas", "analyse"],
        analysis_root=tmp_path, work_dir=tmp_path,
    )  # fmt: skip
    worker = Worker(settings)
    assert worker.main_caps == ["ingest", "transcode", "nas"]
    assert worker.background_caps == ["nas", "analyse"]
    no_root = Worker(settings.model_copy(update={"analysis_root": None}))
    assert no_root.background_caps == [] and "analyse" not in no_root.main_caps

    main_calls: list[tuple[bool, Any]] = []
    background_calls: list[tuple[bool, Any, list[str]]] = []

    class Main:
        def claim(self, caps: list[str], *, restarted: bool = False, kinds: Any = None) -> Any:
            main_calls.append((restarted, kinds))
            if len(main_calls) > 1:
                worker.stopping.wait(10)  # a long poll, until the background lane has claimed
            return [], False

    class Background:
        def claim(self, caps: list[str], *, restarted: bool = False, kinds: Any = None) -> Any:
            background_calls.append((restarted, kinds, list(caps)))
            worker.stopping.set()
            return [], False

    lane = threading.Thread(
        target=worker._lane,
        args=(Background(), worker.background_caps, ["analyse", "train_rallies"]),
        kwargs={"first": False},
    )
    lane.start()
    worker._lane(Main(), worker.main_caps, None, first=True)  # type: ignore[arg-type]
    lane.join(10)
    # The main lane tells the API it restarted (old leases free) before the other claims.
    assert main_calls[0] == (True, None)
    assert background_calls == [(False, ["analyse", "train_rallies"], ["nas", "analyse"])]
