"""Reading a scoresheet photo (PLAN §10.3a, SS4), the parts without the API: the form's layout,
the registration of a phone photo (turned, tilted, cut off, a faint carbon copy), cutting the
cells, decoding the reader's alternatives with the rules (which half is us, who served first, a
misread fixed and flagged, typed cells kept, a sheet with holes not made up), and the reader
itself end to end. Synthetic sheets only: the form is drawn from its layout, the handwriting is
OpenCV's script font; no real sheet, photo or crop is in the repository."""

from __future__ import annotations

import random
from typing import Any

import cv2
import numpy as np
import pytest

from wieplay.ml import sheet as sh
from wieplay.ml import sheet_cells as sc
from wieplay.ml import sheet_form as sf
from wieplay.ml import sheet_read as rd
from wieplay.ml import sheet_register as sr
from wieplay.ml import sheet_synth as ss

GLYPHS = ss.MixedGlyphs([(ss.HersheyGlyphs(), 1), (ss.StrokeGlyphs(), 1)])


def photo_of(seed: int, **kw: Any) -> tuple[ss.SheetContent, ss.Photo]:
    rng = np.random.default_rng(seed)
    content = ss.match_content(rng, sets=4)
    ink = kw.pop("ink", None)
    page = ss.page(content, GLYPHS, rng, ink=ink)
    return content, ss.photograph(page, rng, **kw)


def error_mm(reg: sr.Registration, truth: np.ndarray, shape: tuple[int, ...]) -> float:
    """The worst distance (mm) between where a service box really is and where the
    registration puts it, over the boxes in the picture."""
    worst = 0.0
    for c in sf.CELLS:
        if c.kind != "box":
            continue
        centre = np.array([[c.rect.x + c.rect.w / 2, c.rect.y + c.rect.h / 2]])
        true = sr.apply(truth, centre)[0]
        if not (0 <= true[0] < shape[1] and 0 <= true[1] < shape[0]):
            continue
        found = reg.to_photo(c.set_no, centre)[0]
        worst = max(
            worst, float(np.hypot(*(true - found))) / sr.px_per_mm(truth, sf.BLOCKS[c.set_no].rect)
        )
    return worst


# --- the form -------------------------------------------------------------------------------------


def test_the_form_has_every_cell_once_and_inside_the_page() -> None:
    assert len(sf.BY_KEY) == len(sf.CELLS)
    for c in sf.CELLS:
        assert 0 < c.rect.x < c.rect.x1 < sf.PAGE_W and 0 < c.rect.y < c.rect.y1 < sf.PAGE_H
        assert c.window.w > c.rect.w or c.kind in ("time", "mark")
    # Sets 1-4: eight boxes per position and team, the deciding set six; two halves, and the
    # deciding set's third (the left team after the change of sides).
    boxes = [c for c in sf.CELLS if c.kind == "box"]
    assert sum(1 for c in boxes if c.set_no == 1) == 2 * 6 * 8
    assert sum(1 for c in boxes if c.set_no == 5) == 3 * 6 * 6
    assert sf.BY_KEY["s2.R.box.4.7"].box == 7
    # Box 5 stands right of box 1, in the same row (the sheet's two columns per position).
    b1, b5 = sf.BY_KEY["s1.L.box.3.1"].rect, sf.BY_KEY["s1.L.box.3.5"].rect
    assert b5.x == pytest.approx(b1.x1, abs=0.01) and b5.y == b1.y
    assert [sf.left_letter(n) for n in range(1, 6)] == ["A", "B", "A", "B", None]


def test_the_synthetic_page_draws_the_form_where_the_layout_says() -> None:
    page = ss.render_form(4.0)
    b = sf.BLOCKS[3]
    # The block's top rule is dark, the paper just above it is light.
    y = round(b.y * 4)
    x = round((b.x + 40) * 4)
    assert page[y, x] < 120 and page[y - 6, x] > 200


# --- registration ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seed", "kw"),
    [
        (2, {}),
        (5, {"quarter_turns": 1}),  # a portrait shot of the landscape sheet
        (10, {"quarter_turns": 3, "underlay": True}),  # turned the other way, on another sheet
        (11, {"crop": 0.08}),  # one edge cut off
        (12, {"ink": ss.Ink((150, 70, 40), 0.3, 0.0)}),  # a faint carbon copy
    ],
)
def test_a_phone_photo_is_matched_to_the_form(seed: int, kw: dict[str, Any]) -> None:
    _, photo = photo_of(seed, width=2000, **kw)
    reg = sr.register(photo.image)
    assert reg.score > 0.6
    assert set(reg.blocks) >= {1, 2, 3, 4}
    assert error_mm(reg, photo.h_page, photo.image.shape) < 1.2
    # Kept as numbers only: it maps the form onto this photo, nothing of the picture.
    doc = reg.as_json()
    assert doc["width"] == photo.image.shape[1] and len(doc["page"]) == 9
    again = sr.Registration.from_json(doc)
    assert np.allclose(again.mapping(2), reg.mapping(2), atol=1e-3)


