"""Players through the API (PLAN §10.5): the job after the trajectory, its inputs and result,
the gallery (name, merge, split, not a player, swap teams), the attribution of actions and
its rules (a person's number is never overwritten), permissions."""

from __future__ import annotations

import gzip
import json
import uuid
from typing import Any

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.fakes import FakeStore
from tests.test_analysis_api import claim_kind, complete, make_ready, payload
from tests.test_court_job_api import BUMP_AT, court_result
from wieplay.core.config import Settings
from wieplay.core.models import Event
from wieplay.core.players import Ball, box_distance, nearest
from wieplay.ml import players as pl

CAPS = ["analyse", "nas"]
KINDS = ["analyse", "train_rallies", "trajectory", "court", "players"]
# Contacts: our player hits at 12 s (high, near side), theirs digs at 15 s (low, far side).
TRACK = {
    "schema": 1, "fps": 15.0, "scale": 1000, "frames": 54000, "duration_s": 3600.0,
    "tracks": [[150, 400, 600, 10, -12, 11, -10]],
    "contacts": [[12_000, 300, 500, 2], [15_000, 700, 300, 1], [16_000, 500, 900, 0]],
    "stats": {"tracks": 1, "samples": 3, "contacts": 3, "bounces": 1},
}  # fmt: skip
# Boxes (normalised x1, y1, x2, y2) of five tracks during the rally window.
BOXES = {
    1: (0.27, 0.45, 0.33, 0.75),  # ours, under the 12 s contact
    2: (0.50, 0.45, 0.56, 0.75),  # ours, elsewhere
    3: (0.68, 0.25, 0.72, 0.40),  # theirs, under the 15 s contact
    4: (0.40, 0.25, 0.44, 0.40),  # theirs
    5: (0.02, 0.30, 0.05, 0.50),  # the referee
}


@pytest.fixture
def settings(settings: Settings) -> Settings:
    """Most tests follow the automatic chain (court → players); off is the default and has
    its own tests below."""
    return settings.model_copy(update={"players_auto": True})


