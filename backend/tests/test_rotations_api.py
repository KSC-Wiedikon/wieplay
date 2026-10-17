"""Step 5a/5b over the API: a set's starting lineup, rotations and the setter analysis in
GET /api/videos/{id}/stats and /api/teams/{id}/stats, and their CSV parts."""

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
    MEDIA_CAPS,
    create_match,
    run_pipeline,
    upload_all,
    worker_client,
)


@pytest.fixture
def ready(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> dict[str, Any]:
    video = upload_all(club["coach"], store, create_match(club["coach"]).json())
    run_pipeline(worker_client(app, club["admin"], MEDIA_CAPS), MEDIA_CAPS, store)
    return video


def tag(code: str, t_ms: int, grade: str | None = None, side: str = "us", number: int = 7,
        **zones: int) -> dict[str, Any]:  # fmt: skip
    attributes: dict[str, Any] = {"grade": grade} if grade else {}
    return {"id": str(uuid.uuid4()), "code": code, "t_ms": t_ms, "side": side,
            "number": number, "attributes": {**attributes, **zones}}  # fmt: skip


LINEUP = [3, 7, 11, 9, 1, 14]  # setter #3 in zone I


def tag_a_set(client: TestClient, video_id: str) -> dict[str, Any]:
    """Set 1 from rotation 1 with a lineup, three rallies:
    1. they serve, #1 passes perfectly, #3 sets, #7 kills from 4: side-out (to rotation 6)
    2. we serve (#7), they attack, #1 digs, #3 sets, #11 from 3 into the block
    3. they serve, #7 passes poorly, #3 sets, #7 from 4 in play; we win the rally (side-out)
    """
    period = {"id": str(uuid.uuid4()), "no": 1, "start_ms": 0, "first_serve": "them",
              "start_rotation": 1, "start_lineup": LINEUP}  # fmt: skip
    rallies = [
        {"id": str(uuid.uuid4()), "kind": "rally", "start_ms": start, "end_ms": start + 8_000,
         "won_by": won}
        for start, won in ((1_000, "us"), (21_000, "them"), (41_000, "us"))
    ]  # fmt: skip
    events = [
        tag("S", 1_100, "+", side="them", number=12),
        tag("R", 2_500, "#", number=1),
        tag("E", 3_500, number=3),
        tag("A", 4_000, "#", zone_from=4),
        tag("S", 21_100, "-"),
        tag("A", 23_000, "+", side="them", number=4),
        tag("D", 24_000, "+", number=1),
        tag("E", 25_000, number=3),
        tag("A", 25_500, "/", number=11, zone_from=3),
        tag("B", 25_600, "#", side="them", number=9),
        tag("S", 41_100, "+", side="them", number=12),
        tag("R", 42_500, "-"),
        tag("E", 43_500, number=3),
        tag("A", 44_000, "+", zone_from=4),
    ]
    out = client.post(
        f"/api/videos/{video_id}/changes",
        json={"periods": [period], "segments": rallies, "events": events},
    )
    assert out.status_code == 200, out.text
    return out.json()


def test_the_lineup_is_kept_and_checked(ready: dict[str, Any], club: dict[str, TestClient]) -> None:
    coach = club["coach"]
    body = tag_a_set(coach, ready["id"])
    (period,) = body["periods"]
    assert period["start_lineup"] == LINEUP and period["start_rotation"] == 1
    url = f"/api/videos/{ready['id']}/changes"
    keep = {k: period[k] for k in ("id", "no", "start_ms", "first_serve")}
    # Left out, the lineup stays; null clears it; a zone may stay empty.
    out = coach.post(url, json={"periods": [{**keep, "base_version": period["version"]}]})
    assert out.json()["periods"][0]["start_lineup"] == LINEUP
    version = out.json()["periods"][0]["version"]
    partly = [3, None, 11, None, None, 14]
    out = coach.post(url, json={"periods": [{**keep, "base_version": version,
                                             "start_lineup": partly}]})  # fmt: skip
    assert out.json()["periods"][0]["start_lineup"] == partly
    version = out.json()["periods"][0]["version"]
    for bad in ([3, 7, 11, 9, 1], [3, 3, 11, 9, 1, 14], [3, 7, 11, 9, 1, 100]):
        wrong = coach.post(url, json={"periods": [{**keep, "base_version": version,
                                                   "start_lineup": bad}]})  # fmt: skip
        assert wrong.status_code == 422, bad
    out = coach.post(url, json={"periods": [{**keep, "base_version": version,
                                             "start_lineup": [None] * 6}]})  # fmt: skip
    assert out.json()["periods"][0]["start_lineup"] is None
    tagging = coach.get(f"/api/videos/{ready['id']}/tagging").json()
    assert tagging["periods"][0]["start_lineup"] is None


def test_match_rotations_and_setter(
    ready: dict[str, Any], club: dict[str, TestClient], as_: Any
) -> None:
    tag_a_set(club["coach"], ready["id"])
    body = as_("player-h3").get(f"/api/videos/{ready['id']}/stats").json()
    assert [c["key"] for c in body["rotation_columns"]][:4] == [
        "serve_ta", "serve_ace", "serve_err", "serve_eff",
    ]  # fmt: skip
    lines = {line["rotation"]: line for line in body["match"]["rotations"]}
    one, six = lines[1], lines[6]
    assert (one["rallies"], one["side_out_won"], one["values"]["att_hit"]) == (1, 1, 1.0)
    assert one["lineup"] == LINEUP and one["lineup_varies"] is False
    # Rotation 6: the player from zone II (#7) serves; #3 is in zone VI.
    assert six["lineup"] == [7, 11, 9, 1, 14, 3]
    assert (six["rallies"], six["break_point_of"], six["side_out_won"]) == (2, 1, 1)
    assert (six["values"]["serve_ta"], six["values"]["rec_ta"], six["values"]["att_ta"]) == (
        1, 1, 2,
    )  # fmt: skip
    assert [p["won_by"] for p in six["plays"]] == ["them", "us"]
    (first,) = body["sets"]
    assert (first["start_rotation"], first["start_lineup"]) == (1, LINEUP)
    assert first["rotations"] == body["match"]["rotations"]

    setter = body["match"]["setter"]
    assert (setter["sets"], setter["attacks"]) == (3, 3)
    seven, eleven = setter["by_attacker"]
    assert (seven["number"], seven["name"], seven["attacks"], seven["kills"]) == (
        7, "Alex Muster", 2, 1,
    )  # fmt: skip
    assert seven["share"] == pytest.approx(2 / 3) and seven["zones"] == {"left": 2}
    assert seven["filter"] == {"attacker": 7} and seven["hit_pct"] == 0.5
    assert (eleven["blocked"], eleven["hit_pct"]) == (1, -1.0)
    assert {q["key"]: q["sets"] for q in setter["by_reception"]} == {
        "perfect": 1, "poor": 1, "transition": 1,
    }  # fmt: skip
    rotations = {r["key"]: r for r in setter["by_rotation"]}
    assert (rotations["1"]["sets"], rotations["6"]["sets"], rotations["6"]["zones"]) == (
        1, 2, {"middle": 1, "left": 1},
    )  # fmt: skip
    (setter_line,) = setter["by_setter"]
    assert (setter_line["number"], setter_line["name"]) == (3, "Sam Beispiel")
    plays = setter["plays"]
    assert [(p["set_no"], p["attacker"], p["zone"], p["quality"], p["rotation"], p["outcome"])
            for p in plays] == [
        (1, 7, "left", "perfect", 1, "kill"),
        (1, 11, "middle", "transition", 6, "blocked"),
        (1, 7, "left", "poor", 6, "play"),
    ]  # fmt: skip
    assert body["sets"][0]["setter"]["plays"] is None
    assert body["sets"][0]["setter"]["by_attacker"] == setter["by_attacker"]


def test_season_rotations_and_setter(ready: dict[str, Any], club: dict[str, TestClient]) -> None:
    tag_a_set(club["coach"], ready["id"])
    body = club["coach"].get(f"/api/teams/{H3}/stats").json()
    season = body["season_box"]
    lines = {line["rotation"]: line for line in season["rotations"]}
    assert lines[6]["rallies"] == 2 and lines[6]["plays"] == []  # not played on the team page
    assert lines[1]["lineup"] == LINEUP
    assert body["rotation_columns"][0]["key"] == "serve_ta"
    setter = season["setter"]
    assert setter["plays"] is None and setter["attacks"] == 3
    assert setter["by_attacker"][0]["filter"] == {"attacker_member": 101}
    assert setter["by_attacker"][0]["name"] == "Alex Muster"


def test_rotation_and_setter_csv(ready: dict[str, Any], club: dict[str, TestClient]) -> None:
    tag_a_set(club["coach"], ready["id"])
    url = f"/api/videos/{ready['id']}/stats.csv"

    def read(response: Any) -> list[dict[str, str]]:
        assert response.status_code == 200, response.text
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        return [dict(zip(rows[0], row, strict=True)) for row in rows[1:]]

    out = club["coach"].get(f"{url}?part=rotations")
    assert 'filename="nomal-rotations-' in out.headers["content-disposition"]
    rows = read(out)
    # The match and set 1, six rotations each.
    assert [(r["Set"], r["Rotation"]) for r in rows][:7] == [
        *(("Match", str(n)) for n in range(1, 7)), ("1", "1"),
    ]  # fmt: skip
    assert rows[0]["Lineup (zones I-VI)"] == "3 7 11 9 1 14" and rows[0]["Side-out %"] == "100.0"
    out = club["coach"].get(f"{url}?part=setter&lang=de")
    assert 'filename="nomal-setter-' in out.headers["content-disposition"]
    rows = read(out)
    first = rows[0]
    assert (first["Satz"], first["Aufteilung"], first["Option"], first["Angriffe"]) == (
        "Spiel", "Angreifer", "#7 Alex Muster", "2",
    )  # fmt: skip
    assert first["Anteil der Angriffe %"] == "66.7" and first["Links (4)"] == "2"
    assert club["coach"].get(f"{url}?part=heatmap").status_code == 422
    # The box score stays the default.
    assert read(club["coach"].get(url))[0]["Set"] == "Match"
    season = club["coach"].get(f"/api/teams/{H3}/stats.csv?part=setter")
    assert {r["Set"] for r in read(season)} == {"Season"}
    season = club["coach"].get(f"/api/teams/{H3}/stats.csv?part=rotations")
    assert len(read(season)) == 6
