"""What WiePlay asks about its rallies (PLAN §10.0, docs/research/rally-asks-2026-11.md): a game
without a trusted board log asks about ASK_RALLIES at most, the others stay its guesses."""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from tests.fakes import FakeStore
from tests.media_helpers import worker_client
from tests.test_analysis_api import ANALYSE_CAPS, change, make_ready
from tests.test_scoring_api import _seg_in, analysed, match, rallies_of, tagging
from wieplay.core import analysis, scoring
from wieplay.core import tagging as core_tagging
from wieplay.core.config import Settings
from wieplay.core.models import Segment, Video


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"analysis_train_delay_seconds": 0})


@pytest.fixture
def ready(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> Any:
    return make_ready(app, club, store, k=0)


@pytest.fixture
def scored(ready: Any, app: FastAPI, club: dict[str, TestClient]) -> Any:
    analysed(worker_client(app, club["admin"], ANALYSE_CAPS), match()[0])
    return ready


def asked_of(client: TestClient, video: Any) -> list[dict[str, Any]]:
    return [r for r in rallies_of(tagging(client, video)) if r["review"] == "check"]


def test_a_game_without_a_log_asks_about_twenty_rallies(
    scored: Any, club: dict[str, TestClient]
) -> None:
    rallies = rallies_of(tagging(club["coach"], scored))
    asked = [r for r in rallies if r["review"] == "check"]
    assert len(asked) == scoring.ASK_RALLIES < len(rallies)
    # The others stay WiePlay's guesses: they count, not sure, and nobody is asked.
    assert all(r["score_sure"] is False for r in rallies)
    assert {r["review"] for r in rallies if r not in asked} == {"auto"}
    # Only what is asked carries the flag that says "check this".
    assert all(r["score_conflict"] for r in asked)
    assert not any(r["score_conflict"] for r in rallies if r not in asked)
    found = club["coach"].get(f"/api/videos/{scored['id']}/analysis").json()
    assert found["to_check"]["rallies"] == scoring.ASK_RALLIES
    assert found["rally_guesses"] == len(rallies) - scoring.ASK_RALLIES
    stats = club["coach"].get(f"/api/videos/{scored['id']}/stats").json()
    assert stats["rally_guesses"] == found["rally_guesses"]


def test_the_questions_are_spread_over_the_sets(scored: Any, club: dict[str, TestClient]) -> None:
    data = tagging(club["coach"], scored)
    spans = sorted((p["start_ms"], p["end_ms"], p["no"]) for p in data["periods"])
    sets = Counter(
        next(no for a, b, no in spans if a <= r["start_ms"] < b)
        for r in rallies_of(data)
        if r["review"] == "check"
    )
    assert sets == {1: 5, 2: 5, 3: 5, 4: 5}


def test_each_answer_uses_up_a_question(scored: Any, club: dict[str, TestClient]) -> None:
    asked = asked_of(club["coach"], scored)
    first = asked[0]
    answer = {**_seg_in(first), "review": "confirmed", "won_by": first["model_won_by"]}
    change(club["coach"], scored, answer)
    assert len(asked_of(club["coach"], scored)) == scoring.ASK_RALLIES - 1
    # A person's answer to a rally WiePlay did not ask about counts the same.
    guess = next(
        r
        for r in rallies_of(tagging(club["coach"], scored))
        if r["review"] == "auto" and r["score_sure"] is False
    )
    change(club["coach"], scored, {**_seg_in(guess), "review": "confirmed", "won_by": "us"})
    assert len(asked_of(club["coach"], scored)) == scoring.ASK_RALLIES - 2


def test_guesses_keep_rallies_done_and_the_training_away(
    scored: Any, club: dict[str, TestClient], db_engine: Engine
) -> None:
    r = club["coach"].post(f"/api/videos/{scored['id']}/rallies-reviewed", json={})
    assert r.status_code == 409
    n = len(rallies_of(tagging(club["coach"], scored)))
    assert r.json()["detail"]["to_check"] == n
    with Session(db_engine) as db:
        video = db.get(Video, uuid.UUID(scored["id"]))
        assert video is not None
        # A game marked done before WiePlay guessed part of it teaches nothing now.
        video.rallies_reviewed_at = datetime.now(UTC)
        db.flush()
        job: Any = NS(kind="train_rallies", params={}, video_id=None)
        out = analysis.job_inputs(db, job)
        assert [e["reason"] for e in out["skipped"]] == ["to check"]
        assert out["train"] == []
        db.rollback()


def test_sure_only_leaves_the_guesses_out(
    scored: Any, club: dict[str, TestClient], db_engine: Engine
) -> None:
    live = (Segment.video_id == uuid.UUID(scored["id"])) & (Segment.kind == "rally")
    count = select(func.count()).select_from(Segment).where(live, Segment.deleted_at.is_(None))
    with Session(db_engine) as db:
        assert db.scalar(count.where(core_tagging.stands())) == len(match()[0])
        assert db.scalar(count.where(core_tagging.stands(sure_only=True))) == 0


def test_choosing_again_without_scoring(scored: Any, db_engine: Engine) -> None:
    with Session(db_engine) as db:
        video = db.get(Video, uuid.UUID(scored["id"]))
        assert video is not None
        rows = db.scalars(
            select(Segment).where(Segment.video_id == video.id, Segment.kind == "rally")
        ).all()
        for row in rows:
            row.review = "check"  # as before the limit
        db.flush()
        assert scoring.rally_checks_again(db, video) == scoring.ASK_RALLIES
        db.rollback()


# --- the choice, pure -------------------------------------------------------------------------


def game(reviewed: datetime | None = None) -> Any:
    return NS(id=uuid.uuid4(), rallies_reviewed_at=reviewed)


def rally_row(start_ms: int, confidence: float | None = 0.6, **extra: Any) -> Any:
    fields: dict[str, Any] = {
        "id": uuid.uuid4(),
        "start_ms": start_ms,
        "source": "model",
        "review": "check",
        "won_by": None,
        "score_sure": False,
        "score_confidence": confidence,
        "score_note": "unsure",
        "score_conflict": True,
        "updated_by": None,
        "deleted_at": None,
    }
    return NS(**{**fields, **extra})


SETS = [NS(no=1, start_ms=0, end_ms=1000), NS(no=2, start_ms=1000, end_ms=2000)]


def test_the_most_doubtful_of_the_set_with_the_fewest_answers() -> None:
    low = rally_row(10, 0.51)
    high = rally_row(20, 0.9)
    other = rally_row(1010, 0.8)
    rows = [low, high, other, *(rally_row(30 + k, 0.7) for k in range(30))]
    asked = scoring.ask_rallies(rows, SETS, [])
    assert len(asked) == scoring.ASK_RALLIES
    assert low.id in asked and other.id in asked  # set 2's only one before more of set 1
    assert high.id not in asked


def test_answers_and_rallies_taken_away_leave_less_room() -> None:
    answered = [rally_row(k, review="confirmed", won_by="us") for k in range(5)]
    own = rally_row(6, source="manual", review="unreviewed")
    rows = [*answered, own, *(rally_row(100 + k) for k in range(30))]
    assert len(scoring.ask_rallies(rows, SETS, [7, 8])) == scoring.ASK_RALLIES - 8


def test_a_rally_a_person_left_open_stays_asked() -> None:
    touched = rally_row(500, 0.99, updated_by=7)
    rows = [touched, *(rally_row(k, 0.5) for k in range(40))]
    assert touched.id in scoring.ask_rallies(rows, SETS, [])


def test_sure_rallies_are_not_asked_and_rallies_outside_a_set_come_last() -> None:
    sure = rally_row(10, 0.99, score_sure=True, review="auto")
    outside = rally_row(5000, None)
    inside = rally_row(20)
    assert scoring.ask_rallies([sure, outside, inside], SETS, []) == {inside.id, outside.id}
    many = [rally_row(k) for k in range(25)]
    assert outside.id not in scoring.ask_rallies([outside, *many], SETS, [])


def test_no_more_than_the_limit_whatever_the_answers() -> None:
    answered = [rally_row(k, review="confirmed", won_by="us") for k in range(25)]
    assert scoring.ask_rallies([*answered, rally_row(100)], SETS, []) == set()


NO_GONE: Any = NS(scalars=lambda _q: NS(all=lambda: []))
NO_DB: Any = None


def test_a_trusted_log_asks_about_every_unsure_rally() -> None:
    rows = [rally_row(k, review="auto") for k in range(40)]
    log = NS(any_trusted=True)
    assert scoring.choose_rally_checks(NO_DB, game(), rows, SETS, log) == 40
    assert {r.review for r in rows} == {"check"}


@pytest.mark.parametrize("log", [None, NS(any_trusted=False)])
def test_without_a_trusted_log_a_sheet_makes_no_difference(log: Any) -> None:
    # A typed sheet alone leaves most rallies unsure: adding it must not bring every question back.
    rows = [rally_row(k) for k in range(40)]
    assert scoring.choose_rally_checks(NO_GONE, game(), rows, SETS, log) == scoring.ASK_RALLIES
    assert Counter(r.review for r in rows) == {"check": scoring.ASK_RALLIES, "auto": 20}


def test_a_game_a_person_marked_done_keeps_every_unsure_rally_asked() -> None:
    rows = [rally_row(k, review="auto") for k in range(40)]
    assert scoring.choose_rally_checks(NO_DB, game(datetime.now(UTC)), rows, SETS, None) == 40


def test_a_persons_answer_is_never_replaced() -> None:
    answered = rally_row(5, review="confirmed", won_by="us")
    scoring.choose_rally_checks(NO_GONE, game(), [answered], SETS, None)
    assert answered.review == "confirmed"
