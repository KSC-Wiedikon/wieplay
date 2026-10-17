"""Suggested actions through the API (PLAN §10.4a): the actions job after the trajectory and
after a scoring, the worker's inputs and result, the suggestion rules (never into a rally a
coach tagged, untouched ones replaced, rejected ones not again), reviewing a suggestion like
a rally, facts-only stats, "actions done" and the training loop, permissions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.fakes import FakeStore
from tests.media_helpers import worker_client
from tests.test_analysis_api import ANALYSE_CAPS, complete, make_ready
from tests.test_court_api import result as trajectory_result
from tests.test_court_api import stored
from tests.test_scoring_api import analysed, match, rallies_of, tagging
from wieplay.core.config import Settings

KINDS = ["analyse", "train_rallies", "trajectory", "actions", "train_actions"]


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """Suggestions written whatever the measured score (the bar is in test_actions_gate.py)."""
    return settings.model_copy(
        update={"analysis_train_delay_seconds": 0, "action_suggestions": "on"}
    )


@pytest.fixture
def ready(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> Any:
    return make_ready(app, club, store, k=0)


@pytest.fixture
def analyser(app: FastAPI, club: dict[str, TestClient]) -> TestClient:
    return worker_client(app, club["admin"], ANALYSE_CAPS)


def claim(w: TestClient, kind: str | None = None) -> dict[str, Any] | None:
    r = w.post("/api/worker/claim", json={"caps": ANALYSE_CAPS, "wait": False, "kinds": KINDS})
    assert r.status_code == 200, r.text
    jobs = r.json()["jobs"]
    if kind is None and not jobs:
        return None
    [job] = jobs
    assert job["kind"] == kind, job["kind"]
    out: dict[str, Any] = job
    return out


def inputs(w: TestClient, job: dict[str, Any]) -> dict[str, Any]:
    r = w.post(f"/api/worker/jobs/{job['id']}/inputs")
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def through_trajectory(analyser: TestClient, store: FakeStore) -> dict[str, Any]:
    """Analyse a match (rallies, sets, sides), run its trajectory job; the actions job is
    queued next and claimed."""
    played, _ = match()
    analysed(analyser, played)
    job = claim(analyser, "trajectory")
    assert job is not None
    stored(store, job)
    complete(analyser, job, trajectory_result(job))
    found = claim(analyser, "actions")
    assert found is not None
    return found


def plan(rally: dict[str, Any], *, shift: int = 0) -> list[dict[str, Any]]:
    """Serve, reception, set, attack of one rally in the camera's terms."""
    server = "near" if (rally["serve_near"] or 0.5) >= 0.5 else "far"
    other = "far" if server == "near" else "near"
    start = rally["start_ms"] + shift
    steps = [(800, "S", server), (2000, "R", other), (3000, "E", other), (3800, "A", other)]
    return [
        {
            "rally_id": rally["id"],
            "t_ms": start + dt,
            "side": side,
            "code": code,
            "grade": None,
            "confidence": 0.7,
            "ball_x": 0.5,
            "ball_y": 0.45,
            "time_precision": "adjusted",
        }
        for dt, code, side in steps
    ]


