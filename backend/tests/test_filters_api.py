"""Saved filters (PLAN §9.7, wieplay/api/filters.py): everyone keeps their own searches; the
team's coaches and helpers share some with the team; who sees, changes and deletes them."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi.testclient import TestClient

from tests.media_helpers import D1, H3, HERREN1

H3_PREV = 9001
PIA = 111  # a D1 player


def save(client: TestClient, title: str, query: str, **body: Any) -> Any:
    return client.post("/api/filters", json={"title": title, "query": query, **body})


def titles(client: TestClient, **params: Any) -> list[str]:
    out = client.get("/api/filters", params=params)
    assert out.status_code == 200, out.text
    return [f["title"] for f in out.json()]


def test_own_filters_are_private(club: dict[str, TestClient], as_: Any) -> None:
    player = as_("player-h3")
    mine = save(player, "  My kills ", "#3 attack #", team_id=H3)
    assert mine.status_code == 201, mine.text
    body = mine.json()
    assert (body["title"], body["query"], body["visibility"]) == (
        "My kills",
        "#3 attack #",
        "private",
    )
    assert body["mine"] and body["can_edit"] and body["can_delete"]
    assert body["owner_name"] == "Alex Muster"
    assert save(player, "Anywhere", "reception -").status_code == 201  # no team needed
    assert titles(player) == ["Anywhere", "My kills"]
    assert titles(player, team_id=H3) == ["Anywhere", "My kills"]  # private ones go everywhere
    # Nobody else sees them, not even the coach.
    assert titles(club["coach"]) == [] and titles(club["coach"], team_id=H3) == []
    other = club["coach"]
    assert (
        other.put(f"/api/filters/{body['id']}", json={"title": "x", "query": "y"}).status_code
        == 404
    )
    assert other.delete(f"/api/filters/{body['id']}").status_code == 404

    renamed = player.put(
        f"/api/filters/{body['id']}", json={"title": "Kills", "query": "#3 attack #, set 3"}
    )
    assert renamed.status_code == 200, renamed.text
    assert (renamed.json()["title"], renamed.json()["query"]) == ("Kills", "#3 attack #, set 3")
    assert renamed.json()["lineage_id"] == body["lineage_id"]  # still saved for H3
    assert player.delete(f"/api/filters/{body['id']}").status_code == 204
    assert titles(player) == ["Anywhere"]
    assert player.delete(f"/api/filters/{body['id']}").status_code == 404


def test_coaches_and_helpers_share_with_the_team(club: dict[str, TestClient], as_: Any) -> None:
    coach, player = club["coach"], as_("player-h3")
    # Players keep their filters to themselves.
    assert save(player, "Ours", "set 3", visibility="team", team_id=H3).status_code == 403
    shared = save(coach, "Side-out, rotation 2", "side-out rotation 2", visibility="team",
                  team_id=H3)  # fmt: skip
    assert shared.status_code == 201, shared.text
    filter_id = shared.json()["id"]
    assert save(coach, "No team", "set 1", visibility="team").status_code == 422

    # The team sees it, every season, with who shared it; other teams don't.
    seen = player.get("/api/filters", params={"team_id": H3}).json()
    facts = [(f["title"], f["owner_name"], f["mine"], f["can_edit"], f["can_delete"]) for f in seen]
    assert facts == [("Side-out, rotation 2", "Jo Trainer", False, False, False)]
    assert titles(player, team_id=H3_PREV) == ["Side-out, rotation 2"]
    assert titles(player) == ["Side-out, rotation 2"]
    assert titles(as_("player-d1")) == []
    assert as_("player-d1").get("/api/filters", params={"team_id": H3}).status_code == 404
    assert titles(as_("basketball-player")) == []
    # The coach's view of another team they lead holds their private filters only.
    assert titles(coach, team_id=HERREN1) == []

    # Only the owner changes it; the player can't.
    assert (
        player.put(f"/api/filters/{filter_id}", json={"title": "x", "query": "y"}).status_code
        == 403
    )
    assert player.delete(f"/api/filters/{filter_id}").status_code == 403

    # A helper shares too, and can't delete the coach's.
    assert (
        club["coach"].post(f"/api/teams/{H3}/helpers", json={"member_id": PIA}).status_code == 201
    )
    pia = as_("player-d1")
    helped = save(pia, "Pia's", "them attack", visibility="team", team_id=H3)
    assert helped.status_code == 201, helped.text
    assert (
        save(pia, "D1", "set 1", visibility="team", team_id=D1).status_code == 403
    )  # a player there
    assert pia.delete(f"/api/filters/{filter_id}").status_code == 403
    # The coach (a team manager) tidies up the helper's shared one.
    assert coach.delete(f"/api/filters/{helped.json()['id']}").status_code == 204
    assert titles(player, team_id=H3) == ["Side-out, rotation 2"]

    # Unsharing makes it the coach's again; sharing back needs the right to share.
    private = coach.put(
        f"/api/filters/{filter_id}", json={"title": "Mine now", "query": "rotation 2"}
    )
    assert private.status_code == 200 and private.json()["visibility"] == "private"
    assert titles(player, team_id=H3) == []
    back = coach.put(
        f"/api/filters/{filter_id}",
        json={"title": "Mine now", "query": "rotation 2", "visibility": "team"},
    )
    assert back.status_code == 200 and back.json()["visibility"] == "team"
    assert titles(player, team_id=H3) == ["Mine now"]


def test_filter_input_is_checked(club: dict[str, TestClient]) -> None:
    coach = club["coach"]
    assert save(coach, "", "set 1").status_code == 422
    assert save(coach, "x" * 121, "set 1").status_code == 422
    assert save(coach, "Long", "x" * 301).status_code == 422
    assert save(coach, "Bad", "set 1", visibility="public").status_code == 422
    assert save(coach, "Gone", "set 1", team_id=999_999).status_code == 404
    assert (
        coach.put(f"/api/filters/{uuid.uuid4()}", json={"title": "a", "query": "b"}).status_code
        == 404
    )
