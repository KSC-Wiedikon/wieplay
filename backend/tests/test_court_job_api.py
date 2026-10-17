"""The court set automatically, through the API (PLAN §10.6): the court job after the
trajectory job, its result (the camera's setups, the court from the hall's memory or the
finder), a coach's calibration that always wins, fixing one setup, the hall's memory for
the next videos, the old single calibration, the CLI."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from nomal.core import calibration, court
from nomal.core.models import ModelRun
from tests.fakes import FakeStore
from tests.test_analysis_api import ANALYSE_CAPS, claim_kind, complete, make_ready, payload
from tests.test_court import camera_points
from tests.test_court_api import TRACK, result, stored

KINDS = ["analyse", "train_rallies", "trajectory", "court"]
BUMP_AT = 4_350_000  # 1:12:30
END = 7_200_000
# Setup 2: the camera knocked 3 % of the picture to the right.
SHIFT = [[1.0, 0.0, 0.03], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]  # setup 2 picture → setup 1's
EYE = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]


@pytest.fixture
def ready(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> Any:
    return make_ready(app, club, store)


@pytest.fixture
def analyser(app: FastAPI, club: dict[str, TestClient]) -> TestClient:
    from tests.media_helpers import worker_client

    return worker_client(app, club["admin"], ANALYSE_CAPS)


def claim(w: TestClient, kind: str, kinds: list[str] = KINDS) -> dict[str, Any]:
    r = w.post("/api/worker/claim", json={"caps": ANALYSE_CAPS, "wait": False, "kinds": kinds})
    assert r.status_code == 200, r.text
    [job] = r.json()["jobs"]
    assert job["kind"] == kind, job["kind"]
    return job


def court_job(analyser: TestClient, store: FakeStore) -> dict[str, Any]:
    """Analysis, trajectory: the court job comes next."""
    job = claim_kind(analyser, "analyse")
    complete(analyser, job, payload(job, [(10_000, 20_000)]))
    traj = claim(analyser, "trajectory")
    assert traj["params"]["court"] is False  # the court job finds the court now
    stored(store, traj)
    complete(analyser, traj, result(traj))
    return claim(analyser, "court")


def corners(shift: float = 0.0) -> dict[str, list[float]]:
    found = camera_points((4.5, -6.0, 5.0), 1.1, list(court.CORNERS))
    return {k: [u - shift, v] for k, (u, v) in found.items()}


def court_result(
    job: dict[str, Any],
    *,
    method: str = "auto-hall",
    confidence: float = 0.82,
    bump: bool = True,
    second: str = "bump",
    reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    setups: list[dict[str, Any]] = [
        {
            "from_ms": 0,
            "to_ms": BUMP_AT if bump else END,
            "start": "start",
            "shift_px": 0.0,
            "samples": 140,
            "view": 0,
            "to_main": EYE,
        },
    ]
    found: list[dict[str, Any]] = [{"points": corners()}]
    if bump:
        setups.append(
            {
                "from_ms": BUMP_AT,
                "to_ms": END,
                "start": second,
                "shift_px": 38.4,
                "samples": 98,
                "view": 1 if second == "moved" else 0,
                "to_main": EYE if second == "moved" else SHIFT,
            }
        )
        found.append({"points": corners(0.03)})
    return {
        "model": {"name": "nomal-court", "version": "abc123", "params": {"step_s": 30}},
        "frames": {
            "nas_path": f"{job['params']['out_dir']}/court-{job['id'][:8]}.npz",
            "sha256": "e" * 64,
            "bytes": 180_000,
            "schema_version": 1,
            "stats": {"size": [1280, 720], "frames": 15},
        },
        "setups": setups,
        "main": 0,
        "court": {
            "method": method,
            "confidence": confidence,
            "reference": reference,
            "setups": found,
            "stats": {"how": "direct", "inliers": 140},
        },
        "candidates": {"0": {"hall": [], "finder": None}},
        "timing": {"wall_s": 95.0},
    }


def same(points: list[dict[str, Any]], expected: dict[str, list[float]], tol: float = 1e-5) -> bool:
    """The expected landmarks are among the points, where expected (a court carried over to
    another setup has all its landmarks that show)."""
    got = {p["landmark"]: (p["u"], p["v"]) for p in points}
    return set(expected) <= set(got) and all(
        abs(got[k][0] - u) <= tol and abs(got[k][1] - v) <= tol for k, (u, v) in expected.items()
    )


def court_of(club: dict[str, TestClient], video: Any) -> dict[str, Any]:
    r = club["coach"].get(f"/api/videos/{video['id']}/court")
    assert r.status_code == 200, r.text
    body: dict[str, Any] = r.json()
    return body


def runs(app: FastAPI) -> list[ModelRun]:
    from tests.conftest import TEST_DB

    engine = create_engine(TEST_DB)
    with Session(engine) as db:
        out = list(db.scalars(select(ModelRun).where(ModelRun.kind == "court")))
    engine.dispose()
    return out


# --- the job and its result ---------------------------------------------------------------------


def test_the_court_job_follows_the_trajectory_job(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    job = court_job(analyser, store)
    params = job["params"]
    assert params["video_id"] == ready["id"] and params["duration_ms"] > 0
    assert params["out_dir"].startswith("videos/") and params["inputs"]
    assert params["track"]["nas_path"].endswith(".json.gz")
    assert params["references"] == []  # nothing calibrated yet
    assert court_of(club, ready)["court_job"] == "running"


def test_a_sure_court_from_the_hall_is_set_to_be_checked_with_the_setups(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    job = court_job(analyser, store)
    ref = {"calibration_id": str(uuid.uuid4()), "video_id": str(uuid.uuid4())}
    complete(analyser, job, court_result(job, reference=ref))
    state = court_of(club, ready)
    cal = state["calibration"]
    assert cal["method"] == "auto-hall" and cal["checked"] is False
    assert cal["confidence"] == 0.82 and state["suggestions"] == []
    assert state["court_job"] == "done"
    assert [(s["from_ms"], s["to_ms"], s["start"]) for s in state["setups"]] == [
        (0, BUMP_AT, "start"),
        (BUMP_AT, END, "bump"),
    ]
    first, second = cal["segments"]
    assert first["main"] and not second["main"] and second["start"] == "bump"
    assert second["shift_px"] == 38.4 and not first["checked"]
    # The calibration's own fields are the main setup's: an old reader gets most of the match.
    assert cal["points"] == first["points"] and cal["court_to_image"] == first["court_to_image"]
    near = {p["landmark"]: p["u"] for p in second["points"]}
    assert near["near_left"] == pytest.approx(corners()["near_left"][0] - 0.03, abs=1e-5)
    # Each moment maps through its own setup.
    with Session(create_engine(_db())) as db:
        row = calibration.current(db, uuid.UUID(ready["id"]))
        assert row is not None and row.segments is not None
        late = calibration.segment_at(row, BUMP_AT + 1)
        early = calibration.segment_at(row, BUMP_AT - 1)
        assert late is row.segments[1] and early is row.segments[0]
        assert calibration.segment_at(row, END + 60_000) is row.segments[1]
        x, y = court.apply(court.from_lists(late["homography"]), tuple(corners(0.03)["far_right"]))
        assert (x, y) == (pytest.approx(9.0, abs=1e-3), pytest.approx(18.0, abs=1e-3))


def _db() -> str:
    from tests.conftest import TEST_DB

    return TEST_DB



def test_a_camera_carried_to_another_spot_is_a_setup_too(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    # The worker calls a setup on another view "moved"; prod jobs failed on it (17.10.2026).
    job = court_job(analyser, store)
    complete(analyser, job, court_result(job, second="moved"))
    state = court_of(club, ready)
    assert state["court_job"] == "done"
    assert [s["start"] for s in state["setups"]] == ["start", "moved"]

def test_an_unsure_court_is_a_guess_and_a_weak_one_nothing(
    app: FastAPI, ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    job = court_job(analyser, store)
    complete(analyser, job, court_result(job, method="auto", confidence=0.5, bump=False))
    state = court_of(club, ready)
    assert state["calibration"] is None  # not sure enough to set it
    [guess] = state["suggestions"]
    assert guess["source"] == "auto" and guess["confidence"] == 0.5
    rerun = club["coach"].post(f"/api/videos/{ready['id']}/analyse")
    assert rerun.status_code == 202
    job2 = court_job(analyser, store)
    complete(analyser, job2, court_result(job2, method="auto", confidence=0.3, bump=False))
    assert court_of(club, ready)["suggestions"] == []
    assert len(runs(app)) == 2


def test_a_coachs_court_wins_and_gains_the_setups(
    app: FastAPI, ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    url = f"/api/videos/{ready['id']}/court"
    # The coach set the court up on a frame after the bump (1:30:00).
    mine = [{"landmark": k, "u": u, "v": v} for k, (u, v) in corners(0.03).items()]
    saved = club["coach"].put(url, json={"points": mine, "at_ms": 5_400_000})
    assert saved.status_code == 200, saved.text
    assert saved.json()["checked"] is True and saved.json()["segments"] is None
    # No picture of the empty hall yet: the court job makes one (for the hall's memory).
    job = claim(analyser, "court", ["court"])
    assert job["params"]["track"] is None and job["params"]["references"] == []
    out = court_result(job)
    out["court"]["setups"][1]["points"] = corners(0.035)  # the job is 0.5 % off after the bump
    complete(analyser, job, out)
    cal = court_of(club, ready)["calibration"]
    assert cal["method"] == "manual" and cal["checked"] is True  # still the coach's
    first, second = cal["segments"]
    assert second["checked"] and second["method"] == "manual"
    assert same(second["points"], corners(0.03))
    # Before the bump, the coach's court follows the camera back.
    assert first["method"] == "derived" and not first["checked"]
    assert same(first["points"], corners())
    # The job's court against the coach's, held out: 0.5 % of the picture off.
    [run] = runs(app)
    vs = run.metrics["vs_coach"]
    assert vs["points"] == 4 and 5 < vs["rms_cm"] < 80


def test_fixing_one_setup_carries_over_to_the_setups_nobody_set(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    job = court_job(analyser, store)
    complete(analyser, job, court_result(job))
    url = f"/api/videos/{ready['id']}/court"
    fixed = [{"landmark": k, "u": u, "v": v} for k, (u, v) in corners(0.032).items()]
    r = club["coach"].put(url, json={"points": fixed, "method": "manual", "segment": 1})
    assert r.status_code == 200, r.text
    cal = r.json()
    assert cal["checked"] is True and cal["method"] == "manual"
    first, second = cal["segments"]
    assert second["checked"] and second["method"] == "manual"
    assert first["method"] == "derived"  # followed the fix through the pictures
    u = {p["landmark"]: p["u"] for p in first["points"]}
    assert u["near_left"] == pytest.approx(corners(0.002)["near_left"][0], abs=1e-5)
    # Now the first setup by hand: the second, set by the coach, stays.
    again = [{"landmark": k, "u": u, "v": v} for k, (u, v) in corners().items()]
    r = club["coach"].put(url, json={"points": again, "segment": 0})
    first, second = r.json()["segments"]
    assert first["checked"] and second["checked"]
    u = {p["landmark"]: p["u"] for p in second["points"]}
    assert u["near_left"] == pytest.approx(corners(0.032)["near_left"][0], abs=1e-5)
    # A setup that does not exist.
    assert club["coach"].put(url, json={"points": again, "segment": 7}).status_code == 422
    # A later court job does not touch the coach's court.
    rerun = club["coach"].post(f"/api/videos/{ready['id']}/analyse")
    assert rerun.status_code == 202
    job2 = court_job(analyser, store)
    complete(analyser, job2, court_result(job2, confidence=0.99))
    assert court_of(club, ready)["calibration"]["segments"][1]["checked"] is True


def test_the_hall_memory_offers_other_videos_calibrations(
    app: FastAPI, ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    job = court_job(analyser, store)
    complete(analyser, job, court_result(job, bump=False))
    # Confirmed by the coach: a picture of the hall with a court.
    club["coach"].put(
        f"/api/videos/{ready['id']}/court",
        json={"points": [{"landmark": k, "u": u, "v": v} for k, (u, v) in corners().items()]},
    )
    other = make_ready(app, club, store, k=2)
    job2 = court_job(analyser, store)
    assert job2["video_id"] == other["id"]
    [ref] = job2["params"]["references"]
    assert ref["video_id"] == ready["id"] and ref["checked"] is True and ref["same_team"] is True
    assert ref["frames"]["nas_path"].endswith(f"court-{job['id'][:8]}.npz")
    to_image = court.from_lists(ref["court_to_image"])
    u, v = court.apply(to_image, (0.0, 0.0))
    assert (u, v) == (
        pytest.approx(corners()["near_left"][0], abs=1e-4),
        pytest.approx(corners()["near_left"][1], abs=1e-4),
    )


def test_an_old_single_calibration_reads_as_one_setup(
    ready: Any, club: dict[str, TestClient]
) -> None:
    url = f"/api/videos/{ready['id']}/court"
    pts = [{"landmark": k, "u": u, "v": v} for k, (u, v) in corners().items()]
    cal = club["coach"].put(url, json={"points": pts, "method": "manual"}).json()
    assert cal["segments"] is None and cal["checked"] is True
    with Session(create_engine(_db())) as db:
        row = calibration.current(db, uuid.UUID(ready["id"]))
        assert row is not None
        for t in (0, 3_600_000, 10**9):
            seg = calibration.segment_at(row, t)
            assert seg["homography"] == row.homography and seg["main"] is True
    # Saving a setup of a video without setups is saving its court.
    r = club["coach"].put(url, json={"points": pts, "segment": 0})
    assert r.status_code == 200 and r.json()["segments"] is None


def test_bad_court_results_fail_the_job_not_the_video(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    def outside(r: dict[str, Any]) -> None:
        r["frames"]["nas_path"] = "videos/elsewhere/court.npz"

    def one_court_for_two_setups(r: dict[str, Any]) -> None:
        r["court"]["setups"] = r["court"]["setups"][:1]

    def no_such_main(r: dict[str, Any]) -> None:
        r["main"] = 5

    for k, spoil in enumerate((outside, one_court_for_two_setups, no_such_main)):
        if k:
            assert club["coach"].post(f"/api/videos/{ready['id']}/analyse").status_code == 202
        job = court_job(analyser, store)
        bad = court_result(job)
        spoil(bad)
        complete(analyser, job, bad, status=422)
    assert club["coach"].get(f"/api/videos/{ready['id']}").json()["status"] == "ready"
    assert court_of(club, ready)["calibration"] is None


def test_the_cli_queues_court_jobs_and_reports_them(
    ready: Any,
    analyser: TestClient,
    store: FakeStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from nomal import cli
    from nomal.core.config import get_settings

    job = court_job(analyser, store)
    complete(analyser, job, court_result(job))
    monkeypatch.setenv("NOMAL_DATABASE_URL", _db())
    get_settings.cache_clear()
    try:
        assert cli.main(["court", "--all-ready", "--dry-run"]) == 0
        assert "0 queued" in capsys.readouterr().out  # it ran for that video
        assert cli.main(["court", "--video", ready["id"], "--force"]) == 0
        assert "1 queued" in capsys.readouterr().out
        assert claim(analyser, "court")["video_id"] == ready["id"]
        assert cli.main(["court-eval"]) == 0
        out = capsys.readouterr().out
        assert "auto-hall" in out and "setups 2 (bumps 1)" in out
        assert "1 of 1 videos with a court" in out
    finally:
        get_settings.cache_clear()


def test_the_trajectory_results_court_still_counts_for_older_workers(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    """A worker from before the court job sends the trajectory job's court: a proposal."""
    job = claim_kind(analyser, "analyse")
    complete(analyser, job, payload(job, [(10_000, 20_000)]))
    traj = claim(analyser, "trajectory")
    stored(store, traj)
    found = {k: v for k, v in corners().items()}
    complete(analyser, traj, result(traj, court_points=found, confidence=0.85))
    [auto] = court_of(club, ready)["suggestions"]
    assert auto["source"] == "auto" and auto["confidence"] == 0.85
    assert TRACK["stats"]["bounces"] == 1
