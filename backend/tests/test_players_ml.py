"""The players pipeline without a video (PLAN §10.5): the tracker on synthetic boxes, the
detector's output formats, shirt-number decoding, teams, clustering, the index the API
reads, the job's windows."""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from wieplay.core import court
from wieplay.core.players import Index
from wieplay.ml import bytetrack, identities, people
from wieplay.ml import players as pl
from wieplay.worker import players as job


def box(cx: float, cy: float, w: float = 40, h: float = 100) -> list[float]:
    return [cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2]


# --- tracker --------------------------------------------------------------------------------


def run_tracker(frames: list[list[tuple[list[float], float]]]) -> list[list[int]]:
    tracker = bytetrack.ByteTracker()
    out = []
    for dets in frames:
        boxes = np.array([b for b, _ in dets], dtype=float).reshape(-1, 4)
        scores = np.array([s for _, s in dets], dtype=float)
        out.append(tracker.update(boxes, scores).ids.tolist())
    return out


def test_two_people_keep_their_ids_while_walking() -> None:
    frames = [[(box(100 + 6 * k, 300), 0.9), (box(400 - 5 * k, 320), 0.85)] for k in range(20)]
    ids = run_tracker(frames)
    assert ids[0][0] != ids[0][1]
    assert all(row == ids[0] for row in ids)


def test_crossing_paths_do_not_swap_ids() -> None:
    # Two players pass each other, one in front: their boxes overlap for a few frames.
    frames = []
    for k in range(30):
        a = (box(100 + 10 * k, 300, 40, 100), 0.9)
        b = (box(400 - 10 * k, 330, 46, 116), 0.9)
        frames.append([a, b])
    ids = run_tracker(frames)
    first = ids[0]
    assert ids[-1] == first  # the left-to-right walker is still the first id


def test_a_missed_frame_and_a_weak_box_keep_the_track() -> None:
    frames: list[list[tuple[list[float], float]]] = []
    for k in range(12):
        if k == 5:
            frames.append([])  # not detected at all
        elif k == 8:
            frames.append([(box(100 + 5 * k, 300), 0.3)])  # half hidden: a weak box
        else:
            frames.append([(box(100 + 5 * k, 300), 0.9)])
    ids = run_tracker(frames)
    seen = {i for row in ids for i in row if i >= 0}
    assert len(seen) == 1 and ids[8] == ids[0]


def test_weak_boxes_never_start_a_track_and_ids_are_unique_after_reset() -> None:
    tracker = bytetrack.ByteTracker()
    out = tracker.update(np.array([box(100, 100)]), np.array([0.3]))
    assert out.ids.tolist() == [-1]
    first = tracker.update(np.array([box(100, 100)]), np.array([0.9])).ids[0]
    tracker.reset()
    second = tracker.update(np.array([box(100, 100)]), np.array([0.9])).ids[0]
    assert first >= 1 and second > first


def test_iou_matrix() -> None:
    a = np.array([[0, 0, 10, 10], [20, 20, 30, 30]], dtype=float)
    b = np.array([[5, 0, 15, 10]], dtype=float)
    iou = bytetrack.iou_matrix(a, b)
    assert iou.shape == (2, 1)
    assert iou[0, 0] == pytest.approx(50 / 150)
    assert iou[1, 0] == 0


# --- detector output, numbers ---------------------------------------------------------------


def test_decode_end_to_end_output_keeps_people_only() -> None:
    out = np.array(
        [[10, 20, 50, 120, 0.9, 0], [0, 0, 5, 5, 0.95, 32], [60, 20, 90, 100, 0.1, 0]],
        dtype=np.float32,
    )
    boxes, scores = people.decode(out, conf=0.15)
    assert boxes.tolist() == [[10, 20, 50, 120]] and scores.tolist() == pytest.approx([0.9])


def test_decode_classic_output_suppresses_duplicates() -> None:
    anchors = np.zeros((84, 3), dtype=np.float32)
    anchors[:4, 0] = [50, 50, 20, 40]
    anchors[:4, 1] = [51, 50, 20, 40]  # the same person again
    anchors[:4, 2] = [200, 80, 20, 40]
    anchors[4, :] = [0.8, 0.7, 0.6]
    boxes, scores = people.decode(anchors, conf=0.25)
    assert len(boxes) == 2
    assert scores.tolist() == pytest.approx([0.8, 0.6])