def test_a_photo_of_one_set_is_matched_to_that_set() -> None:
    rng = np.random.default_rng(4)
    content = ss.match_content(rng, sets=2)
    page = ss.page(content, GLYPHS, rng)
    s = 8.0
    b = sf.BLOCKS[2].rect.grow(0.08, 0.2, 0.08, 0.2)
    crop = page[int(b.y * s) : int(b.y1 * s), int(b.x * s) : int(b.x1 * s)]
    shift = np.array([[1, 0, -b.x * s], [0, 1, -b.y * s], [0, 0, 1]]) @ np.diag([s, s, 1])
    reg = sr.register(crop, set_no=2)
    assert list(reg.blocks) == [2]
    centre = np.array([[sf.BLOCKS[2].x + 60, sf.BLOCKS[2].y + 40]])
    assert np.hypot(*(sr.apply(shift, centre)[0] - reg.to_photo(2, centre)[0])) / s < 1.0


def test_no_form_on_the_photo() -> None:
    rng = np.random.default_rng(1)
    noise = (rng.random((900, 1200)) * 60 + 150).astype(np.uint8)
    with pytest.raises(sr.NotFound):
        sr.register(noise)


# --- cutting --------------------------------------------------------------------------------------


def test_each_cell_is_cut_from_where_it_is_written() -> None:
    content, photo = photo_of(3, width=2400, tilt=0.03, angle=3.0, curl=0.0)
    gray = cv2.cvtColor(photo.image, cv2.COLOR_BGR2GRAY)
    reg = sr.register(gray)
    truth = ss.truth_cells(content)
    cells = [c for c in sf.cells_of(1) if c.kind == "box"]
    patches, _ = sc.cut_cells(gray, reg, cells)
    assert patches.shape == (len(cells), sc.PATCH_H, sc.PATCH_W)
    # The written boxes hold clearly more ink in their middle than the empty ones.
    middle = patches[:, 10:30, 12:36].mean(axis=(1, 2))
    written = [m for c, m in zip(cells, middle, strict=True) if truth[c.key] is not None]
    empty = [m for c, m in zip(cells, middle, strict=True) if truth[c.key] is None]
    assert np.median(written) > 2 * np.median(empty)


# --- decoding with the rules ----------------------------------------------------------------------


def readings_of(
    sets: dict[int, sh.SheetSet],
    letter: str,
    rng: random.Random,
    misread: float = 0.0,
    unsure: float = 0.0,
) -> tuple[rd.Readings, set[tuple[int, str]]]:
    """What a reader would report for these sets: each written cell read right with high
    probability, some misread (the right value second), some unsure; empty boxes blank."""
    cells: dict[str, rd.Cell] = {}
    wrong: set[tuple[int, str]] = set()
    for no, s in sets.items():
        for side in sh.SIDES:
            half = rd.us_half_for(no, letter)
            if side == "them":
                half = "R" if half == "L" else "L"
            first = s.first_serve == side
            for p in range(1, 7):
                for b in range(1, 9):
                    v: Any = s.value(side, p, b)
                    if (p, b) == (1, 1) and not first:
                        v = "X"
                    key = f"s{no}.{half}.box.{p}.{b}"
                    if v is None:
                        cells[key] = rd.Cell(((None, 0.97), (1, 0.02)))
                        continue
                    other = (v + 10) % 40 if isinstance(v, int) else 1
                    roll = rng.random()
                    if roll < misread:
                        cells[key] = rd.Cell(((other, 0.6), (v, 0.35), (None, 0.03)))
                        wrong.add((no, f"box.{side}.{p}.{b}"))
                    elif roll < misread + unsure:
                        cells[key] = rd.Cell(((v, 0.55), (other, 0.4)))
                    else:
                        cells[key] = rd.Cell(((v, 0.96), (other, 0.03)))
                lineup = s.lineup.get(side) or []
                if lineup:
                    cells[f"s{no}.{half}.lineup.{p}"] = rd.Cell(((lineup[p - 1], 0.97),))
    return rd.Readings(cells), wrong


