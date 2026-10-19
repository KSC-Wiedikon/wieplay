"""Reading the scoresheet's photos on club hardware (PLAN §10.3a, SS4), API side.

    a photo added while nothing is typed ─┐
    "Read the photo" on the sheet page ───┴─▶ `sheet_read` job (the Lenovo's CPU: the worker's
                                              main lane, caps "analyse", WIEPLAY_SHEET_READ_CAPS)
        the worker fetches each photo through a URL the API signed for this job only, matches
        it to the form, reads every cell (wieplay/ml/sheet_cells.py) and sends back what it saw;
        the photo never leaves the worker's memory, nothing goes to any other service
    complete ─▶ the readings decoded with the rules, the official set scores, our roster and
        what people typed (wieplay/ml/sheet_read.py) ─▶ the sets as drafts: shown on the page
        with the cells to check, never scored; audited (`scoresheet_read`, where it ran)
    a coach confirms a set ─▶ a typed set like any other (scored); its cells become labelled
        examples for the reader (scoresheet_label: where the cell is and what it holds)

Never over what a person typed: a typed set that fits is left alone; in one that does not, the
typed cells stay and only the empty ones are proposed; a draft a coach corrected keeps the
corrections when the photo is read again.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from wieplay.core import jobs
from wieplay.core import scoresheet as ss
from wieplay.core import sheet_photos as sp
from wieplay.core.models import (
    Job,
    ModelRun,
    Scoresheet,
    ScoresheetImage,
    ScoresheetLabel,
    Video,
    VideoRoster,
    Worker,
)
from wieplay.ml import sheet as sh
from wieplay.ml import sheet_form as sf
from wieplay.ml import sheet_read as rd

KIND = "sheet_read"
KINDS = frozenset({KIND})
# The Lenovo's CPU by default (a worker that analyses also reads sheets, on its main lane: a
# coach is waiting); "cuda,ml" would send it to the GPU worker.
CAPS = tuple(
    c.strip() for c in os.environ.get("WIEPLAY_SHEET_READ_CAPS", "analyse").split(",") if c.strip()
)
PRIORITY = 9  # before transcodes: a few seconds of CPU
RUN_KIND = "sheet_read"
MAX_ALTS = 6
MIN_CELLS_FOR_SET = 6  # a set with fewer boxes read than this is not proposed (a stray mark)


# --- the job --------------------------------------------------------------------------------------


def active_job(db: Session, video_id: uuid.UUID) -> Job | None:
    return db.scalars(
        select(Job)
        .where(Job.kind == KIND, Job.video_id == video_id, Job.status.in_(jobs.ACTIVE))
        .order_by(Job.created_at.desc())
    ).first()


def latest_job(db: Session, video_id: uuid.UUID) -> Job | None:
    return db.scalars(
        select(Job)
        .where(Job.kind == KIND, Job.video_id == video_id)
        .order_by(Job.created_at.desc())
    ).first()


def queue(
    db: Session,
    video: Video,
    row: Scoresheet,
    photos: Sequence[ScoresheetImage],
    actor: int | None,
    *,
    auto: bool = False,
) -> Job | None:
    """Read the sheet's photos (a queued job takes the new list; a running one is followed by
    a new one: it may have missed a photo). None without photos."""
    if not photos:
        return None
    params = {
        "video_id": str(video.id),
        "sheet_id": str(row.id),
        "images": [
            {
                "id": str(p.id),
                "key": p.object_key,
                "set_no": p.set_no,
                "width": p.width,
                "height": p.height,
            }
            for p in photos
        ],
        "requested_by": actor,
        "auto": auto,
    }
    queued = db.scalars(
        select(Job)
        .where(Job.kind == KIND, Job.video_id == video.id, Job.status == "queued")
        .with_for_update()
    ).first()
    if queued is not None:
        queued.params = params
        return queued
    return jobs.enqueue(
        db, KIND, caps=CAPS, video_id=video.id, priority=PRIORITY, max_attempts=2, params=params
    )


def may_sign(job: Job, op: str, key: str) -> bool:
    """The job may fetch its own photos, nothing else, and write nothing."""
    return op == "get" and key in {img.get("key") for img in job.params.get("images", [])}


def wants_auto(row: Scoresheet | None) -> bool:
    """Read a new photo by itself while nothing is typed yet."""
    if row is None:
        return True
    return not any(_has_content(doc) for doc in row.sets or [])


def _has_content(doc: Mapping[str, Any]) -> bool:
    if any(
        v is not None for k in ("boxes_us", "boxes_them") for col in doc.get(k) or [] for v in col
    ):
        return True
    if any(v is not None for k in ("lineup_us", "lineup_them") for v in doc.get(k) or []):
        return True
    return bool(doc.get("timeouts") or doc.get("subs") or doc.get("start") or doc.get("end"))


# --- what the worker sends ------------------------------------------------------------------------


class CellIn(BaseModel):
    alts: list[tuple[int | str | None, float]] = Field(max_length=MAX_ALTS)
    quad: list[float] | None = Field(default=None, min_length=8, max_length=8)

    @field_validator("alts")
    @classmethod
    def _values(
        cls, alts: list[tuple[int | str | None, float]]
    ) -> list[tuple[int | str | None, float]]:
        for v, p in alts:
            if not (v is None or v == "X" or (isinstance(v, int) and 0 <= v <= 99)):
                raise ValueError("a cell holds a number 0-99, X or nothing")
            if not 0 <= p <= 1:
                raise ValueError("a probability is 0-1")
        return alts

    @field_validator("quad")
    @classmethod
    def _quad(cls, q: list[float] | None) -> list[float] | None:
        if q is not None and any(not -1 <= v <= 2 for v in q):
            raise ValueError("a cell's place is a fraction of the photo")
        return q


class ImageIn(BaseModel):
    id: uuid.UUID
    error: str | None = Field(default=None, max_length=200)
    registration: dict[str, Any] | None = None
    cells: dict[str, CellIn] = Field(default_factory=dict)
    marks: dict[str, float] = Field(default_factory=dict)
    strength: dict[str, float] = Field(default_factory=dict)

    @field_validator("cells")
    @classmethod
    def _known(cls, cells: dict[str, CellIn]) -> dict[str, CellIn]:
        unknown = [k for k in cells if k not in sf.BY_KEY]
        if unknown:
            raise ValueError(f"unknown cells: {unknown[:3]}")
        return cells

    @field_validator("marks")
    @classmethod
    def _marks(cls, marks: dict[str, float]) -> dict[str, float]:
        if any(k not in sf.BY_KEY or sf.BY_KEY[k].kind != "mark" for k in marks):
            raise ValueError("unknown marks")
        return marks


class ModelIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    version: str = Field(min_length=1, max_length=64)
    weights_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReadResult(BaseModel):
    model: ModelIn
    images: list[ImageIn] = Field(max_length=sp.MAX_PHOTOS)
    timing: dict[str, Any] = Field(default_factory=dict)
    host: str | None = Field(default=None, max_length=64)


def complete(db: Session, video: Video | None, job: Job, payload: dict[str, Any]) -> None:
    """Keep what the worker read and propose the sheet from it."""
    if video is None:
        raise ValueError("job has no video")
    result = ReadResult.model_validate(payload)
    wanted = {img["id"] for img in job.params.get("images", [])}
    if any(str(i.id) not in wanted for i in result.images):
        raise ValueError("a photo the job was not given")
    worker = db.get(Worker, job.lease_owner) if job.lease_owner else None
    row = ss.stored(db, video.id)
    if row is None or video.status != "ready":
        jobs.succeed(job, {"skipped": "sheet removed" if video.status == "ready" else video.status})
        return
    photos = {str(p.id): p for p in sp.live(db, row)}
    read_ok = 0
    for img in result.images:
        photo = photos.get(str(img.id))
        if photo is None:
            continue  # removed meanwhile
        if img.error or img.registration is None:
            photo.registration = None
            photo.reading = {"error": img.error or "not_found", "job_id": str(job.id)}
            continue
        photo.registration = img.registration
        photo.reading = {
            "job_id": str(job.id),
            "model": result.model.model_dump(),
            "cells": {k: c.model_dump() for k, c in img.cells.items()},
            "marks": img.marks,
            "strength": img.strength,
        }
        read_ok += 1
    run = ModelRun(
        id=uuid.uuid4(),
        kind=RUN_KIND,
        job_id=job.id,
        video_id=video.id,
        model_name=result.model.name,
        model_version=result.model.version,
        weights_sha256=result.model.weights_sha256,
        params={
            "images": len(result.images),
            "worker": worker.name if worker else None,
            "host": result.host,
        },
        timing=result.timing,
    )
    db.add(run)
    db.flush()
    summary: dict[str, Any] = {"images": len(result.images), "read": read_ok}
    if read_ok:
        actor = job.params.get("requested_by")
        summary |= apply(
            db,
            video,
            row,
            list(photos.values()),
            actor=actor,
            job=job,
            worker=worker.name if worker else None,
            run=run,
        )
    else:
        _set_read(row, {**(row.read or {}), "error": "not_found", "job_id": str(job.id)})
    run.metrics = summary
    from wieplay.core.models import AuditLog

    db.add(
        AuditLog(
            actor_member_id=None,
            action="scoresheet_read",
            object_type="video",
            object_id=str(video.id),
            details={
                "where": "club hardware",
                "worker": worker.name if worker else None,
                "caps": list(job.required_caps),
                "host": result.host,
                "model": f"{result.model.name} {result.model.version}",
                "requested_by": job.params.get("requested_by"),
                **summary,
            },
        )
    )
    jobs.succeed(job, {"model_run_id": str(run.id), **summary})


def _set_read(row: Scoresheet, doc: dict[str, Any]) -> None:
    row.read = doc  # a new dict: SQLAlchemy sees the change


# --- proposing the sheet --------------------------------------------------------------------------


def readings_of(photos: Sequence[ScoresheetImage]) -> rd.Readings:
    docs = [(str(p.id), p.reading) for p in photos if p.reading and p.reading.get("cells")]
    return rd.Readings.from_photos(docs)


def roster_of(db: Session, video: Video) -> set[int]:
    return set(
        db.scalars(
            select(VideoRoster.number).where(
                VideoRoster.video_id == video.id, VideoRoster.side == "us"
            )
        )
    )


def _boxes(doc: Mapping[str, Any], side: str) -> dict[tuple[int, int], int]:
    out = {}
    for p, col in enumerate(doc.get(f"boxes_{side}") or [], start=1):
        for b, v in enumerate(col, start=1):
            if v is not None:
                out[(p, b)] = v
    return out


def person_boxes(doc: Mapping[str, Any], proposed: Mapping[str, Any] | None) -> rd.Fixed:
    """The boxes a person wrote: all of a typed set's, or a draft's that differ from what the
    last reading proposed (a correction)."""
    out: dict[sh.Side, dict[tuple[int, int], int | str | None]] = {}
    for side in sh.SIDES:
        have = _boxes(doc, side)
        if proposed is not None:
            before = _boxes(proposed, side)
            have = {k: v for k, v in have.items() if before.get(k) != v}
            # A proposed box a coach emptied stays empty.
            for k in before:
                if k not in _boxes(doc, side):
                    out.setdefault(side, {})[k] = None
        out.setdefault(side, {}).update(have)
    return out


def typed_boxes(doc: Mapping[str, Any], state: Mapping[str, Any]) -> rd.Fixed:
    """The boxes of a draft a person wrote: corrected after the reading, or typed before it (a
    typed set the photo was read into keeps them: `before`)."""
    out = person_boxes(doc, state.get("proposed"))
    before = state.get("before")
    if before:
        for side in sh.SIDES:
            mine = out.setdefault(side, {})  # type: ignore[attr-defined]
            now = _boxes(doc, side)
            for pos, v in _boxes(before, side).items():
                if now.get(pos) == v:
                    mine[pos] = v
    return out


def _owned(doc: Mapping[str, Any] | None, proposed: Mapping[str, Any] | None, name: str) -> bool:
    """Is this field (lineup, timeouts, ...) a person's: set, and not what the reading proposed."""
    if doc is None:
        return False
    value = doc.get(name)
    if value in (None, [], ""):
        return False
    if name.startswith("lineup") and all(v is None for v in value):
        return False
    return proposed is None or proposed.get(name) != value


