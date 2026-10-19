"""Photos of the paper scoresheet (PLAN §10.3a, SS3; research note §5.1, §6.1; the owner's
privacy OK of 18.10.2026, PLAN O14).

    coach's phone or browser               the API                            the bucket
    photo ─▶ redaction editor ─▶ JPEG ─POST─▶ decode, check, re-encode ─▶ sheets/<video>/<id>.jpg
    (boxes burned into the pixels:          ≤ 10 MB, JPEG/PNG/WebP; refused when
     dates of birth; rotated, cropped;      it still carries EXIF, GPS, XMP or IPTC;
     re-encoded, so EXIF/GPS are gone)      stored: a JPEG made from the pixels alone

- **Birthdates are never stored**: the coach blacks them out before a byte leaves the device
  (the editor makes it a required step; the upload says so, `redacted`). The server cannot see
  where they were; it makes sure no camera metadata survives and keeps nothing but pixels.
- **Seen** by the team's coaches, team managers, sport admins and admins only
  (permissions.can_see_sheet_photo), through a URL signed for 5 minutes; every view is audited.
- **Removed** by them at any time (the object is deleted at once, the sweep retries), with the
  typed sheet, and with the video (its `sheets/<video>/` prefix, like `media/`).
- **Read on club hardware only** (SS4, wieplay/core/sheet_reading.py): the Lenovo's CPU, in
  memory; what it read is kept with the photo and goes with it, as do the labelled cells.
"""

from __future__ import annotations

import hashlib
import io
import logging
import uuid
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image, UnidentifiedImageError
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from wieplay.core.models import Scoresheet, ScoresheetImage, ScoresheetLabel, Video
from wieplay.core.storage import ObjectStore

log = logging.getLogger(__name__)

# Content type of the upload -> Pillow's format name. HEIC and PDF are not taken: the browser's
# editor turns any photo it can show into a JPEG first.
FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}
MAX_BYTES = 10 * 1024 * 1024
MIN_SIDE = 300  # smaller than this shows no readable box
MAX_PIXELS = 40_000_000  # checked from the header, before decoding (no decompression bombs)
MAX_SIDE = 4000  # the stored JPEG's longer side
QUALITY = 92
MAX_PHOTOS = 8  # per sheet: front and back, or one per set, and a spare
STORED_TYPE = "image/jpeg"
PREFIX = "sheets/"
GPS_IFD = 0x8825
# What Pillow reports for metadata blocks that can carry where and when a photo was taken.
META_KEYS = {
    "exif": "exif",
    "xmp": "xmp",
    "XML:com.adobe.xmp": "xmp",
    "photoshop": "iptc",
    "iptc": "iptc",
}


