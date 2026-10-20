"""Automatic analysis through the API (PLAN §10.1): queueing, results, the proposal rules,
review actions, "rallies done" and the training loop, permissions."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from tests.fakes import FakeStore
from tests.media_helpers import (
    H3,
    MEDIA_CAPS,
    claim,
    game_id,
    run_pipeline,
    upload_all,
    worker_client,
)
from wieplay.core.config import Settings

ANALYSE_CAPS = ["analyse", "nas"]
KINDS = ["analyse", "train_rallies"]


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """Trainings run as soon as they are queued (no 10-minute wait in tests)."""
    return settings.model_copy(update={"analysis_train_delay_seconds": 0})


def make_ready(app: FastAPI, club: dict[str, TestClient], store: FakeStore, k: int = 1) -> Any:
    coach = club["coach"]
    created = coach.post(
        f"/api/teams/{H3}/videos",
        json={
            "kind": "match",
            "game_id": game_id(H3, k),
            "files": [{"name": "C0001.MP4", "size": 1000, "type": "video/mp4"}],
        },
    )
    assert created.status_code == 201, created.text
    video = upload_all(coach, store, created.json())
    run_pipeline(worker_client(app, club["admin"], MEDIA_CAPS), MEDIA_CAPS, store)
    assert coach.get(f"/api/videos/{video['id']}").json()["status"] == "ready"
    return video


@pytest.fixture
def ready(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> Any:
    return make_ready(app, club, store)


@pytest.fixture
def analyser(app: FastAPI, club: dict[str, TestClient]) -> TestClient:
    return worker_client(app, club["admin"], ANALYSE_CAPS)


def claim_kind(w: TestClient, kind: str) -> dict[str, Any]:
    r = w.post("/api/worker/claim", json={"caps": ANALYSE_CAPS, "wait": False, "kinds": KINDS})
    assert r.status_code == 200, r.text
    [job] = r.json()["jobs"]
    assert job["kind"] == kind
    return job


def payload(
    job: dict[str, Any],
    proposals: list[tuple[int, int]],
    *,
    finder: str = "v0",
    model_run_id: str | None = None,
    name: str = "signals-test.npz",
) -> dict[str, Any]:
    return {
        "artifact": {
            "nas_path": f"{job['params']['out_dir']}/{name}",
            "sha256": "b" * 64,
            "bytes": 1234,
            "format": "npz",
            "schema_version": 1,
            "stats": {"whistles": 300, "ball_in_track": 0.21},
        },
        "model": {
            "name": "wieplay-signals/VballNetFastV1_155",
            "version": "abc123",
            "weights_sha256": "c" * 64,
            "params": {"analysis_fps": 25.0},
        },
        "timing": {"wall_s": 12.5},
        "rallies": {
            "finder": finder,
            "model_run_id": model_run_id,
            "proposals": [{"start_ms": s, "end_ms": e, "confidence": 0.7} for s, e in proposals],
        },
    }


def complete(w: TestClient, job: dict[str, Any], body: dict[str, Any], status: int = 204) -> None:
    r = w.post(f"/api/worker/jobs/{job['id']}/complete", json=body)
    assert r.status_code == status, r.text


def segments(client: TestClient, video: dict[str, Any]) -> list[dict[str, Any]]:
    """The video's rallies (the scoring's gap labels are tested in test_scoring_api.py)."""
    found = client.get(f"/api/videos/{video['id']}/tagging").json()["segments"]
    return [s for s in found if s["kind"] == "rally"]


def rally(start: int, end: int, **extra: Any) -> dict[str, Any]:
    return {"id": str(uuid.uuid4()), "kind": "rally", "start_ms": start, "end_ms": end, **extra}


def change(client: TestClient, video: dict[str, Any], *segs: dict[str, Any]) -> Any:
    r = client.post(f"/api/videos/{video['id']}/changes", json={"segments": list(segs)})
    assert r.status_code == 200, r.text
    return r.json()


def edit(seg: dict[str, Any], **changes: Any) -> dict[str, Any]:
    keep = ("id", "kind", "start_ms", "end_ms", "won_by", "review")
    return {**{k: seg[k] for k in keep}, "base_version": seg["version"], **changes}


# --- queueing ------------------------------------------------------------------------------


def test_a_ready_volleyball_video_is_analysed(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    state = club["coach"].get(f"/api/videos/{ready['id']}/analysis").json()
    assert state["status"] == "queued" and state["model_run"] is None
    assert state["proposals"] == {
        "auto": 0, "check": 0, "confirmed": 0, "corrected": 0, "rejected": 0, "merged": 0,
    }  # fmt: skip
    # The media lane (no "analyse" cap) never gets it; the background lane does.
    media = worker_client(club["coach"].app, club["admin"], MEDIA_CAPS)  # type: ignore[arg-type]
    assert claim(media, MEDIA_CAPS) == []
    job = claim_kind(analyser, "analyse")
    params = job["params"]
    assert params["out_dir"].startswith("videos/") and params["out_dir"].endswith(ready["id"][:8])
    assert params["inputs"] == [f"/nas/wieplay/raw/{params['out_dir'][7:]}/01.mp4"]
    assert (params["fps"], params["has_audio"], params["duration_ms"]) == (50.0, True, 3_600_000)
    inputs = analyser.post(f"/api/worker/jobs/{job['id']}/inputs", json={})
    assert inputs.status_code == 200
    assert inputs.json() == {"rally_model": None, "serve_model": None}
    assert club["coach"].get(f"/api/videos/{ready['id']}/analysis").json()["status"] == "running"


def test_automatic_analysis_can_be_switched_off(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, analyser: TestClient
) -> None:
    app.state.settings = app.state.settings.model_copy(update={"analysis_auto": False})
    video = make_ready(app, club, store)
    r = analyser.post("/api/worker/claim", json={"caps": ANALYSE_CAPS, "wait": False})
    assert r.json()["jobs"] == []
    state = club["coach"].get(f"/api/videos/{video['id']}/analysis").json()
    assert state["status"] == "none" and state["can_analyse"] is True
    # A coach can still ask for it; asking twice queues one job.
    first = club["coach"].post(f"/api/videos/{video['id']}/analyse")
    assert first.status_code == 202 and first.json()["status"] == "queued"
    assert (
        club["coach"].post(f"/api/videos/{video['id']}/analyse").json()["job"]["id"]
        == (first.json()["job"]["id"])
    )


def test_permissions(ready: Any, club: dict[str, TestClient], as_: Any) -> None:
    url = f"/api/videos/{ready['id']}"
    player = as_("player-h3")
    seen = player.get(f"{url}/analysis")
    assert seen.status_code == 200 and seen.json()["can_analyse"] is False
    assert player.post(f"{url}/analyse").status_code == 403
    # Players tag and review their own games (owner, 17.10.2026).
    assert player.post(f"{url}/rallies-reviewed", json={"reviewed": True}).status_code != 403
    assert as_("basketball-player").get(f"{url}/analysis").status_code == 404
    assert as_("vb-sport-admin").post(f"{url}/analyse").status_code == 202
    anonymous = TestClient(club["coach"].app)  # type: ignore[arg-type]
    assert anonymous.get(f"{url}/analysis").status_code == 401


# --- results and the proposal rules --------------------------------------------------------


def test_proposals_are_written_with_their_model_run(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, db_engine: Engine
) -> None:
    job = claim_kind(analyser, "analyse")
    complete(analyser, job, payload(job, [(10_000, 20_000), (40_000, 52_000), (80_000, 90_000)]))
    segs = segments(club["coach"], ready)
    assert len(segs) == 3
    assert {(s["source"], s["review"], s["confidence"]) for s in segs} == {("model", "check", 0.7)}
    state = club["coach"].get(f"/api/videos/{ready['id']}/analysis").json()
    assert state["status"] == "done" and state["metrics_vs_human"] is None
    run = state["model_run"]
    assert {s["model_run_id"] for s in segs} == {run["id"]}
    assert run["kind"] == "analyse" and run["model_version"] == "abc123"
    assert run["metrics"]["mode"] == "proposals" and run["metrics"]["proposed"] == 3
    assert run["metrics"]["signals"]["whistles"] == 300
    assert state["proposals"]["check"] == 3
    with db_engine.connect() as conn:
        artifact = conn.execute(
            text("SELECT kind, nas_path, video_id FROM analysis_artifact")
        ).one()
        assert artifact[0] == "features" and artifact[1].endswith("/signals-test.npz")
        assert str(artifact[2]) == ready["id"]
        # The model wrote them: the revision trigger records no person.
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM segment_revision r JOIN segment s ON s.id = r.segment_id"
                    " WHERE r.changed_by IS NULL AND s.kind = 'rally'"
                )
            ).scalar()
            == 3
        )


def test_video_lists_say_where_the_analysis_stands(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    """ "Ready" says the video plays; the analysis brief says whether WiePlay looked at it."""
    coach = club["coach"]

    def brief() -> Any:
        listed = coach.get(f"/api/teams/{ready['team_id']}/videos").json()
        one = coach.get(f"/api/videos/{ready['id']}").json()["analysis"]
        assert next(v for v in listed if v["id"] == ready["id"])["analysis"] == one
        assert one["open"] == one["to_check"]
        return {"status": one["status"], "open": one["open"]}

    assert brief() == {"status": "queued", "open": 0}
    job = claim_kind(analyser, "analyse")
    assert brief() == {"status": "running", "open": 0}
    complete(analyser, job, payload(job, [(10_000, 20_000), (40_000, 52_000)]))
    # Two rallies to check, and the break after the last one (the end of the match).
    assert brief() == {"status": "done", "open": 3}
    first = segments(coach, ready)[0]
    change(coach, ready, edit(first, review="confirmed"))
    assert brief() == {"status": "done", "open": 2}


def test_a_bad_result_fails_the_analysis_not_the_video(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    job = claim_kind(analyser, "analyse")
    body = payload(job, [(10_000, 20_000)])
    body["artifact"]["nas_path"] = "videos/elsewhere/signals.npz"
    complete(analyser, job, body, status=422)
    assert club["coach"].get(f"/api/videos/{ready['id']}").json()["status"] == "ready"
    assert club["coach"].get(f"/api/videos/{ready['id']}/analysis").json()["status"] == "failed"
    # A worker that gives up does not fail the video either.
    club["coach"].post(f"/api/videos/{ready['id']}/analyse")
    again = claim_kind(analyser, "analyse")
    r = analyser.post(
        f"/api/worker/jobs/{again['id']}/fail", json={"error": "x", "retryable": False}
    )
    assert r.status_code == 204
    assert club["coach"].get(f"/api/videos/{ready['id']}").json()["status"] == "ready"
    assert segments(club["coach"], ready) == []


def test_rerun_replaces_untouched_proposals_and_respects_rejections(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    job = claim_kind(analyser, "analyse")
    complete(analyser, job, payload(job, [(10_000, 20_000), (40_000, 52_000)]))
    first = {s["start_ms"]: s for s in segments(club["coach"], ready)}
    change(club["coach"], ready, edit(first[40_000], deleted=True))  # the coach rejects one
    assert club["coach"].get(f"/api/videos/{ready['id']}/analysis").json()["proposals"] == {
        "auto": 0, "check": 1, "confirmed": 0, "corrected": 0, "rejected": 1, "merged": 0,
    }  # fmt: skip

    club["coach"].post(f"/api/videos/{ready['id']}/analyse")
    job = claim_kind(analyser, "analyse")
    complete(
        analyser,
        job,
        payload(job, [(10_500, 20_500), (40_500, 52_000), (70_000, 80_000)], name="signals-2.npz"),
    )
    now = segments(club["coach"], ready)
    # The untouched one is replaced; the rejected rally is not proposed again.
    assert sorted(s["start_ms"] for s in now) == [10_500, 70_000]
    assert first[10_000]["id"] not in {s["id"] for s in now}
    state = club["coach"].get(f"/api/videos/{ready['id']}/analysis").json()
    assert state["model_run"]["metrics"]["replaced"] == 1
    assert state["model_run"]["metrics"]["skipped"] == 1


def test_human_rallies_are_never_overwritten_only_scored(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    mine = [rally(10_000, 20_000, won_by="us"), rally(40_000, 50_000, won_by="them")]
    change(club["coach"], ready, *mine)
    job = claim_kind(analyser, "analyse")
    complete(analyser, job, payload(job, [(10_400, 19_700), (40_000, 53_000), (70_000, 80_000)]))
    segs = segments(club["coach"], ready)
    assert {s["source"] for s in segs} == {"manual"} and len(segs) == 2
    state = club["coach"].get(f"/api/videos/{ready['id']}/analysis").json()
    assert state["model_run"]["metrics"]["mode"] == "metrics"
    vs = state["metrics_vs_human"]
    # Boundaries: 10.4/19.7 and 40.0 match within 1 s, 53.0 does not, 70/80 are extra.
    assert (vs["rallies_human"], vs["rallies_model"]) == (2, 3)
    assert vs["precision"] == 0.5 and vs["recall"] == 0.75
    assert vs["f1_1s"] == pytest.approx(0.6)
    assert vs["labels_complete"] is False and vs["held_out"] is False


# --- review actions ------------------------------------------------------------------------


def test_review_actions_on_proposals(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, db_engine: Engine
) -> None:
    job = claim_kind(analyser, "analyse")
    complete(
        analyser,
        job,
        payload(job, [(10_000, 20_000), (40_000, 52_000), (80_000, 90_000), (90_500, 99_000)]),
    )
    by_start = {s["start_ms"]: s for s in segments(club["coach"], ready)}
    a, b, c, d = (by_start[t] for t in (10_000, 40_000, 80_000, 90_500))
    out = change(
        club["coach"],
        ready,
        edit(a, review="confirmed"),  # accept
        edit(b, start_ms=41_000),  # nudge a boundary (a client that says nothing)
        edit(c, end_ms=99_000, review="corrected"),  # merge d into c: c grows ...
        edit(d, deleted=True, review="merged", merged_into=c["id"]),  # ... d goes
    )
    after = {s["id"]: s for s in out["segments"]}
    assert after[a["id"]]["review"] == "confirmed"
    assert after[b["id"]]["review"] == "corrected"
    assert (after[c["id"]]["review"], after[c["id"]]["end_ms"]) == ("corrected", 99_000)
    assert after[d["id"]]["review"] == "merged" and after[d["id"]]["merged_into"] == c["id"]
    assert after[d["id"]]["deleted_at"] is not None
    assert {s["source"] for s in after.values()} == {"model"}
    state = club["coach"].get(f"/api/videos/{ready['id']}/analysis").json()
    assert state["proposals"] == {
        "auto": 0, "check": 0, "confirmed": 1, "corrected": 2, "rejected": 0, "merged": 1,
    }  # fmt: skip

    # Undo, as the review page sends it: the fields as they were and "unreviewed".
    undo = change(
        club["coach"],
        ready,
        edit(after[a["id"]], review="unreviewed"),  # reopen an accepted one
        edit(after[b["id"]], start_ms=40_000, review="unreviewed"),  # undo the correction
        edit(after[c["id"]], end_ms=90_000, review="unreviewed"),
        edit(after[d["id"]], deleted=False, review="unreviewed", merged_into=None),
    )
    back = {s["id"]: s for s in undo["segments"]}
    assert [(back[x["id"]]["review"], back[x["id"]]["start_ms"], back[x["id"]]["end_ms"])
            for x in (a, b, c, d)] == [
        ("check", 10_000, 20_000), ("check", 40_000, 52_000),
        ("check", 80_000, 90_000), ("check", 90_500, 99_000),
    ]  # fmt: skip
    assert back[d["id"]]["merged_into"] is None and back[d["id"]]["deleted_at"] is None

    # Reject: a soft delete, kept as "not a rally"; undoing it reopens the proposal.
    gone = change(club["coach"], ready, edit(back[c["id"]], deleted=True))
    rejected = {s["id"]: s for s in gone["segments"]}[c["id"]]
    assert rejected["review"] == "rejected" and rejected["deleted_at"] is not None
    restored = change(club["coach"], ready, edit(rejected, deleted=False))
    assert {s["id"]: s for s in restored["segments"]}[c["id"]]["review"] == "check"
    # merged_into must name another segment of the video.
    bad = edit(back[a["id"]], deleted=True, review="merged", merged_into=str(uuid.uuid4()))
    r = club["coach"].post(f"/api/videos/{ready['id']}/changes", json={"segments": [bad]})
    assert r.status_code == 422

    with db_engine.connect() as conn:
        actions = dict(
            conn.execute(
                text(
                    "SELECT s.id::text, string_agg(r.action, ',' ORDER BY r.id) FROM segment s "
                    "JOIN segment_revision r ON r.segment_id = s.id GROUP BY s.id"
                )
            ).all()
        )
        before = conn.execute(
            text(
                "SELECT r.before->>'start_ms' FROM segment_revision r WHERE r.segment_id = :id "
                "AND r.after->>'start_ms' = '41000'"
            ),
            {"id": b["id"]},
        ).scalar()
    assert actions[a["id"]] == "create,confirm,update"
    assert actions[b["id"]] == "create,update,update"
    assert actions[d["id"]] == "create,delete,restore"
    assert actions[c["id"]] == "create,update,update,delete,restore"
    assert before == "40000"  # the correction keeps what the model said


def test_merged_and_rejected_proposals_in_training_and_reruns(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    """A merged proposal is no "not a rally": a rerun may propose there again only where
    nothing covers it; a rejected one keeps its span free of proposals."""
    job = claim_kind(analyser, "analyse")
    complete(analyser, job, payload(job, [(10_000, 15_000), (15_500, 22_000), (40_000, 50_000)]))
    by_start = {s["start_ms"]: s for s in segments(club["coach"], ready)}
    a, b, c = (by_start[t] for t in (10_000, 15_500, 40_000))
    change(
        club["coach"],
        ready,
        edit(b, deleted=True, review="merged", merged_into=a["id"]),
        edit(c, deleted=True),
    )
    # a is still open: a rerun replaces it; c's span stays empty, b's does not.
    club["coach"].post(f"/api/videos/{ready['id']}/analyse")
    job = claim_kind(analyser, "analyse")
    complete(analyser, job, payload(job, [(15_000, 22_000), (40_500, 50_000)], name="s2.npz"))
    assert [s["start_ms"] for s in segments(club["coach"], ready)] == [15_000]


def test_training_gets_the_rejected_proposals_as_hard_negatives(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    """A proposal the coach rejected reaches the training as "not a rally" (a hard
    negative); a merged proposal, a coach's own rally they deleted and the kept ones do
    not."""
    job = claim_kind(analyser, "analyse")
    complete(
        analyser,
        job,
        payload(job, [(10_000, 20_000), (25_000, 29_000), (40_000, 46_000), (46_500, 52_000)]),
    )
    by_start = {s["start_ms"]: s for s in segments(club["coach"], ready)}
    a, b, c, d = (by_start[t] for t in (10_000, 25_000, 40_000, 46_500))
    own = rally(60_000, 70_000)
    change(club["coach"], ready, own)
    change(
        club["coach"],
        ready,
        edit(a, review="confirmed"),
        edit(b, deleted=True),  # X: the ball thrown back over the net, not a rally
        edit(c, end_ms=52_000, review="corrected"),
        edit(d, deleted=True, review="merged", merged_into=c["id"]),
        {**own, "base_version": 1, "deleted": True},  # the coach's own mistake
    )
    done = club["coach"].post(f"/api/videos/{ready['id']}/rallies-reviewed", json={})
    assert done.status_code == 200, done.text
    train = claim_kind(analyser, "train_rallies")
    inputs = analyser.post(f"/api/worker/jobs/{train['id']}/inputs", json={}).json()
    [entry] = inputs["train"]
    assert entry["rallies"] == [[10_000, 20_000], [40_000, 52_000]]
    assert entry["rejected"] == [[25_000, 29_000]]


def test_rallies_reviewed_needs_every_proposal_answered_and_trains_once(
    app: FastAPI, ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    url = f"/api/videos/{ready['id']}/rallies-reviewed"
    assert club["coach"].post(url, json={"reviewed": True}).status_code == 409  # no rallies yet
    job = claim_kind(analyser, "analyse")
    complete(analyser, job, payload(job, [(10_000, 20_000), (40_000, 52_000)]))
    first = {s["start_ms"]: s for s in segments(club["coach"], ready)}
    change(club["coach"], ready, edit(first[40_000], deleted=True))
    refused = club["coach"].post(url, json={"reviewed": True})
    assert refused.status_code == 409
    assert refused.json()["detail"]["to_check"] == 1
    # Nothing was confirmed behind the coach's back.
    assert [s["review"] for s in segments(club["coach"], ready)] == ["check"]
    change(club["coach"], ready, edit(first[10_000], review="confirmed"))
    done = club["coach"].post(url, json={"reviewed": True})
    assert done.status_code == 200, done.text
    assert done.json()["rallies_reviewed_at"]
    train_id = done.json()["training_job_id"]
    for where in (f"/api/videos/{ready['id']}", f"/api/videos/{ready['id']}/tagging"):
        assert (
            club["coach"].get(where).json()["rallies_reviewed_at"]
            == done.json()["rallies_reviewed_at"]
        )
    # A second game marked right after: still one training.
    other = make_ready(app, club, store, k=2)
    change(club["coach"], other, rally(5_000, 15_000))
    again = club["coach"].post(
        f"/api/videos/{other['id']}/rallies-reviewed", json={"reviewed": True}
    )
    assert again.json()["training_job_id"] == train_id
    jobs = club["admin"].get("/api/admin/jobs").json()
    assert sum(j["kind"] == "train_rallies" for j in jobs) == 1
    # Taking it back clears the flag (and the training will see one game fewer).
    back = club["coach"].post(url, json={"reviewed": False})
    assert back.json()["rallies_reviewed_at"] is None and back.json()["training_job_id"] == train_id


def test_training_learns_from_reviewed_games_and_proposes_for_the_rest(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, analyser: TestClient
) -> None:
    games = [make_ready(app, club, store, k=k) for k in (0, 1, 2)]
    for n, video in enumerate(games):
        job = claim_kind(analyser, "analyse")
        assert job["video_id"] == video["id"]
        complete(analyser, job, payload(job, [] if n < 2 else [(1_000, 9_000)], name=f"s{n}.npz"))
    for video in games[:2]:  # the coach does two games by hand
        change(club["coach"], video, rally(10_000, 20_000), rally(30_000, 42_000))
        r = club["coach"].post(f"/api/videos/{video['id']}/rallies-reviewed", json={})
        assert r.status_code == 200
    train = claim_kind(analyser, "train_rallies")
    inputs = analyser.post(f"/api/worker/jobs/{train['id']}/inputs", json={}).json()
    assert {v["video_id"] for v in inputs["train"]} == {g["id"] for g in games[:2]}
    assert inputs["train"][0]["rallies"] == [[10_000, 20_000], [30_000, 42_000]]
    assert inputs["train"][0]["features"]["nas_path"].endswith(".npz")
    assert [v["video_id"] for v in inputs["others"]] == [games[2]["id"]]
    held_out = {
        "v0": {"boundary": {"f1": 0.5, "precision": 0.5, "recall": 0.5}, "rallies_true": 2,
               "rallies_pred": 2, "live_iou": 0.6},
        "v1": {"boundary": {"f1": 0.9, "precision": 0.9, "recall": 0.9}, "rallies_true": 2,
               "rallies_pred": 2, "live_iou": 0.8},
    }  # fmt: skip
    body = {
        "model": {"name": "rallies-v1-hgb", "version": "abc123", "params": {"v1": {}}},
        "artifact": {
            "nas_path": "models/rallies/rally-v1-test.pkl", "sha256": "d" * 64, "bytes": 99,
            "format": "pickle", "schema_version": 1, "stats": {"games": 2},
        },
        "accepted": True,
        "timing": {"wall_s": 3.0},
        "evaluation": {
            "summary": {
                "v0": {"videos": 2, "boundary_f1": 0.5},
                "v1": {"videos": 2, "boundary_f1": 0.9},
            },
            "videos": {games[0]["id"]: held_out, games[1]["id"]: held_out},
        },
        "dataset": [{"video_id": g["id"], "rallies": 2} for g in games[:2]],
        "predictions": {
            games[2]["id"]: {"finder": "v1", "proposals": [{"start_ms": 2_000, "end_ms": 9_500,
                                                            "confidence": 0.9}]},
        },
    }  # fmt: skip
    complete(analyser, train, body)
    third = club["coach"].get(f"/api/videos/{games[2]['id']}/analysis").json()
    assert (
        third["model_run"]["kind"] == "train_rallies"
        and third["model_run"]["metrics"]["finder"] == "v1"
    )
    assert third["rally_model"]["finder"] == "v1" and third["rally_model"]["games"] == 2
    [proposal] = segments(club["coach"], games[2])
    assert (proposal["start_ms"], proposal["model_run_id"]) == (2_000, third["model_run"]["id"])
    first = club["coach"].get(f"/api/videos/{games[0]['id']}/analysis").json()
    assert first["metrics_vs_human"]["f1_1s"] == 0.9 and first["metrics_vs_human"]["held_out"]
    assert first["metrics_vs_human"]["labels_complete"] is True
    # The next analysis uses the learned model.
    club["coach"].post(f"/api/videos/{games[2]['id']}/analyse")
    job = claim_kind(analyser, "analyse")
    model = analyser.post(f"/api/worker/jobs/{job['id']}/inputs", json={}).json()["rally_model"]
    assert model["nas_path"] == "models/rallies/rally-v1-test.pkl"
    assert model["model_run_id"] == third["model_run"]["id"]
    bad = payload(job, [(1_000, 2_000)], finder="v1", model_run_id=str(uuid.uuid4()))
    complete(analyser, job, bad, status=422)  # v1 proposals must name a real training


def test_a_training_that_does_not_beat_v0_changes_nothing(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    job = claim_kind(analyser, "analyse")
    complete(analyser, job, payload(job, [(10_000, 20_000)]))
    [proposal] = segments(club["coach"], ready)
    change(club["coach"], ready, edit(proposal, review="confirmed"))
    club["coach"].post(f"/api/videos/{ready['id']}/rallies-reviewed", json={"reviewed": True})
    train = claim_kind(analyser, "train_rallies")
    body = {
        "model": {"name": "rallies-v1-hgb", "version": "abc123"},
        "accepted": False,
        "evaluation": {"summary": {"v0": {"videos": 1}, "v1": {"videos": 0}}, "videos": {}},
        "predictions": {ready["id"]: {"finder": "v1", "proposals": []}},
    }
    complete(analyser, train, body, status=422)  # v1 proposals from a model that lost
    body["predictions"] = {}
    club["coach"].post(f"/api/videos/{ready['id']}/rallies-reviewed", json={"reviewed": True})
    train = claim_kind(analyser, "train_rallies")
    complete(analyser, train, body)
    state = club["coach"].get(f"/api/videos/{ready['id']}/analysis").json()
    assert state["rally_model"]["finder"] == "v0" and state["rally_model"]["run_id"]


# --- worker plumbing -----------------------------------------------------------------------


def test_inputs_only_for_leased_analysis_jobs(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    job = claim_kind(analyser, "analyse")
    other = worker_client(club["coach"].app, club["admin"], ANALYSE_CAPS)  # type: ignore[arg-type]
    assert other.post(f"/api/worker/jobs/{job['id']}/inputs", json={}).status_code == 409
    via_cloudflare = analyser.post(
        f"/api/worker/jobs/{job['id']}/inputs", json={}, headers={"CF-Connecting-IP": "1.2.3.4"}
    )
    assert via_cloudflare.status_code == 404


def test_deleting_a_video_purges_its_analysis_folder(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    job = claim_kind(analyser, "analyse")
    assert club["coach"].delete(f"/api/videos/{ready['id']}").status_code == 204
    assert analyser.post(f"/api/worker/jobs/{job['id']}/heartbeat", json={}).json()["cancel"]
    purge = worker_client(club["coach"].app, club["admin"], ["nas"])  # type: ignore[arg-type]
    [job] = claim(purge, ["nas"])
    assert job["kind"] == "purge"
    assert job["params"]["analysis_subdir"] == f"videos/{job['params']['raw_subdir']}"


def test_training_waits_for_the_debounce(
    app: FastAPI, analyser: TestClient, db_engine: Engine
) -> None:
    from wieplay.core import analysis
    from wieplay.core.db import make_sessionmaker

    sessions = make_sessionmaker(db_engine)
    with sessions() as db:
        now = datetime.now(UTC)
        first = analysis.queue_training(db, timedelta(minutes=10), now=now)
        later = analysis.queue_training(db, timedelta(minutes=10), now=now + timedelta(minutes=5))
        assert first.id == later.id
        assert later.not_before == now + timedelta(minutes=15)
        db.commit()
    r = analyser.post("/api/worker/claim", json={"caps": ANALYSE_CAPS, "wait": False})
    assert r.json()["jobs"] == []


def test_wieplays_rallies_count_from_the_start(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    """Condensed playback and the stats use WiePlay's rallies at once (PLAN §10.0); a rally a
    coach rejected no longer counts."""
    job = claim_kind(analyser, "analyse")
    complete(analyser, job, payload(job, [(10_000, 20_000), (40_000, 52_000)]))
    url = f"/api/videos/{ready['id']}"
    assert len(club["coach"].get(f"{url}/condensed").json()) == 2
    by_start = {s["start_ms"]: s for s in segments(club["coach"], ready)}
    change(club["coach"], ready, edit(by_start[40_000], review="rejected", deleted=True))
    pieces = club["coach"].get(f"{url}/condensed").json()
    assert len(pieces) == 1 and pieces[0]["start_ms"] < 10_000 < pieces[0]["end_ms"]
    assert club["coach"].get(f"{url}/stats").status_code == 200


def test_a_reopened_game_does_not_train_until_it_is_done_again(
    ready: Any, club: dict[str, TestClient], analyser: TestClient
) -> None:
    job = claim_kind(analyser, "analyse")
    complete(analyser, job, payload(job, [(10_000, 20_000)]))
    [proposal] = segments(club["coach"], ready)
    mine = rally(30_000, 40_000)
    accepted = change(club["coach"], ready, edit(proposal, review="confirmed"), mine)["segments"]
    accepted = next(s for s in accepted if s["id"] == proposal["id"])
    club["coach"].post(f"/api/videos/{ready['id']}/rallies-reviewed", json={"reviewed": True})
    change(club["coach"], ready, edit(accepted, review="unreviewed"))  # the coach reopens it
    train = claim_kind(analyser, "train_rallies")
    inputs = analyser.post(f"/api/worker/jobs/{train['id']}/inputs", json={}).json()
    assert inputs["train"] == [] and inputs["others"] == []
    assert [(e["video_id"], e["reason"]) for e in inputs["skipped"]] == [(ready["id"], "to check")]