def test_letterbox_pads_to_multiples_of_32() -> None:
    img = np.zeros((600, 1610, 3), np.uint8)
    out, scale = people.letterbox(img, 1280)
    assert out.shape[1] == 1280 and out.shape[0] % 32 == 0
    assert scale == pytest.approx(1280 / 1610)


def test_ctc_digits() -> None:
    chars = [*"0123456789", *"ABCDEFGHIJKLMNOPQRSTUVWXYZ"]

    def probs(seq: list[int]) -> np.ndarray:
        p = np.full((len(seq), len(chars) + 2), 0.01, dtype=np.float32)
        for t, k in enumerate(seq):
            p[t, k] = 0.9
        return p

    # "1", "1", blank, "2" → "12"; class k is chars[k - 1]
    assert people.ctc_digits(probs([2, 2, 0, 3]), chars)[0] == "12"
    assert people.ctc_digits(probs([2, 0, 2]), chars)[0] == "11"
    text, conf = people.ctc_digits(probs([2, 12]), chars)  # "1A": a sponsor, not a number
    assert text == "" and conf == 0.0


def test_text_boxes_grow_around_a_blob() -> None:
    prob = np.zeros((64, 64), np.float32)
    prob[20:30, 25:35] = 0.9
    [(y0, y1, x0, x1)] = people.text_boxes(prob)
    assert y0 < 20 and y1 > 30 and x0 < 25 and x1 > 35


# --- geometry, teams, clusters ---------------------------------------------------------------


def calibration() -> dict[str, object]:
    """A camera behind the near end line, 5 m up (as in test_court)."""
    from tests.test_court import camera_points

    pts = camera_points((4.5, -6.0, 5.0), 1.1, list(court.LANDMARKS))
    sol = court.solve(pts, 16 / 9)
    assert sol.camera is not None
    return {"homography": court.as_lists(sol.image_to_court), "camera": sol.camera.as_dict()}


def test_geometry_masks_the_court_and_splits_at_the_net() -> None:
    geom = pl.geometry(calibration(), 16 / 9)
    assert geom.calibrated and geom.camera is not None
    x0, y0, x1, y1 = geom.roi
    assert 0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1
    assert y0 > 0.05  # the ceiling is cut away
    to_image = court.invert(court.from_lists(calibration()["homography"]))  # type: ignore[arg-type]
    near = court.apply(to_image, (4.5, 3.0))
    far = court.apply(to_image, (4.5, 15.0))
    stands = court.apply(to_image, (-6.0, 9.0))
    assert geom.side(geom.foot(*near), near[1]) == pl.NEAR
    assert geom.side(geom.foot(*far), far[1]) == pl.FAR
    assert geom.inside(geom.foot(*near)) and not geom.inside(geom.foot(*stands))


def test_geometry_without_a_calibration_uses_the_floor_and_the_feet() -> None:
    geom = pl.geometry(None, 16 / 9)
    assert geom.roi == (0.0, pl.FALLBACK_TOP, 1.0, 1.0) and not geom.calibrated
    feet = [0.55 + 0.01 * (i % 5) for i in range(30)] + [0.85 + 0.01 * (i % 5) for i in range(30)]
    split = pl.split_from_feet(feet)
    assert split is not None and 0.6 < split < 0.85


def test_each_camera_setup_has_its_own_court() -> None:
    calib = calibration()
    shifted = court.as_lists(
        court.matmul(court.from_lists(calib["homography"]), ((1, 0, -0.03), (0, 1, 0), (0, 0, 1)))  # type: ignore[arg-type]
    )
    setups = pl.Setups(
        [
            {"from_ms": 0, "to_ms": 60_000, "homography": calib["homography"], "camera": None},
            {"from_ms": 60_000, "to_ms": 90_000, "homography": shifted, "camera": None},
            {"from_ms": 90_000, "to_ms": None, "homography": None, "camera": None},
        ],
        None,
        16 / 9,
    )
    early, late, gone = setups.at(10_000), setups.at(70_000), setups.at(95_000)
    assert early.calibrated and late.calibrated and not gone.calibrated
    assert setups.at(20_000) is early
    # The same court point in the picture of the knocked camera sits 3 % further right.
    foot = court.apply(court.invert(court.from_lists(calib["homography"])), (4.5, 3.0))  # type: ignore[arg-type]
    x_early = early.foot(*foot)
    x_late = late.foot(foot[0] + 0.03, foot[1])
    assert x_early is not None and x_late is not None
    assert x_early == pytest.approx(x_late, abs=1e-6)
    single = pl.Setups([], calib, 16 / 9)
    assert single.at(0) is single.at(10**7) and single.at(0).calibrated


