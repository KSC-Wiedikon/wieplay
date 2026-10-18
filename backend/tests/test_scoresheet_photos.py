"""Photos of the paper scoresheet (PLAN §10.3a, SS3): who adds, sees and removes them, what is
refused (camera metadata, other types, too large or too small), what is stored (a JPEG of the
pixels alone), the signed and audited view, and that the bucket objects go with the photo, the
sheet and the video. Synthetic images only: a grey page with lines, never a real sheet."""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from tests.fakes import FakeStore
from tests.test_analysis_api import make_ready
from tests.test_scoresheet_api import put, sheet_sets
from tests.test_scoring_api import match
from wieplay.core import sheet_photos as sp
from wieplay.core.config import Settings
from wieplay.core.models import AuditLog, Scoresheet, ScoresheetImage

H3 = 9101
PIA = 111  # player-d1, an H3 helper once a coach adds her
PLAYER = "Alex Muster"  # player-h3


@pytest.fixture
def settings(settings: Settings) -> Settings:
    return settings.model_copy(update={"analysis_train_delay_seconds": 0})


@pytest.fixture
def ready(app: FastAPI, club: dict[str, TestClient], store: FakeStore) -> Any:
    return make_ready(app, club, store, k=0)


def page(
    size: tuple[int, int] = (1200, 850), fmt: str = "JPEG", shade: int = 235, **save: Any
) -> bytes:
    """A synthetic sheet: a light page with a grid of boxes and one blacked-out column."""
    image = Image.new("RGB", size, (shade, shade, shade - 10))
    draw = ImageDraw.Draw(image)
    w, h = size
    for k in range(1, 12):
        draw.line([(k * w // 12, 0), (k * w // 12, h)], fill=(90, 90, 120), width=2)
        draw.line([(0, k * h // 12), (w, k * h // 12)], fill=(90, 90, 120), width=2)
    draw.rectangle([int(w * 0.88), int(h * 0.55), int(w * 0.97), int(h * 0.8)], fill=(0, 0, 0))
    out = io.BytesIO()
    image.save(out, fmt, **save)
    return out.getvalue()


def camera_exif() -> bytes:
    exif = Image.Exif()
    exif[0x010F] = "SomePhone"  # Make
    exif[0x0132] = "2026:10:18 21:40:00"  # DateTime
    exif[0x8825] = {1: "N", 2: (47.0, 22.0, 3.0), 3: "E", 4: (8.0, 31.0, 7.0)}  # GPS
    return exif.tobytes()


TYPES = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def upload(
    client: TestClient,
    video: Any,
    data: bytes | None = None,
    content_type: str = "image/jpeg",
    **params: Any,
) -> Any:
    query = {"redacted": "true", "boxes": 1, **params}
    return client.post(
        f"/api/videos/{video['id']}/scoresheet/images",
        params={k: v for k, v in query.items() if v is not None},
        content=page() if data is None else data,
        headers={"content-type": content_type},
    )


def sheet(client: TestClient, video: Any) -> Any:
    return client.get(f"/api/videos/{video['id']}/scoresheet").json()


def audits(db_engine: Engine, action: str) -> list[AuditLog]:
    with Session(db_engine) as db:
        return list(db.scalars(select(AuditLog).where(AuditLog.action == action)))


def test_a_coach_adds_a_photo_and_only_its_pixels_are_kept(
    ready: Any, club: dict[str, TestClient], as_: Any, store: FakeStore, db_engine: Engine
) -> None:
    coach = club["coach"]
    empty = sheet(coach, ready)
    assert empty["images"] == [] and empty["can_add_images"] is True

    r = upload(coach, ready, page(fmt="PNG"), "image/png", set_no=2, rotation=90, cropped="true")
    assert r.status_code == 201, r.text
    photo = r.json()
    assert photo["page"] == 1 and photo["set_no"] == 2
    assert (photo["width"], photo["height"]) == (1200, 850)
    assert photo["uploaded_by"] == "Jo Trainer"
    assert photo["url"] == f"/api/videos/{ready['id']}/scoresheet/images/{photo['id']}"
    key = f"sheets/{ready['id']}/{photo['id']}.jpg"
    assert store.types[key] == "image/jpeg"
    with Image.open(io.BytesIO(store.contents[key])) as stored:
        assert stored.format == "JPEG" and stored.size == (1200, 850)
        assert sp.metadata_of(stored) == [] and "icc_profile" not in stored.info
        # The blacked-out column stays black: the stored JPEG is the uploaded pixels.
        assert max(stored.convert("RGB").getpixel((int(1200 * 0.92), int(850 * 0.7)))) < 16

    # The page lists it for the coach; the sheet itself is still "not typed in".
    out = sheet(coach, ready)
    assert [i["id"] for i in out["images"]] == [photo["id"]]
    assert out["exists"] is False and out["entered_by"] is None
    with Session(db_engine) as db:
        row = db.get(ScoresheetImage, photo["id"])
        assert row is not None and row.redacted and row.exif_stripped
        assert row.redaction == {"boxes": 1, "rotation": 90, "cropped": True}
        assert row.bytes == len(store.contents[key]) and len(row.sha256) == 64
        sheet_row = db.scalars(select(Scoresheet)).one()
        assert sheet_row.sets == [] and sheet_row.version == 0
    [entry] = audits(db_engine, "scoresheet_photo_uploaded")
    assert entry.object_id == ready["id"] and entry.details["image"] == photo["id"]

    # Typing the sheet afterwards: nothing to overwrite, the typist entered it.
    played, _ = match()
    assert put(as_("player-h3"), ready, sheet_sets(played)).status_code == 200
    typed = sheet(coach, ready)
    assert typed["exists"] is True and typed["entered_by"] == PLAYER
    assert typed["images"][0]["id"] == photo["id"]

    # A second page; the same bytes again are the same photo (a resend).
    second = upload(coach, ready, page(shade=220))
    assert second.status_code == 201 and second.json()["page"] == 2
    again = upload(coach, ready, page(shade=220))
    assert again.status_code == 200 and again.json()["id"] == second.json()["id"]
    assert len(sheet(coach, ready)["images"]) == 2


@pytest.mark.parametrize("fmt", ["JPEG", "PNG", "WEBP"])
def test_camera_metadata_is_refused(
    fmt: str, ready: Any, club: dict[str, TestClient], store: FakeStore
) -> None:
    r = upload(club["coach"], ready, page(fmt=fmt, exif=camera_exif()), TYPES[fmt])
    assert r.status_code == 422
    assert r.json()["detail"]["code"] == "metadata"
    assert "GPS" in r.json()["detail"]["message"]
    assert not [k for k in store.objects if k.startswith("sheets/")]
    # The same image as the browser's editor sends it (re-encoded, no EXIF) is taken.
    assert upload(club["coach"], ready, page(fmt=fmt), TYPES[fmt]).status_code == 201


def test_xmp_is_refused_and_icc_or_comments_are_stripped(
    ready: Any, club: dict[str, TestClient], store: FakeStore
) -> None:
    coach = club["coach"]
    r = upload(coach, ready, page(xmp=b"<x:xmpmeta><GPSLatitude>47</GPSLatitude></x:xmpmeta>"))
    assert r.status_code == 422 and r.json()["detail"]["code"] == "metadata"
    icc = Image.new("RGB", (8, 8)).info.get("icc_profile") or b"\0" * 128
    r = upload(coach, ready, page(icc_profile=icc, comment=b"taken at the hall"))
    assert r.status_code == 201, r.text
    [key] = [k for k in store.objects if k.startswith("sheets/")]
    with Image.open(io.BytesIO(store.contents[key])) as stored:
        assert "icc_profile" not in stored.info and "comment" not in stored.info


def test_types_and_sizes(ready: Any, club: dict[str, TestClient], store: FakeStore) -> None:
    coach = club["coach"]
    refused = [
        (upload(coach, ready, b"%PDF-1.4 a scan", "application/pdf"), 415, "type"),
        (upload(coach, ready, page(), "image/heic"), 415, "type"),
        (upload(coach, ready, b"\xff\xd8\xff" + b"0" * 4000), 422, "unreadable"),
        (upload(coach, ready, page(fmt="PNG"), "image/jpeg"), 422, "unreadable"),  # not a JPEG
        (upload(coach, ready, page(size=(200, 150))), 422, "too_small"),
        (upload(coach, ready, page(size=(9000, 5000), fmt="PNG"), "image/png"), 422,
         "too_many_pixels"),
        (upload(coach, ready, b"\xff\xd8" + b"\0" * (sp.MAX_BYTES + 1)), 413, "too_large"),
        (upload(coach, ready, redacted=None), 422, "not_redacted"),
        (upload(coach, ready, redacted="false"), 422, "not_redacted"),
    ]  # fmt: skip
    for r, status, code in refused:
        assert r.status_code == status, r.text
        assert r.json()["detail"]["code"] == code
    assert upload(coach, ready, set_no=6).status_code == 422
    assert upload(coach, ready, rotation=45).status_code == 422
    assert not [k for k in store.objects if k.startswith("sheets/")]
    # A large photo is kept at 4000 px on its longer side.
    r = upload(coach, ready, page(size=(5200, 3600)))
    assert r.status_code == 201 and (r.json()["width"], r.json()["height"]) == (4000, 2769)
    # At most eight photos per sheet.
    for k in range(sp.MAX_PHOTOS - 1):
        assert upload(coach, ready, page(shade=200 + k)).status_code == 201
    r = upload(coach, ready, page(shade=150))
    assert r.status_code == 409 and r.json()["detail"]["code"] == "too_many"


def test_who_sees_adds_and_removes_photos(
    ready: Any, club: dict[str, TestClient], as_: Any, store: FakeStore
) -> None:
    coach = club["coach"]
    photo = upload(coach, ready).json()
    url = photo["url"]
    # Players type the sheet but never see its photos.
    player = as_("player-h3")
    seen = sheet(player, ready)
    assert seen["images"] is None and seen["can_add_images"] is False and seen["can_edit"] is True
    assert player.get(f"/api/videos/{ready['id']}/scoresheet/images").status_code == 403
    assert player.get(url).status_code == 403
    assert upload(player, ready).status_code == 403
    assert player.delete(url).status_code == 403
    # Helpers neither.
    assert coach.post(f"/api/teams/{H3}/helpers", json={"member_id": PIA}).status_code == 201
    helper = as_("player-d1")
    assert sheet(helper, ready)["images"] is None
    assert helper.get(url).status_code == 403 and upload(helper, ready).status_code == 403
    # Not the team at all: the video is not there.
    assert as_("basketball-player").get(url).status_code == 404
    # Sport admins and admins do.
    for who in (as_("vb-sport-admin"), club["admin"]):
        assert [i["id"] for i in sheet(who, ready)["images"]] == [photo["id"]]
        assert who.get(url).status_code == 302
    assert upload(as_("vb-sport-admin"), ready, page(shade=210)).status_code == 201
    # Nobody is asked before a session: no upload without one.
    anonymous = TestClient(club["coach"].app)
    assert upload(anonymous, ready).status_code == 401


def test_a_view_is_signed_for_minutes_and_audited(
    ready: Any, club: dict[str, TestClient], store: FakeStore, db_engine: Engine
) -> None:
    coach = club["coach"]
    photo = upload(coach, ready).json()
    for _ in range(2):
        r = coach.get(photo["url"])
        assert r.status_code == 302
        assert r.headers["location"].startswith(f"https://s3.test/sheets/{ready['id']}/")
        assert r.headers["cache-control"] == "private, no-store"
        assert r.headers["referrer-policy"] == "no-referrer"
    assert store.signed.count(("get", f"sheets/{ready['id']}/{photo['id']}.jpg", 300)) == 2
    views = audits(db_engine, "scoresheet_photo_viewed")
    assert len(views) == 2 and {v.details["image"] for v in views} == {photo["id"]}
    assert {v.actor_member_id for v in views} == {106}
    other = f"/api/videos/{ready['id']}/scoresheet/images/00000000-0000-4000-8000-000000000000"
    assert coach.get(other).status_code == 404


def test_removing_a_photo_deletes_its_object(
    app: FastAPI, ready: Any, club: dict[str, TestClient], store: FakeStore, db_engine: Engine
) -> None:
    coach = club["coach"]
    first, second = upload(coach, ready).json(), upload(coach, ready, page(shade=200)).json()
    r = coach.delete(first["url"])
    assert r.status_code == 204
    assert f"sheets/{ready['id']}/{first['id']}.jpg" not in store.objects
    assert f"sheets/{ready['id']}/{second['id']}.jpg" in store.objects
    assert [i["id"] for i in sheet(coach, ready)["images"]] == [second["id"]]
    assert coach.get(first["url"]).status_code == 404
    assert coach.delete(first["url"]).status_code == 404
    [entry] = audits(db_engine, "scoresheet_photo_removed")
    assert entry.details["image"] == first["id"]
    with Session(db_engine) as db:
        row = db.get(ScoresheetImage, first["id"])
        assert row is not None and row.deleted_at and row.purged_at and row.deleted_by == 106

    # The bucket is unreachable: the photo is removed now, its object by the sweep later.
    store.fail_deletes = True
    assert coach.delete(second["url"]).status_code == 204
    assert f"sheets/{ready['id']}/{second['id']}.jpg" in store.objects
    assert sheet(coach, ready)["images"] == []
    with app.state.sessionmaker() as db:
        assert sp.sweep(db, store, datetime.now(UTC)) == 0  # still down
        store.fail_deletes = False
        assert sp.sweep(db, store, datetime.now(UTC)) == 1
        assert sp.sweep(db, store, datetime.now(UTC)) == 0
    assert not [k for k in store.objects if k.startswith("sheets/")]


def test_storage_down_on_upload_keeps_nothing(
    ready: Any, club: dict[str, TestClient], store: FakeStore, db_engine: Engine
) -> None:
    store.fail_writes = True
    r = upload(club["coach"], ready)
    assert r.status_code == 503
    with Session(db_engine) as db:
        assert db.scalars(select(ScoresheetImage)).first() is None
    store.fail_writes = False
    assert upload(club["coach"], ready).status_code == 201


def test_photos_go_with_the_sheet(
    ready: Any, club: dict[str, TestClient], as_: Any, store: FakeStore, db_engine: Engine
) -> None:
    coach = club["coach"]
    played, _ = match()
    assert put(coach, ready, sheet_sets(played)).status_code == 200
    photo = upload(coach, ready).json()
    # A player types and removes a sheet, but not one with photos: those are the coaches'.
    player = as_("player-h3")
    r = player.delete(f"/api/videos/{ready['id']}/scoresheet")
    assert r.status_code == 403
    assert coach.delete(f"/api/videos/{ready['id']}/scoresheet").status_code == 204
    assert not [k for k in store.objects if k.startswith("sheets/")]
    out = sheet(coach, ready)
    assert out["exists"] is False and out["images"] == []
    [entry] = audits(db_engine, "scoresheet_removed")
    assert entry.details["photos"] == 1
    with Session(db_engine) as db:
        row = db.get(ScoresheetImage, photo["id"])
        assert row is not None and row.deleted_at and row.purged_at
    # A photo after the removal brings the row back empty: the typed sets stay removed.
    assert upload(coach, ready, page(shade=200)).status_code == 201
    out = sheet(coach, ready)
    assert out["exists"] is False and out["sets"] == [] and len(out["images"]) == 1
    # A photo-only sheet: removed with its photos too; nothing at all is a 404.
    assert coach.delete(f"/api/videos/{ready['id']}/scoresheet").status_code == 204
    assert coach.delete(f"/api/videos/{ready['id']}/scoresheet").status_code == 404
    assert not [k for k in store.objects if k.startswith("sheets/")]


def test_photos_go_with_the_video(
    app: FastAPI, ready: Any, club: dict[str, TestClient], store: FakeStore, db_engine: Engine
) -> None:
    from wieplay.api.videos import sweep_deleted

    coach = club["coach"]
    photo = upload(coach, ready).json()
    assert coach.delete(f"/api/videos/{ready['id']}").status_code == 204
    assert not [k for k in store.objects if k.startswith("sheets/")]
    with Session(db_engine) as db:
        row = db.get(ScoresheetImage, photo["id"])
        assert row is not None and row.deleted_at is not None and row.deleted_by == 106
    # Anything left (a late write, a failed purge) goes with the deleted video's sweep.
    store.objects[f"sheets/{ready['id']}/late.jpg"] = 1
    with app.state.sessionmaker() as db:
        assert sweep_deleted(db, store, now=datetime.now(UTC) + timedelta(hours=3)) == 1
        assert sp.sweep(db, store, datetime.now(UTC)) == 1
    assert not [k for k in store.objects if k.startswith("sheets/")]


def test_photos_follow_the_videos_retention(
    app: FastAPI, ready: Any, club: dict[str, TestClient], store: FakeStore, db_engine: Engine
) -> None:
    from wieplay.api.videos import sweep_deleted
    from wieplay.core.retention import run_housekeeping

    photo = upload(club["coach"], ready).json()
    with db_engine.begin() as conn:
        conn.execute(text("UPDATE video SET retention_until = current_date - 1"))
    with app.state.sessionmaker() as db:
        assert run_housekeeping(db).videos == 1
        assert sp.sweep(db, store, datetime.now(UTC)) == 1  # the photo's own object
        sweep_deleted(db, store, now=datetime.now(UTC) + timedelta(hours=3))
    assert not [k for k in store.objects if k.startswith("sheets/")]
    with Session(db_engine) as db:
        row = db.get(ScoresheetImage, photo["id"])
        assert row is not None and row.deleted_at is not None and row.deleted_by is None


def test_a_photo_only_sheet_is_no_previous_lineup(
    app: FastAPI, ready: Any, club: dict[str, TestClient], store: FakeStore
) -> None:
    """The next match's form starts from the last typed lineup, not from a sheet with photos
    only."""
    coach = club["coach"]
    played, _ = match()
    assert put(coach, ready, sheet_sets(played)).status_code == 200
    later = make_ready(app, club, store, k=1)
    assert upload(coach, later).status_code == 201
    third = make_ready(app, club, store, k=2)
    assert sheet(coach, third)["previous_lineup"] == [7, 3, 11, 5, 9, 1]