def random_sets(seed: int, n: int = 3) -> dict[int, sh.SheetSet]:
    rng = random.Random(seed)  # noqa: S311 - test data
    return {no: sh.synthetic_set(rng, no)[0] for no in range(1, n + 1)}


def test_the_rules_fix_a_misread_box_and_flag_it() -> None:
    sets = random_sets(1)
    official = [sh.decode(s).final for s in sets.values()]
    r, wrong = readings_of(sets, "A", random.Random(5), misread=0.06)  # noqa: S311 - test data
    assert wrong  # some boxes read wrongly first
    out = rd.read_sheet(r, official)
    assert out.us_letter == "A"
    for s in out.sets:
        truth = sets[s.no]
        assert s.decoded and s.doc["first_serve"] == truth.first_serve
        for side in sh.SIDES:
            assert s.doc[f"boxes_{side}"] == [list(col) for col in truth.boxes[side]]
    # Every box read wrongly first is flagged: the rules took the reader's second reading.
    by_no = {s.no: s for s in out.sets}
    for no, key in wrong:
        cell = by_no[no].cells[key]
        assert cell.check and cell.why == "rules", (no, key)


def test_which_half_is_us_comes_from_the_official_score() -> None:
    sets = random_sets(2)
    official = [sh.decode(s).final for s in sets.values()]
    r, _ = readings_of(sets, "B", random.Random(1))  # noqa: S311 - test data
    out = rd.read_sheet(r, official)
    assert out.us_letter == "B"
    assert [s.us_half for s in out.sets] == ["R", "L", "R"]
    assert all(s.decoded and s.to_check == 0 for s in out.sets)
    # Asked to read it as team A instead: the coach's choice holds.
    assert rd.read_sheet(r, official, us_letter="A").us_letter == "A"


def test_typed_boxes_are_kept_and_never_proposed_over() -> None:
    sets = random_sets(3, n=1)
    s = sets[1]
    official = [sh.decode(s).final]
    r, _ = readings_of(sets, "A", random.Random(2), misread=0.1)  # noqa: S311 - test data
    first_box = sh.cells(s.first_serve == "us")[0]
    typed = s.value("us", *first_box)
    fixed: rd.Fixed = {"us": {first_box: typed}}
    out = rd.read_sheet(r, official, fixed={1: fixed})
    [got] = out.sets
    assert got.doc["boxes_us"][first_box[0] - 1][first_box[1] - 1] == typed
    assert f"box.us.{first_box[0]}.{first_box[1]}" not in got.cells


def test_a_sheet_with_holes_is_shown_as_read_not_made_up() -> None:
    """A scorer who only struck the small numbers for the first turns (no score written)."""
    sets = random_sets(4, n=1)
    s = sets[1]
    official = [sh.decode(s).final]
    r, _ = readings_of(sets, "A", random.Random(3))  # noqa: S311 - test data
    for side in sh.SIDES:
        half = "L" if side == "us" else "R"
        for p, b in sh.cells(s.first_serve == side)[:8]:
            r.cells[f"s1.{half}.box.{p}.{b}"] = rd.Cell(((None, 0.95),))
    [got] = rd.read_sheet(r, official).sets
    assert not got.decoded and got.note in ("not_decoded", "too_many_repairs")
    # Only what was read, nothing filled in: what the reader was not sure of is to check.
    for k, prop in got.cells.items():
        if k.startswith("box."):
            side, p, b = k.split(".")[1:]
            half = "L" if side == "us" else "R"
            cell = r.cells[f"s1.{half}.box.{p}.{b}"]
            assert prop.value == cell.best
            if cell.p_best < rd.SURE:
                assert prop.check
    unsure = rd.Cell(((7, 0.55), (17, 0.4)))
    ordered = [pb for pb in sh.cells(s.first_serve == "us")[8:] if s.value("us", *pb) is not None]
    r.cells["s1.L.box.{}.{}".format(*ordered[0])] = unsure
    [got] = rd.read_sheet(r, official).sets
    assert got.cells["box.us.{}.{}".format(*ordered[0])].check
    read_values = {v for col in got.doc["boxes_us"] for v in col if v is not None}
    shown = {
        r.cells[k].best
        for k in r.cells
        if k.startswith("s1.L.box.") and isinstance(r.cells[k].best, int)
    }
    assert read_values <= shown


