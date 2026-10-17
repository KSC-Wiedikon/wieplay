"""The game recap (PLAN §7.7, nomal/core/recap.py): the best plays from the facts (kills, aces,
blocks, long rallies, set and match points), the rally finder's rallies when nothing is tagged,
the title card and the score bug in the viewer's language, the export job that renders it."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nomal.core import recap
from nomal.core.models import Video
from tests.fakes import FakeStore
from tests.media_helpers import claim, worker_client
from tests.test_analysis_api import ANALYSE_CAPS, claim_kind, complete, make_ready, payload
from tests.test_library import EXPORT_CAPS, tagged  # noqa: F401 - the fixture


def test_a_recap_is_cut_from_the_facts(
    app: FastAPI,
    tagged: dict[str, Any],  # noqa: F811 - the fixture of test_library
    club: dict[str, TestClient],
    as_: Any,
) -> None:
    coach = club["coach"]
    body = {"kind": "recap", "video_id": tagged["id"], "lang": "de"}
    assert as_("player-h3").post("/api/exports", json=body).status_code == 403
    created = coach.post("/api/exports", json=body)
    assert created.status_code == 201, created.text
    export = created.json()
    assert export["kind"] == "recap" and export["title"].endswith("(Zusammenfassung)")

    w = worker_client(app, club["admin"], EXPORT_CAPS)
    [job] = claim(w, EXPORT_CAPS)
    params = job["params"]
    card = params["recap"]["card"]
    # Game k=1 is an away game: the home team is written first, as Wiedisync has it.
    assert card["eyebrow"] == "SPIELZUSAMMENFASSUNG"
    assert card["matchup"] == "Testclub B \N{EN DASH} KSC Wiedikon H3"
    assert card["result"] == "3:1" and card["sets"] == "25:20 · 23:25 · 25:18 · 25:22"
    assert card["when"].endswith("· Auswärtshalle")
    assert card["footer"] == "Die besten Szenen · WiePlay · KSC Wiedikon"
    assert params["recap"]["card_ms"] == recap.TITLE_MS and params["recap"]["source"] == "facts"
    first, second = params["pieces"]
    # Rally 1 (10-20 s): our kill by #7; rally 2 (21-30 s): the last of the match.
    assert (first["start_ms"], first["end_ms"]) == (10_000 - recap.PRE_MS, 20_000 + recap.POST_MS)
    assert second["start_ms"] == first["end_ms"]  # no footage twice
    assert first["bug"] == {
        "set": "Satz 1",
        "before": "0:0",
        "after": "0:1",  # home (them) first
        "point_ms": 20_000,
        "label": "Angriffspunkt #7",
        "teams": "Testclub B \N{EN DASH} KSCW H3",  # whose score is whose, short names
    }
    assert second["bug"]["label"] == "Matchball" and second["bug"]["after"] == "1:1"
    assert (
        export["duration_ms"]
        == sum(p["end_ms"] - p["start_ms"] for p in params["pieces"]) + recap.TITLE_MS
    )

    english = coach.post("/api/exports", json={**body, "lang": "en"}).json()
    assert english["title"].endswith("(recap)")


def test_a_friendly_s_card_names_the_opponent_not_the_title(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore
) -> None:
    video = make_ready(app, club, store, k=0)
    with app.state.sessionmaker() as db:
        row = db.get(Video, uuid.UUID(video["id"]))
        assert row is not None
        row.game_id, row.title = None, "Testspiel gegen Volero Zürich"
        db.commit()
        side, _ = recap.sides(db, row, "de")
        assert (side.left, side.right, side.left_is_us) == ("KSCW H3", "Volero Zürich", True)
        assert side.teams == "KSCW H3 \N{EN DASH} Volero Zürich"
        row.name_them = "Volero"  # a coach's name wins
        assert recap.sides(db, row, "de")[0].right == "Volero"
        row.title, row.name_them = "Testspiel", None  # nobody named: "Gegner"
        assert recap.sides(db, row, "de")[0].right == "Gegner"


def test_without_tags_the_rally_finder_s_longest_rallies(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore
) -> None:
    video = make_ready(app, club, store, k=0)
    analyser = worker_client(app, club["admin"], ANALYSE_CAPS)
    job = claim_kind(analyser, "analyse")
    # 60 rallies of 5 s and five long ones (20 s): proposals nobody has answered.
    spans = [(60_000 + k * 20_000, 65_000 + k * 20_000) for k in range(60)]
    spans += [(2_000_000 + k * 40_000, 2_020_000 + k * 40_000) for k in range(5)]
    complete(analyser, job, payload(job, spans))
    with app.state.sessionmaker() as db:
        row = db.get(Video, uuid.UUID(video["id"]))
        assert row is not None
        chosen = recap.select_plays(db, row)
    assert chosen.source == "rallies"
    longs = [p for p in chosen.plays if "long" in p.kinds]
    assert len(longs) == 5
    assert chosen.total_ms <= recap.MAX_MS
    assert [p.start_ms for p in chosen.plays] == sorted(p.start_ms for p in chosen.plays)


def test_nothing_to_choose_from(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore
) -> None:
    video = make_ready(app, club, store, k=0)
    r = club["coach"].post("/api/exports", json={"kind": "recap", "video_id": video["id"]})
    assert r.status_code == 409


def test_the_choice_keeps_to_the_length_and_the_match_point() -> None:
    plays = [
        recap.Play(
            start_ms=k * 30_000,
            end_ms=k * 30_000 + 12_000,
            point_ms=k * 30_000 + 10_000,
            score=1.0 + k % 3,
        )
        for k in range(40)
    ]
    plays[0].ends = "match_point"
    plays[0].score = 0.1  # the least of them, still in
    chosen = recap._choose(plays)
    assert plays[0] in chosen
    assert sum(p.length_ms for p in chosen) <= recap.MAX_MS
    assert sum(p.length_ms for p in chosen if p.ends != "match_point") <= recap.TARGET_MS


@pytest.mark.parametrize(
    ("kinds", "number", "lang", "text"),
    [
        (["kill"], 7, "en", "Kill #7"),
        (["ace", "set_point"], None, "de", "Satzball · Ass"),
        (["long"], None, "en", "Long rally"),
        (["long", "match_point"], None, "en", "Match point"),
        ([], None, "en", None),
    ],
)
def test_labels(kinds: list[Any], number: int | None, lang: Any, text: str | None) -> None:
    play = recap.Play(start_ms=0, end_ms=1, point_ms=1, kinds=kinds, number=number)
    assert recap.label(play, lang) == text


def test_dates_in_both_languages() -> None:
    assert recap.long_date(date(2026, 9, 17), "de") == "Donnerstag, 17. September 2026"
    assert recap.long_date(date(2026, 9, 17), "en") == "Thursday, 17 September 2026"


# --- the score bug shows facts only -------------------------------------------------------------


def recap_job(app: FastAPI, club: dict[str, TestClient], video: dict[str, Any]) -> dict[str, Any]:
    created = club["coach"].post(
        "/api/exports", json={"kind": "recap", "video_id": video["id"], "lang": "de"}
    )
    assert created.status_code == 201, created.text
    [job] = claim(worker_client(app, club["admin"], EXPORT_CAPS), EXPORT_CAPS)
    return job["params"]


def test_bug_set_and_score_facts(
    app: FastAPI,
    tagged: dict[str, Any],  # noqa: F811 - the fixture of test_library
    club: dict[str, TestClient],
) -> None:
    """A coach's set, the coach's winners: the set and the score."""
    first, second = (p["bug"] for p in recap_job(app, club, tagged)["pieces"])
    assert (first["set"], first["before"], first["after"]) == ("Satz 1", "0:0", "0:1")
    assert (second["set"], second["before"], second["after"]) == ("Satz 1", "0:1", "1:1")
    assert second["label"] == "Matchball"


def test_bug_set_fact_score_not(
    app: FastAPI, club: dict[str, TestClient], store: FakeStore
) -> None:
    """A coach's set, but nobody said who won the rallies: "Satz 1" and the play, no score."""
    from tests.media_helpers import MEDIA_CAPS, create_match, run_pipeline, upload_all

    coach = club["coach"]
    video = upload_all(coach, store, create_match(coach).json())
    run_pipeline(worker_client(app, club["admin"], MEDIA_CAPS), MEDIA_CAPS, store)
    tag = {"id": str(uuid.uuid4()), "code": "A", "t_ms": 15_000, "side": "us", "number": 7,
           "attributes": {"grade": "#"}}  # fmt: skip
    body = {
        "periods": [{"id": str(uuid.uuid4()), "no": 1, "start_ms": 0}],
        "segments": [
            {"id": str(uuid.uuid4()), "kind": "rally", "start_ms": 10_000, "end_ms": 20_000},
            {"id": str(uuid.uuid4()), "kind": "rally", "start_ms": 31_000, "end_ms": 52_000},
        ],
        "events": [tag],
    }
    assert coach.post(f"/api/videos/{video['id']}/changes", json=body).status_code == 200
    bugs = [p["bug"] for p in recap_job(app, club, video)["pieces"]]
    assert bugs[0] == {"set": "Satz 1", "before": "", "after": "", "point_ms": 20_000,
                       "label": "Angriffspunkt #7"}  # fmt: skip
    assert all(b["before"] == b["after"] == "" for b in bugs)
    assert all(b["label"] not in ("Satzball", "Matchball") for b in bugs)  # not a fact either


