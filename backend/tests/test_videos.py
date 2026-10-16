"""M1: filming rules, uploads, the job pipeline, playback, deletion (PLAN §5.3, §7, §8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from nomal.api.worker import reap
from tests.fakes import FakeStore
from tests.media_helpers import (
    D1,
    DU23,
    H3,
    MEDIA_CAPS,
    PART,
    PLAYLIST,
    THUMBS,
    TODAY,
    claim,
    create_match,
    game_id,
    ingest_result,
    run_pipeline,
    transcode_result,
    upload_all,
    worker_client,
)


def test_an_adult_team_films_without_asking_each_player(as_: Any, store: FakeStore) -> None:
    """Swiss Volley allows recording matches (Art. 86); the club asks no per-player consent."""
    admin = as_("admin")
    admin.post("/api/admin/sync")
    admin.put(f"/api/admin/teams/{H3}", json={"enabled": True})
    admin.post("/api/admin/sync")
    assert create_match(as_("coach-h3")).status_code == 201
    assert as_("player-h3").get("/api/me/consent").status_code == 404  # no such thing any more


def test_a_refused_upload_stores_nothing(
    club: dict[str, TestClient], store: FakeStore, db_engine: Engine
) -> None:
    lineage = club["coach"].get(f"/api/teams/{H3}").json()["lineage_id"]
    policy = {"matches_allowed": False, "reason": "test"}
    assert club["admin"].put(f"/api/admin/policies/{lineage}", json=policy).status_code == 200
    refused = create_match(club["coach"])
    assert refused.status_code == 409
    assert refused.json()["blockers"] == [
        {"code": "policy_matches", "count": None, "detail": "test"}
    ]
    assert store.uploads == {}  # nothing stored, not even a staging upload
    with db_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM video")).scalar_one() == 0
        assert (
            conn.execute(
                text("SELECT count(*) FROM audit_log WHERE action='upload_refused'")
            ).scalar_one()
            == 1
        )


def test_adults_only_and_team_policy(club: dict[str, TestClient], as_: Any) -> None:
    sport_admin = as_("vb-sport-admin")
    # DU23-1 has a fictional 15-year-old on the roster: blocked.
    du23 = sport_admin.post(
        f"/api/teams/{DU23}/videos",
        json={"game_id": game_id(DU23, 1), "files": [{"name": "a.mp4", "size": 10}]},
    )
    assert du23.status_code == 409
    codes = {b["code"]: b["count"] for b in du23.json()["blockers"]}
    assert codes["under_18"] == 1
    # D1: matches switched off by an admin.
    lineage = club["admin"].get(f"/api/teams/{D1}").json()["lineage_id"]
    r = club["admin"].put(
        f"/api/admin/policies/{lineage}", json={"matches_allowed": False, "reason": "test"}
    )
    assert r.status_code == 200
    assert "trainings_allowed" not in r.json()
    d1 = sport_admin.post(
        f"/api/teams/{D1}/videos",
        json={"kind": "match", "game_id": game_id(D1, 1), "files": [{"name": "a.mp4", "size": 10}]},
    )
    assert d1.status_code == 409
    assert {"code": "policy_matches", "count": None, "detail": "test"} in d1.json()["blockers"]
    upcoming = sport_admin.get(f"/api/teams/{D1}/upcoming-filming").json()
    assert upcoming and all(u["kind"] == "match" and not u["allowed"] for u in upcoming)
    assert {u["id"] for u in upcoming} <= {game_id(D1, k) for k in range(6)}  # games only


def test_who_may_upload(club: dict[str, TestClient], as_: Any) -> None:
    assert create_match(as_("player-h3")).status_code == 403
    assert create_match(club["admin"]).status_code == 403  # admins watch and delete, coaches upload
    assert create_match(as_("basketball-player")).status_code == 404  # cannot even see H3
    assert as_("coach-h3").get(f"/api/teams/{H3}").json()["can_upload"] is True
    bad = club["coach"].post(
        f"/api/teams/{H3}/videos",
        json={"kind": "match", "game_id": game_id(D1, 1), "files": [{"name": "a.mp4", "size": 1}]},
    )
    assert bad.status_code == 422  # another team's game
    notvideo = club["coach"].post(
        f"/api/teams/{H3}/videos",
        json={
            "kind": "match",
            "game_id": game_id(H3, 1),
            "files": [{"name": "notes.txt", "size": 1}],
        },
    )
    assert notvideo.status_code == 415


def friendly(client: TestClient, team: int = H3, **fields: Any) -> Any:
    """A match that is not in Wiedisync: a date and a title instead of a game."""
    body = {"recorded_on": (TODAY - timedelta(days=2)).isoformat(), "title": "Friendly"}
    body.update(fields)
    return client.post(
        f"/api/teams/{team}/videos", json={**body, "files": [{"name": "a.mp4", "size": 10}]}
    )


def test_a_match_without_a_wiedisync_game_needs_a_date_and_a_title(
    club: dict[str, TestClient],
) -> None:
    coach = club["coach"]
    created = friendly(coach, title="  Friendly vs. VBC Zürich  ")
    assert created.status_code == 201, created.text
    video = created.json()
    assert video["kind"] == "match" and video["game_id"] is None
    assert video["title"] == "Friendly vs. VBC Zürich"
    assert video["recorded_on"] == (TODAY - timedelta(days=2)).isoformat()

    assert friendly(coach, title=None).status_code == 422
    assert friendly(coach, title="   ").status_code == 422
    assert friendly(coach, title="x" * 201).status_code == 422
    assert friendly(coach, recorded_on=None).status_code == 422  # neither game nor date
    assert friendly(coach, game_id=game_id(H3, 1)).status_code == 422  # both
    future = (TODAY + timedelta(days=2)).isoformat()
    assert friendly(coach, recorded_on=future).status_code == 422
    # A date of another season belongs on that season's team page (retention counts from it).
    last_year = friendly(coach, recorded_on=(TODAY - timedelta(days=400)).isoformat())
    assert last_year.status_code == 422 and "season" in last_year.json()["detail"]


def test_a_match_without_a_game_passes_the_filming_rules_too(
    club: dict[str, TestClient], as_: Any, store: FakeStore
) -> None:
    sport_admin = as_("vb-sport-admin")
    du23 = friendly(sport_admin, team=DU23)
    assert du23.status_code == 409
    assert {b["code"] for b in du23.json()["blockers"]} >= {"under_18"}
    lineage = club["admin"].get(f"/api/teams/{H3}").json()["lineage_id"]
    policy = {"matches_allowed": False, "reason": "test"}
    assert club["admin"].put(f"/api/admin/policies/{lineage}", json=policy).status_code == 200
    refused = friendly(club["coach"])
    assert refused.status_code == 409
    assert refused.json()["blockers"] == [
        {"code": "policy_matches", "count": None, "detail": "test"}
    ]
    assert store.uploads == {}


def test_trainings_are_gone(club: dict[str, TestClient], db_engine: Engine) -> None:
    """Matches only (16.10.2026): no training uploads, but older training rows still list."""
    coach = club["coach"]
    training = coach.post(
        f"/api/teams/{H3}/videos",
        json={
            "kind": "training",
            "recorded_on": TODAY.isoformat(),
            "files": [{"name": "a.mp4", "size": 10}],
        },
    )
    assert training.status_code == 422
    on = TODAY.isoformat()
    assert (
        coach.get(f"/api/teams/{H3}/filming", params={"kind": "training", "on": on}).status_code
        == 422
    )
    for params in ({"kind": "match", "on": on}, {"on": on}):
        r = coach.get(f"/api/teams/{H3}/filming", params=params)
        assert r.status_code == 200 and r.json()["allowed"] is True

    video = create_match(coach).json()
    with db_engine.begin() as conn:
        conn.execute(text("UPDATE video SET kind = 'training', game_id = NULL, training_id = 1"))
    listed = coach.get(f"/api/teams/{H3}/videos")
    assert listed.status_code == 200 and listed.json()[0]["kind"] == "training"
    got = coach.get(f"/api/videos/{video['id']}")
    assert got.status_code == 200 and "training_id" not in got.json()


def test_upload_to_playback_to_deletion(
    app: FastAPI, club: dict[str, TestClient], as_: Any, store: FakeStore, db_engine: Engine
) -> None:
    coach = club["coach"]
    created = create_match(coach, sizes=(PART * 2 + 5, 1000))
    assert created.status_code == 201, created.text
    video = created.json()
    assert video["status"] == "uploading"
    assert [f["part_count"] for f in video["files"]] == [3, 1]
    assert len(store.uploads) == 2

    first = video["files"][0]
    out_of_range = coach.post(
        f"/api/videos/{video['id']}/files/{first['id']}/parts", json={"parts": [4]}
    )
    assert out_of_range.status_code == 422
    early = coach.post(f"/api/videos/{video['id']}/files/{first['id']}/complete")
    assert early.status_code == 409  # parts missing

    video = upload_all(coach, store, video)
    assert video["status"] == "queued"
    assert all(f["uploaded"] for f in video["files"])

    w = worker_client(app, club["admin"], MEDIA_CAPS)
    # A worker without the NAS cannot take the ingest.
    assert claim(worker_client(app, club["admin"], ["transcode"]), ["transcode"]) == []
    [ingest] = claim(w, MEDIA_CAPS)
    forbidden = w.post(
        f"/api/worker/jobs/{ingest['id']}/sign",
        json={"op": "put", "keys": ["media/x/1080p/seg_00000.m4s"]},
    )
    assert forbidden.status_code == 403
    assert (
        w.post(
            f"/api/worker/jobs/{ingest['id']}/fail", json={"error": "network", "retryable": True}
        ).status_code
        == 204
    )
    with db_engine.begin() as conn:
        conn.execute(text("UPDATE job SET not_before = now() - interval '1 second'"))
    run_pipeline(w, MEDIA_CAPS, store)

    ready = coach.get(f"/api/videos/{video['id']}").json()
    assert ready["status"] == "ready"
    assert [r["name"] for r in ready["renditions"]] == ["1080p", "540p"]
    assert ready["meets_standard"] is True and ready["has_thumbs"] is True

    player = as_("player-h3")
    master = player.get(f"/api/media/{video['id']}/master.m3u8")
    assert master.status_code == 200
    assert "1080p/index.m3u8" in master.text and "RESOLUTION=1920x1080" in master.text
    assert master.headers["cache-control"] == "private, no-store"
    playlist = player.get(f"/api/media/{video['id']}/540p/index.m3u8")
    assert playlist.text == PLAYLIST
    seg = player.get(f"/api/media/{video['id']}/540p/seg_00001.m4s")
    assert seg.status_code == 302
    assert seg.headers["location"].startswith(
        f"https://s3.test/media/{video['id']}/540p/seg_00001.m4s"
    )
    assert store.signed[-1] == ("get", f"media/{video['id']}/540p/seg_00001.m4s", 300)
    assert player.get(f"/api/media/{video['id']}/540p/..%2Fsecret").status_code == 404
    assert player.get(f"/api/media/{video['id']}/540p/seg_1.m4s").status_code == 404
    vtt = player.get(f"/api/media/{video['id']}/thumbs.vtt")
    assert vtt.text.startswith("WEBVTT") and "thumbs/sprite_001.jpg#xywh=0,0,160,90" in vtt.text
    assert player.get(f"/api/media/{video['id']}/poster.jpg").status_code == 302

    outsider = as_("basketball-player")
    assert outsider.get(f"/api/videos/{video['id']}").status_code == 404
    assert outsider.get(f"/api/media/{video['id']}/540p/seg_00001.m4s").status_code == 404
    assert player.delete(f"/api/videos/{video['id']}").status_code == 403

    assert coach.delete(f"/api/videos/{video['id']}").status_code == 204
    assert player.get(f"/api/media/{video['id']}/540p/seg_00001.m4s").status_code == 404
    [purge] = claim(w, MEDIA_CAPS)
    assert purge["kind"] == "purge" and len(purge["params"]["nas_paths"]) == 2
    with db_engine.connect() as conn:
        actions = conn.execute(text("SELECT action FROM audit_log ORDER BY id")).scalars().all()
    for action in (
        "video_created",
        "video_uploaded",
        "video_ready",
        "video_viewed",
        "video_deleted",
    ):
        assert action in actions
    assert actions.count("video_viewed") == 1


def test_lower_resolution_video_is_flagged_and_gets_only_540p(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore
) -> None:
    video = upload_all(club["coach"], store, create_match(club["coach"]).json())
    w = worker_client(app, club["admin"], MEDIA_CAPS)
    [ingest] = claim(w, MEDIA_CAPS)
    w.post(
        f"/api/worker/jobs/{ingest['id']}/complete", json=ingest_result(ingest, height=720, fps=25)
    )
    kinds = []
    while jobs := claim(w, MEDIA_CAPS):
        kinds.append(
            (
                jobs[0]["kind"],
                jobs[0]["params"].get("rendition", {}).get("name"),
                jobs[0]["params"].get("fps"),
            )
        )
        body = transcode_result(jobs[0]) if jobs[0]["kind"] == "transcode" else THUMBS
        w.post(f"/api/worker/jobs/{jobs[0]['id']}/complete", json=body)
    assert ("transcode", "540p", 25.0) in kinds and not any(k[1] == "1080p" for k in kinds)
    got = club["coach"].get(f"/api/videos/{video['id']}").json()
    assert got["status"] == "ready"
    assert got["meets_standard"] is False and got["standard_issues"] == ["720p", "25fps"]


def test_bad_results_and_expired_leases_fail_the_video(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, db_engine: Engine
) -> None:
    video = upload_all(club["coach"], store, create_match(club["coach"]).json())
    w = worker_client(app, club["admin"], MEDIA_CAPS)
    claim(w, MEDIA_CAPS)
    # The lease runs out three times: back to the queue twice, then failed for good.
    for attempt in range(3):
        with db_engine.begin() as conn:
            conn.execute(
                text(
                    "UPDATE job SET lease_expires_at = now() - interval '1 minute' "
                    "WHERE status = 'running'"
                )
            )
        with app.state.sessionmaker() as db:
            assert reap(db) == 1
        if attempt < 2:
            with db_engine.begin() as conn:
                conn.execute(text("UPDATE job SET not_before = NULL"))
            assert claim(w, MEDIA_CAPS)[0]["attempt"] == attempt + 2
    got = club["coach"].get(f"/api/videos/{video['id']}").json()
    assert got["status"] == "failed" and "lease expired" in got["error"]

    video2 = upload_all(club["coach"], store, create_match(club["coach"]).json())
    [ingest2] = claim(w, MEDIA_CAPS)
    assert ingest2["video_id"] == video2["id"]
    bad = w.post(f"/api/worker/jobs/{ingest2['id']}/complete", json={"files": []})
    assert bad.status_code == 422
    assert club["coach"].get(f"/api/videos/{video2['id']}").json()["status"] == "failed"
    # Heartbeat on a job that is no longer ours tells the worker to stop.
    hb = w.post(f"/api/worker/jobs/{ingest2['id']}/heartbeat", json={"progress": 0.5})
    assert hb.json() == {"cancel": True}


def test_transcode_result_with_foreign_playlist_uri_is_rejected(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore
) -> None:
    upload_all(club["coach"], store, create_match(club["coach"]).json())
    w = worker_client(app, club["admin"], MEDIA_CAPS)
    [ingest] = claim(w, MEDIA_CAPS)
    w.post(f"/api/worker/jobs/{ingest['id']}/complete", json=ingest_result(ingest))
    job = next(j for j in iter(lambda: claim(w, MEDIA_CAPS)[0], None) if j["kind"] == "transcode")
    body = transcode_result(job)
    body["playlist"] = PLAYLIST.replace("seg_00001.m4s", "https://evil.example/x.m4s")
    assert w.post(f"/api/worker/jobs/{job['id']}/complete", json=body).status_code == 422


def test_worker_routes_are_internal_and_tokens_revocable(
    app: FastAPI, club: dict[str, TestClient]
) -> None:
    w = worker_client(app, club["admin"], MEDIA_CAPS)
    through_cloudflare = w.post(
        "/api/worker/claim",
        json={"caps": MEDIA_CAPS, "wait": False},
        headers={"CF-Connecting-IP": "1.2.3.4"},
    )
    assert through_cloudflare.status_code == 404
    workers = club["admin"].get("/api/admin/workers").json()
    assert "token" not in workers[0]
    club["admin"].delete(f"/api/admin/workers/{workers[0]['id']}")
    assert w.post("/api/worker/claim", json={"caps": MEDIA_CAPS, "wait": False}).status_code == 401
    assert club["coach"].get("/api/admin/workers").status_code == 403


def test_inbox_files_become_a_video_without_upload(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore
) -> None:
    w = worker_client(app, club["admin"], [*MEDIA_CAPS, "inbox"])
    now = datetime.now(UTC)
    an_hour_ago = (now - timedelta(hours=1)).isoformat()
    files = [
        {"path": "vb-H3/C0001.MP4", "size": 5_000_000_000, "mtime": an_hour_ago,
         "probe": {"duration_ms": 3_600_000, "width": 1920, "height": 1080, "fps": 50.0}},
        {"path": "vb-H3/C0002.MP4", "size": 1_000_000_000, "mtime": now.isoformat(), "probe": {}},
    ]  # fmt: skip
    assert w.post("/api/worker/inbox", json={"files": files}).status_code == 204
    other = {"path": "vb-D1/C0009.MP4", "size": 1, "mtime": an_hour_ago, "probe": {}}
    assert w.post("/api/worker/inbox", json={"files": [*files, other]}).status_code == 204
    # The H3 coach sees H3's folder only (D1's file is another team's).
    assert as_inbox(club["coach"]) == ["vb-H3/C0001.MP4", "vb-H3/C0002.MP4"]
    ids = [f["id"] for f in club["coach"].get("/api/inbox").json()]
    created = club["coach"].post(
        f"/api/teams/{H3}/videos",
        json={"kind": "match", "game_id": game_id(H3, 2), "inbox_file_ids": ids},
    )
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "queued" and created.json()["source"] == "inbox"
    assert as_inbox(club["coach"]) == []
    [ingest] = claim(w, [*MEDIA_CAPS, "inbox"])
    assert [f["inbox_path"] for f in ingest["params"]["files"]] == [
        "vb-H3/C0001.MP4",
        "vb-H3/C0002.MP4",
    ]
    # A file that disappears from the share disappears from the list.
    assert w.post("/api/worker/inbox", json={"files": files[:1]}).status_code == 204
    assert club["coach"].get("/api/inbox").json() == []
    assert as_inbox_status(club["admin"]) is None


def as_inbox(client: TestClient) -> list[str]:
    return [f["path"] for f in client.get("/api/inbox").json()]


def as_inbox_status(client: TestClient) -> None:
    assert client.get("/api/inbox").status_code == 403  # admins do not upload


# --- Hardening after the security review --------------------------------------------------


def test_part_urls_carry_the_exact_part_size(club: dict[str, TestClient]) -> None:
    video = create_match(club["coach"], sizes=(PART * 2 + 5,)).json()
    f = video["files"][0]
    urls = (
        club["coach"]
        .post(f"/api/videos/{video['id']}/files/{f['id']}/parts", json={"parts": [1, 3]})
        .json()
    )
    assert urls[0]["url"].endswith(f"size={PART}")
    assert urls[1]["url"].endswith("size=5")  # the last part is the remainder


def test_a_repeated_completion_is_idempotent(
    club: dict[str, TestClient], store: FakeStore, db_engine: Engine
) -> None:
    video = upload_all(club["coach"], store, create_match(club["coach"]).json())
    f = video["files"][0]
    # The first call completed the S3 upload but its response got lost: try again.
    with db_engine.begin() as conn:
        conn.execute(
            text("UPDATE video_source_file SET uploaded_at = NULL, upload_id = 'up1'"),
        )
        conn.execute(text("UPDATE video SET status = 'uploading'"))
        conn.execute(text("DELETE FROM job"))
    again = club["coach"].post(f"/api/videos/{video['id']}/files/{f['id']}/complete")
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "queued"


def test_a_policy_change_during_the_upload_refuses_it(
    club: dict[str, TestClient], store: FakeStore
) -> None:
    coach = club["coach"]
    video = create_match(coach).json()
    lineage = coach.get(f"/api/teams/{H3}").json()["lineage_id"]
    policy = {"matches_allowed": False}
    assert club["admin"].put(f"/api/admin/policies/{lineage}", json=policy).status_code == 200
    f = video["files"][0]
    signed = coach.post(f"/api/videos/{video['id']}/files/{f['id']}/parts", json={"parts": [1, 2]})
    for p in signed.json():
        upload_id = p["url"].split("uploadId=")[1].split("&")[0]
        store.put_part(upload_id, p["part"], PART if p["part"] == 1 else 10)
    done = coach.post(f"/api/videos/{video['id']}/files/{f['id']}/complete")
    assert done.status_code == 409
    assert done.json()["blockers"][0]["code"] == "policy_matches"
    got = coach.get(f"/api/videos/{video['id']}").json()
    assert got["status"] == "refused"


def test_a_camera_date_that_was_blocked_refuses_the_video(
    app: FastAPI, club: dict[str, TestClient], as_: Any, store: FakeStore, db_engine: Engine
) -> None:
    """The coach says today; the camera says a day when filming was not allowed."""
    video = upload_all(club["coach"], store, create_match(club["coach"]).json())
    w = worker_client(app, club["admin"], MEDIA_CAPS)
    [ingest] = claim(w, MEDIA_CAPS)
    result = ingest_result(ingest)
    # A day 10 days off is plausible; make that day fail the adult check by giving the
    # team a member who turns 18 after it (Lia Jung is on DU23-1, so use the DU23 policy
    # route instead: switch matches off, which applies to any day).
    lineage = club["admin"].get(f"/api/teams/{H3}").json()["lineage_id"]
    club["admin"].put(
        f"/api/admin/policies/{lineage}", json={"matches_allowed": False, "reason": "test"}
    )
    camera = datetime.now(UTC) - timedelta(days=10)
    result["capture"]["creation_time"] = camera.isoformat()
    assert w.post(f"/api/worker/jobs/{ingest['id']}/complete", json=result).status_code == 204
    got = club["coach"].get(f"/api/videos/{video['id']}").json()
    assert got["status"] == "refused" and "camera recorded this" in got["error"]
    [purge] = claim(w, MEDIA_CAPS)
    assert purge["kind"] == "purge" and purge["params"]["raw_subdir"]
    with db_engine.connect() as conn:
        active = "SELECT count(*) FROM job WHERE kind='transcode' AND status <> 'cancelled'"
        assert conn.execute(text(active)).scalar() == 0


def test_an_unset_camera_clock_is_only_noted(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, db_engine: Engine
) -> None:
    upload_all(club["coach"], store, create_match(club["coach"]).json())
    w = worker_client(app, club["admin"], MEDIA_CAPS)
    [ingest] = claim(w, MEDIA_CAPS)
    result = ingest_result(ingest)
    result["capture"]["creation_time"] = "2016-01-01T00:00:00Z"
    assert w.post(f"/api/worker/jobs/{ingest['id']}/complete", json=result).status_code == 204
    with db_engine.connect() as conn:
        actions = conn.execute(text("SELECT action FROM audit_log")).scalars().all()
    assert "camera_clock_unset" in actions


@pytest.mark.parametrize(
    "bad",
    [
        {"nas_path": "/nas/nomal/raw/elsewhere/01.mp4"},
        {"nas_path": "/nas/nomal/raw/{subdir}/../../other/01.mp4"},
        {"nas_path": "/nas/nomal/raw/{subdir}/sub/01.mp4"},
    ],
)
def test_ingest_results_with_foreign_paths_are_rejected(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, bad: dict[str, str]
) -> None:
    upload_all(club["coach"], store, create_match(club["coach"]).json())
    w = worker_client(app, club["admin"], MEDIA_CAPS)
    [ingest] = claim(w, MEDIA_CAPS)
    result = ingest_result(ingest)
    result["files"][0]["nas_path"] = bad["nas_path"].format(subdir=ingest["params"]["raw_subdir"])
    assert w.post(f"/api/worker/jobs/{ingest['id']}/complete", json=result).status_code == 422


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        (
            "playlist",
            PLAYLIST.replace('#EXT-X-MAP:URI="init.mp4"', "#EXT-X-MAP:URI=https://e/x"),
            422,
        ),
        ("playlist", PLAYLIST.replace("#EXT-X-ENDLIST", "#EXT-X-KEY:METHOD=AES-128,URI=x"), 422),
        ("playlist", PLAYLIST.replace("seg_00001.m4s", "../seg_00001.m4s"), 422),
        ("playlist", PLAYLIST.replace("seg_00001.m4s", "seg_00001.m4s\n"), 204),  # blank line
        ("codecs", 'avc1",URI="https://e/x', 422),
    ],
)
def test_transcode_results_are_checked_line_by_line(
    app: FastAPI,
    club: dict[str, TestClient],
    store: FakeStore,
    field: str,
    value: str,
    expected: int,
) -> None:
    upload_all(club["coach"], store, create_match(club["coach"]).json())
    w = worker_client(app, club["admin"], MEDIA_CAPS)
    [ingest] = claim(w, MEDIA_CAPS)
    w.post(f"/api/worker/jobs/{ingest['id']}/complete", json=ingest_result(ingest))
    job = next(j for j in iter(lambda: claim(w, MEDIA_CAPS)[0], None) if j["kind"] == "transcode")
    body = transcode_result(job)
    body[field] = value
    assert w.post(f"/api/worker/jobs/{job['id']}/complete", json=body).status_code == expected


def test_players_see_ready_videos_only_and_no_internals(
    app: FastAPI, club: dict[str, TestClient], as_: Any, store: FakeStore
) -> None:
    video = upload_all(club["coach"], store, create_match(club["coach"]).json())
    player = as_("player-h3")
    assert player.get(f"/api/teams/{H3}/videos").json() == []
    assert player.get(f"/api/videos/{video['id']}").status_code == 404
    assert club["coach"].get(f"/api/videos/{video['id']}").json()["files"]
    w = worker_client(app, club["admin"], MEDIA_CAPS)
    run_pipeline(w, MEDIA_CAPS, store)
    seen = player.get(f"/api/videos/{video['id']}").json()
    assert seen["status"] == "ready" and seen["files"] == [] and seen["error"] is None


def test_filming_status_only_for_a_sensible_date_range(club: dict[str, TestClient]) -> None:
    far = (TODAY + timedelta(days=200)).isoformat()
    r = club["coach"].get(f"/api/teams/{H3}/filming", params={"kind": "match", "on": far})
    assert r.status_code == 422


def test_admin_retries_a_failed_job_with_the_ones_it_cancelled(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore
) -> None:
    video = upload_all(club["coach"], store, create_match(club["coach"]).json())
    w = worker_client(app, club["admin"], MEDIA_CAPS)
    [ingest] = claim(w, MEDIA_CAPS)
    w.post(f"/api/worker/jobs/{ingest['id']}/complete", json=ingest_result(ingest))
    [first] = claim(w, MEDIA_CAPS)
    w.post(f"/api/worker/jobs/{first['id']}/fail", json={"error": "disk full", "retryable": False})
    assert club["coach"].get(f"/api/videos/{video['id']}").json()["status"] == "failed"
    assert club["coach"].post(f"/api/admin/jobs/{first['id']}/retry").status_code == 403
    retried = club["admin"].post(f"/api/admin/jobs/{first['id']}/retry")
    assert retried.status_code == 200 and retried.json()["status"] == "queued"
    run_left = []
    while jobs := claim(w, MEDIA_CAPS):
        run_left.append(jobs[0]["kind"])
        body = transcode_result(jobs[0]) if jobs[0]["kind"] == "transcode" else THUMBS
        w.post(f"/api/worker/jobs/{jobs[0]['id']}/complete", json=body)
    assert sorted(run_left) == ["thumbs", "transcode", "transcode"]
    assert club["coach"].get(f"/api/videos/{video['id']}").json()["status"] == "ready"


def test_deleted_videos_are_swept_again_after_the_lease_window(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore, db_engine: Engine
) -> None:
    from nomal.api.videos import sweep_deleted

    video = upload_all(club["coach"], store, create_match(club["coach"]).json())
    club["coach"].delete(f"/api/videos/{video['id']}")
    store.objects[f"media/{video['id']}/1080p/seg_00009.m4s"] = 1  # a late upload
    with app.state.sessionmaker() as db:
        assert sweep_deleted(db, store) == 0  # too early
        later = datetime.now(UTC) + timedelta(hours=3)
        assert sweep_deleted(db, store, now=later) == 1
    assert not [k for k in store.objects if video["id"] in k]
    with db_engine.connect() as conn:
        assert conn.execute(text("SELECT purged_at IS NOT NULL FROM video")).scalar() is True