@pytest.fixture
def ready(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> Any:
    return make_ready(app, club, store)


@pytest.fixture
def analyser(app: FastAPI, club: dict[str, TestClient]) -> TestClient:
    from tests.media_helpers import worker_client

    return worker_client(app, club["admin"], CAPS)


def claim(w: TestClient, kind: str) -> dict[str, Any]:
    r = w.post("/api/worker/claim", json={"caps": CAPS, "wait": False, "kinds": KINDS})
    assert r.status_code == 200, r.text
    [job] = r.json()["jobs"]
    assert job["kind"] == kind
    return job


def trajectory(analyser: TestClient, store: FakeStore, **court: Any) -> None:
    """Analyse (one rally, 10-20 s), the trajectory job with TRACK, then the court job (by
    default unsure: no calibration, the image distance counts)."""
    job = claim_kind(analyser, "analyse")
    complete(analyser, job, payload(job, [(10_000, 20_000)]))
    traj = claim(analyser, "trajectory")
    data = gzip.compress(json.dumps(TRACK).encode(), mtime=0)
    store.put_object(traj["params"]["track_key"], data)
    complete(
        analyser,
        traj,
        {
            "model": {"name": "wieplay-trajectory", "version": "abc123", "params": {}},
            "track": {
                "nas_path": f"{traj['params']['out_dir']}/ball-track-x.json.gz",
                "key": traj["params"]["track_key"],
                "sha256": "d" * 64,
                "bytes": len(data),
                "schema_version": 1,
                "stats": {"fps": 15.0, "contacts": 3},
            },
            "court": None,
        },
    )
    job = claim(analyser, "court")
    found = {"method": "auto", "confidence": 0.4, "bump": False, **court}
    complete(analyser, job, court_result(job, **found))


def index_bytes() -> bytes:
    geom = pl.Geometry(roi=(0.0, 0.0, 1.0, 1.0))
    col = pl.Collector(geom, 1000, 1000, (0, 0, 1000, 1000))
    img = np.zeros((1000, 1000, 3), np.uint8)
    ids = np.array(sorted(BOXES))
    boxes = np.array([[v * 1000 for v in BOXES[i]] for i in ids], dtype=float)
    for k in range(120):  # 9 s to 21 s at 10 fps
        col.add(9_000 + 100 * k, 0, img, boxes, np.full(len(ids), 0.9), ids)
    tracks = pl.finish_tracks(col)
    return gzip.compress(json.dumps(pl.encode_index(col, tracks)).encode(), mtime=0)


def track_row(tid: int, side: str) -> dict[str, Any]:
    return {"id": tid, "t0": 9_000, "t1": 20_900, "n": 120, "side": side, "number": None,
            "number_conf": 0.0, "crop": [0, 4 + tid], "h": 120.0, "foot": None}  # fmt: skip


def players_result(job: dict[str, Any], store: FakeStore) -> dict[str, Any]:
    prefix, out = job["params"]["prefix"], job["params"]["out_dir"]
    data = index_bytes()
    store.put_object(f"{prefix}index.json.gz", data)
    store.put_object(f"{prefix}sheet-0.jpg", b"\xff\xd8jpeg")

    def cluster(key: int, team: str, tids: list[int], **extra: Any) -> dict[str, Any]:
        side = "near" if team == "us" else "far"
        return {"key": key, "team": team, "crops": [[0, key]],
                "tracks": [track_row(t, side) for t in tids], **extra}  # fmt: skip

    return {
        "model": {
            "name": "wieplay-players",
            "version": "abc123",
            "weights_sha256": "e" * 64,
            "params": {"detect_fps": 10.0},
        },
        "tracks_artifact": {"nas_path": f"{out}/players-x.npz", "sha256": "f" * 64, "bytes": 99},
        "index": {
            "key": f"{prefix}index.json.gz",
            "nas_path": f"{out}/players-x/index.json.gz",
            "sha256": "a" * 64,
            "bytes": len(data),
        },
        "sheets": {
            "keys": [f"{prefix}sheet-0.jpg"],
            "nas_dir": f"{out}/players-x",
            "cell": [80, 160],
            "cols": 10,
            "rows": 8,
        },
        "clusters": [
            cluster(1, "us", [1]),
            cluster(2, "us", [2], number=3, number_conf=0.7),
            cluster(3, "them", [3]),
            cluster(4, "them", [4]),
            cluster(5, "them", [5], referee=True),
        ],
        "stats": {"tracks": 5, "clusters": 5},
        "timing": {"wall_s": 12.0},
    }


def with_players(analyser: TestClient, store: FakeStore) -> dict[str, Any]:
    trajectory(analyser, store)
    job = claim(analyser, "players")
    complete(analyser, job, players_result(job, store))
    return job


def gallery(client: TestClient, video: dict[str, Any]) -> dict[str, Any]:
    r = client.get(f"/api/videos/{video['id']}/players")
    assert r.status_code == 200, r.text
    return dict(r.json())


def by_key(state: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {c["key"]: c for c in state["clusters"]}


def tag(t_ms: int, side: str = "us", number: int | None = None, code: str = "A") -> dict[str, Any]:
    return {"id": str(uuid.uuid4()), "code": code, "t_ms": t_ms, "side": side,
            "number": number, "attributes": {}}  # fmt: skip


def model_event(app: FastAPI, video: dict[str, Any], t_ms: int, side: str, **extra: Any) -> str:
    """A suggested action as the actions job (PLAN §10.4a) writes it: no player."""
    eid = uuid.uuid4()
    with app.state.sessionmaker() as db:
        db.add(
            Event(
                id=eid, video_id=uuid.UUID(video["id"]), sport="volleyball", taxonomy_version=1,
                code=extra.pop("code", "A"), t_ms=t_ms, time_precision="adjusted", side=side,
                attributes={}, source="model", review="unreviewed", confidence=0.8, version=1,
                **extra,
            )
        )  # fmt: skip
        db.commit()
    return str(eid)


def event(app: FastAPI, eid: str) -> Event:
    with app.state.sessionmaker() as db:
        row = db.scalars(select(Event).where(Event.id == uuid.UUID(eid))).one()
        db.expunge(row)
        return row


# --- the job --------------------------------------------------------------------------------


def test_the_players_job_follows_the_trajectory_with_its_inputs(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    trajectory(analyser, store)
    job = claim(analyser, "players")
    params = job["params"]
    assert params["prefix"].startswith(f"media/{ready['id']}/analysis/players-")
    assert params["parts"] == [{"offset_ms": 0, "duration_ms": params["duration_ms"]}]
    assert len(params["inputs"]) == 1 and params["out_dir"].startswith("videos/")
    found = analyser.post(f"/api/worker/jobs/{job['id']}/inputs").json()
    assert found["calibration"] is None
    assert [(r["start_ms"], r["end_ms"]) for r in found["rallies"]] == [(10_000, 20_000)]
    # The worker may write its index and sheets under its prefix, nothing else.
    ok = [f"{params['prefix']}index.json.gz", f"{params['prefix']}sheet-0.jpg"]
    sign = analyser.post(f"/api/worker/jobs/{job['id']}/sign", json={"op": "put", "keys": ok})
    assert sign.status_code == 200
    for bad in (f"{params['prefix']}evil.html", f"media/{ready['id']}/1080p/seg_00001.m4s"):
        r = analyser.post(f"/api/worker/jobs/{job['id']}/sign", json={"op": "put", "keys": [bad]})
        assert r.status_code == 403
    state = gallery(club["coach"], ready)
    assert state["status"] == "running" and state["clusters"] == []
    complete(analyser, job, players_result(job, store))
    state = gallery(club["coach"], ready)
    assert state["status"] == "done" and state["can_edit"] is True
    assert [(c["key"], c["team"]) for c in state["clusters"]] == [
        (1, "us"), (2, "us"), (3, "them"), (4, "them"), (5, "them"),
    ]  # fmt: skip
    clusters = by_key(state)
    assert clusters[2]["ocr_number"] == 3 and clusters[2]["number"] is None
    assert clusters[5]["not_player"] and clusters[5]["referee_guess"]
    assert clusters[1]["seconds"] == 12.0 and clusters[1]["tracks"][0]["crop"] == [0, 5]
    assert state["sheets"]["count"] == 1 and {r["number"] for r in state["roster"]} >= {7, 3}
    sheet = club["coach"].get(f"/api/videos/{ready['id']}/players/sheets/0")
    assert sheet.status_code == 200 and sheet.headers["content-type"] == "image/jpeg"
    assert club["coach"].get(f"/api/videos/{ready['id']}/players/sheets/1").status_code == 404
    # Not twice: a second trajectory does not queue the players again.
    assert (
        analyser.post(
            "/api/worker/claim", json={"caps": CAPS, "wait": False, "kinds": ["players"]}
        ).json()["jobs"]
        == []
    )


def test_players_are_off_by_default_and_found_on_demand(
    ready: Any,
    app: FastAPI,
    club: dict[str, TestClient],
    analyser: TestClient,
    store: FakeStore,
    as_: Any,
) -> None:
    app.state.settings = app.state.settings.model_copy(update={"players_auto": False})
    assert Settings.model_fields["players_auto"].default is False
    hit = model_event(app, ready, 12_000, "us")
    trajectory(analyser, store)  # the court job is done: no players job follows
    none = analyser.post(
        "/api/worker/claim", json={"caps": CAPS, "wait": False, "kinds": ["players"]}
    )
    assert none.json()["jobs"] == []
    coach = club["coach"]
    summary = coach.get(f"/api/videos/{ready['id']}/analysis").json()["players"]
    assert summary["status"] == "none" and summary["run_id"] is None and summary["clusters"] == 0
    assert gallery(coach, ready)["status"] == "none"
    # Whoever may tag finds them on demand; asking twice queues one job.
    assert as_("player-h3").post(f"/api/videos/{ready['id']}/players/find").status_code == 403
    first = coach.post(f"/api/videos/{ready['id']}/players/find")
    assert first.status_code == 202 and first.json()["status"] == "queued"
    again = coach.post(f"/api/videos/{ready['id']}/players/find")
    assert again.json()["job"]["id"] == first.json()["job"]["id"]
    job = claim(analyser, "players")
    complete(analyser, job, players_result(job, store))
    # With players there, the attribution runs as always.
    cid = by_key(gallery(coach, ready))[1]["id"]
    coach.patch(f"/api/videos/{ready['id']}/players/clusters/{cid}", json={"number": 7})
    assert event(app, hit).number == 7
    summary = coach.get(f"/api/videos/{ready['id']}/analysis").json()["players"]
    assert summary["status"] == "done" and summary["clusters"] == 4 and summary["named"] == 1


def test_the_cli_finds_players_on_demand(
    ready: Any,
    app: FastAPI,
    analyser: TestClient,
    store: FakeStore,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tests.conftest import TEST_DB
    from wieplay import cli
    from wieplay.core.config import get_settings

    app.state.settings = app.state.settings.model_copy(update={"players_auto": False})
    trajectory(analyser, store)
    monkeypatch.setenv("WIEPLAY_DATABASE_URL", TEST_DB)
    get_settings.cache_clear()
    try:
        with pytest.raises(SystemExit):
            cli.main(["players"])  # --video or --all-ready
        assert cli.main(["players", "--all-ready", "--dry-run"]) == 0
        assert "would queue" in capsys.readouterr().out
        assert cli.main(["players", "--all-ready"]) == 0
        assert "1 queued" in capsys.readouterr().out
        assert cli.main(["players", "--video", ready["id"]]) == 0
        assert "already queued" in capsys.readouterr().out
        job = claim(analyser, "players")
        complete(analyser, job, players_result(job, store))
        assert cli.main(["players", "--all-ready"]) == 0
        out = capsys.readouterr().out
        assert "has players" in out and "0 queued" in out
        assert cli.main(["players", "--video", ready["id"], "--force"]) == 0
        assert "1 queued" in capsys.readouterr().out
    finally:
        get_settings.cache_clear()


def test_the_job_gets_the_court_of_each_camera_setup(
    ready: Any, analyser: TestClient, store: FakeStore
) -> None:
    # A sure court with a bump: the calibration is set, with two setups.
    trajectory(analyser, store, confidence=0.82, bump=True)
    job = claim(analyser, "players")
    found = analyser.post(f"/api/worker/jobs/{job['id']}/inputs").json()
    assert [s["from_ms"] for s in found["setups"]] == [0, BUMP_AT]
    assert all(s["homography"] for s in found["setups"])
    assert found["setups"][0]["homography"] != found["setups"][1]["homography"]
    assert found["calibration"]["homography"] == found["setups"][0]["homography"]


def test_a_bad_result_fails_the_job_not_the_video(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    trajectory(analyser, store)
    job = claim(analyser, "players")
    body = players_result(job, store)
    body["index"]["key"] = f"media/{ready['id']}/elsewhere.json.gz"
    complete(analyser, job, body, status=422)
    assert club["coach"].get(f"/api/videos/{ready['id']}").json()["status"] == "ready"
    assert gallery(club["coach"], ready)["status"] == "failed"


def test_permissions(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore, as_: Any
) -> None:
    with_players(analyser, store)
    cid = by_key(gallery(club["coach"], ready))[1]["id"]
    member = as_("player-h3")
    state = gallery(member, ready)
    # Players name the cards of their own game (17.10.2026); finding them (~2 h of CPU)
    # stays with coaches and helpers.
    assert state["can_edit"] is True and state["can_find"] is False
    assert len(state["clusters"]) == 5
    assert (
        member.patch(
            f"/api/videos/{ready['id']}/players/clusters/{cid}", json={"number": 7}
        ).status_code
        == 200
    )
    assert member.post(f"/api/videos/{ready['id']}/players/find").status_code == 403
    assert gallery(club["coach"], ready)["can_find"] is True
    outsider = as_("basketball-player")
    assert outsider.get(f"/api/videos/{ready['id']}/players").status_code == 404
    assert outsider.get(f"/api/videos/{ready['id']}/players/sheets/0").status_code == 404


# --- the gallery ----------------------------------------------------------------------------


def test_name_merge_split_not_a_player_and_swap(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    with_players(analyser, store)
    coach = club["coach"]
    url = f"/api/videos/{ready['id']}/players"
    ids = {k: c["id"] for k, c in by_key(gallery(coach, ready)).items()}
    r = coach.patch(f"{url}/clusters/{ids[1]}", json={"number": 7})
    assert r.status_code == 200, r.text
    named = by_key(r.json())[1]
    assert named["number"] == 7 and named["member_id"] == 101 and named["name"] == "Alex Muster"
    # Ours must be in the roster snapshot; theirs can be any number.
    assert coach.patch(f"{url}/clusters/{ids[2]}", json={"number": 42}).status_code == 422
    assert coach.patch(f"{url}/clusters/{ids[3]}", json={"number": 42}).status_code == 200
    r = coach.patch(f"{url}/clusters/{ids[4]}", json={"not_player": True})
    assert by_key(r.json())[4]["not_player"] is True
    # Merge 2 into 1, then split it back out by its track.
    r = coach.post(f"{url}/merge", json={"into": ids[1], "sources": [ids[2]]})
    assert r.status_code == 200, r.text
    state = by_key(r.json())
    assert 2 not in state and [t["id"] for t in state[1]["tracks"]] == [1, 2]
    assert coach.post(f"{url}/merge", json={"into": ids[1], "sources": [ids[3]]}).status_code == 422
    assert coach.post(f"{url}/merge", json={"into": ids[1], "sources": [ids[1]]}).status_code == 422
    r = coach.post(f"{url}/clusters/{ids[1]}/split", json={"tracks": [2]})
    assert r.status_code == 200, r.text
    state = by_key(r.json())
    assert [t["id"] for t in state[1]["tracks"]] == [1] and state[1]["number"] == 7
    [new] = [c for c in state.values() if c["source"] == "split"]
    assert [t["id"] for t in new["tracks"]] == [2] and new["number"] is None
    assert coach.post(f"{url}/clusters/{ids[1]}/split", json={"tracks": [1]}).status_code == 422
    assert coach.post(f"{url}/clusters/{ids[2]}/split", json={"tracks": [2]}).status_code == 404
    # Swap: ours become theirs (the roster link goes, the number stays).
    r = coach.post(f"{url}/swap-teams")
    state = by_key(r.json())
    assert state[1]["team"] == "them" and state[1]["member_id"] is None and state[3]["team"] == "us"


def test_a_rerun_keeps_the_coachs_numbers_where_the_same_number_is_read(
    ready: Any, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    with_players(analyser, store)
    coach = club["coach"]
    ids = {k: c["id"] for k, c in by_key(gallery(coach, ready)).items()}
    coach.patch(f"/api/videos/{ready['id']}/players/clusters/{ids[2]}", json={"number": 3})
    r = coach.post(f"/api/videos/{ready['id']}/players/find")
    assert r.status_code == 202 and r.json()["status"] == "queued"
    job = claim(analyser, "players")
    complete(analyser, job, players_result(job, store))
    state = by_key(gallery(coach, ready))
    assert state[2]["number"] == 3 and state[2]["id"] != ids[2]  # read "3" again: kept
    assert state[1]["number"] is None


# --- attribution ------------------------------------------------------------------------------


def test_actions_get_the_player_nearest_the_ball_and_coach_numbers_stay(
    ready: Any, app: FastAPI, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    coach = club["coach"]
    url = f"/api/videos/{ready['id']}"
    loose, named = tag(12_050), tag(12_100, number=3)  # the coach's: without and with a number
    assert coach.post(f"{url}/changes", json={"events": [loose, named]}).status_code == 200
    dig = model_event(app, ready, 15_000, "them", code="D")
    hit = model_event(app, ready, 12_000, "us")
    kept = model_event(app, ready, 12_000, "us", number=1)  # a person named it already
    with_players(analyser, store)
    ids = {k: c["id"] for k, c in by_key(gallery(coach, ready)).items()}
    # Nothing named yet: no numbers anywhere, but the actions point at their clusters.
    state = gallery(coach, ready)
    assert by_key(state)[1]["actions"] == 4 and by_key(state)[3]["actions"] == 1
    assert event(app, hit).number is None
    assert state["attribution"]["identified"] == 2  # the coach's #3 and the named model one

    coach.patch(f"{url}/players/clusters/{ids[1]}", json={"number": 7})
    coach.patch(f"{url}/players/clusters/{ids[3]}", json={"number": 12})
    row = event(app, hit)
    assert row.number == 7 and row.member_id == 101 and row.version == 2
    assert row.track_ref is not None and row.track_ref.endswith(":1")
    assert row.source == "model" and row.review == "unreviewed"
    assert event(app, dig).number == 12  # theirs, at the far side, a low contact
    assert event(app, kept).number == 1  # never overwritten
    tags = {e["id"]: e for e in coach.get(f"{url}/tagging").json()["events"]}
    assert tags[loose["id"]]["number"] is None  # the coach's tag is never changed
    assert tags[named["id"]]["number"] == 3
    state = gallery(coach, ready)
    att = state["attribution"]
    assert att["actions"] == 5 and att["identified"] == 5 and att["suggested"] == 3
    # The coach's #3 at 12.1 s is where WiePlay sees #7: one compared, wrong.
    assert att["vs_coach"] == {"compared": 2, "correct": 0, "accuracy": 0.0}
    assert state["open_suggestions"] == 1

    # The coach takes the suggestion for the loose tag: now it is the coach's number.
    r = coach.post(f"{url}/players/apply", json={})
    assert r.status_code == 200 and r.json()["applied"] == 1
    tags = {e["id"]: e for e in coach.get(f"{url}/tagging").json()["events"]}
    assert tags[loose["id"]]["number"] == 7 and tags[loose["id"]]["member_id"] == 101
    assert r.json()["players"]["open_suggestions"] == 0

    # Taking the number away takes back only what WiePlay wrote.
    coach.patch(f"{url}/players/clusters/{ids[1]}", json={"number": None})
    assert event(app, hit).number is None
    assert event(app, kept).number == 1
    tags = {e["id"]: e for e in coach.get(f"{url}/tagging").json()["events"]}
    assert tags[loose["id"]]["number"] == 7  # applied: the coach's now

    analysis = coach.get(f"{url}/analysis").json()["players"]
    assert analysis["status"] == "done" and analysis["clusters"] == 4
    assert analysis["named"] == 1 and analysis["actions"] == 5


def test_a_number_a_coach_changed_is_never_taken_back(
    ready: Any, app: FastAPI, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    coach = club["coach"]
    url = f"/api/videos/{ready['id']}"
    hit = model_event(app, ready, 12_000, "us")
    with_players(analyser, store)
    ids = {k: c["id"] for k, c in by_key(gallery(coach, ready)).items()}
    coach.patch(f"{url}/players/clusters/{ids[1]}", json={"number": 7})
    row = event(app, hit)
    assert row.number == 7
    change = {"id": hit, "base_version": row.version, "code": "A", "t_ms": 12_000,
              "side": "us", "number": 3, "attributes": {}, "review": "unreviewed"}  # fmt: skip
    assert coach.post(f"{url}/changes", json={"events": [change]}).status_code == 200
    coach.patch(f"{url}/players/clusters/{ids[1]}", json={"number": 11})
    assert event(app, hit).number == 3
    coach.post(f"{url}/players/swap-teams")
    assert event(app, hit).number == 3


def test_new_tags_are_attributed_on_the_next_read(
    ready: Any, app: FastAPI, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    coach = club["coach"]
    url = f"/api/videos/{ready['id']}"
    with_players(analyser, store)
    ids = {k: c["id"] for k, c in by_key(gallery(coach, ready)).items()}
    coach.patch(f"{url}/players/clusters/{ids[3]}", json={"number": 12})
    dig = model_event(app, ready, 15_000, "them", code="D")
    assert event(app, dig).number is None
    state = gallery(coach, ready)  # the read attributes what is new
    assert event(app, dig).number == 12 and by_key(state)[3]["actions"] == 1


# --- geometry -------------------------------------------------------------------------------


def test_box_distance_is_in_box_heights() -> None:
    box = (0.4, 0.2, 0.5, 0.6)
    assert box_distance((0.45, 0.3), box) == 0
    assert box_distance((0.45, 0.1), box) == pytest.approx(0.25)
    assert box_distance((0.3, 0.4), box) == pytest.approx(0.25)


def test_nearest_picks_the_closer_player_and_its_confidence_reflects_the_margin() -> None:
    from wieplay.core.players import Candidate

    a, b = Candidate(1, (0.40, 0.2, 0.45, 0.6)), Candidate(2, (0.60, 0.2, 0.65, 0.6))
    pick = nearest((0.44, 0.3), [a, b])
    assert pick is not None and pick.track_id == 1 and pick.method == "image"
    close = nearest((0.52, 0.3), [a, b])
    assert close is not None and close.confidence < pick.confidence
    assert nearest((0.5, 0.5), []) is None


def test_nearest_on_the_court_with_a_camera() -> None:
    from tests.test_court import camera_points
    from wieplay.core import court
    from wieplay.core.players import Candidate

    sol = court.solve(camera_points((4.5, -6.0, 5.0), 1.1, list(court.LANDMARKS)), 16 / 9)
    cam = sol.camera
    assert cam is not None
    to_image = sol.court_to_image

    def feet_box(x: float, y: float) -> tuple[float, float, float, float]:
        u, v = court.apply(to_image, (x, y))
        return (u - 0.01, v - 0.08, u + 0.01, v)

    a, b = Candidate(1, feet_box(2.0, 6.0)), Candidate(2, feet_box(6.0, 6.5))
    ball = cam.project(2.2, 6.3, 2.3)
    assert ball is not None
    pick = nearest(ball, [a, b], to_court=sol.image_to_court, camera=cam, height=2.3)
    assert pick is not None and pick.method == "court" and pick.track_id == 1
    assert pick.distance < 0.5


def test_ball_contacts_skip_bounces() -> None:
    ball = Ball.from_bytes(gzip.compress(json.dumps(TRACK).encode()))
    found = ball.contact_near(15_300, 500)
    assert found is not None and found[0] == 15_000
    assert ball.contact_near(15_900, 500) is None  # too far from the tag
    assert ball.contact_near(16_050, 100) is None  # the bounce at 16 s is the floor
    assert ball.at(10_000) == pytest.approx((0.4, 0.6))


# --- players v2: the GPU worker without the NAS (PLAN §8.2) ------------------------------------


def test_a_gpu_worker_reads_the_rendition_and_writes_to_the_bucket(
    ready: Any, app: FastAPI, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    trajectory(analyser, store)
    job = claim(analyser, "players")
    params, vid = job["params"], ready["id"]
    assert params["rendition"] == "1080p"
    found = analyser.post(f"/api/worker/jobs/{job['id']}/inputs").json()
    rendition = found["rendition"]
    assert rendition["init"] == f"media/{vid}/1080p/init.mp4"
    assert rendition["segments"] == [
        [f"media/{vid}/1080p/seg_00000.m4s", 0, 4000],
        [f"media/{vid}/1080p/seg_00001.m4s", 4000, 4000],
    ]
    # Shirt numbers only (no names): readings of other numbers are misreads.
    assert 7 in found["roster"]["us"] and all(isinstance(n, int) for n in found["roster"]["us"])

    def sign(op: str, *keys: str) -> int:
        r = analyser.post(f"/api/worker/jobs/{job['id']}/sign", json={"op": op, "keys": keys})
        return r.status_code

    assert sign("get", rendition["init"], rendition["segments"][1][0]) == 200
    assert sign("get", f"media/{vid}/540p/seg_00000.m4s") == 403
    assert sign("get", f"media/{vid}/1080p/../thumbs/sprite_0.jpg") == 403
    assert sign("get", f"{params['prefix']}index.json.gz") == 403
    assert sign("put", f"{params['prefix']}tracks.npz") == 200
    assert sign("put", f"media/{vid}/1080p/seg_00000.m4s") == 403

    result = players_result(job, store)
    prefix = params["prefix"]
    result["tracks_artifact"] = {"key": f"{prefix}tracks.npz", "sha256": "f" * 64, "bytes": 99}
    del result["index"]["nas_path"], result["sheets"]["nas_dir"]
    for c in result["clusters"]:
        c["confidence"] = 0.3 if c["key"] == 1 else 0.9
    bad = {**result, "tracks_artifact": {**result["tracks_artifact"], "key": f"{prefix}x.npz"}}
    complete(analyser, job, bad, status=422)  # and the job failed with it
    from pydantic import ValidationError

    from wieplay.core.players import FileIn

    with pytest.raises(ValidationError):  # on the NAS or in the bucket, not both
        FileIn(nas_path="x/y.npz", key=f"{prefix}tracks.npz", sha256="f" * 64, bytes=1)
    with pytest.raises(ValidationError):
        FileIn(sha256="f" * 64, bytes=1)


def test_bucket_results_are_stored_with_their_keys_and_the_cards_confidence(
    ready: Any, app: FastAPI, club: dict[str, TestClient], analyser: TestClient, store: FakeStore
) -> None:
    from wieplay.core.models import AnalysisArtifact

    trajectory(analyser, store)
    job = claim(analyser, "players")
    prefix = job["params"]["prefix"]
    result = players_result(job, store)
    result["tracks_artifact"] = {"key": f"{prefix}tracks.npz", "sha256": "f" * 64, "bytes": 99}
    del result["index"]["nas_path"], result["sheets"]["nas_dir"]
    for c in result["clusters"]:
        c["confidence"] = 0.3 if c["key"] == 1 else 0.9
    complete(analyser, job, result)
    with app.state.sessionmaker() as db:
        arts = {a.kind: a for a in db.scalars(select(AnalysisArtifact))}
    assert arts["player_tracks"].nas_path == f"s3:{prefix}tracks.npz"
    assert arts["player_tracks"].stats["object_key"] == f"{prefix}tracks.npz"
    assert arts["player_index"].nas_path == f"s3:{prefix}index.json.gz"
    state = gallery(club["coach"], ready)
    clusters = by_key(state)
    assert clusters[1]["confidence"] == 0.3 and clusters[1]["uncertain"] is True
    assert clusters[2]["uncertain"] is False and state["find_minutes"] == 120
    summary = club["coach"].get(f"/api/videos/{ready['id']}/analysis").json()["players"]
    assert summary["uncertain"] == 1
    # Named or merged by the coach: no longer the job's guess.
    r = club["coach"].patch(
        f"/api/videos/{ready['id']}/players/clusters/{clusters[1]['id']}",
        json={"number": 7, "not_player": False},
    )
    assert r.status_code == 200 and by_key(r.json())[1]["uncertain"] is False
    r = club["coach"].post(
        f"/api/videos/{ready['id']}/players/merge",
        json={"into": clusters[3]["id"], "sources": [clusters[4]["id"]]},
    )
    assert r.status_code == 200 and by_key(r.json())[3]["confidence"] is None


def test_a_gpu_worker_gives_a_job_back_without_losing_an_attempt(
    ready: Any, app: FastAPI, analyser: TestClient, store: FakeStore
) -> None:
    from wieplay.core.models import Job

    trajectory(analyser, store)
    job = claim(analyser, "players")
    r = analyser.post(
        f"/api/worker/jobs/{job['id']}/fail",
        json={"error": "GPU busy", "retryable": True, "retry_after_s": 1800, "given_back": True},
    )
    assert r.status_code == 204
    with app.state.sessionmaker() as db:
        row = db.get(Job, uuid.UUID(job["id"]))
        assert row is not None
        assert row.status == "queued" and row.attempts == 0 and row.not_before is not None
    # Waiting until then; a restarted GPU worker frees its leases without taking a job.
    r = analyser.post(
        "/api/worker/claim", json={"caps": CAPS, "wait": False, "max": 0, "restarted": True}
    )
    assert r.status_code == 200 and r.json()["jobs"] == []