FIELDS = ("first_serve", "lineup_us", "lineup_them", "timeouts", "subs", "start", "end")
FIELD_CELLS = {
    "first_serve": ("first_serve",),
    "lineup_us": ("lineup.us.",),
    "lineup_them": ("lineup.them.",),
    "timeouts": ("to.",),
    "subs": ("sub.",),
    "start": ("start",),
    "end": ("end",),
}


def apply(
    db: Session,
    video: Video,
    row: Scoresheet,
    photos: Sequence[ScoresheetImage],
    *,
    actor: int | None,
    job: Job | None = None,
    worker: str | None = None,
    run: ModelRun | None = None,
    us_letter: Literal["A", "B"] | None = None,
) -> dict[str, Any]:
    """Decode the photos' readings and write the sets as drafts. Returns a summary."""
    readings = readings_of(photos)
    official = ss.official_sets(db, video)
    roster = roster_of(db, video)
    old = row.read or {}
    old_sets: dict[str, Any] = dict(old.get("sets") or {})
    docs = {int(d["no"]): d for d in row.sets or []}
    checks = {c.no: c for c in ss.check(list(docs.values()), official, ss.drafts_of(row))}
    fixed: dict[int, rd.Fixed] = {}
    first: dict[int, sh.Side] = {}
    skipped: dict[int, str] = {}
    candidates = [no for no in readings.sets() if _enough(readings, no)]
    if official:
        candidates = [no for no in candidates if no <= len(official)]
    for no in candidates:
        doc = docs.get(no)
        state = old_sets.get(str(no)) or {}
        draft = state.get("state") == "draft"
        if doc is None or not _has_content(doc):
            continue
        if not draft and checks.get(no) is not None and checks[no].status == "consistent":
            skipped[no] = "typed"
            continue
        proposed = state.get("proposed") if draft else None
        fixed[no] = person_boxes(doc, proposed)
        if _owned(doc, proposed, "first_serve"):
            first[no] = doc["first_serve"]
    numbers = [no for no in candidates if no not in skipped]
    letter = us_letter or old.get("us_letter_coach")
    result = rd.read_sheet(
        readings, official, roster or None, fixed, sets=numbers, us_letter=letter, first=first
    )
    rescore = False
    new_sets: dict[str, Any] = {k: v for k, v in old_sets.items() if int(k) not in numbers}
    for s in result.sets:
        doc = docs.get(s.no)
        state = old_sets.get(str(s.no)) or {}
        was_draft = state.get("state") == "draft"
        proposed = state.get("proposed") if was_draft else None
        before = state.get("before") if was_draft else (doc if doc and _has_content(doc) else None)
        merged = dict(s.doc)
        cells = dict(s.cells)
        if doc is not None:
            for name in FIELDS:
                if _owned(doc, proposed, name):
                    merged[name] = doc[name]
                    for prefix in FIELD_CELLS[name]:
                        for key in [k for k in cells if k.startswith(prefix)]:
                            del cells[key]
            merged["use"] = doc.get("use", True)
            for name in ("sanctions", "cancelled"):
                merged[name] = doc.get(name) or []
            # The boxes a person wrote stay as they are, whatever the photo says.
            for side, boxes in person_boxes(doc, proposed).items():
                grid = [list(col) for col in merged[f"boxes_{side}"]]
                for (p, b), v in boxes.items():
                    if p - 1 < len(grid) and b - 1 < len(grid[p - 1]):
                        grid[p - 1][b - 1] = v
                    cells.pop(f"box.{side}.{p}.{b}", None)
                merged[f"boxes_{side}"] = grid
        # A draft is evidence for the scoring (PLAN §10.3): every new reading scores again.
        rescore = True
        merged = ss.SetSheetIn.model_validate(merged).model_dump(mode="json")
        docs[s.no] = merged
        new_sets[str(s.no)] = {
            "state": "draft",
            "us_half": s.us_half,
            "decoded": s.decoded,
            "note": s.note,
            "to_check": sum(1 for p in cells.values() if p.check),
            "cells": {k: p.as_json() for k, p in cells.items()},
            "proposed": s.doc,
            "before": before,
        }
    for no, why in skipped.items():
        new_sets[str(no)] = {"state": "skipped", "note": why}
    had_typed = any(_has_content(d) for d in row.sets or [])
    row.sets = [docs[no] for no in sorted(docs)]
    _set_read(
        row,
        {
            "version": 1,
            "job_id": str(job.id) if job else old.get("job_id"),
            "run_id": str(run.id) if run else old.get("run_id"),
            "worker": worker or old.get("worker"),
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
            "model": (run.model_name + " " + run.model_version) if run else old.get("model"),
            "model_version": run.model_version if run else old.get("model_version"),
            "us_letter": result.us_letter,
            "us_letter_coach": us_letter or old.get("us_letter_coach"),
            "images": [str(p.id) for p in photos if p.reading and p.reading.get("cells")],
            "sets": new_sets,
        },
    )
    row.status = "draft" if ss.drafts_of(row) else "entered"
    if not had_typed:
        row.source = "read"
    row.game_id = video.game_id
    row.updated_by = actor
    row.version = (row.version or 0) + 1
    video.no_scoresheet = False
    db.flush()
    if rescore:
        from wieplay.core import scoring

        scoring.score_video(db, video, reason="scoresheet")
    drafts = [s for s in result.sets]
    return {
        "sets": len(drafts),
        "skipped": len(skipped),
        "to_check": sum(new_sets[str(s.no)]["to_check"] for s in drafts),
        "decoded": sum(1 for s in drafts if s.decoded),
        "us_letter": result.us_letter,
    }