def body(actions: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {
        "model": {"name": "wieplay-actions", "version": "abc123", "params": {"finder": "v0"}},
        "finder": "v0",
        "model_run_id": None,
        "actions": actions,
        "stats": {"candidates": 99},
        "timing": {"wall_s": 1.5},
        **extra,
    }


def suggested(client: TestClient, video: Any) -> list[dict[str, Any]]:
    events = tagging(client, video)["events"]
    return [e for e in events if e["source"] == "model"]


def event_in(e: dict[str, Any], **changes: Any) -> dict[str, Any]:
    keep = ("id", "code", "t_ms", "frame", "time_precision", "side", "number", "attributes",
            "review")  # fmt: skip
    return {**{k: e[k] for k in keep}, "base_version": e["version"], **changes}


def post_events(client: TestClient, video: Any, *events: dict[str, Any]) -> dict[str, Any]:
    r = client.post(f"/api/videos/{video['id']}/changes", json={"events": list(events)})
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


# --- queueing, inputs, results ---------------------------------------------------------------


def test_the_actions_follow_the_trajectory_and_are_suggested(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    coach = club["coach"]
    job = through_trajectory(analyser, store)
    assert job["params"]["reason"] == "trajectory"
    state = coach.get(f"/api/videos/{ready['id']}/analysis").json()["actions"]
    assert state["status"] == "running" and state["run_id"] is None
    found = inputs(analyser, job)
    assert found["features"]["nas_path"].endswith("signals-test.npz")
    assert found["camera"] is None and found["model"] is None and found["fps"] == 50.0
    rallies = found["rallies"]
    assert len(rallies) == len(rallies_of(tagging(coach, ready)))
    # Set 1: we play near and serve first; the model's server and winner as probabilities.
    first = rallies[0]
    assert first["serve_near"] > 0.8 and first["won_near"] is not None
    actions = [a for r in rallies[:3] for a in plan(r)]
    complete(analyser, job, body(actions))

    events = suggested(coach, ready)
    assert len(events) == 12
    serve = events[0]
    assert (serve["code"], serve["side"], serve["review"]) == ("S", "us", "unreviewed")
    assert serve["number"] is None and serve["confidence"] == 0.7
    assert (serve["ball_x"], serve["ball_y"]) == (0.5, 0.45)
    assert serve["time_precision"] == "adjusted" and serve["frame"] == round(serve["t_ms"] / 20)
    rally_ids = {r["id"] for r in rallies[:3]}
    assert {e["segment_id"] for e in events} == rally_ids
    assert [e["side"] for e in events[:4]] == ["us", "them", "them", "them"]

    state = coach.get(f"/api/videos/{ready['id']}/analysis").json()["actions"]
    assert state["status"] == "done" and state["finder"] == "v0"
    assert state["proposed"] == 12 and state["counts"]["S"] == 3 and state["counts"]["B"] == 0
    assert state["suggestions"] == {"unreviewed": 12, "confirmed": 0, "corrected": 0, "rejected": 0}
    assert state["vs_coach"] is None and state["model"]["finder"] == "v0"
    # Facts only: the stats count none of them yet, and say how many wait.
    stats = coach.get(f"/api/videos/{ready['id']}/stats").json()
    assert stats["tags"] == 0 and stats["suggested"] == 12
    assert coach.get(f"/api/teams/{ready['team_id']}/stats").json()["suggested"] == 12
    # A suggested serve is no fact: the rally's server stays the model's.
    seg = next(s for s in rallies_of(tagging(coach, ready)) if s["id"] == first["id"])
    assert seg["serving"] is None


def test_bad_results_fail_the_job(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    job = through_trajectory(analyser, store)
    rally = inputs(analyser, job)["rallies"][0]
    bad = plan(rally)
    bad[0]["code"] = "X"
    complete(analyser, job, body(bad), status=422)
    # v1 must name its training.
    r = club["coach"].post(f"/api/videos/{ready['id']}/actions")
    assert r.status_code == 202, r.text
    job = claim(analyser, "actions")
    assert job is not None
    complete(analyser, job, body(plan(rally), finder="v1"), status=422)
    assert suggested(club["coach"], ready) == []


def test_unknown_rallies_are_skipped(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    job = through_trajectory(analyser, store)
    rally = inputs(analyser, job)["rallies"][0]
    actions = [*plan(rally), {**plan(rally)[0], "rally_id": str(uuid.uuid4())}]
    complete(analyser, job, body(actions))
    state = club["coach"].get(f"/api/videos/{ready['id']}/analysis").json()["actions"]
    assert state["proposed"] == 4
    run = state["run_id"]
    assert run is not None


# --- the suggestion rules ------------------------------------------------------------------------


def test_reruns_replace_stale_suggestions_and_never_touch_a_coachs_rally(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    coach = club["coach"]
    job = through_trajectory(analyser, store)
    rallies = inputs(analyser, job)["rallies"][:4]
    complete(analyser, job, body([a for r in rallies for a in plan(r)]))
    events = suggested(coach, ready)
    by_rally: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        by_rally.setdefault(e["segment_id"], []).append(e)
    r0, r1, r2, r3 = (by_rally[r["id"]] for r in rallies)
    # Rally 1: the coach tags it by hand. Rally 2: the coach accepts one suggestion.
    # Rally 3: the coach rejects one.
    manual = {
        "id": str(uuid.uuid4()), "code": "S", "t_ms": rallies[1]["start_ms"] + 900,
        "side": "us", "number": 7, "attributes": {"grade": "+"}, "time_precision": "live",
    }  # fmt: skip
    post_events(
        coach,
        ready,
        manual,
        event_in(r2[1], review="confirmed"),
        {**event_in(r3[2]), "deleted": True},
    )
    # Run again with everything shifted by 100 ms.
    r = coach.post(f"/api/videos/{ready['id']}/actions")
    assert r.status_code == 202, r.text
    job = claim(analyser, "actions")
    assert job is not None
    complete(analyser, job, body([a for r in rallies for a in plan(r, shift=100)]))
    after = suggested(coach, ready)
    now: dict[str, list[dict[str, Any]]] = {}
    for e in after:
        now.setdefault(e["segment_id"], []).append(e)
    # Rally 0: replaced (new ids, new times).
    assert {e["id"] for e in now[rallies[0]["id"]]}.isdisjoint({e["id"] for e in r0})
    assert [e["t_ms"] for e in now[rallies[0]["id"]]] == [e["t_ms"] + 100 for e in r0]
    # Rally 1 (tagged by hand) and rally 2 (one accepted) get nothing new: what is there
    # stays as it was, not even the untouched suggestions are replaced.
    assert [(e["id"], e["t_ms"]) for e in now[rallies[1]["id"]]] == [
        (e["id"], e["t_ms"]) for e in r1
    ]
    assert {e["id"] for e in now[rallies[2]["id"]]} == {e["id"] for e in r2}
    # Rally 3: the rejected one is not suggested again (within 0.3 s), the others are new.
    times = sorted(e["t_ms"] for e in now[rallies[3]["id"]])
    assert r3[2]["t_ms"] + 100 not in times and len(times) == 3
    state = coach.get(f"/api/videos/{ready['id']}/analysis").json()["actions"]
    assert state["suggestions"]["confirmed"] == 1 and state["suggestions"]["rejected"] == 1
    # The two rallies the coach stands behind are scored against the coach's tags.
    vs = state["vs_coach"]
    assert vs is not None and vs["coach"] == 2 and vs["labels_complete"] is False
    assert vs["matched"] == 2 and vs["skill_accuracy"] == 1.0
    assert state["coach_rallies"] == 2


# --- reviewing a suggestion ----------------------------------------------------------------


def test_a_suggestion_is_accepted_corrected_or_rejected(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    coach = club["coach"]
    job = through_trajectory(analyser, store)
    rally = inputs(analyser, job)["rallies"][0]
    complete(analyser, job, body(plan(rally)))
    s, r, e, a = suggested(coach, ready)
    out = post_events(
        coach,
        ready,
        event_in(s, review="confirmed", number=9),  # accepted, with the server's number
        event_in(r, code="D"),  # another skill, the state passed through: corrected
        event_in(e, t_ms=e["t_ms"] + 20, time_precision="frame", review="confirmed"),  # nudged
        {**event_in(a), "deleted": True},  # not an action: rejected
    )
    reviews = {x["id"]: (x["review"], x["deleted_at"] is not None) for x in out["events"]}
    assert reviews[s["id"]] == ("confirmed", False)
    assert reviews[r["id"]] == ("corrected", False)
    assert reviews[e["id"]] == ("corrected", False)
    assert reviews[a["id"]] == ("rejected", True)
    # Undo the rejection: open again.
    gone = next(x for x in out["events"] if x["id"] == a["id"])
    back = post_events(coach, ready, {**event_in(gone), "review": "rejected", "deleted": False})
    assert back["events"][0]["review"] == "unreviewed"
    # The accepted and corrected ones count now; the open one does not.
    stats = coach.get(f"/api/videos/{ready['id']}/stats").json()
    assert stats["tags"] == 3 and stats["suggested"] == 1
    # An accepted serve is a fact: it names the rally's server.
    seg = next(x for x in rallies_of(tagging(coach, ready)) if x["id"] == rally["id"])
    assert seg["serving"] == s["side"]
    revisions = coach.get(f"/api/events/{r['id']}/revisions").json()
    assert [x["action"] for x in revisions] == ["create", "update"]


# --- "actions done" and the training -------------------------------------------------------


def test_actions_done_waits_for_every_answer_and_trains(
    ready: Any,
    club: dict[str, TestClient],
    analyser: TestClient,
    store: FakeStore,
    db_engine: Engine,
) -> None:
    coach = club["coach"]
    job = through_trajectory(analyser, store)
    rallies = inputs(analyser, job)["rallies"][:2]
    complete(analyser, job, body([a for r in rallies for a in plan(r)]))
    url = f"/api/videos/{ready['id']}/actions-reviewed"
    refused = coach.post(url, json={"reviewed": True})
    assert refused.status_code == 409
    assert refused.json()["detail"]["open_suggestions"] == 8
    events = suggested(coach, ready)
    post_events(coach, ready, *(event_in(e, review="confirmed") for e in events[:7]))
    post_events(coach, ready, {**event_in(events[7]), "deleted": True})
    done = coach.post(url, json={"reviewed": True})
    assert done.status_code == 200, done.text
    assert done.json()["actions_reviewed_at"] and done.json()["training_job_id"]
    assert tagging(coach, ready)["actions_reviewed_at"] is not None

    job = claim(analyser, "train_actions")
    assert job is not None and job["params"]["out_dir"] == "models/actions"
    found = inputs(analyser, job)
    [entry] = found["train"]
    assert entry["video_id"] == ready["id"] and len(entry["events"]) == 7
    _, code, side, rally_id = entry["events"][0]
    assert (code, side, rally_id) == ("S", "near", rallies[0]["id"])
    # A training that kept v0.
    complete(
        analyser,
        job,
        {
            "model": {"name": "actions-v1-hgb", "version": "abc123", "params": {}},
            "artifact": None,
            "accepted": False,
            "evaluation": {"summary": {"games": 1}, "videos": {}},
            "dataset": [{"video_id": ready["id"]}],
        },
    )
    state = coach.get(f"/api/videos/{ready['id']}/analysis").json()["actions"]
    assert state["model"]["finder"] == "v0" and state["model"]["games"] == 1
    assert state["reviewed_at"] is not None

    # An accepted model: stored, used by the next actions job.
    with db_engine.begin() as conn:  # a second game nobody has done
        conn.execute(text("UPDATE video SET actions_reviewed_at = NULL"))
    r = coach.post(url, json={"reviewed": True})
    assert r.status_code == 200
    job = claim(analyser, "train_actions")
    assert job is not None
    complete(
        analyser,
        job,
        {
            "model": {"name": "actions-v1-hgb", "version": "abc123", "params": {}},
            "artifact": {
                "nas_path": "models/actions/actions-v1-abc.pkl",
                "sha256": "e" * 64,
                "bytes": 999,
                "format": "pickle",
                "schema_version": 1,
            },
            "accepted": True,
            "evaluation": {"summary": {"v0": {"action_f1": 0.4}, "v1": {"action_f1": 0.6}}},
            "dataset": [],
        },
    )
    state = coach.get(f"/api/videos/{ready['id']}/analysis").json()["actions"]
    assert state["model"]["finder"] == "v1"
    # The video is "done" again (reviewed): no new suggestions are queued for it.
    assert claim(analyser) is None
    r = coach.post(f"/api/videos/{ready['id']}/actions")
    assert r.status_code == 202
    job = claim(analyser, "actions")
    assert job is not None
    model = inputs(analyser, job)["model"]
    assert model["nas_path"] == "models/actions/actions-v1-abc.pkl"
    # Taking "done" back works and retrains.
    undone = coach.post(url, json={"reviewed": False})
    assert undone.status_code == 200 and undone.json()["actions_reviewed_at"] is None


def test_a_training_outside_its_folder_is_refused(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    played, _ = match()
    analysed(analyser, played)
    claim(analyser, "trajectory")
    assert (
        club["coach"]
        .post(f"/api/videos/{ready['id']}/actions-reviewed", json={"reviewed": True})
        .status_code
        == 200
    )  # nothing suggested yet: nothing open
    job = claim(analyser, "train_actions")
    assert job is not None
    bad = {
        "model": {"name": "actions-v1-hgb", "version": "abc123"},
        "artifact": {"nas_path": "videos/x.pkl", "sha256": "e" * 64, "bytes": 9,
                     "format": "pickle", "schema_version": 1},
        "accepted": True,
    }  # fmt: skip
    complete(analyser, job, bad, status=422)


# --- after a scoring ----------------------------------------------------------------------------


def test_a_scoring_on_request_queues_the_actions_once(
    ready: Any,
    club: dict[str, TestClient],
    analyser: TestClient,
    store: FakeStore,
    db_engine: Engine,
) -> None:
    coach = club["coach"]
    job = through_trajectory(analyser, store)
    complete(analyser, job, body([]))
    before = datetime.now(UTC)
    assert coach.post(f"/api/videos/{ready['id']}/scoring").status_code == 200
    assert coach.post(f"/api/videos/{ready['id']}/scoring").status_code == 200
    with db_engine.begin() as conn:
        queued = conn.execute(
            text("SELECT not_before, params FROM job WHERE kind = 'actions' AND status = 'queued'")
        ).all()
    [(not_before, params)] = queued  # one job, moved rather than a second one added
    assert not_before > before and params["reason"] == "score:request"
    state = coach.get(f"/api/videos/{ready['id']}/analysis").json()["actions"]
    assert state["status"] == "queued"
    assert claim(analyser) is None  # debounced: not claimable yet


# --- permissions ----------------------------------------------------------------------------------


def test_only_coaches_and_helpers_ask_for_actions_players_mark_them_done(
    ready: Any, club: dict[str, TestClient], as_: Any
) -> None:
    player = as_("player-h3")
    url = f"/api/videos/{ready['id']}"
    assert player.post(f"{url}/actions").status_code == 403
    # Players tag and review their own games (owner, 17.10.2026).
    assert player.post(f"{url}/actions-reviewed", json={"reviewed": True}).status_code != 403
    # Not analysed yet: nothing to suggest from.
    assert club["coach"].post(f"{url}/actions").status_code == 409
    analysis = player.get(f"{url}/analysis").json()
    assert analysis["actions"]["status"] == "none"
