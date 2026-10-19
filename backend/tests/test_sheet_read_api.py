"""Reading the scoresheet's photo through the API (PLAN §10.3a, SS4): a photo added while nothing
is typed queues the `sheet_read` job for the Lenovo's CPU; the worker may fetch that photo only;
its readings become drafts (never scored, audited with where it ran); a coach confirms a set and
it is a typed set like any other, scored, its cells kept as labelled examples. What people typed
is never proposed over. Who may read and confirm: whoever may type the sheet; the photo itself
stays under the photo rule. Synthetic data only (made-up readings of a made-up match)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tests.fakes import FakeStore
from tests.media_helpers import worker_client
from tests.test_analysis_api import ANALYSE_CAPS, make_ready
from tests.test_scoresheet_api import LINEUP_THEM, LINEUP_US, put, sheet_sets
from tests.test_scoresheet_photos import PIA, page, upload
from tests.test_scoring_api import analysed, match
from wieplay.core import scoresheet as ss_core
from wieplay.core import sheet_reading as sr
from wieplay.core.config import Settings
from wieplay.core.models import (
    AuditLog,
    Job,
    ModelRun,
    Scoresheet,
    ScoresheetImage,
    ScoresheetLabel,
)
from wieplay.ml import sheet_synth as ss

QUAD = [0.1, 0.1, 0.12, 0.1, 0.12, 0.13, 0.1, 0.13]
SHA = "a" * 64
H3 = 9101


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"analysis_train_delay_seconds": 0})


@pytest.fixture
def ready(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> Any:
    return make_ready(app, club, store, k=0)  # a home game, official sets OFFICIAL


@pytest.fixture
def analyser(app: FastAPI, club: dict[str, TestClient]) -> TestClient:
    return worker_client(app, club["admin"], ANALYSE_CAPS)


@pytest.fixture
def lenovo(app: FastAPI, club: dict[str, TestClient]) -> TestClient:
    """The Lenovo's worker: media caps and "analyse" (its main lane takes the sheet's reading)."""
    return worker_client(app, club["admin"], ["ingest", "transcode", "thumbs", "nas", "analyse"])


MAIN_KINDS = ["import", "ingest", "transcode", "thumbs", "purge", "export", "sheet_read"]


def claim_read(w: TestClient) -> dict[str, Any]:
    r = w.post(
        "/api/worker/claim",
        json={
            "caps": ["ingest", "transcode", "thumbs", "nas", "analyse"],
            "wait": False,
            "kinds": MAIN_KINDS,
        },
    )
    assert r.status_code == 200, r.text
    [job] = r.json()["jobs"]
    assert job["kind"] == "sheet_read"
    return job


def alts(value: Any, *, misread: bool = False) -> dict[str, Any]:
    if value is None:
        return {"alts": [[None, 0.97], [1, 0.02]], "quad": QUAD}
    other = (value + 10) % 40 if isinstance(value, int) else 1
    if misread:
        return {"alts": [[other, 0.6], [value, 0.35], [None, 0.03]], "quad": QUAD}
    return {"alts": [[value, 0.96], [other, 0.03]], "quad": QUAD}


def reading_of(
    sets: list[dict[str, Any]],
    *,
    misread: set[str] = frozenset(),
    letter: str = "A",
    holes: int = 0,
) -> dict[str, Any]:  # type: ignore[assignment]
    """What the worker would send for a photo of these sets: per cell its readings."""
    cells: dict[str, Any] = {}
    for doc in sets:
        no = doc["no"]
        for side in ("us", "them"):
            half = ss.half_of(no, side, letter)
            first = doc["first_serve"] == side
            grid = doc[f"boxes_{side}"]
            n = 0
            for p in range(1, 7):
                for b in range(1, 9):
                    v = grid[p - 1][b - 1]
                    if (p, b) == (1, 1) and not first:
                        v = "X"
                    key = f"s{no}.{half}.box.{p}.{b}"
                    if holes and isinstance(v, int) and n < holes:
                        v = None  # a scorer who wrote nothing there
                    n += 1
                    cells[key] = alts(v, misread=key in misread)
                lineup = doc[f"lineup_{side}"]
                cells[f"s{no}.{half}.lineup.{p}"] = alts(lineup[p - 1])
    return {
        "registration": {"page": [1, 0, 0, 0, 1, 0, 0, 0, 1], "score": 0.9},
        "cells": cells,
        "marks": {},
        "strength": {"1": 0.8},
    }


def result(
    job: dict[str, Any], reading: dict[str, Any] | None, error: str | None = None
) -> dict[str, Any]:
    [img] = job["params"]["images"]
    image: dict[str, Any] = {"id": img["id"]}
    if error:
        image["error"] = error
    else:
        image |= reading or {}
    return {
        "model": {"name": "wieplay-sheet-cells", "version": "test", "weights_sha256": SHA},
        "images": [image],
        "timing": {"total_s": 2.5},
        "host": "lenovo",
    }


def complete(w: TestClient, job: dict[str, Any], body: dict[str, Any]) -> None:
    r = w.post(f"/api/worker/jobs/{job['id']}/complete", json=body)
    assert r.status_code == 204, r.text


def sheet(client: TestClient, video: Any) -> Any:
    r = client.get(f"/api/videos/{video['id']}/scoresheet")
    assert r.status_code == 200, r.text
    return r.json()


def test_a_photo_is_read_on_club_hardware_and_confirmed_set_by_set(
    app: FastAPI,
    ready: Any,
    club: dict[str, TestClient],
    analyser: TestClient,
    lenovo: TestClient,
    store: FakeStore,
    db_engine: Engine,
) -> None:
    played, _ = match()
    analysed(analyser, played)
    coach = club["coach"]
    truth = sheet_sets(played)

    # Nothing typed yet: the photo is read by itself, on the Lenovo's CPU.
    assert upload(coach, ready).status_code == 201
    out = sheet(coach, ready)
    assert out["read"]["job"]["status"] == "queued" and out["can_read"] is True
    with Session(db_engine) as db:
        [queued] = db.scalars(select(Job).where(Job.kind == "sheet_read")).all()
        assert queued.required_caps == ["analyse"] and queued.priority == sr.PRIORITY
    # A worker without the cap, or the background lane (long jobs only), does not take it.
    media = worker_client(app, club["admin"], ["ingest", "transcode", "thumbs", "nas"])
    assert (
        media.post(
            "/api/worker/claim",
            json={"caps": ["ingest", "transcode", "thumbs", "nas"], "wait": False},
        ).json()["jobs"]
        == []
    )
    back = lenovo.post(
        "/api/worker/claim",
        json={"caps": ANALYSE_CAPS, "wait": False, "kinds": ["analyse", "scoreboard"]},
    )
    assert back.json()["jobs"] == []
    job = claim_read(lenovo)
    [img] = job["params"]["images"]
    key = img["key"]
    # It may fetch its photo, nothing else, and write nothing.
    ok = lenovo.post(f"/api/worker/jobs/{job['id']}/sign", json={"op": "get", "keys": [key]})
    assert ok.status_code == 200 and key in ok.json()["urls"]
    for op, keys in (
        ("get", [f"media/{ready['id']}/540p/seg_00001.m4s"]),
        ("put", [key]),
        ("delete", [key]),
    ):
        assert (
            lenovo.post(
                f"/api/worker/jobs/{job['id']}/sign", json={"op": op, "keys": keys}
            ).status_code
            == 403
        )

    # Two boxes read wrongly first (the right value second): the rules take the right one.
    first_box = "s1.L.box.1.1" if truth[0]["first_serve"] == "us" else "s1.L.box.2.1"
    misread = {first_box, "s2.R.box.3.2"}
    complete(lenovo, job, result(job, reading_of(truth, misread=misread)))
    out = sheet(coach, ready)
    assert out["status"] == "draft" and out["source"] == "read" and out["exists"]
    read = out["read"]
    assert read["drafts"] == [1, 2, 3, 4] and read["us_letter"] == "A" and read["worker"]
    for k, doc in enumerate(out["sets"]):
        assert (
            doc["boxes_us"] == truth[k]["boxes_us"] and doc["boxes_them"] == truth[k]["boxes_them"]
        )
        assert doc["first_serve"] == truth[k]["first_serve"]
        assert doc["lineup_us"] == LINEUP_US and doc["lineup_them"] == LINEUP_THEM
    cells1 = read["sets"]["1"]["cells"]
    field = "box.us.1.1" if truth[0]["first_serve"] == "us" else "box.us.2.1"
    flagged = [k for k, c in cells1.items() if c["check"]]
    # The scoring takes the drafts at once (PLAN §10.3) and resolves the boxes it is sure of:
    # a misread box the rules and the video decide is no longer to check ("decoded").
    assert flagged in ([field], []) and cells1[field]["why"] in ("rules", "decoded")
    assert cells1[field]["v"] == (truth[0]["boxes_us"][0][0] if field == "box.us.1.1" else
                                  truth[0]["boxes_us"][1][0])  # fmt: skip
    assert cells1[field]["quad"] == QUAD and cells1[field]["img"] == img["id"]
    assert 0 < read["to_check"] <= 2
    # Drafts are shown and checked, and scored as read: soft evidence (PLAN §10.3).
    assert [c["use"] for c in out["checks"]] == ["read"] * 4 and out["trusted"] is False
    with Session(db_engine) as db:
        photo = db.get(ScoresheetImage, img["id"])
        assert photo is not None and photo.registration and photo.reading["cells"]
        run = db.scalars(select(ModelRun).where(ModelRun.kind == "sheet_read")).one()
        assert run.weights_sha256 == SHA and run.metrics["sets"] == 4
        [audit] = db.scalars(select(AuditLog).where(AuditLog.action == "scoresheet_read")).all()
        assert audit.details["where"] == "club hardware" and audit.details["worker"].startswith(
            "w-"
        )
        assert audit.details["caps"] == ["analyse"]
        row = db.scalars(select(Scoresheet)).one()
        assert ss_core.drafts_of(row) == {1, 2, 3, 4}

    # Set 1 confirmed: a typed set from now on, scored; its cells kept as labels.
    body = {
        "base_version": out["version"],
        "sets": out["sets"],
        "use_in_scoring": True,
        "confirm": [1],
    }
    r = coach.post(f"/api/videos/{ready['id']}/scoresheet/confirm", json=body)
    assert r.status_code == 200, r.text
    done = r.json()
    assert done["read"]["drafts"] == [2, 3, 4] and done["status"] == "draft"
    assert done["read"]["sets"]["1"]["state"] == "confirmed"
    assert [c["use"] for c in done["checks"]] == ["path", "read", "read", "read"]
    with Session(db_engine) as db:
        labels = db.scalars(select(ScoresheetLabel)).all()
        assert labels and {lb.set_no for lb in labels} == {1}
        assert all(lb.quad == QUAD and lb.image_id == photo.id for lb in labels)
        # Labels plus boxes only: no picture is copied anywhere.
        assert len(store.contents) == 1 + sum(1 for k in store.contents if k.startswith("media/"))
        assert db.scalars(
            select(AuditLog).where(AuditLog.action == "scoresheet_confirmed")
        ).one().details["labels"] == len(labels)
    # The others too: the sheet is entered, every set lines the scoring up.
    body = {
        "base_version": done["version"],
        "sets": done["sets"],
        "use_in_scoring": True,
        "confirm": [2, 3, 4],
    }
    final = coach.post(f"/api/videos/{ready['id']}/scoresheet/confirm", json=body).json()
    assert final["status"] == "entered" and final["read"]["drafts"] == []
    assert final["trusted"] is True and [c["use"] for c in final["checks"]] == ["path"] * 4
    assert (
        coach.post(
            f"/api/videos/{ready['id']}/scoresheet/confirm",
            json={**body, "base_version": final["version"]},
        ).status_code
        == 409
    )


def test_what_people_typed_is_never_proposed_over(
    ready: Any,
    club: dict[str, TestClient],
    analyser: TestClient,
    lenovo: TestClient,
    db_engine: Engine,
) -> None:
    played, _ = match()
    analysed(analyser, played)
    coach = club["coach"]
    truth = sheet_sets(played)
    # Set 1 typed and fitting; set 2 typed only in part, with a typo.
    typed = [dict(truth[0]), {**truth[1], "boxes_us": [list(c) for c in truth[1]["boxes_us"]]}]
    typed[1]["boxes_us"] = [
        [v if b < 1 else None for b, v in enumerate(col)] for col in typed[1]["boxes_us"]
    ]
    first2 = next((p, 0) for p in range(6) if typed[1]["boxes_us"][p][0] is not None)
    typed[1]["boxes_us"][first2[0]][0] = 99  # a typo a person made: stays
    typed[1]["timeouts"] = [{"side": "us", "own": 3, "opp": 4}]
    assert put(coach, ready, typed).status_code == 200
    assert upload(coach, ready).status_code == 201
    out = sheet(coach, ready)
    assert out["read"] is None  # something is typed: no reading by itself
    r = coach.post(f"/api/videos/{ready['id']}/scoresheet/read", json={})
    assert r.status_code == 200 and r.json()["read"]["job"]["status"] == "queued"
    job = claim_read(lenovo)
    complete(lenovo, job, result(job, reading_of(truth)))
    out = sheet(coach, ready)
    sets = {s["no"]: s for s in out["sets"]}
    states = out["read"]["sets"]
    assert states["1"]["state"] == "skipped"  # typed and fitting: left alone
    assert sets[1]["boxes_us"] == truth[0]["boxes_us"]
    assert sets[2]["boxes_us"][first2[0]][0] == 99  # the person's value, not the photo's
    assert sets[2]["timeouts"] == [{"side": "us", "own": 3, "opp": 4}]
    assert f"box.us.{first2[0] + 1}.1" not in states["2"]["cells"]
    assert not any(k.startswith("to.") for k in states["2"]["cells"])
    assert out["read"]["drafts"] == [2, 3, 4]
    # A draft corrected by a coach keeps the correction when the photo is read again.
    sets[3]["lineup_them"] = [40, 41, 42, 43, 44, 45]
    saved = coach.put(
        f"/api/videos/{ready['id']}/scoresheet",
        json={"base_version": out["version"], "sets": list(sets.values()), "use_in_scoring": True},
    )
    assert saved.status_code == 200 and saved.json()["status"] == "draft"
    assert [c["use"] for c in saved.json()["checks"]][2] == "read"  # still a draft: as read
    coach.post(f"/api/videos/{ready['id']}/scoresheet/read", json={})
    job = claim_read(lenovo)
    complete(lenovo, job, result(job, reading_of(truth)))
    again = {s["no"]: s for s in sheet(coach, ready)["sets"]}
    assert again[3]["lineup_them"] == [40, 41, 42, 43, 44, 45]
    # Dropping the drafts: the typed set 2 is back as it was, the read sets 3 and 4 go.
    gone = coach.delete(f"/api/videos/{ready['id']}/scoresheet/read").json()
    assert [s["no"] for s in gone["sets"]] == [1, 2] and gone["status"] == "entered"
    assert gone["sets"][1]["boxes_us"][first2[0]][0] == 99


def test_who_may_read_and_confirm_and_who_sees_the_photo(
    ready: Any, club: dict[str, TestClient], as_: Any, lenovo: TestClient
) -> None:
    played, _ = match()
    coach = club["coach"]
    truth = sheet_sets(played)
    assert upload(coach, ready).status_code == 201
    job = claim_read(lenovo)
    complete(lenovo, job, result(job, reading_of(truth)))
    # A player of the team may type the sheet: read and confirm too, but never sees the photo or
    # where a cell is on it.
    player = as_("player-h3")
    seen = sheet(player, ready)
    assert seen["images"] is None and seen["can_read"] is True
    cell = next(iter(seen["read"]["sets"]["1"]["cells"].values()))
    assert "quad" not in cell and "img" not in cell
    assert (
        player.post(f"/api/videos/{ready['id']}/scoresheet/read", json={"us": "B"}).status_code
        == 200
    )
    assert (
        player.post(f"/api/videos/{ready['id']}/scoresheet/read", json={"us": "A"}).json()["read"][
            "us_letter"
        ]
        == "A"
    )
    body = {
        "base_version": sheet(player, ready)["version"],
        "sets": sheet(player, ready)["sets"],
        "use_in_scoring": True,
        "confirm": [1],
    }
    assert (
        player.post(f"/api/videos/{ready['id']}/scoresheet/confirm", json=body).status_code == 200
    )
    # A helper the coach added, likewise; someone of another team sees nothing.
    assert coach.post(f"/api/teams/{H3}/helpers", json={"member_id": PIA}).status_code == 201
    helper = as_("player-d1")
    assert sheet(helper, ready)["images"] is None
    assert helper.post(f"/api/videos/{ready['id']}/scoresheet/read", json={}).status_code == 200
    other = as_("basketball-player")
    assert other.post(f"/api/videos/{ready['id']}/scoresheet/read", json={}).status_code == 404
    assert other.post(f"/api/videos/{ready['id']}/scoresheet/confirm", json=body).status_code == 404


def test_a_sheet_with_holes_and_a_photo_without_a_form(
    ready: Any, club: dict[str, TestClient], lenovo: TestClient, db_engine: Engine
) -> None:
    played, _ = match()
    coach = club["coach"]
    truth = sheet_sets(played)
    assert upload(coach, ready).status_code == 201
    job = claim_read(lenovo)
    # No form found on the photo: nothing proposed, the page says so.
    complete(lenovo, job, result(job, None, error="not_found"))
    out = sheet(coach, ready)
    assert out["read"]["error"] == "not_found" and out["sets"] == [] and out["status"] == "entered"
    # A scorer who left the first boxes empty: the sets are shown as read, nothing made up;
    # what the reader was not sure of is to check.
    coach.post(f"/api/videos/{ready['id']}/scoresheet/read", json={})
    job = claim_read(lenovo)
    complete(lenovo, job, result(job, reading_of(truth, holes=8)))
    out = sheet(coach, ready)
    first = out["read"]["sets"]["1"]
    assert first["decoded"] is False and first["note"] in ("not_decoded", "too_many_repairs")
    boxes = {k: c for k, c in first["cells"].items() if k.startswith("box.")}
    assert boxes and all(c["v"] == c["alts"][0][0] for c in boxes.values())
    # Read surely and in order: nothing to check there (the set itself stays a draft).
    assert not any(c["check"] for c in boxes.values())
    assert out["status"] == "draft" and 1 in out["read"]["drafts"]


def test_a_worker_result_is_checked(
    ready: Any, club: dict[str, TestClient], lenovo: TestClient
) -> None:
    played, _ = match()
    assert upload(club["coach"], ready).status_code == 201
    job = claim_read(lenovo)
    bad = result(job, reading_of(sheet_sets(played)))
    bad["images"][0]["cells"]["s1.L.names.1"] = {"alts": [[1, 0.9]]}  # not a cell of the form
    r = lenovo.post(f"/api/worker/jobs/{job['id']}/complete", json=bad)
    assert r.status_code == 422


def test_the_photos_are_read_once_together(
    ready: Any, club: dict[str, TestClient], db_engine: Engine
) -> None:
    coach = club["coach"]
    assert upload(coach, ready).status_code == 201
    assert upload(coach, ready, page(shade=220)).status_code == 201  # a second page
    assert coach.post(f"/api/videos/{ready['id']}/scoresheet/read", json={}).status_code == 200
    with Session(db_engine) as db:
        [job] = db.scalars(select(Job).where(Job.kind == "sheet_read")).all()
        assert len(job.params["images"]) == 2  # one queued job, with both photos


def test_removing_the_photo_removes_what_was_read_on_it(
    ready: Any, club: dict[str, TestClient], lenovo: TestClient, db_engine: Engine
) -> None:
    played, _ = match()
    coach = club["coach"]
    assert upload(coach, ready).status_code == 201
    job = claim_read(lenovo)
    complete(lenovo, job, result(job, reading_of(sheet_sets(played))))
    out = sheet(coach, ready)
    body = {"base_version": out["version"], "sets": out["sets"], "use_in_scoring": True}
    assert (
        coach.post(
            f"/api/videos/{ready['id']}/scoresheet/confirm", json={**body, "confirm": [1]}
        ).status_code
        == 200
    )
    with Session(db_engine) as db:
        assert db.scalars(select(ScoresheetLabel)).first() is not None
    [photo] = sheet(coach, ready)["images"]
    assert coach.delete(photo["url"]).status_code == 204
    with Session(db_engine) as db:
        row = db.get(ScoresheetImage, photo["id"])
        assert row is not None and row.reading is None and row.registration is None
        assert db.scalars(select(ScoresheetLabel)).first() is None
    # The typed (confirmed) sheet stays.
    assert sheet(coach, ready)["sets"][0]["boxes_us"] == sheet_sets(played)[0]["boxes_us"]
