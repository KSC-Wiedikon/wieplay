"""The trajectory and court jobs on the worker, for real: after the analysis of a generated
clip, the handler smooths the ball's path, uploads it through a signed URL, and the result
goes through the API to the browser's endpoint; the court job follows (the camera's setups,
the empty hall's picture on the NAS). Skipped without ffmpeg."""

from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nomal.worker.config import WorkerSettings
from nomal.worker.handlers import BACKGROUND_KINDS, HANDLERS
from tests.fakes import FakeStore
from tests.media_helpers import worker_client
from tests.test_worker_analyse import (
    ANALYSE_CAPS,
    ApiThroughTestClient,
    context,
    ready_with_real_file,
    worker_settings,  # noqa: F401 - a fixture
)

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


class SigningApi(ApiThroughTestClient):
    def sign(self, job_id: str, op: str, keys: list[str]) -> dict[str, str]:
        r = self.w.post(f"/api/worker/jobs/{job_id}/sign", json={"op": op, "keys": keys})
        assert r.status_code == 200, r.text
        urls: dict[str, str] = r.json()["urls"]
        return urls


def test_trajectory_end_to_end_on_a_generated_clip(
    app: FastAPI,
    club: dict[str, TestClient],
    store: FakeStore,
    worker_settings: WorkerSettings,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert "trajectory" in BACKGROUND_KINDS
    video = ready_with_real_file(app, club, store, worker_settings)
    w = worker_client(app, club["admin"], ANALYSE_CAPS)
    kinds = {"caps": ANALYSE_CAPS, "wait": False, "kinds": list(BACKGROUND_KINDS)}
    [job] = w.post("/api/worker/claim", json=kinds).json()["jobs"]
    assert job["kind"] == "analyse"
    result = HANDLERS["analyse"](context(worker_settings, SigningApi(w), job))
    assert w.post(f"/api/worker/jobs/{job['id']}/complete", json=result).status_code == 204

    [job] = w.post("/api/worker/claim", json=kinds).json()["jobs"]
    assert job["kind"] == "trajectory"
    puts: list[tuple[str, bytes, str]] = []

    def put(url: str, *, content: bytes, headers: dict[str, str], timeout: Any) -> httpx.Response:
        key = url.split("https://s3.test/", 1)[1].split("?", 1)[0]
        store.put_object(key, content)
        puts.append((key, content, headers["Content-Type"]))
        return httpx.Response(200)

    monkeypatch.setattr("nomal.worker.trajectory.httpx.put", put)
    ctx = context(worker_settings, SigningApi(w), job)
    out = HANDLERS["trajectory"](ctx)
    assert w.post(f"/api/worker/jobs/{job['id']}/complete", json=out).status_code == 204

    [(key, data, kind)] = puts
    assert key == job["params"]["track_key"] and kind == "application/gzip"
    nas = worker_settings.analysis_root / out["track"]["nas_path"]  # type: ignore[operator]
    assert nas.read_bytes() == data and hashlib.sha256(data).hexdigest() == out["track"]["sha256"]
    track = json.loads(gzip.decompress(data))
    assert track["schema"] == 1 and track["fps"] == 15.0 and track["scale"] == 1000
    # The court is the court job's now.
    assert out["court"] is None
    assert not any(m == "finding the court" for _, m in ctx.seen)  # type: ignore[attr-defined]

    [job] = w.post("/api/worker/claim", json=kinds).json()["jobs"]
    assert job["kind"] == "court" and job["params"]["track"]["nas_path"] == out["track"]["nas_path"]
    court_ctx = context(worker_settings, SigningApi(w), job)
    found = HANDLERS["court"](court_ctx)
    assert w.post(f"/api/worker/jobs/{job['id']}/complete", json=found).status_code == 204
    # A still camera: one setup; its empty hall kept on the NAS for the hall's memory.
    [setup] = found["setups"]
    assert setup["from_ms"] == 0 and setup["to_ms"] == job["params"]["duration_ms"]
    hall = worker_settings.analysis_root / found["frames"]["nas_path"]  # type: ignore[operator]
    assert hashlib.sha256(hall.read_bytes()).hexdigest() == found["frames"]["sha256"]
    # A green floor with a dot: no court to find, or not one to trust.
    assert found["court"] is None or found["court"]["confidence"] < 0.6

    coach = club["coach"]
    state = coach.get(f"/api/videos/{video['id']}/court").json()
    assert state["ball_track"]["available"] is True and state["ball_track"]["job"] == "done"
    assert state["court_job"] == "done" and len(state["setups"]) == 1
    assert state["calibration"] is None
    served = coach.get(f"/api/videos/{video['id']}/ball-track")
    assert served.status_code == 200 and served.json() == track