def test_roi_pixels_are_even() -> None:
    x, y, w, h = pl.roi_pixels((0.1234, 0.333, 0.9, 0.999), 1920, 1080)
    assert x % 2 == y % 2 == w % 2 == h % 2 == 0
    assert x + w <= 1920 and y + h <= 1080


def colour(hue: float, sat: float = 0.9) -> np.ndarray:
    """A crop whose shirt has the hue, dark shorts, skin-ish head."""
    import colorsys

    img = np.zeros((96, 40, 3), np.uint8)
    r, g, b = colorsys.hsv_to_rgb(hue, sat, 0.8)
    img[:, :] = (200, 160, 130)
    img[14:48] = (int(r * 255), int(g * 255), int(b * 255))
    img[48:68] = (20, 20, 20)
    return img


def track(
    tid: int, first: int, last: int, *, side: int = pl.NEAR, window: int = 0,
    hue: float = 0.0, number: int | None = None, votes: float = 2.0,
) -> pl.Track:  # fmt: skip
    t = pl.Track(tid, first, last, last - first + 1, side, window)
    t.descriptor = pl.descriptor(colour(hue))
    if number is not None:
        t.votes = {number: votes}
        pl.settle_number(t)
    return t


def test_descriptor_tells_kits_apart() -> None:
    red, red2, blue = (pl.descriptor(colour(h)) for h in (0.0, 0.01, 0.62))
    assert pl.distance(red, red2) < 0.3 < pl.distance(red, blue)


def test_teams_from_the_rally_sides_and_from_kits() -> None:
    # Window 0: we (red) are near; window 1 unknown: the teams switched ends.
    tracks = [
        track(1, 0, 50, side=pl.NEAR, window=0, hue=0.0),
        track(2, 0, 50, side=pl.FAR, window=0, hue=0.62),
        track(3, 60, 90, side=pl.FAR, window=1, hue=0.0),
        track(4, 60, 90, side=pl.NEAR, window=1, hue=0.62),
    ]
    info = identities.assign_teams(tracks, ["us", None]).info
    assert [t.team for t in tracks] == ["us", "them", "us", "them"]
    assert info == {
        "windows": 2, "known_windows": 1, "kit_guessed_windows": 1, "kit_corrected_windows": 0,
    }  # fmt: skip
    # Nothing known: near in the first window is ours, the kits do the rest.
    for t in tracks:
        t.team = "us"
    identities.assign_teams(tracks, [None, None])
    assert [t.team for t in tracks] == ["us", "them", "us", "them"]


def test_clusters_link_numbers_and_never_two_people_at_once() -> None:
    tracks = [
        track(1, 0, 50, number=7),
        track(2, 100, 150, number=7),  # the same number later: the same player
        track(3, 0, 50, hue=0.01),  # same kit, same time as 1: someone else
        track(4, 200, 260, hue=0.02),  # same kit, later: may be anyone, joins by kit
        track(5, 0, 50, side=pl.FAR, hue=0.62),
    ]
    identities.assign_teams(tracks, ["us"])
    clusters = pl.cluster_tracks(tracks)
    by_track = {t.track_id: c for c in clusters for t in c.tracks}
    assert by_track[1] is by_track[2]
    assert by_track[1].number == 7 and by_track[1].team == "us"
    assert by_track[3] is not by_track[1]
    assert by_track[5].team == "them"
    for c in clusters:
        spans = sorted((t.first, t.last) for t in c.tracks)
        assert all(a[1] < b[0] for a, b in itertools.pairwise(spans))
    assert [c.key for c in clusters] == list(range(1, len(clusters) + 1))


