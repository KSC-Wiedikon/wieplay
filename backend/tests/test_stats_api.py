"""M3b: the stats endpoints (wieplay/api/stats.py): who may read them, the numbers, CSV."""

from __future__ import annotations

import csv
import io
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.fakes import FakeStore
from tests.media_helpers import (
    H3,
    HERREN1,
    MEDIA_CAPS,
    create_match,
    run_pipeline,
    upload_all,
    worker_client,
)


@pytest.fixture
def ready(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> dict[str, Any]:
    """A processed H3 match (game k=1: KSC away)."""
    video = upload_all(club["coach"], store, create_match(club["coach"]).json())
    run_pipeline(worker_client(app, club["admin"], MEDIA_CAPS), MEDIA_CAPS, store)
    return video


def tag(code: str, t_ms: int, grade: str | None = None, side: str = "us",
        number: int | None = 7) -> dict[str, Any]:  # fmt: skip
    return {
        "id": str(uuid.uuid4()),
        "code": code,
        "t_ms": t_ms,
        "side": side,
        "number": number,
        "attributes": {"grade": grade} if grade else {},
    }


def segment(start: int, end: int, won_by: str) -> dict[str, Any]:
    return {"id": str(uuid.uuid4()), "kind": "rally", "start_ms": start, "end_ms": end,
            "won_by": won_by}  # fmt: skip


def tag_a_set(client: TestClient, video_id: str) -> None:
    """Set 1, three rallies:
    1. we serve (#7), they receive, #3 kills: 1:0
    2. we serve (#7), an error: 1:1
    3. they serve (#12), #1 passes perfectly, #7 attacks into the block: 1:2
    """
    period = {"id": str(uuid.uuid4()), "no": 1, "start_ms": 0, "first_serve": "us"}
    rallies = [segment(1_000, 9_000, "us"), segment(11_000, 14_000, "them"),
               segment(16_000, 24_000, "them")]  # fmt: skip
    events = [
        tag("S", 1_200, "+"),
        tag("R", 2_500, "-", side="them", number=4),
        tag("A", 6_000, "#", number=3),
        tag("S", 11_200, "="),
        tag("S", 15_500, "!", side="them", number=12),  # the toss, 0.5 s before the rally
        tag("R", 17_000, "#", number=1),
        tag("A", 20_000, "/"),
        tag("B", 20_100, "#", side="them", number=9),
    ]
    out = client.post(
        f"/api/videos/{video_id}/changes",
        json={"periods": [period], "segments": rallies, "events": events},
    )
    assert out.status_code == 200, out.text


def test_match_stats(ready: dict[str, Any], club: dict[str, TestClient], as_: Any) -> None:
    empty = club["coach"].get(f"/api/videos/{ready['id']}/stats")
    assert empty.status_code == 200, empty.text
    assert empty.json()["tags"] == 0 and empty.json()["match"]["players"] == []
    assert empty.json()["sets"] == []

    tag_a_set(club["coach"], ready["id"])
    body = as_("player-h3").get(f"/api/videos/{ready['id']}/stats").json()  # players read it too
    assert (body["tags"], body["rallies"]) == (8, 3)
    columns = {c["key"]: c for c in body["columns"]}
    assert columns["att_kill"] == {"key": "att_kill", "group": "attack", "format": "count",
                                   "codes": ["A"], "grades": ["#"]}  # fmt: skip
    assert columns["serve_in"]["grades"] == ["+", "!", "-", "/", None]
    assert body["team_filters"]["side_out_won"] == {"serving": "them", "won_by": "us"}

    match = body["match"]
    rows = {r["number"]: r for r in match["players"]}
    assert list(rows) == [1, 3, 7]
    seven = rows[7]
    assert (seven["name"], seven["member_id"], seven["filter"]) == (
        "Alex Muster", 101, {"side": "us", "number": 7},
    )  # fmt: skip
    v = seven["values"]
    assert (v["serve_ta"], v["serve_ace"], v["serve_err"], v["serve_in"]) == (2, 0, 1, 1)
    assert v["serve_eff"] == pytest.approx(-0.5) and v["serve_rtg"] == pytest.approx(1.0)
    assert (v["att_ta"], v["att_blocked"], v["att_hit"], v["points"]) == (1, 1, -1.0, 0)
    assert v["rec_ta"] is None and v["blk_point"] is None
    assert rows[1]["values"]["rec_rtg"] == 3.0 and rows[1]["values"]["rec_perfect"] == 1.0
    assert rows[3]["values"]["points"] == 1 and rows[3]["values"]["att_kill_pct"] == 1.0
    assert match["team"]["filter"] == {"side": "us"}
    assert match["team"]["values"]["att_hit"] == pytest.approx(0.0)  # (1 - 0 - 1) / 2
    them = match["opponent"]["values"]
    assert (them["serve_ta"], them["blk_point"], them["rec_positive"]) == (1, 1, 0.0)

    summary = match["summary"]
    assert (summary["rallies"], summary["won"], summary["lost"]) == (3, 1, 2)
    assert (summary["break_point_won"], summary["break_point_of"]) == (1, 2)
    assert (summary["side_out_won"], summary["side_out_of"]) == (0, 1)
    assert summary["side_out_pct"] == 0.0 and summary["break_point_pct"] == 0.5
    (first,) = body["sets"]
    assert (first["no"], first["score_us"], first["score_them"]) == (1, 1, 2)
    assert first["summary"] == summary and len(first["players"]) == 3


def test_who_may_read_match_stats(
    ready: dict[str, Any], club: dict[str, TestClient], as_: Any, client: TestClient
) -> None:
    url = f"/api/videos/{ready['id']}/stats"
    assert client.get(url).status_code == 401
    assert as_("basketball-player").get(url).status_code == 404  # can't see the video
    assert as_("basketball-player").get(f"{url}.csv").status_code == 404
    assert as_("vb-sport-admin").get(url).status_code == 200
    assert club["admin"].get(url).status_code == 200
    assert club["coach"].get(f"/api/videos/{uuid.uuid4()}/stats").status_code == 404


def test_match_csv(ready: dict[str, Any], club: dict[str, TestClient]) -> None:
    tag_a_set(club["coach"], ready["id"])
    out = club["coach"].get(f"/api/videos/{ready['id']}/stats.csv")
    assert out.status_code == 200
    assert out.headers["content-type"] == "text/csv; charset=utf-8"
    disposition = out.headers["content-disposition"]
    assert disposition.startswith('attachment; filename="wieplay-stats-')
    assert disposition.endswith('.csv"') and " " not in disposition.split("filename=")[1]
    assert out.content.startswith(b"\xef\xbb\xbf")  # BOM for spreadsheet programs
    rows = list(csv.reader(io.StringIO(out.content.decode("utf-8-sig"))))
    head = rows[0]
    assert head[:3] == ["Set", "Number", "Player"]
    lines = [dict(zip(head, r, strict=True)) for r in rows[1:]]
    # The match (3 players, team, opponent), then set 1 (the same five).
    assert [line["Set"] for line in lines] == ["Match"] * 5 + ["1"] * 5
    seven = next(line for line in lines if line["Number"] == "7")
    assert (seven["Player"], seven["Serve attempts"], seven["Hitting %"]) == (
        "Alex Muster", "2", "-100.0",
    )  # fmt: skip
    # The team's and the opponent's rows by the teams' names (wieplay/core/names.py).
    team = next(line for line in lines if line["Player"] == "KSCW H3")
    assert (team["Rallies won"], team["Break-point %"]) == ("1", "50.0")
    assert sum(line["Player"] == ready["names"]["them"] for line in lines) == 2
    german = club["coach"].get(f"/api/videos/{ready['id']}/stats.csv?lang=de")
    head = next(csv.reader(io.StringIO(german.content.decode("utf-8-sig"))))
    assert head[:4] == ["Satz", "Nummer", "Spieler", "Punkte"]
    assert club["coach"].get(f"/api/videos/{ready['id']}/stats.csv?lang=fr").status_code == 422


def test_season_stats(ready: dict[str, Any], club: dict[str, TestClient], as_: Any) -> None:
    tag_a_set(club["coach"], ready["id"])
    ungraded = tag("S", 30_000)  # a serve without a grade: in play
    club["coach"].post(f"/api/videos/{ready['id']}/changes", json={"events": [ungraded]})
    player = as_("player-h3")
    body = player.get(f"/api/teams/{H3}/stats").json()
    assert (body["team_id"], body["videos"], body["tagged"]) == (H3, 1, 1)
    season = body["season_box"]
    seven = next(r for r in season["players"] if r["number"] == 7)
    assert seven["filter"] == {"side": "us", "member_id": 101} and seven["matches"] == 1
    assert (seven["values"]["serve_ta"], seven["values"]["serve_in"]) == (3, 2)
    # A season number opens a smart reel with the same query; null stands for "no grade".
    serve_in = next(c for c in body["columns"] if c["key"] == "serve_in")
    query = {"lineage_id": body["lineage_id"], "season": body["season"], "member_id": 101,
             "codes": serve_in["codes"], "grades": serve_in["grades"]}  # fmt: skip
    pieces = player.post("/api/reels/preview", json=query).json()
    assert [(p["code"], p["grade"]) for p in pieces] == [("S", "+"), ("S", None)]
    kills = player.post("/api/reels/preview", json={**query, "grades": ["#"]}).json()
    assert kills == []
    assert season["team"]["matches"] == 1 and season["summary"]["won"] == 1
    (match,) = body["matches"]
    assert match["video_id"] == ready["id"] and match["tagged"] is True
    assert match["summary"]["break_point_pct"] == 0.5
    # Game k=1 is a played away game: the result is turned into [us, them].
    assert match["result_us"] is not None and match["result_them"] is not None

    out = club["coach"].get(f"/api/teams/{H3}/stats.csv")
    assert out.status_code == 200 and out.headers["content-type"] == "text/csv; charset=utf-8"
    assert 'filename="wieplay-stats-h3-' in out.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(out.content.decode("utf-8-sig"))))
    assert rows[0][3] == "Matches" and {r[0] for r in rows[1:]} == {"Season"}


def test_who_may_read_season_stats(club: dict[str, TestClient], as_: Any) -> None:
    assert as_("basketball-player").get(f"/api/teams/{H3}/stats").status_code == 404
    assert as_("player-h3").get(f"/api/teams/{H3}/stats").json()["videos"] == 0
    assert club["coach"].get(f"/api/teams/{HERREN1}/stats").status_code == 409  # basketball
    assert club["coach"].get(f"/api/teams/{HERREN1}/stats.csv").status_code == 409