def test_timeouts_and_substitutions_are_read_on_the_path() -> None:
    sets = random_sets(6, n=1)
    s = sets[1]
    d = sh.decode(s)
    official = [d.final]
    r, _ = readings_of(sets, "A", random.Random(4))  # noqa: S311 - test data
    us, them = d.scores()[10]
    # A timeout of theirs (their score first), read with a doubtful second digit.
    r.cells["s1.R.to.1.0"] = rd.Cell(((them, 0.9),))
    r.cells["s1.R.to.1.1"] = rd.Cell(((us + 30, 0.5), (us, 0.45)))
    # A substitution of ours in position IV.
    r.cells["s1.L.sub_in.4"] = rd.Cell(((17, 0.95),))
    r.cells["s1.L.sub_at.4.0"] = rd.Cell(((us, 0.95),))
    r.cells["s1.L.sub_at.4.1"] = rd.Cell(((them, 0.95),))
    [got] = rd.read_sheet(r, official).sets
    assert {"side": "them", "own": them, "opp": us} in got.doc["timeouts"]
    assert got.cells["to.them.1"].check and got.cells["to.them.1"].why == "rules"
    [sub] = got.doc["subs"]
    assert sub == {"side": "us", "position": 4, "number": 17, "at": [us, them], "back": None}


# --- the reader, end to end -----------------------------------------------------------------------


def test_the_reader_reads_a_synthetic_photo_end_to_end() -> None:
    reader = sc.CellReader()
    assert reader.sha256 == sc.WEIGHTS_SHA256
    content, photo = photo_of(8, width=3000, tilt=0.03, angle=4.0)
    gray = cv2.cvtColor(photo.image, cv2.COLOR_BGR2GRAY)
    reading = sc.read_photo(gray, reader)
    doc = reading.as_json()
    assert set(doc) == {"registration", "cells", "marks", "strength"}
    r = rd.Readings.from_photos([("p1", doc)])
    official = [content.finals[no] for no in sorted(content.finals)]
    roster = set(content.truth[1].lineup.get("us") or [])
    out = rd.read_sheet(r, official, roster)
    assert out.us_letter == "A"
    exact = 0
    for s in out.sets:
        truth = content.truth[s.no]
        if s.doc["first_serve"] == truth.first_serve and all(
            s.doc[f"boxes_{side}"] == [list(col) for col in truth.boxes[side]] for side in sh.SIDES
        ):
            exact += 1
    assert exact >= 3, [s.to_check for s in out.sets]
    # Where each cell is, for the page to zoom to: fractions of the photo.
    quad = doc["cells"]["s1.L.box.2.1"]["quad"]
    assert len(quad) == 8 and all(-0.1 < v < 1.1 for v in quad)


def test_the_worker_job_reads_its_photo_in_memory(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The job fetches its photo through the URL the API signed, reads it without writing it
    anywhere, and sends back what the API accepts."""
    import threading

    from wieplay.core.sheet_reading import ReadResult
    from wieplay.worker import handlers
    from wieplay.worker import sheet_read as job_module
    from wieplay.worker.client import ClaimedJob
    from wieplay.worker.config import WorkerSettings

    _, photo = photo_of(9, width=2400, tilt=0.03, angle=3.0)
    ok, jpeg = cv2.imencode(".jpg", photo.image)
    assert ok
    fetched: list[str] = []

    def fetch(url: str) -> bytes:
        fetched.append(url)
        return bytes(jpeg)

    monkeypatch.setattr(job_module, "_fetch", fetch)
    signed: list[tuple[str, list[str]]] = []

    class FakeApi:
        def sign(self, job_id: str, op: str, keys: list[str]) -> dict[str, str]:
            signed.append((op, keys))
            return {k: f"https://bucket.example/{k}?signed" for k in keys}

    key = "sheets/v/i.jpg"
    job = ClaimedJob(
        id="job-read",
        kind="sheet_read",
        video_id="v",
        attempt=1,
        checkpoint=None,
        params={
            "images": [{"id": "00000000-0000-4000-8000-000000000001", "key": key, "set_no": None}]
        },
    )
    work = tmp_path / "work"
    settings = WorkerSettings(api_url="http://unused", work_dir=work)
    ctx = handlers.Context(settings, FakeApi(), job, threading.Event(), lambda p, m: None)  # type: ignore[arg-type]
    result = handlers.HANDLERS["sheet_read"](ctx)
    assert signed == [("get", [key])] and fetched == [f"https://bucket.example/{key}?signed"]
    checked = ReadResult.model_validate(result)
    [image] = checked.images
    assert image.error is None and image.registration and len(image.cells) > 500
    assert checked.model.weights_sha256 == sc.WEIGHTS_SHA256
    # Nothing of the photo was written to the worker's disk.
    assert not work.exists() or not any(work.rglob("*"))
