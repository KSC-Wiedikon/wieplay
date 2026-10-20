"""Whether suggested actions are written (PLAN §10.4a): since 20.10.2026 from the start (auto, on),
each with a "sure" flag and the likeliest other answer, never when switched off (off). A
training that beats the rules brings its suggestions to every game nobody has reviewed; its
held-out score on reviewed games is reported with the gate, like the measured numbers."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.fakes import FakeStore
from tests.media_helpers import worker_client
from tests.test_actions_api import (
    body,
    claim,
    inputs,
    plan,
    suggested,
    through_trajectory,
)
from tests.test_analysis_api import ANALYSE_CAPS, complete, make_ready
from wieplay.core import actions
from wieplay.core.config import Settings
from wieplay.core.models import ModelRun


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"analysis_train_delay_seconds": 0})


@pytest.fixture
def ready(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> Any:
    return make_ready(app, club, store, k=0)


@pytest.fixture
def analyser(app: FastAPI, club: dict[str, TestClient]) -> TestClient:
    return worker_client(app, club["admin"], ANALYSE_CAPS)


def training(
    accepted: bool, precision: float, recall: float, games: int = 2, artifact: bool = False
) -> dict[str, Any]:
    finder = "v1" if accepted else "v0"
    return {
        "model": {"name": "actions-v1-hgb", "version": "abc123", "params": {}},
        "artifact": {
            "nas_path": "models/actions/actions-v1-abc.pkl", "sha256": "e" * 64, "bytes": 9,
            "format": "pickle", "schema_version": 1,
        } if artifact or accepted else None,
        "accepted": accepted,
        "evaluation": {
            "summary": {
                "v0": {"games": games, "action_precision": 0.3, "action_f1": 0.3},
                finder: {"games": games, "action_precision": precision, "action_f1": precision},
                "contact_recall": recall,
                "games": games,
            },
            "videos": {},
        },
        "dataset": [],
    }  # fmt: skip


def train(analyser: TestClient, payload: dict[str, Any]) -> None:
    job = claim(analyser, "train_actions")
    assert job is not None
    complete(analyser, job, payload)


def gate_of(client: TestClient, video: Any) -> dict[str, Any]:
    out: dict[str, Any] = client.get(f"/api/videos/{video['id']}/analysis").json()["actions"][
        "gate"
    ]
    return out


def queue_training(app: FastAPI) -> None:
    from datetime import timedelta

    with app.state.sessionmaker() as db:
        actions.queue_training(db, timedelta(0))
        db.commit()


# --- the gate itself ---------------------------------------------------------------------------


def run(accepted: bool, precision: float | None, recall: float | None, games: int) -> ModelRun:
    finder = "v1" if accepted else "v0"
    return ModelRun(
        id=uuid.uuid4(),
        kind="train_actions",
        metrics={
            "accepted": accepted,
            "summary": {
                finder: {"action_precision": precision},
                "contact_recall": recall,
                "games": games,
            },
        },
    )


@pytest.mark.parametrize(
    ("mode", "the_run", "is_open", "finder", "games"),
    [
        ("auto", None, True, "v0", 0),  # no coach tagged anything: the rules suggest
        ("auto", run(False, 0.3, 0.6, 1), True, "v0", 1),
        ("auto", run(True, 0.8, 0.7, 3), True, "v1", 3),
        ("on", None, True, "v0", 0),
        ("off", run(True, 0.95, 0.95, 5), False, "v1", 5),
    ],
)
def test_the_gate(
    settings: Settings,
    mode: str,
    the_run: ModelRun | None,
    is_open: bool,
    finder: str,
    games: int,
) -> None:
    g = actions.gate_of(the_run, settings.model_copy(update={"action_suggestions": mode}))
    assert (g.open, g.reason, g.finder, g.games) == (is_open, mode, finder, games)
    assert "min_precision" not in g.as_dict()


# --- through the API --------------------------------------------------------------------------


def test_suggestions_from_the_start_with_sure_flags(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    coach = club["coach"]
    job = through_trajectory(analyser, store)
    found = inputs(analyser, job)
    rallies = found["rallies"][:2]
    assert "parts" in found  # the camera files, for the player-at-the-ball cue
    items = [a for r in rallies for a in plan(r)]
    items[0] = {**items[0], "sure": True, "confidence": 0.98}
    items[1] = {**items[1], "alternative": f"D:{items[1]['side']}"}  # a dig on the same side
    items[2] = {**items[2], "alternative": "none"}
    complete(analyser, job, body(items))
    rows = sorted(suggested(coach, ready), key=lambda e: e["t_ms"])
    assert len(rows) == 8  # no training, no reviewed game: written anyway
    assert rows[0]["model_sure"] is True and rows[0]["code"] == "S"
    assert rows[1]["model_sure"] is False
    # The alternative in the coach's terms: the reception's side, the other skill.
    assert rows[1]["model_alternative"] in ("D:us", "D:them")
    assert rows[1]["model_alternative"].split(":")[1] == rows[1]["side"]
    assert rows[2]["model_alternative"] == "none"
    state = coach.get(f"/api/videos/{ready['id']}/analysis").json()["actions"]
    assert state["proposed"] == 8 and state["gate"]["open"] is True
    assert state["gate"]["reason"] == "auto" and state["gate"]["games"] == 0
    assert set(state["gate"]["measured"]) >= {"rallies", "precision", "sure", "sure_wrong"}
    stats = coach.get(f"/api/videos/{ready['id']}/stats").json()
    assert stats["suggested"] == 8 and stats["actions_gate"]["open"] is True


def test_a_bad_alternative_is_refused(ready: Any, analyser: TestClient, store: FakeStore) -> None:
    job = through_trajectory(analyser, store)
    rallies = inputs(analyser, job)["rallies"][:1]
    items = [{**a, "alternative": "X:left"} for a in plan(rallies[0])]
    r = analyser.post(f"/api/worker/jobs/{job['id']}/complete", json=body(items))
    assert r.status_code in (400, 422), r.text


def test_a_training_that_beats_the_rules_suggests_for_every_game_nobody_reviewed(
    app: FastAPI, ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    coach = club["coach"]
    job = through_trajectory(analyser, store)
    complete(
        analyser, job, body([a for r in inputs(analyser, job)["rallies"][:2] for a in plan(r)])
    )
    assert len(suggested(coach, ready)) == 8

    queue_training(app)
    train(analyser, training(accepted=False, precision=0.3, recall=0.8, games=3))
    g = gate_of(coach, ready)
    assert (g["open"], g["finder"], g["games"], g["precision"]) == (True, "v0", 3, 0.3)
    assert claim(analyser) is None  # the rules stay: nothing new to suggest

    queue_training(app)
    train(analyser, training(accepted=True, precision=0.8, recall=0.7, games=3))
    g = gate_of(coach, ready)
    assert (g["open"], g["finder"]) == (True, "v1")
    job = claim(analyser, "actions")
    assert job is not None and job["params"]["reason"] == "train_actions"
    found = inputs(analyser, job)
    assert found["model"]["nas_path"] == "models/actions/actions-v1-abc.pkl"
    complete(analyser, job, body([a for r in found["rallies"][:2] for a in plan(r)], finder="v1",
                                 model_run_id=found["model"]["model_run_id"]))  # fmt: skip
    assert len(suggested(coach, ready)) == 8


def test_off_writes_nothing(
    app: FastAPI,
    ready: Any,
    club: dict[str, TestClient],
    analyser: TestClient,
    store: FakeStore,
) -> None:
    app.state.settings = app.state.settings.model_copy(update={"action_suggestions": "off"})
    queue_training(app)
    train(analyser, training(accepted=True, precision=0.95, recall=0.95, games=4))
    job = through_trajectory(analyser, store)
    complete(
        analyser, job, body([a for r in inputs(analyser, job)["rallies"][:2] for a in plan(r)])
    )
    assert suggested(club["coach"], ready) == []
    assert gate_of(club["coach"], ready)["reason"] == "off"