def _enough(r: rd.Readings, no: int) -> bool:
    """The photo shows this set: a few of its boxes hold a number."""
    n = 0
    for key, cell in r.cells.items():
        if (
            key.startswith(f"s{no}.")
            and ".box." in key
            and isinstance(cell.best, int)
            and cell.p_best >= 0.5
        ):
            n += 1
    return n >= MIN_CELLS_FOR_SET


# --- resolved by the joint decode (PLAN §10.3) ---------------------------------------------------

# A draft's box the joint decode (the video, the log, the official score and the other boxes) is
# at least this sure of is resolved: its value written into the draft (when the reader proposed
# another) and no longer to check. The coach decides an unsure rally in the review, the decode
# runs again, and the box behind it resolves without a separate check on the sheet page. Only
# to a value the ink allows (the reader gave it at least CELL_INK): where the reader saw another
# number clearly, the paper and the decode disagree (a scorer's slip the log shows, say), and the
# box stays to check.
CELL_SURE = 0.98
CELL_INK = 0.05


def resolve(db: Session, video: Video, match: Any) -> int:
    """The drafts' boxes the joint decode is sure of, resolved (CELL_SURE). Returns how many
    cells changed. Boxes a person typed are never touched."""
    row = ss.stored(db, video.id)
    if row is None or not row.read or not row.sets:
        return 0
    state = dict(row.read)
    sets = dict(state.get("sets") or {})
    docs = {int(d["no"]): dict(d) for d in row.sets}
    touched = 0
    doc_changed = False
    for so in match.sets:
        st = sets.get(str(so.no))
        doc = docs.get(so.no)
        if so.sheet != "read" or not so.boxes or not st or st.get("state") != "draft" or not doc:
            continue
        st = dict(st)
        cells = dict(st.get("cells") or {})
        proposed = dict(st.get("proposed") or {})
        typed = typed_boxes(doc, st)
        grids = {side: [list(col) for col in doc.get(f"boxes_{side}") or []] for side in sh.SIDES}
        pgrids = {
            side: [list(col) for col in proposed.get(f"boxes_{side}") or []] for side in sh.SIDES
        }
        for (side, p, b), (value, prob) in so.boxes.items():
            if prob < CELL_SURE or (p, b) in typed.get(side, {}):
                continue
            grid = grids.get(side) or []
            if p - 1 >= len(grid) or b - 1 >= len(grid[p - 1]):
                continue
            key = f"box.{side}.{p}.{b}"
            cell = cells.get(key)
            if cell is not None and cell.get("alts") and not any(
                v == value and pr >= CELL_INK for v, pr in cell["alts"]
            ):
                continue
            if grid[p - 1][b - 1] != value:
                grid[p - 1][b - 1] = value
                pg = pgrids.get(side) or []
                if p - 1 < len(pg) and b - 1 < len(pg[p - 1]):
                    pg[p - 1][b - 1] = value  # the reading's, not a person's: proposed too
                doc_changed = True
            elif cell is None or not cell.get("check"):
                continue
            found = dict(cell or {"alts": []})
            found.update({"v": value, "check": False, "why": "decoded"})
            cells[key] = found
            touched += 1
        for side in sh.SIDES:
            doc[f"boxes_{side}"] = grids[side]
            if pgrids[side]:
                proposed[f"boxes_{side}"] = pgrids[side]
        st["cells"] = cells
        st["proposed"] = proposed
        st["to_check"] = sum(1 for c in cells.values() if c.get("check"))
        sets[str(so.no)] = st
        docs[so.no] = doc
    if not touched and not doc_changed:
        return 0
    _set_read(row, {**state, "sets": sets})
    if doc_changed:
        row.sets = [docs[no] for no in sorted(docs)]
        row.version = (row.version or 0) + 1
    db.flush()
    return touched