def test_bug_neither_a_fact(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> None:
    """The scoring's own sets (no official score to check them) and its winners: the play
    only; no set point the scoring merely guessed."""
    video = make_ready(app, club, store, k=3)  # today's game: no result yet
    analyser = worker_client(app, club["admin"], ANALYSE_CAPS)
    job = claim_kind(analyser, "analyse")
    spans = [(60_000 + k * 20_000, 65_000 + k * 20_000) for k in range(40)]
    spans += [(1_000_000 + k * 40_000, 1_018_000 + k * 40_000) for k in range(5)]
    complete(analyser, job, payload(job, spans))
    params = recap_job(app, club, video)
    bugs = [p.get("bug") for p in params["pieces"]]
    assert bugs and all(b is not None for b in bugs)
    for b in bugs:
        assert (b["set"], b["before"], b["after"]) == ("", "", "")
        assert b["label"] in ("Langer Ballwechsel", "")
    assert params["recap"]["card"]["sets"] is None


def test_bug_text_for_each_case() -> None:
    side = recap.Sides("A", "B", True)
    full = recap.Play(start_ms=0, end_ms=1, point_ms=1, kinds=["kill"], number=7, set_no=2,
                      before=(21, 20), after=(22, 20))  # fmt: skip
    assert recap.bug(full, side, "de") == {
        "set": "Satz 2", "before": "21:20", "after": "22:20", "point_ms": 1,
        "label": "Angriffspunkt #7", "teams": "A \N{EN DASH} B",
    }  # fmt: skip
    set_only = recap.Play(
        start_ms=0, end_ms=1, point_ms=1, kinds=["ace"], set_no=2, before=(21, 20)
    )
    out = recap.bug(set_only, side, "de")
    assert out is not None and (out["set"], out["before"], out["after"]) == ("Satz 2", "", "")
    label_only = recap.Play(start_ms=0, end_ms=1, point_ms=1, kinds=["block"])
    out = recap.bug(label_only, side, "en")
    assert out is not None and (out["set"], out["before"], out["after"], out["label"]) == (
        "",
        "",
        "",
        "Block point",
    )
    assert recap.bug(recap.Play(start_ms=0, end_ms=1, point_ms=1), side, "en") is None
