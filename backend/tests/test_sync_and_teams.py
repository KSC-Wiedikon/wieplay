from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from nomal.api.dev_fixtures import personas
from nomal.core.sync import BridgeError
from tests.conftest import login

P = personas(datetime.now(UTC).date())
H3, H3_PREV, D1, HERREN1, HERREN1_PREV = 9101, 9001, 9102, 9104, 9004


def sync(client: TestClient) -> dict[str, Any]:
    response = client.post("/api/admin/sync")
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def enable(client: TestClient, team_id: int, on: bool = True) -> None:
    response = client.put(f"/api/admin/teams/{team_id}", json={"enabled": on})
    assert response.status_code == 200, response.text
    assert response.json()["enabled"] is on


def count(db: Engine, table: str) -> int:
    with db.connect() as conn:
        return int(conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())  # noqa: S608


def test_admin_enables_teams_and_the_sync_mirrors_only_those(
    client: TestClient, db_engine: Engine
) -> None:
    login(client, P["admin"])
    first = sync(client)
    assert first["counts"]["teams"] == 10
    assert first["counts"]["members"] == 0  # nothing enabled: no personal data at all

    teams = client.get("/api/admin/teams").json()
    assert [t["sport"] for t in teams[:8]] == ["volleyball"] * 8
    assert teams[-1]["sport"] == "basketball"

    enable(client, H3)
    after = sync(client)
    assert after["counts"]["enabled"] == 2  # both seasons of the H3 lineage
    assert after["counts"]["members"] == 5
    assert count(db_engine, "wd_member") == 5

    enable(client, H3, on=False)
    assert sync(client)["counts"]["members"] == 0
    assert count(db_engine, "wd_member") == 0  # purged


def test_lineage_spans_seasons(client: TestClient) -> None:
    login(client, P["admin"])
    sync(client)
    enable(client, H3)
    sync(client)
    current = client.get(f"/api/teams/{H3}").json()
    previous = client.get(f"/api/teams/{H3_PREV}").json()
    assert current["lineage_id"] == previous["lineage_id"]
    assert current["season"] != previous["season"]


def test_members_see_their_teams_and_games(app: FastAPI, db_engine: Engine) -> None:
    with TestClient(app, follow_redirects=False) as admin:
        login(admin, P["admin"])
        sync(admin)
        enable(admin, H3)
        enable(admin, HERREN1)
        sync(admin)

    with TestClient(app, follow_redirects=False) as player:
        login(player, P["player-h3"])
        teams = player.get("/api/teams").json()
        assert {t["id"] for t in teams} == {H3, H3_PREV}
        assert next(t for t in teams if t["id"] == H3)["rel"] == ["captain", "player"]
        games = player.get(f"/api/teams/{H3}/games").json()
        assert len(games) == 6
        assert games == sorted(games, key=lambda g: (g["date"], g["time"]))
        assert player.get(f"/api/teams/{HERREN1}/games").status_code == 404
        assert player.get(f"/api/teams/{D1}").status_code == 404
        assert player.get("/api/admin/teams").status_code == 403

    with TestClient(app, follow_redirects=False) as coach:
        login(coach, P["coach-h3"])
        ids = {t["id"] for t in coach.get("/api/me").json()["teams"]}
        assert ids == {H3, H3_PREV, HERREN1, HERREN1_PREV}  # managers see every season
        assert len(coach.get(f"/api/teams/{HERREN1}/games").json()) == 6
        # Matches only (16.10.2026): no trainings in the API; the sync still mirrors them.
        assert coach.get(f"/api/teams/{HERREN1}/trainings").status_code == 404
        assert count(db_engine, "wd_training") > 0

    with TestClient(app, follow_redirects=False) as vb_admin:
        login(vb_admin, P["vb-sport-admin"])
        ids = {t["id"] for t in vb_admin.get("/api/teams").json()}
        assert ids == {H3, H3_PREV}


class BrokenBridge:
    def list_teams(self) -> Mapping[str, Any]:
        raise BridgeError("bridge unreachable: test")

    def snapshot(self, team_ids: Sequence[int]) -> Mapping[str, Any]:
        raise AssertionError("not reached")


def test_sync_failure_is_recorded(app: FastAPI) -> None:
    app.state.bridge_client_factory = BrokenBridge
    with TestClient(app, follow_redirects=False) as admin:
        login(admin, P["admin"])
        assert admin.post("/api/admin/sync").status_code == 502
        state = admin.get("/api/admin/sync").json()
        assert state["error"] == "bridge unreachable: test"
        assert state["last_ok"] is None


class LeakyBridge:
    """Returns personal data for a team that was not asked for."""

    def list_teams(self) -> Mapping[str, Any]:
        return {
            "version": 1,
            "teams": [
                {"id": 1, "name": "A", "sport": "volleyball", "season": "2026/27", "team_key": "a"},
            ],
        }

    def snapshot(self, team_ids: Sequence[int]) -> Mapping[str, Any]:
        return {
            "version": 1,
            "members": [],
            "games": [],
            "trainings": [],
            "memberships": [{"team": 2, "member": 1, "rel": "player"}],
        }


def test_snapshot_with_unrequested_teams_is_rejected(app: FastAPI, db_engine: Engine) -> None:
    app.state.bridge_client_factory = LeakyBridge
    with TestClient(app, follow_redirects=False) as admin:
        login(admin, P["admin"])
        sync(admin)
        enable(admin, 1)
        assert admin.post("/api/admin/sync").status_code == 502
    assert count(db_engine, "wd_member") == 0