# --- confirming, discarding -----------------------------------------------------------------------


def confirm(
    db: Session, video: Video, row: Scoresheet, sets: Sequence[int], actor: int, now: datetime
) -> dict[str, int]:
    """The coach checked these drafts: they become typed sets (scored from now on); their cells
    are labelled examples. Returns how many sets and labels."""
    state = dict(row.read or {})
    states = dict(state.get("sets") or {})
    docs = {int(d["no"]): d for d in row.sets or []}
    photos = {str(p.id): p for p in sp.live(db, row)}
    confirmed = labels = changed = 0
    for no in sets:
        st = states.get(str(no))
        if not st or st.get("state") != "draft" or no not in docs:
            continue
        doc = docs[no]
        labels += _labels(db, video, no, st, doc, photos, state.get("model_version"), actor, now)
        changed += _changed(st.get("proposed") or {}, doc)
        states[str(no)] = {
            **{k: v for k, v in st.items() if k not in ("cells", "before")},
            "state": "confirmed",
            "confirmed_by": actor,
            "confirmed_at": now.isoformat(timespec="seconds"),
            "changed": _changed(st.get("proposed") or {}, doc),
        }
        confirmed += 1
    _set_read(row, {**state, "sets": states})
    row.status = "draft" if ss.drafts_of(row) else "entered"
    row.updated_by = actor
    db.flush()
    return {"sets": confirmed, "labels": labels, "changed": changed}


