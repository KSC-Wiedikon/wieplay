"""WiePlay assigns everything, the coach checks only the unsure part (PLAN §10.0): WiePlay's
rallies, breaks, sets and actions are tags that count from the start ("auto" when sure, else
"check"); a person's answer wins and stays; "N still to check" and "Sure only" in the stats;
the chain runs by itself, also past a failed step, and says how far it is."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from tests.fakes import FakeStore
from tests.test_actions_api import body, claim, event_in, plan, post_events, suggested
from tests.test_actions_api import inputs as job_inputs
from tests.test_analysis_api import complete, make_ready
from tests.test_court_api import result as trajectory_result
from tests.test_court_api import stored
from tests.test_scoring_api import OFFICIAL, analysed, match, rallies_of, tagging
from wieplay.core import chain
from wieplay.core import tagging as core_tagging
from wieplay.core.config import Settings
from wieplay.core.models import Job, Segment, Video


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(
        update={"analysis_train_delay_seconds": 0, "action_suggestions": "on"}
    )


@pytest.fixture
def ready(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> Any:
    return make_ready(app, club, store, k=0)


@pytest.fixture
def analyser(app: FastAPI, club: dict[str, TestClient]) -> TestClient:
    from tests.media_helpers import worker_client
    from tests.test_analysis_api import ANALYSE_CAPS

    return worker_client(app, club["admin"], ANALYSE_CAPS)


def analysis(client: TestClient, video: Any) -> dict[str, Any]:
    r = client.get(f"/api/videos/{video['id']}/analysis")
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def stats(client: TestClient, video: Any, **params: Any) -> dict[str, Any]:
    r = client.get(f"/api/videos/{video['id']}/stats", params=params)
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


def seg_in(seg: dict[str, Any], **changes: Any) -> dict[str, Any]:
    keep = ("id", "kind", "start_ms", "end_ms", "won_by", "review")
    return {**{k: seg[k] for k in keep}, "base_version": seg["version"], **changes}


def changes(client: TestClient, video: Any, *segments: dict[str, Any]) -> dict[str, Any]:
    r = client.post(f"/api/videos/{video['id']}/changes", json={"segments": list(segments)})
    assert r.status_code == 200, r.text
    out: dict[str, Any] = r.json()
    return out


# --- WiePlay's tags count, the unsure ones are to check ---------------------------------------


def test_wieplays_rallies_sets_and_breaks_count_and_say_what_to_check(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    coach = club["coach"]
    played, _ = match()
    analysed(analyser, played)
    data = tagging(coach, ready)
    rallies = rallies_of(data)
    # The video and the official score alone: never sure (PLAN §10.3), so every rally is to
    # check; the sets after long breaks are sure; a short warm-up and the end are not.
    assert {r["review"] for r in rallies} == {"check"}
    assert {p["review"] for p in data["periods"]} == {"auto"}
    gaps = {s["kind"]: s["review"] for s in data["segments"] if s["kind"] != "rally"}
    assert gaps == {"warmup": "check", "set_break": "auto", "other_dead": "check"}
    # WiePlay's other answer for each rally: the other winner (or no point).
    assert all(r["model_alternative"] in ("us", "them", "none") for r in rallies)
    assert all(r["model_alternative"] != r["model_won_by"] for r in rallies)

    state = analysis(coach, ready)
    assert state["proposals"]["check"] == len(played) and state["proposals"]["auto"] == 0
    assert state["to_check"] == {
        "rallies": len(played), "breaks": 2, "sets": 0, "actions": 0, "players": 0,
        "total": len(played) + 2,
    }  # fmt: skip

    # They count: the sets, the rallies and WiePlay's winners; the stats say what is to check.
    found = stats(coach, ready)
    assert found["to_check"] == len(played) + 2 and found["sure_only"] is False
    assert [(s["score_us"], s["score_them"]) for s in found["sets"]] == OFFICIAL
    assert found["rallies"] == len(played)
    assert found["match"]["summary"]["won"] == sum(us for us, _ in OFFICIAL)
    # Sure only: without the rallies to check (the sets stay: they are sure).
    sure = stats(coach, ready, sure="true")
    assert sure["sure_only"] is True and sure["rallies"] == 0 and len(sure["sets"]) == 4
    assert sure["to_check"] == found["to_check"]
    season = coach.get(f"/api/teams/{ready['team_id']}/stats").json()
    assert season["to_check"] == len(played) + 2
    # The video list says the same.
    brief = coach.get(f"/api/videos/{ready['id']}").json()["analysis"]
    assert brief["to_check"] == brief["open"] == len(played) + 2


def test_a_persons_answer_wins_and_wieplay_follows_its_own_sureness(
    db_engine: Engine, ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    coach = club["coach"]
    played, _ = match()
    analysed(analyser, played)
    first, second = rallies_of(tagging(coach, ready))[:2]
    # Enter in "To check": the bounds and WiePlay's winner are the coach's now.
    out = changes(coach, ready, seg_in(first, review="confirmed", won_by=first["model_won_by"]))
    done = next(s for s in out["segments"] if s["id"] == first["id"])
    assert done["review"] == "confirmed" and done["won_by"] == first["model_won_by"]
    left = analysis(coach, ready)["to_check"]["rallies"]
    assert left == len(played) - 1
    # An undo sends WiePlay's state back: WiePlay's own again, to check.
    out = changes(coach, ready, seg_in(done, review="unreviewed", won_by=None))
    assert next(s for s in out["segments"] if s["id"] == first["id"])["review"] == "check"
    # A client that saw "auto" where it is "check" now answers nothing by passing it through.
    out = changes(coach, ready, seg_in(second, review="auto"))
    assert next(s for s in out["segments"] if s["id"] == second["id"])["review"] == "check"

    # WiePlay's switch between its own states is no one's edit: no revision.
    with Session(db_engine) as db:
        rid = uuid.UUID(second["id"])
        before = db.scalar(
            text("SELECT count(*) FROM segment_revision WHERE segment_id = :id"), {"id": rid}
        )
        row = db.get(Segment, rid)
        assert row is not None
        core_tagging.keep_model_state(row, True)
        db.commit()
        assert row.review == "auto"
        after = db.scalar(
            text("SELECT count(*) FROM segment_revision WHERE segment_id = :id"), {"id": rid}
        )
        assert after == before
        # A person's answer is never followed.
        row.review = "confirmed"
        db.commit()
        core_tagging.keep_model_state(row, False)
        assert row.review == "confirmed"
        db.rollback()

    # A rerun of the analysis keeps the rally the coach answered and replaces nothing else
    # that a person touched.
    rejected = rallies_of(tagging(coach, ready))[2]
    changes(coach, ready, {**seg_in(rejected, review="rejected"), "deleted": True})
    assert not any(r["id"] == rejected["id"] for r in rallies_of(tagging(coach, ready)))


def through_trajectory(analyser: TestClient, store: FakeStore) -> dict[str, Any]:
    played, _ = match()
    analysed(analyser, played)
    job = claim(analyser, "trajectory")
    assert job is not None
    stored(store, job)
    complete(analyser, job, trajectory_result(job))
    found = claim(analyser, "actions")
    assert found is not None
    return found


def test_wieplays_actions_count_sure_ones_auto_unsure_ones_to_check(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    coach = club["coach"]
    job = through_trajectory(analyser, store)
    rallies = job_inputs(analyser, job)["rallies"]
    actions = [a for r in rallies[:3] for a in plan(r)]
    for a in actions:
        a["sure"] = a["code"] == "S"
    complete(analyser, job, body(actions))
    events = suggested(coach, ready)
    assert {(e["code"], e["review"]) for e in events} == {
        ("S", "auto"), ("R", "check"), ("E", "check"), ("A", "check")
    }  # fmt: skip
    state = analysis(coach, ready)
    assert state["actions"]["suggestions"] == {
        "auto": 3, "check": 9, "confirmed": 0, "corrected": 0, "rejected": 0
    }  # fmt: skip
    assert state["to_check"]["actions"] == 9
    # They count in the stats; "sure only" keeps the sure serves.
    found = stats(coach, ready)
    assert found["tags"] == 12
    sure = stats(coach, ready, sure="true")
    assert sure["tags"] == 3
    # WiePlay's serve is still no fact for the scoring.
    first = next(s for s in rallies_of(tagging(coach, ready)) if s["id"] == rallies[0]["id"])
    assert first["serving"] is None
    # "Actions done" waits for the ones to check.
    r = coach.post(f"/api/videos/{ready['id']}/actions-reviewed", json={"reviewed": True})
    assert r.status_code == 409 and r.json()["detail"]["to_check"] == 9
    reception = next(e for e in events if e["code"] == "R")
    out = post_events(coach, ready, event_in(reception, review="confirmed"))
    assert out["events"][0]["review"] == "confirmed"
    # The undo of an answer gives WiePlay's state back.
    undone = post_events(coach, ready, event_in(out["events"][0], review="check"))
    assert undone["events"][0]["review"] == "check"
    # The box CSV ends in "To check"; a sure-only file says so in its name.
    csv = coach.get(f"/api/videos/{ready['id']}/stats.csv")
    head = csv.text.splitlines()[0].split(",")
    assert head[-1] == "To check"
    sure_csv = coach.get(f"/api/videos/{ready['id']}/stats.csv", params={"sure": "true"})
    assert "-sure-only.csv" in sure_csv.headers["content-disposition"]


# --- the chain ----------------------------------------------------------------------------------


def test_the_chain_says_how_far_it_is_and_goes_on_past_a_failed_step(
    app: FastAPI,
    db_engine: Engine,
    ready: Any,
    club: dict[str, TestClient],
    analyser: TestClient,
    settings: Settings,
) -> None:
    coach = club["coach"]
    played, _ = match()
    analysed(analyser, played)
    steps = analysis(coach, ready)["steps"]
    assert [s["key"] for s in steps["items"]] == list(chain.STEPS)
    assert (steps["done"], steps["total"], steps["running"]) == (2, 7, True)
    assert steps["items"][2] == {"key": "ball", "status": "queued"}
    # The trajectory fails for good: the court is still looked for.
    job = claim(analyser, "trajectory")
    assert job is not None
    r = analyser.post(
        f"/api/worker/jobs/{job['id']}/fail", json={"error": "boom", "retryable": False}
    )
    assert r.status_code == 204, r.text
    with Session(db_engine) as db:
        kinds = {
            (j.kind, j.status)
            for j in db.scalars(select(Job).where(Job.video_id == uuid.UUID(ready["id"])))
        }
    assert ("trajectory", "failed") in kinds and ("court", "queued") in kinds
    steps = analysis(coach, ready)["steps"]
    assert steps["items"][2]["status"] == "failed" and steps["done"] == 3
    # The court fails too: the actions and the players run on the nominal camera.
    court = analyser.post(
        "/api/worker/claim", json={"caps": ["analyse", "nas"], "wait": False, "kinds": ["court"]}
    ).json()["jobs"]
    assert [j["kind"] for j in court] == ["court"]
    r = analyser.post(
        f"/api/worker/jobs/{court[0]['id']}/fail", json={"error": "boom", "retryable": False}
    )
    assert r.status_code == 204, r.text
    with Session(db_engine) as db:
        queued = {
            j.kind
            for j in db.scalars(
                select(Job).where(Job.video_id == uuid.UUID(ready["id"]), Job.status == "queued")
            )
        }
    assert queued == {"actions", "players"}
    steps = analysis(coach, ready)["steps"]
    assert [s["status"] for s in steps["items"]] == [
        "done", "done", "failed", "failed", "queued", "queued", "none"
    ]  # fmt: skip
    assert (steps["done"], steps["running"]) == (4, True)


def test_resume_queues_a_step_nobody_queued(
    db_engine: Engine, ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    played, _ = match()
    analysed(analyser, played)
    vid = uuid.UUID(ready["id"])
    with Session(db_engine) as db:
        # A lost hand-off: the trajectory job of an older video was never queued.
        for job in db.scalars(select(Job).where(Job.video_id == vid, Job.kind == "trajectory")):
            db.delete(job)
        db.commit()
        video = db.get(Video, vid)
        assert video is not None
        found = chain.steps(db, video)
        assert found is not None and found.of("ball") == "none" and not found.running
        assert chain.resume(db, video) == "trajectory"
        db.commit()
        # Something of the chain is waiting: nothing more is queued.
        assert chain.resume(db, video) is None
        assert chain.resume_all(db) == []