def test_different_numbers_never_merge_and_a_misread_number_is_dropped() -> None:
    tracks = [
        track(1, 0, 50, number=7),
        track(2, 100, 150, number=9),
        track(3, 20, 40, number=7, votes=1.3),  # also "7" while 1 plays: a misread
    ]
    identities.assign_teams(tracks, ["us"])
    clusters = pl.cluster_tracks(tracks)
    by_track = {t.track_id: c for c in clusters for t in c.tracks}
    assert by_track[1] is not by_track[2]
    assert by_track[3] is not by_track[1] and tracks[2].number is None


def test_off_court_tracks_form_a_not_a_player_cluster() -> None:
    t = track(1, 0, 50)
    t.referee = True
    clusters = pl.cluster_tracks([t, track(2, 0, 50)])
    assert clusters[-1].referee and clusters[-1].tracks == [t]


# --- the index the API reads ------------------------------------------------------------------


def test_the_index_round_trips_into_the_api_reader() -> None:
    import gzip
    import json

    geom = pl.Geometry(roi=(0.0, 0.0, 1.0, 1.0))
    col = pl.Collector(geom, 1000, 500, (0, 0, 1000, 500))
    img = np.zeros((500, 1000, 3), np.uint8)
    boxes = np.array([[100, 100, 140, 200], [600, 50, 620, 100]], dtype=float)
    for k in range(6):
        col.add(1000 + 100 * k, 0, img, boxes + k, np.array([0.9, 0.8]), np.array([1, 2]))
    tracks = pl.finish_tracks(col)
    assert {t.track_id for t in tracks} == {1, 2}
    payload = pl.encode_index(col, tracks)
    index = Index.from_bytes(gzip.compress(json.dumps(payload).encode()))
    assert list(index.t_ms) == [1000, 1100, 1200, 1300, 1400, 1500]
    frame = index.frame_near(1290)
    assert frame == 3
    found = dict(index.boxes(frame))
    assert set(found) == {1, 2}
    x1, _, _, y2 = found[1]
    assert x1 == pytest.approx(103 / 1000, abs=1e-4) and y2 == pytest.approx(203 / 500, abs=1e-4)
    assert index.frame_near(5000) is None


def test_contact_sheets_place_cluster_and_track_crops() -> None:
    t1, t2 = track(1, 0, 10), track(2, 20, 30)
    for t in (t1, t2):
        t.crops = [pl.Crop(100.0, 0, pl.encode_jpeg(colour(0.0)))]
    sheets = pl.contact_sheets([pl.Cluster(1, "us", [t1, t2])])
    assert len(sheets.pages) == 1 and sheets.pages[0][:2] == b"\xff\xd8"
    assert sheets.cluster_crops[1] == [[0, 0], [0, 1]]
    assert sheets.track_crops == {1: [0, 2], 2: [0, 3]}


# --- the job's windows --------------------------------------------------------------------------


def test_windows_pad_join_and_keep_agreeing_sides() -> None:
    rallies = [
        {"start_ms": 10_000, "end_ms": 20_000, "near_team": "us"},
        {"start_ms": 21_500, "end_ms": 30_000, "near_team": "us"},  # 1.5 s later: joined
        {"start_ms": 60_000, "end_ms": 70_000, "near_team": "them"},
        {"start_ms": 71_000, "end_ms": 72_000, "near_team": None},
    ]
    found = job.windows_from(rallies, 100_000)
    assert found == [
        {"start_ms": 9_000, "end_ms": 31_000, "near_team": "us"},
        {"start_ms": 59_000, "end_ms": 73_000, "near_team": None},
    ]


def test_windows_are_cut_per_camera_file() -> None:
    parts = [
        {"path": "/nas/a.mp4", "offset_ms": 0, "duration_ms": 30_000},
        {"path": "/nas/b.mp4", "offset_ms": 30_000, "duration_ms": 30_000},
    ]
    found = job.pieces({"start_ms": 25_000, "end_ms": 40_000}, parts)
    assert found == [("/nas/a.mp4", 0, 25_000, 30_000), ("/nas/b.mp4", 30_000, 30_000, 40_000)]