def _changed(proposed: Mapping[str, Any], doc: Mapping[str, Any]) -> int:
    """How many boxes and lineup numbers the coach corrected."""
    n = 0
    for side in sh.SIDES:
        a, b = _boxes(proposed, side), _boxes(doc, side)
        n += sum(1 for k in set(a) | set(b) if a.get(k) != b.get(k))
        la = proposed.get(f"lineup_{side}") or [None] * 6
        lb = doc.get(f"lineup_{side}") or [None] * 6
        n += sum(1 for x, y in zip(la, lb, strict=False) if x != y)
    return n


def discard(db: Session, row: Scoresheet, actor: int) -> int:
    """Drop the drafts: a set that was empty goes, a typed one is as it was before."""
    state = dict(row.read or {})
    states = dict(state.get("sets") or {})
    docs = {int(d["no"]): d for d in row.sets or []}
    n = 0
    for key, st in list(states.items()):
        if st.get("state") != "draft":
            continue
        no = int(key)
        if st.get("before"):
            docs[no] = st["before"]
        else:
            docs.pop(no, None)
        states[key] = {"state": "discarded", "us_half": st.get("us_half")}
        n += 1
    row.sets = [docs[no] for no in sorted(docs)]
    _set_read(row, {**state, "sets": states})
    row.status = "entered"
    row.updated_by = actor
    row.version = (row.version or 0) + 1
    db.flush()
    return n


