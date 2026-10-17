"""The bar for suggested actions (PLAN §10.4a): written only once the action finder's held-out
score on games whose actions a coach reviewed passes it (auto), always (on), never (off).
Below it the finder still runs and measures itself where a coach tagged; the first training
that passes it brings suggestions to every game nobody has reviewed."""

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
    post_events,
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


# --- the bar itself -----------------------------------------------------------------------------


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
    ("mode", "the_run", "is_open", "reason"),
    [
        ("auto", None, False, "no_training"),
        ("auto", run(True, 0.9, 0.9, 1), False, "too_few_games"),
        ("auto", run(True, 0.69, 0.9, 3), False, "below_bar"),
        ("auto", run(True, 0.9, 0.5, 3), False, "below_bar"),
        ("auto", run(False, None, 0.9, 3), False, "below_bar"),
        ("auto", run(True, 0.7, 0.6, 2), True, "passed"),
        ("auto", run(False, 0.8, 0.7, 2), True, "passed"),  # the rules can pass it too
        ("on", None, True, "on"),
        ("off", run(True, 0.95, 0.95, 5), False, "off"),
    ],
)
def test_the_bar(
    settings: Settings, mode: str, the_run: ModelRun | None, is_open: bool, reason: str
) -> None:
    g = actions.gate_of(the_run, settings.model_copy(update={"action_suggestions": mode}))
    assert (g.open, g.reason) == (is_open, reason)
    assert (g.min_precision, g.min_recall) == (0.70, 0.6)
    stricter = settings.model_copy(
        update={"action_suggest_min_precision": 0.95, "action_suggestions": "auto"}
    )
    if reason == "passed":
        assert actions.gate_of(the_run, stricter).open is False


# --- through the API --------------------------------------------------------------------------


def test_below_the_bar_nothing_is_written_but_the_finder_measures_itself(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    coach = club["coach"]
    job = through_trajectory(analyser, store)
    rallies = inputs(analyser, job)["rallies"][:3]
    # The coach tagged the first rally by hand.
    post_events(
        coach,
        ready,
        *(
            {"id": str(uuid.uuid4()), "code": a["code"], "t_ms": a["t_ms"] + 50,
             "side": "us" if a["side"] == "near" else "them", "attributes": {},
             "time_precision": "live"}
            for a in plan(rallies[0])
        ),
    )  # fmt: skip
    complete(analyser, job, body([a for r in rallies for a in plan(r)]))
    assert suggested(coach, ready) == []
    state = coach.get(f"/api/videos/{ready['id']}/analysis").json()["actions"]
    assert state["proposed"] == 0 and state["gate"]["open"] is False
    assert state["gate"]["reason"] == "no_training" and state["gate"]["games"] == 0
    # Measured where the coach tagged: the four tags matched.
    assert state["vs_coach"]["coach"] == 4 and state["vs_coach"]["matched"] == 4
    stats = coach.get(f"/api/videos/{ready['id']}/stats").json()
    assert stats["suggested"] == 0 and stats["actions_gate"]["open"] is False


def test_a_training_below_the_bar_keeps_it_closed_one_above_opens_it(
    app: FastAPI, ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    coach = club["coach"]
    job = through_trajectory(analyser, store)
    complete(
        analyser, job, body([a for r in inputs(analyser, job)["rallies"][:2] for a in plan(r)])
    )
    assert suggested(coach, ready) == []

    queue_training(app)
    train(analyser, training(accepted=True, precision=0.5, recall=0.8, games=3))
    g = gate_of(coach, ready)
    assert (g["open"], g["reason"], g["games"], g["precision"]) == (False, "below_bar", 3, 0.5)
    assert claim(analyser) is None  # no new suggestions for anyone

    queue_training(app)
    train(analyser, training(accepted=True, precision=0.8, recall=0.7, games=3))
    g = gate_of(coach, ready)
    assert (g["open"], g["reason"], g["finder"]) == (True, "passed", "v1")
    # The bar passed for the first time: every game nobody reviewed gets suggestions.
    job = claim(analyser, "actions")
    assert job is not None and job["params"]["reason"] == "train_actions"
    found = inputs(analyser, job)
    assert found["model"]["nas_path"] == "models/actions/actions-v1-abc.pkl"
    complete(analyser, job, body([a for r in found["rallies"][:2] for a in plan(r)], finder="v1",
                                 model_run_id=found["model"]["model_run_id"]))  # fmt: skip
    assert len(suggested(coach, ready)) == 8

    # A later training that keeps v0 (the rules below the bar) closes it again: the
    # untouched suggestions go with the next run, nothing new is written.
    queue_training(app)
    train(analyser, training(accepted=False, precision=0.3, recall=0.7, games=3))
    assert gate_of(coach, ready)["open"] is False
    r = coach.post(f"/api/videos/{ready['id']}/actions")
    assert r.status_code == 202
    job = claim(analyser, "actions")
    assert job is not None
    complete(
        analyser, job, body([a for r in inputs(analyser, job)["rallies"][:2] for a in plan(r)])
    )
    assert suggested(coach, ready) == []


def test_off_writes_nothing_even_above_the_bar(
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
