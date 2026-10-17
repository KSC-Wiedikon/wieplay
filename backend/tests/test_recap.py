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
    }
    assert second["bug"]["label"] == "Matchball" and second["bug"]["after"] == "1:1"
    assert (
        export["duration_ms"]
        == sum(p["end_ms"] - p["start_ms"] for p in params["pieces"]) + recap.TITLE_MS
    )

    english = coach.post("/api/exports", json={**body, "lang": "en"}).json()
    assert english["title"].endswith("(recap)")


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
    plays[0].kinds.append("match_point")
    plays[0].score = 0.1  # the least of them, still in
    chosen = recap._choose(plays)
    assert plays[0] in chosen
    assert sum(p.length_ms for p in chosen) <= recap.MAX_MS
    assert sum(p.length_ms for p in chosen if "match_point" not in p.kinds) <= recap.TARGET_MS


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