class PhotoRefused(Exception):
    """An upload WiePlay does not keep; `status` is the HTTP answer, `code` for the page."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Cleaned:
    data: bytes
    width: int
    height: int
    sha256: str


def metadata_of(image: Image.Image) -> list[str]:
    """The metadata blocks a decoded upload still carries: exif, gps, xmp, iptc."""
    found: set[str] = set()
    for key, name in META_KEYS.items():
        if image.info.get(key):
            found.add(name)
    exif = image.getexif()
    if len(exif):
        found.add("exif")
        if GPS_IFD in exif or exif.get_ifd(GPS_IFD):
            found.add("gps")
    return sorted(found)


def clean(data: bytes, content_type: str) -> Cleaned:
    """Decode an uploaded photo, refuse it if it is not what the editor makes (an image of a
    sane size without camera metadata), and re-encode it from its pixels alone."""
    kind = FORMATS.get(content_type)
    if kind is None:
        raise PhotoRefused(415, "type", "A scoresheet photo is a JPEG, PNG or WebP image.")
    if len(data) > MAX_BYTES:
        raise PhotoRefused(413, "too_large", "A scoresheet photo is at most 10 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            image = Image.open(io.BytesIO(data), formats=[kind])
            width, height = image.size
            if width * height > MAX_PIXELS:
                raise PhotoRefused(422, "too_many_pixels", "The photo has too many pixels.")
            if min(width, height) < MIN_SIDE:
                raise PhotoRefused(422, "too_small", "The photo is too small to read.")
            found = metadata_of(image)
            if found:
                raise PhotoRefused(
                    422,
                    "metadata",
                    "The photo still carries camera data ("
                    + ", ".join(m.upper() for m in found)
                    + "): add it through WiePlay's redaction step, which removes it.",
                )
            image.load()
    except PhotoRefused:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Warning) as exc:
        raise PhotoRefused(422, "unreadable", "The file is not a readable image.") from exc
    pixels = _flatten(image)
    if max(pixels.size) > MAX_SIDE:
        pixels.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    # A new image from the bare pixels: nothing of the upload's info (ICC, comments, ...) can
    # reach the encoder.
    bare = Image.frombytes("RGB", pixels.size, pixels.tobytes())
    out = io.BytesIO()
    bare.save(out, "JPEG", quality=QUALITY, optimize=True)
    encoded = out.getvalue()
    with Image.open(io.BytesIO(encoded)) as check:
        assert not metadata_of(check), "re-encoded photo carries metadata"
    return Cleaned(encoded, bare.width, bare.height, hashlib.sha256(encoded).hexdigest())


def _flatten(image: Image.Image) -> Image.Image:
    """RGB; a transparent PNG or WebP on white (the paper)."""
    if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(white, rgba).convert("RGB")
    return image.convert("RGB")


# --- rows and objects ---------------------------------------------------------------------------


def key_for(video_id: uuid.UUID, image_id: uuid.UUID) -> str:
    return f"{PREFIX}{video_id}/{image_id}.jpg"


def prefix_for(video_id: uuid.UUID) -> str:
    return f"{PREFIX}{video_id}/"


def live(db: Session, sheet: Scoresheet | None) -> list[ScoresheetImage]:
    if sheet is None:
        return []
    return list(
        db.scalars(
            select(ScoresheetImage)
            .where(
                ScoresheetImage.scoresheet_id == sheet.id,
                ScoresheetImage.deleted_at.is_(None),
            )
            .order_by(ScoresheetImage.page, ScoresheetImage.uploaded_at)
        )
    )


def get(db: Session, video_id: uuid.UUID, image_id: uuid.UUID) -> ScoresheetImage | None:
    row = db.get(ScoresheetImage, image_id)
    if row is None or row.video_id != video_id or row.deleted_at is not None:
        return None
    return row


def add(
    db: Session,
    store: ObjectStore,
    video: Video,
    sheet: Scoresheet,
    photo: Cleaned,
    *,
    set_no: int | None,
    redaction: dict[str, Any],
    actor: int,
) -> tuple[ScoresheetImage, bool]:
    """Keep a cleaned photo: the object first, then its row. The same bytes again (a resend
    after a lost answer) give the photo kept already; `False` then."""
    rows = live(db, sheet)
    for row in rows:
        if row.sha256 == photo.sha256:
            return row, False
    if len(rows) >= MAX_PHOTOS:
        raise PhotoRefused(409, "too_many", f"A scoresheet has at most {MAX_PHOTOS} photos.")
    image_id = uuid.uuid4()
    key = key_for(video.id, image_id)
    store.put(key, photo.data, STORED_TYPE)
    page = (
        db.scalar(
            select(func.max(ScoresheetImage.page)).where(
                ScoresheetImage.scoresheet_id == sheet.id,
                ScoresheetImage.deleted_at.is_(None),
            )
        )
        or 0
    ) + 1
    row = ScoresheetImage(
        id=image_id,
        scoresheet_id=sheet.id,
        video_id=video.id,
        page=page,
        set_no=set_no,
        object_key=key,
        content_type=STORED_TYPE,
        bytes=len(photo.data),
        width=photo.width,
        height=photo.height,
        sha256=photo.sha256,
        exif_stripped=True,
        redacted=True,
        redaction=redaction,
        uploaded_by=actor,
    )
    db.add(row)
    db.flush()
    return row, True


def mark_removed(rows: Sequence[ScoresheetImage], actor: int | None, now: datetime) -> list[str]:
    """Mark photos removed; returns their object keys for the caller to delete (purge). What
    was read on them goes too (SS4): the registration and the cells' readings."""
    for row in rows:
        row.deleted_at = now
        row.deleted_by = actor
        row.registration = None
        row.reading = None
    return [row.object_key for row in rows]


def purge(db: Session, store: ObjectStore, rows: Sequence[ScoresheetImage], now: datetime) -> bool:
    """Delete removed photos' objects now; the sweep tries again if the bucket is down."""
    if not rows:
        return True
    try:
        store.delete([row.object_key for row in rows])
    except (BotoCoreError, ClientError):
        log.exception("could not delete %d scoresheet photos; the sweep retries", len(rows))
        return False
    for row in rows:
        row.purged_at = now
    # The labelled cells of a photo point into it: without the photo they go too (SS4).
    db.execute(delete(ScoresheetLabel).where(ScoresheetLabel.image_id.in_([r.id for r in rows])))
    return True


def mark_video_deleted(db: Session, video_id: uuid.UUID, actor: int | None, now: datetime) -> None:
    """A deleted video's photos: removed too; their objects go with its `sheets/` prefix."""
    rows = db.scalars(
        select(ScoresheetImage).where(
            ScoresheetImage.video_id == video_id, ScoresheetImage.deleted_at.is_(None)
        )
    ).all()
    mark_removed(rows, actor, now)


def sweep(db: Session, store: ObjectStore, now: datetime, limit: int = 100) -> int:
    """Removed photos whose object may still be in the bucket: delete them again."""
    rows = db.scalars(
        select(ScoresheetImage)
        .where(ScoresheetImage.deleted_at.is_not(None), ScoresheetImage.purged_at.is_(None))
        .limit(limit)
    ).all()
    if rows and purge(db, store, rows, now):
        db.commit()
        return len(rows)
    db.rollback()
    return 0