def confirmed_value(doc: Mapping[str, Any], cell: sf.Cell, side: sh.Side) -> int | str | None:
    """What a confirmed set says a form cell holds (for its label)."""
    value = _confirmed(doc, cell, side)
    return value if isinstance(value, int | str) else None


def _confirmed(doc: Mapping[str, Any], cell: sf.Cell, side: sh.Side) -> Any:
    kind = cell.key.split(".")[2]
    p = cell.position or 0
    if kind == "box":
        if (p, cell.box) == (1, 1) and doc.get("first_serve") == sh.other(side):
            return "X"
        grid = doc.get(f"boxes_{side}") or []
        return (
            grid[p - 1][(cell.box or 1) - 1]
            if p - 1 < len(grid) and (cell.box or 1) - 1 < len(grid[p - 1])
            else None
        )
    if kind == "lineup":
        lineup = doc.get(f"lineup_{side}") or [None] * 6
        return lineup[p - 1]
    sub = next((u for u in doc.get("subs") or [] if u["side"] == side and u["position"] == p), None)
    if kind == "sub_in":
        return sub["number"] if sub else None
    if kind in ("sub_at", "sub_back"):
        pair = sub.get("at" if kind == "sub_at" else "back") if sub else None
        return pair[cell.part or 0] if pair else None
    if kind == "to":
        mine = [t for t in doc.get("timeouts") or [] if t["side"] == side]
        k = (cell.box or 1) - 1
        if k < len(mine):
            return mine[k]["own"] if cell.part == 0 else mine[k]["opp"]
        return None
    if kind in ("start", "end"):
        text = doc.get(kind)
        if not text:
            return None
        return int(text[:2]) if cell.part == 0 else int(text[3:])
    return None


