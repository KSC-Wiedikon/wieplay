"""The ball's path and the court through the API (PLAN §10.4, §10.6): the trajectory job
after an analysis, its result, the ball track for the browser, calibrations, permissions."""

from __future__ import annotations

import gzip
import json
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nomal.core import court
from tests.fakes import FakeStore
from tests.test_analysis_api import (
    ANALYSE_CAPS,
    claim_kind,
    complete,
    make_ready,
    payload,
)
from tests.test_court import camera_points

KINDS = ["analyse", "train_rallies", "trajectory"]
TRACK = {
    "schema": 1,
    "fps": 15.0,
    "scale": 1000,
    "frames": 54000,
    "duration_s": 3600.0,
    "tracks": [[150, 400, 600, 10, -12, 11, -10]],
    "contacts": [[10_100, 420, 578, 0]],
    "stats": {"tracks": 1, "samples": 3, "contacts": 1, "bounces": 1},
}


@pytest.fixture
def ready(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> Any:
    return make_ready(app, club, store)


@pytest.fixture
def analyser(app: FastAPI, club: dict[str, TestClient]) -> TestClient:
    from tests.media_helpers import worker_client

    return worker_client(app, club["admin"], ANALYSE_CAPS)


def claim(w: TestClient, kind: str) -> dict[str, Any]:
    r = w.post("/api/worker/claim", json={"caps": ANALYSE_CAPS, "wait": False, "kinds": KINDS})
    assert r.status_code == 200, r.text
    [job] = r.json()["jobs"]
    assert job["kind"] == kind
    return job


def analysed(analyser: TestClient) -> dict[str, Any]:
    """Run the analyse job; its trajectory job is queued next."""
    job = claim_kind(analyser, "analyse")
    complete(analyser, job, payload(job, [(10_000, 20_000)]))
    return claim(analyser, "trajectory")


def result(
    job: dict[str, Any], *, court_points: dict[str, Any] | None = None, confidence: float = 0.8
) -> dict[str, Any]:
    data = gzip.compress(json.dumps(TRACK).encode(), mtime=0)
    return {
        "model": {"name": "nomal-trajectory", "version": "abc123", "params": {"out_fps": 15}},
        "track": {
            "nas_path": f"{job['params']['out_dir']}/ball-track-{job['id'][:8]}.json.gz",
            "key": job["params"]["track_key"],
            "sha256": "d" * 64,
            "bytes": len(data),
            "schema_version": 1,
            "stats": {**TRACK["stats"], "fps": 15.0},  # type: ignore[dict-item]
        },
        "court": (
            {"points": court_points, "confidence": confidence, "stats": {"support": confidence}}
            if court_points
            else None
        ),
        "timing": {"wall_s": 3.2},
    }


def stored(store: FakeStore, job: dict[str, Any]) -> bytes:
    data = gzip.compress(json.dumps(TRACK).encode(), mtime=0)
    store.put_object(job["params"]["track_key"], data)
    return data


def points(
    names: list[str] | None = None, *, center: tuple[float, float, float] = (4.5, -6.0, 5.0)
) -> list[dict[str, Any]]:
    found = camera_points(center, 1.1, names or list(court.CORNERS))
    return [{"landmark": k, "u": u, "v": v} for k, (u, v) in found.items()]


# --- the trajectory job ---------------------------------------------------------------------


def test_the_trajectory_job_follows_the_analysis(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    job = analysed(analyser)
    params = job["params"]
    assert params["video_id"] == ready["id"]
    assert params["features"]["nas_path"].endswith("signals-test.npz")
    assert params["track_key"].startswith(f"media/{ready['id']}/analysis/ball-track-")
    assert params["out_dir"].startswith("videos/")
    state = club["coach"].get(f"/api/videos/{ready['id']}/court").json()
    assert state["ball_track"] == {
        "available": False, "fps": None, "bytes": None, "contacts": None, "bounces": None,
        "created_at": None, "job": "running",
    }  # fmt: skip
    # The worker may put its track under its key, nothing else.
    sign = analyser.post(
        f"/api/worker/jobs/{job['id']}/sign", json={"op": "put", "keys": [params["track_key"]]}
    )
    assert sign.status_code == 200 and params["track_key"] in sign.json()["urls"]
    other = f"media/{ready['id']}/1080p/seg_00001.m4s"
    assert (
        analyser.post(
            f"/api/worker/jobs/{job['id']}/sign", json={"op": "put", "keys": [other]}
        ).status_code
        == 403
    )
    stored(store, job)
    complete(analyser, job, result(job))
    state = club["coach"].get(f"/api/videos/{ready['id']}/court").json()
    assert state["ball_track"]["available"] is True and state["ball_track"]["job"] == "done"
    assert state["ball_track"]["contacts"] == 1 and state["ball_track"]["fps"] == 15.0
    assert state["calibration"] is None and state["suggestions"] == []


def test_the_ball_track_is_served_compressed_and_revalidated(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore, as_: Any
) -> None:
    url = f"/api/videos/{ready['id']}/ball-track"
    assert club["coach"].get(url).status_code == 404
    job = analysed(analyser)
    data = stored(store, job)
    complete(analyser, job, result(job))
    r = club["coach"].get(url, headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200 and r.headers["content-encoding"] == "gzip"
    assert r.json() == TRACK  # the test client inflates it
    assert r.headers["cache-control"] == "private, no-cache"
    assert int(r.headers["content-length"]) == len(data)
    etag = r.headers["etag"]
    again = club["coach"].get(url, headers={"If-None-Match": etag})
    assert again.status_code == 304
    plain = club["coach"].get(url, headers={"Accept-Encoding": "identity"})
    assert plain.status_code == 200 and "content-encoding" not in plain.headers
    assert plain.json() == TRACK
    # Whoever may watch the video, may see it; others not.
    assert as_("player-h3").get(url).status_code == 200
    assert as_("basketball-player").get(url).status_code == 404
    anonymous = TestClient(club["coach"].app)  # type: ignore[arg-type]
    assert anonymous.get(url).status_code == 401


def test_bad_trajectory_results_fail_the_job_not_the_video(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    job = analysed(analyser)
    bad = result(job)
    bad["track"]["key"] = f"media/{ready['id']}/elsewhere.json.gz"
    complete(analyser, job, bad, status=422)
    assert club["coach"].get(f"/api/videos/{ready['id']}").json()["status"] == "ready"
    state = club["coach"].get(f"/api/videos/{ready['id']}/court").json()
    assert state["ball_track"]["available"] is False


def test_a_confident_court_is_offered_and_a_weak_one_is_not(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    job = analysed(analyser)
    stored(store, job)
    found = {p["landmark"]: [p["u"], p["v"]] for p in points()}
    complete(analyser, job, result(job, court_points=found, confidence=0.85))
    state = club["coach"].get(f"/api/videos/{ready['id']}/court").json()
    [auto] = state["suggestions"]
    assert auto["source"] == "auto" and auto["confidence"] == 0.85
    assert {p["landmark"] for p in auto["points"]} == set(court.CORNERS)
    # A rerun with a weak court replaces the proposal; it is not offered.
    rerun = club["coach"].post(f"/api/videos/{ready['id']}/analyse")
    assert rerun.status_code == 202
    job2 = analysed(analyser)
    stored(store, job2)
    complete(analyser, job2, result(job2, court_points=found, confidence=0.3))
    assert club["coach"].get(f"/api/videos/{ready['id']}/court").json()["suggestions"] == []


# --- calibrations -----------------------------------------------------------------------------


def test_solve_shows_the_fit_without_saving(
    ready: Any, club: dict[str, TestClient], as_: Any
) -> None:
    url = f"/api/videos/{ready['id']}/court"
    four = club["coach"].post(f"{url}/solve", json={"points": points()})
    assert four.status_code == 200, four.text
    body = four.json()
    assert body["error_cm"] is None and len(body["lines"]) == len(court.LINES)
    assert body["camera"]["center"] == pytest.approx([4.5, -6.0, 5.0], abs=0.01)
    six = points([*court.CORNERS, "net_left", "net_right"])
    six[4]["u"] += 0.004  # a click a little off
    checked = club["coach"].post(f"{url}/solve", json={"points": six}).json()
    assert checked["error_cm"] > 0 and checked["max_error_cm"] >= checked["error_cm"]
    assert set(checked["residuals_cm"]) == {p["landmark"] for p in six}
    # Anyone who may watch may try; nothing was saved.
    assert as_("player-h3").post(f"{url}/solve", json={"points": points()}).status_code == 200
    assert club["coach"].get(url).json()["calibration"] is None


def test_unusable_points_are_refused(ready: Any, club: dict[str, TestClient]) -> None:
    url = f"/api/videos/{ready['id']}/court/solve"
    good = points()
    swapped = [dict(p) for p in good]
    swapped[0]["landmark"], swapped[1]["landmark"] = "near_right", "near_left"
    swapped[2]["landmark"], swapped[3]["landmark"] = "far_left", "far_right"
    r = club["coach"].post(url, json={"points": swapped})
    assert r.status_code == 422 and "mirrored" in r.json()["detail"]
    twice = [*good[:3], {**good[0]}]
    assert club["coach"].post(url, json={"points": twice}).status_code == 422
    assert club["coach"].post(url, json={"points": good[:3]}).status_code == 422
    odd = [*good[:3], {"landmark": "service_zone", "u": 0.5, "v": 0.5}]
    assert club["coach"].post(url, json={"points": odd}).status_code == 422


def test_save_load_and_prefill_the_next_video(
    app: FastAPI, ready: Any, club: dict[str, TestClient], store: FakeStore, as_: Any
) -> None:
    url = f"/api/videos/{ready['id']}/court"
    body = {"points": points([*court.CORNERS, "net_left", "net_right"]), "method": "manual"}
    assert as_("basketball-player").put(url, json=body).status_code == 404  # another team
    saved = club["coach"].put(url, json=body)
    assert saved.status_code == 200, saved.text
    cal = saved.json()
    assert cal["status"] == "confirmed" and cal["method"] == "manual"
    assert cal["error_cm"] is not None and cal["error_cm"] < 1
    assert cal["camera"]["center"] == pytest.approx([4.5, -6.0, 5.0], abs=0.01)
    state = club["coach"].get(url).json()
    assert state["calibration"]["id"] == cal["id"] and state["can_edit"] is True
    assert state["suggestions"] == []  # it has its own
    # A player of the team sees it and may fix it too (players tag since 17.10.2026).
    seen = as_("player-h3").get(url).json()
    assert seen["calibration"]["id"] == cal["id"] and seen["can_edit"] is True
    # Saving again replaces it.
    again = club["coach"].put(url, json={"points": points()})
    assert again.json()["id"] == cal["id"] and again.json()["error_cm"] is None
    # The team's next video is pre-filled from it (the camera stands at the same spot).
    other = make_ready(app, club, store, k=2)
    nxt = club["coach"].get(f"/api/videos/{other['id']}/court").json()
    [team] = nxt["suggestions"]
    assert team["source"] == "team" and team["video_id"] == ready["id"]
    assert team["calibration_id"] == cal["id"]
    copied = club["coach"].put(
        f"/api/videos/{other['id']}/court",
        json={"points": team["points"], "method": "copied", "copied_from": team["calibration_id"]},
    )
    assert copied.status_code == 200 and copied.json()["copied_from"] == cal["id"]
    # Removing it: anyone who tags it may (players tag since 17.10.2026), not another team.
    assert as_("basketball-player").delete(url).status_code == 404
    assert as_("player-h3").delete(url).status_code == 204
    assert club["coach"].get(url).json()["calibration"] is None
    assert club["coach"].delete(url).status_code == 404


def test_court_permissions(ready: Any, club: dict[str, TestClient], as_: Any) -> None:
    url = f"/api/videos/{ready['id']}/court"
    assert as_("basketball-player").get(url).status_code == 404
    assert as_("vb-sport-admin").put(url, json={"points": points()}).status_code == 200
    anonymous = TestClient(club["coach"].app)  # type: ignore[arg-type]
    assert anonymous.get(url).status_code == 401
    state = club["coach"].get(url).json()
    assert [lm["key"] for lm in state["landmarks"] if lm["corner"]] == list(court.CORNERS)
    assert len(state["lines"]) == len(court.LINES)
    assert state["aspect"] == pytest.approx(16 / 9)