def _labels(
    db: Session,
    video: Video,
    no: int,
    st: Mapping[str, Any],
    doc: Mapping[str, Any],
    photos: Mapping[str, ScoresheetImage],
    model_version: str | None,
    actor: int,
    now: datetime,
) -> int:
    """Record the confirmed set's cells as labelled examples: every cell that holds something,
    and every cell the reader thought did (or was unsure of)."""
    us_half = st.get("us_half", "L")
    halves = {"us": us_half, "them": "R" if us_half == "L" else "L"}
    if no == 5:
        return 0  # the deciding set's part 3 repeats boxes: its labels would be ambiguous
    rows = []
    for photo in photos.values():
        cells = (photo.reading or {}).get("cells") or {}
        for key, c in cells.items():
            cell = sf.BY_KEY.get(key)
            if cell is None or cell.set_no != no:
                continue
            side = next((s for s, h in halves.items() if h == cell.half), None)
            if side is None:
                continue
            value = confirmed_value(doc, cell, side)  # type: ignore[arg-type]
            alts = c.get("alts") or []
            top_v, top_p = (alts[0][0], alts[0][1]) if alts else (None, 0.0)
            p_blank = next((p for v, p in alts if v is None), 0.0)
            if value is None and top_v is None and p_blank >= 0.9:
                continue
            if not c.get("quad"):
                continue
            rows.append(
                {
                    "image_id": photo.id,
                    "video_id": video.id,
                    "set_no": no,
                    "cell": key,
                    "quad": c["quad"],
                    "value": "" if value is None else str(value),
                    "read_value": "" if top_v is None else str(top_v),
                    "read_p": float(top_p),
                    "model_version": model_version,
                    "confirmed_by": actor,
                    "confirmed_at": now,
                }
            )
    if rows:
        stmt = insert(ScoresheetLabel).values(rows)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_scoresheet_label_cell",
            set_={
                k: stmt.excluded[k]
                for k in (
                    "value",
                    "read_value",
                    "read_p",
                    "quad",
                    "model_version",
                    "confirmed_by",
                    "confirmed_at",
                )
            },
        )
        db.execute(stmt)
    return len(rows)


# --- for the page ---------------------------------------------------------------------------------


def state(
    db: Session, video: Video, row: Scoresheet | None, *, photos_visible: bool
) -> dict[str, Any] | None:
    """The reading as the sheet page shows it: the job's state, the drafts with their cells
    (where each is on the photo only for those who may see the photo)."""
    job = latest_job(db, video.id)
    read = (row.read if row is not None else None) or {}
    if not read and job is None:
        return None
    job_state = None
    if job is not None:
        job_state = {
            "status": job.status,
            "error": (job.error or "")[:200] or None,
            "created_at": job.created_at.isoformat() if job.created_at else None,
        }
    sets: dict[str, Any] = {}
    for no, st in (read.get("sets") or {}).items():
        out = {k: v for k, v in st.items() if k not in ("proposed", "before")}
        if not photos_visible and "cells" in out:
            out["cells"] = {
                k: {kk: vv for kk, vv in c.items() if kk not in ("quad", "img")}
                for k, c in out["cells"].items()
            }
        sets[no] = out
    drafts = [s for s in sets.values() if s.get("state") == "draft"]
    return {
        "job": job_state,
        "at": read.get("at"),
        "worker": read.get("worker"),
        "model": read.get("model"),
        "us_letter": read.get("us_letter"),
        "error": read.get("error"),
        "to_check": sum(int(s.get("to_check", 0)) for s in drafts),
        "drafts": sorted(int(no) for no, s in sets.items() if s.get("state") == "draft"),
        "sets": sets,
    }
