"""Players v2 without a video (PLAN §10.5): stretches the tracker follows, the rendition read
from the bucket, the pieces of one person linked into chains (small synthetic tracks), a swap
cut where the shirt number changes, partial readings, "who is missing", the confidence, the
shirt-number glyphs, and the GPU worker's busy guard."""

from __future__ import annotations

import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from wieplay.ml import identities, people
from wieplay.ml import players as pl
from wieplay.worker import gpu_guard
from wieplay.worker import players as job

FPS = 10


def piece(
    tid: int,
    t0: float,
    t1: float,
    *,
    start: tuple[float, float] = (3.0, 4.0),
    end: tuple[float, float] | None = None,
    team: str = "us",
    number: int | None = None,
    votes: float = 2.0,
    look: float = 0.0,
) -> pl.Track:
    """A track seen from t0 to t1 seconds (frames at 10 fps), feet in court metres."""
    first, last = round(t0 * FPS), round(t1 * FPS)
    t = pl.Track(tid, first, last, last - first + 1, pl.NEAR, 0, team=team)
    t.t0_ms, t.t1_ms = round(t0 * 1000), round(t1 * 1000)
    t.first_foot, t.last_foot = start, end or start
    t.descriptor = np.full(44, 0.15) + look
    t.window_sides = {(0, pl.NEAR): t.detections}
    if number is not None:
        t.votes = {number: votes}
        pl.settle_number(t)
    return t


# --- stretches and the rendition ---------------------------------------------------------------


def test_spans_follow_rallies_through_short_breaks() -> None:
    windows = [
        {"start_ms": 0, "end_ms": 10_000},
        {"start_ms": 30_000, "end_ms": 40_000},  # 20 s later: the same set
        {"start_ms": 200_000, "end_ms": 210_000},  # a set break
    ]
    assert job.spans_of(windows, 40_000) == [
        {"start_ms": 0, "end_ms": 40_000, "windows": [0, 1]},
        {"start_ms": 200_000, "end_ms": 210_000, "windows": [2]},
    ]
    assert len(job.spans_of(windows, 2_000)) == 3  # the CPU: the rallies only


def test_segments_of_a_stretch() -> None:
    segments = [["k/seg_00000.m4s", 0, 4000], ["k/seg_00001.m4s", 4000, 4000]]
    segments.append(["k/seg_00002.m4s", 8000, 2500])
    assert job.segments_in(segments, 3_000, 5_000) == [
        ("k/seg_00000.m4s", 0, 4000),
        ("k/seg_00001.m4s", 4000, 4000),
    ]
    assert job.segments_in(segments, 9_000, 20_000) == [("k/seg_00002.m4s", 8000, 2500)]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_segments_joined_behind_the_init_segment_seek_in_the_video_s_time(tmp_path: Path) -> None:
    """A stretch fetched from the bucket is the init segment and its media segments in one
    file: the segments keep their timestamps, and -ss counts from the first one's start."""
    hls = tmp_path / "hls"
    hls.mkdir()
    args = [
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=128x72:rate=50",
        "-t", "16", "-c:v", "libx264", "-g", "100", "-keyint_min", "100",
        "-sc_threshold", "0", "-f", "hls", "-hls_time", "4", "-hls_segment_type", "fmp4",
        "-hls_playlist_type", "vod", "-hls_fmp4_init_filename", "init.mp4",
        "-hls_segment_filename", str(hls / "seg_%05d.m4s"), str(hls / "index.m3u8"),
    ]  # fmt: skip
    subprocess.run(args, check=True)  # noqa: S603 - fixed arguments
    joined = tmp_path / "span.mp4"
    with joined.open("wb") as out:
        for name in ("init.mp4", "seg_00002.m4s", "seg_00003.m4s"):
            out.write((hls / name).read_bytes())
    crop = (0, 0, 128, 72)
    # 9.0 s of the video = 1.0 s into the stretch that starts with segment 2 (8 s).
    got = list(job._frames(joined, 1.0, 0.5, crop, 10, 2))
    whole = tmp_path / "whole.mp4"
    with whole.open("wb") as out:
        for name in ("init.mp4", *(f"seg_0000{i}.m4s" for i in range(4))):
            out.write((hls / name).read_bytes())
    want = list(job._frames(whole, 9.0, 0.5, crop, 10, 2))
    assert len(got) == len(want) == 5
    assert np.abs(got[0].astype(int) - want[0].astype(int)).mean() < 2


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
def test_the_bucket_source_fetches_a_stretch_through_signed_urls(tmp_path: Path) -> None:
    """A GPU worker without the NAS: the stretch's segments come over HTTP (signed URLs from
    the API), the next stretch while the current one is decoded."""
    import functools
    import http.server

    hls = tmp_path / "hls"
    hls.mkdir()
    args = [
        "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=128x72:rate=50",
        "-t", "12", "-c:v", "libx264", "-g", "100", "-keyint_min", "100",
        "-sc_threshold", "0", "-f", "hls", "-hls_time", "4", "-hls_segment_type", "fmp4",
        "-hls_playlist_type", "vod", "-hls_fmp4_init_filename", "init.mp4",
        "-hls_segment_filename", str(hls / "seg_%05d.m4s"), str(hls / "index.m3u8"),
    ]  # fmt: skip
    subprocess.run(args, check=True)  # noqa: S603 - fixed arguments
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(hls))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}/"
    signed: list[str] = []

    class Api:
        def sign(self, job_id: str, op: str, keys: list[str]) -> dict[str, str]:
            assert op == "get"
            signed.extend(keys)
            return {k: base + k.rsplit("/", 1)[-1] for k in keys}

    class Ctx:
        api = Api()
        cancel = threading.Event()

        class job:
            id = "j1"

    rendition = {
        "init": "media/v/1080p/init.mp4",
        "segments": [[f"media/v/1080p/seg_0000{i}.m4s", 4000 * i, 4000] for i in range(3)],
    }
    source = job.BucketSource(Ctx(), rendition, tmp_path / "work")  # type: ignore[arg-type]
    spans = [{"start_ms": 5000, "end_ms": 6000}, {"start_ms": 9000, "end_ms": 9500}]
    got = []
    try:
        for si, piece in source.pieces(spans):
            assert piece.path.stat().st_size > 0
            got.append((si, piece.offset_ms, piece.start_ms, piece.end_ms))
            piece.done()
    finally:
        source.close()
        server.shutdown()
    assert got == [(0, 4000, 5000, 6000), (1, 8000, 9000, 9500)]
    assert "media/v/1080p/seg_00001.m4s" in signed and "media/v/1080p/seg_00000.m4s" not in signed
    assert source.bytes > 0 and not any((tmp_path / "work").glob("span-*/span.mp4"))


# --- chains ------------------------------------------------------------------------------------


def test_a_person_hidden_for_a_moment_is_one_chain() -> None:
    a = piece(1, 0, 5, end=(3.0, 4.0))
    b = piece(2, 5.6, 9, start=(3.5, 4.2))  # 0.6 s later, half a metre away
    c = piece(3, 5.5, 9, start=(8.0, 14.0))  # someone on the other side
    chains = identities.stitch([a, b, c])
    assert [[t.track_id for t in ch] for ch in chains] == [[1, 2], [3]]


def test_the_closer_start_wins_and_numbers_or_teams_never_link() -> None:
    a = piece(1, 0, 5, end=(3.0, 4.0))
    near = piece(2, 5.5, 9, start=(3.3, 4.0))
    farther = piece(3, 5.5, 9, start=(5.0, 4.0))
    assert [[t.track_id for t in ch] for ch in identities.stitch([a, near, farther])] == [
        [1, 2],
        [3],
    ]
    seven = piece(4, 0, 5, number=7)
    nine = piece(5, 5.5, 9, start=(3.2, 4.0), number=9)
    theirs = piece(6, 5.5, 9, start=(3.1, 4.0), team="them")
    assert len(identities.stitch([seven, nine, theirs])) == 3


def test_the_long_pass_links_only_when_nobody_else_could_be_them() -> None:
    a = piece(1, 0, 10, end=(3.0, 4.0))
    b = piece(2, 20, 30, start=(5.0, 6.0))  # 10 s later: only a ended, only b began
    chains = identities.chains_of([a, b])
    assert len(chains) == 1 and [m.track_id for m in chains[0].members] == [1, 2]
    other = piece(3, 21, 30, start=(4.0, 5.0))  # two could be a now
    assert len(identities.chains_of([piece(1, 0, 10), piece(2, 20, 30), other])) == 3


def test_a_chain_that_reads_two_numbers_is_cut_where_it_changes() -> None:
    parts = [piece(1, 0, 4, number=7), piece(2, 5, 9), piece(3, 10, 14, number=7)]
    parts += [piece(4, 15, 19, number=9), piece(5, 20, 24)]
    cut = identities.split_conflicts(parts)
    assert [[t.track_id for t in c] for c in cut] == [[1, 2, 3], [4, 5]]


def test_chains_of_a_person_carry_its_number_and_look() -> None:
    a = piece(1, 0, 5, number=12, votes=1.0)
    b = piece(2, 5.5, 9, start=(3.2, 4.0), number=12, votes=1.0)
    [chain] = identities.chains_of([a, b])
    assert chain.number == 12  # neither piece read it often enough alone
    assert chain.segments == [(a.first, a.last), (b.first, b.last)]
    assert chain.t0_ms == 0 and chain.t1_ms == 9000


# --- readings --------------------------------------------------------------------------------


def test_partial_readings_count_for_the_two_digit_number() -> None:
    assert pl.fold_partials({12: 2.0, 2: 1.0}) == {12: 3.0}
    assert pl.fold_partials({12: 0.3, 2: 2.0}) == {12: 0.3, 2: 2.0}  # 2 is no part of a rare 12
    assert pl.fold_partials({12: 1.0, 21: 1.0, 2: 1.0}) == {12: 1.0, 21: 1.0, 2: 1.0}
    t = pl.Track(1, 0, 50, 51, pl.NEAR, 0, votes={12: 0.9, 2: 0.8})
    pl.settle_number(t)
    assert t.number == 12


def test_a_swap_shows_as_the_number_changing() -> None:
    reads = [(10, 7, 0.9), (20, 7, 0.9), (30, 7, 0.8), (40, 9, 0.9), (50, 9, 0.9)]
    assert identities.number_switch(reads) == 35
    assert identities.number_switch([(10, 7, 0.9), (20, 9, 0.5), (30, 7, 0.9)]) is None
    partial = [(10, 12, 0.9), (20, 2, 0.9), (30, 12, 0.9), (40, 2, 0.8)]
    assert identities.number_switch(identities.fold_reads(partial)) is None


def test_split_swaps_cuts_a_track_and_the_index_follows_the_pieces() -> None:
    import gzip
    import json

    from wieplay.core.players import Index

    geom = pl.Geometry(roi=(0.0, 0.0, 1.0, 1.0))
    col = pl.Collector(geom, 1000, 500, (0, 0, 1000, 500))
    img = np.zeros((500, 1000, 3), np.uint8)
    for k in range(40):
        box = np.array([[100 + k, 100, 140 + k, 300]], dtype=float)
        col.add(1000 * k, 0, img, box, np.array([0.9]), np.array([1]), span=0)
    [track] = pl.finish_tracks(col)
    track.reads = [(f, 7, 0.9) for f in (2, 6, 10, 14)] + [(f, 9, 0.9) for f in (25, 30, 35)]
    teams = identities.assign_teams([track], ["us"])
    parts, cuts = identities.split_swaps([track], col, teams)
    assert cuts == 1
    assert [(p.first, p.last, p.number) for p in parts] == [(0, 19, 7), (20, 39, 9)]
    chains = identities.chains_of(parts)
    payload = pl.encode_index(col, chains)
    index = Index.from_bytes(gzip.compress(json.dumps(payload).encode()))
    ids = {tid for f in range(40) for tid, _ in index.boxes(f)}
    assert ids == {c.track_id for c in chains} and len(ids) == 2


# --- who is missing, confidence ------------------------------------------------------------------


def cluster(key: int, *tracks: pl.Track, number: int | None = None) -> pl.Cluster:
    c = pl.Cluster(key, tracks[0].team, list(tracks))
    c.number = number
    return c


def test_a_card_without_a_number_joins_the_one_teammate_missing_then() -> None:
    seven = cluster(1, piece(1, 0, 60, number=7), number=7)
    nine = cluster(2, piece(2, 0, 30, number=9), piece(3, 45, 60, number=9), number=9)
    unknown = cluster(3, piece(4, 31, 44))
    clusters = [seven, nine, unknown]
    assert identities.absorb(clusters) == 1
    assert [c.number for c in clusters] == [7, 9] and [c.key for c in clusters] == [1, 2]
    assert {t.track_id for t in clusters[1].tracks} == {2, 3, 4}


def test_no_join_when_two_teammates_could_be_it() -> None:
    seven = cluster(1, piece(1, 0, 30, number=7), number=7)
    nine = cluster(2, piece(2, 0, 30, number=9), number=9)
    unknown = cluster(3, piece(3, 33, 44))
    clusters = [seven, nine, unknown]
    assert identities.absorb(clusters) == 0 and len(clusters) == 3


def test_confidence_favours_a_number_read_often_and_time_on_court() -> None:
    sure = cluster(1, piece(1, 0, 200, number=7, votes=6.0), number=7)
    sure.number_conf = 0.8
    brief = cluster(2, piece(2, 0, 5))
    mixed = cluster(3, piece(3, 0, 200))
    mixed.tracks[0].votes = {7: 2.0, 9: 2.0}
    scores = [identities.confidence(c, FPS) for c in (sure, brief, mixed)]
    assert scores[0] > 0.8 > identities.UNCERTAIN > scores[1]
    assert scores[2] < identities.UNCERTAIN


def test_teams_follow_each_detection_through_a_side_switch() -> None:
    # One person followed through two windows: near while we were near, far after the
    # teams switched ends. Ours both times.
    t = piece(1, 0, 60)
    t.window_sides = {(0, pl.NEAR): 300, (1, pl.FAR): 290}
    teams = identities.assign_teams([t], ["us", "them"])
    assert t.team == "us" and teams.near_us == {0: True, 1: False}


# --- shirt numbers ----------------------------------------------------------------------------


def test_lookalike_letters_count_as_digits_only_when_asked() -> None:
    chars = list("0123456789IOS")
    probs = np.zeros((4, 14))
    for t, k in enumerate((11, 0, 12, 0)):  # "I", blank, "O", blank
        probs[t, k] = 0.9
    assert people.ctc_digits(probs, chars) == ("", 0.0)
    text, conf = people.ctc_digits(probs, chars, lookalike=True)
    assert text == "10" and conf == pytest.approx(0.9)


def test_glyph_boxes_find_a_number_on_a_shirt() -> None:
    person = np.zeros((240, 100, 3), np.uint8)
    person[:, :] = (180, 30, 40)  # a red shirt
    person[130:, :] = (20, 20, 20)  # shorts and legs
    person[50:100, 40:46] = (240, 240, 240)  # a white "1"
    person[50:56, 52:70] = (240, 240, 240)  # and a "7": its bar
    person[56:100, 62:68] = (240, 240, 240)  # and its stem
    found = people.glyph_boxes(person, 90.0)
    assert found, "no glyphs"
    y0, y1, x0, x1 = found[0]
    assert y0 <= 50 and y1 >= 100 and x0 <= 40 and x1 >= 70  # both digits, one group


# --- the GPU worker's busy guard ---------------------------------------------------------------


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def guard(
    state: gpu_guard.GpuState,
    *,
    mains: bool = True,
    pause: Path | None = None,
    clock: Clock | None = None,
) -> gpu_guard.GpuGuard:
    return gpu_guard.GpuGuard(
        gpu_guard.GuardSettings(max_other_mib=1024, max_util=40, pause_file=pause, max_pause_s=60),
        read=lambda: state,
        mains=lambda: mains,
        own_pid=42,
        clock=clock or Clock(),
    )


def test_the_guard_waits_for_the_charger_the_card_and_the_pause_file(tmp_path: Path) -> None:
    free = gpu_guard.GpuState(util=0, apps=[gpu_guard.GpuApp(42, "python", 1800)])
    assert guard(free).reason() is None  # its own memory does not count
    assert guard(free, mains=False).reason() == "on battery"
    scribe = gpu_guard.GpuState(util=5, apps=[gpu_guard.GpuApp(7, "ae-loyal-scribe-gpu", 4100)])
    assert "4100 MiB" in (guard(scribe).reason() or "")
    busy = gpu_guard.GpuState(util=90)
    assert guard(busy).reason() == "the GPU is 90 % busy"
    assert guard(busy).reason(in_job=True) is None  # its own load, while it runs a job
    pause = tmp_path / "PAUSE"
    pause.touch()
    assert "paused" in (guard(free, pause=pause).reason() or "")
    broken = gpu_guard.GpuState(error="nvidia-smi: not found")
    assert "cannot read" in (guard(broken).reason() or "")


def test_a_job_gives_way_and_is_given_back_after_too_long() -> None:
    clock = Clock()
    state = gpu_guard.GpuState(util=0)
    g = guard(state, clock=clock)
    cancel = threading.Event()
    g.start_job()
    g.yield_while_busy(cancel)  # free: returns at once
    state.apps = [gpu_guard.GpuApp(7, "scribe", 4000)]
    clock.now = 5.0
    g.yield_while_busy(cancel)  # checked at most every CHECK_EVERY_S
    clock.now = 20.0
    messages: list[str] = []

    def wait(_seconds: float) -> bool:
        clock.now += 30
        return False

    cancel.wait = wait  # type: ignore[method-assign]
    with pytest.raises(gpu_guard.GpuBusy) as raised:
        g.yield_while_busy(cancel, messages.append)
    assert raised.value.seconds == 1800 and messages and "paused" in messages[0]
    assert g.paused_s > 60


def test_power_from_the_supplies(tmp_path: Path) -> None:
    def supply(name: str, kind: str, online: str | None = None) -> None:
        d = tmp_path / name
        d.mkdir()
        (d / "type").write_text(kind)
        if online is not None:
            (d / "online").write_text(online)

    supply("BAT1", "Battery")
    supply("ACAD", "Mains", "0")
    assert gpu_guard.on_mains(tmp_path) is False
    (tmp_path / "ACAD" / "online").write_text("1")
    assert gpu_guard.on_mains(tmp_path) is True
    assert gpu_guard.on_mains(tmp_path / "missing") is True  # a desktop


def test_nvidia_smi_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    outputs = iter(["37, 5900, 8151\n", "4242, /usr/bin/python3, 4100\n77, [N/A], [N/A]\n"])

    def fake_run(*_args: Any, **_kw: Any) -> Any:
        return subprocess.CompletedProcess([], 0, stdout=next(outputs), stderr="")

    monkeypatch.setattr(gpu_guard.subprocess, "run", fake_run)
    state = gpu_guard.read_gpu()
    assert (state.util, state.used_mib, state.total_mib) == (37, 5900, 8151)
    assert [(a.pid, a.name, a.mib) for a in state.apps] == [
        (4242, "python3", 4100),
        (77, "[N/A]", 0),
    ]


def test_one_job_at_a_time_on_the_machine_s_gpu(tmp_path: Path) -> None:
    def g() -> gpu_guard.GpuGuard:
        return gpu_guard.GpuGuard(
            gpu_guard.GuardSettings(lock_file=tmp_path / "gpu.lock"),
            read=gpu_guard.GpuState,
            mains=lambda: True,
        )

    prod, dev = g(), g()
    stop = threading.Event()
    assert prod.lock(stop)
    stop.set()
    assert dev.lock(stop) is False  # busy: waits, and gives up when asked to stop
    prod.unlock()
    assert dev.lock(threading.Event())
    dev.unlock()


def test_numbers_cut_chains_and_a_misread_loses_its_number() -> None:
    a1 = piece(1, 0, 10, number=12, votes=3.0)
    a2 = piece(2, 10.5, 20)
    a3 = piece(3, 20.5, 30, number=9, votes=3.0)
    chain = identities.as_chain([a1, a2, a3])
    rival = identities.as_chain([piece(4, 5, 15, number=12, votes=1.3)])  # "12" while a1 plays
    runs = identities.assign_numbers([chain, rival])
    by_members = {tuple(m.track_id for m in (r.members or [r])): r.number for r in runs}
    assert by_members == {(1, 2): 12, (3,): 9, (4,): None}


def test_the_kits_correct_a_window_whose_sides_were_recorded_the_wrong_way_round() -> None:
    import colorsys

    def look(hue: float) -> np.ndarray:
        img = np.zeros((96, 40, 3), np.uint8)
        r, g, b = colorsys.hsv_to_rgb(hue, 0.9, 0.8)
        img[:, :] = (200, 160, 130)
        img[14:48] = (int(r * 255), int(g * 255), int(b * 255))
        img[48:68] = (20, 20, 20)
        return pl.descriptor(img)

    tracks = []
    for w in range(4):
        # Red (us) near in every window; window 3 says "them" near (recorded wrongly).
        for k, (side, hue) in enumerate(((pl.NEAR, 0.0), (pl.FAR, 0.62))):
            t = piece(10 * w + k, w * 60, w * 60 + 50)
            t.window, t.side, t.descriptor = w, side, look(hue)
            t.window_sides = {(w, side): t.detections}
            tracks.append(t)
    teams = identities.assign_teams(tracks, ["us", "us", "us", "them"])
    assert teams.info["kit_corrected_windows"] == 1 and teams.near_us[3] is True
    assert {t.team for t in tracks if t.side == pl.NEAR} == {"us"}


def test_a_second_card_with_a_number_is_to_be_checked() -> None:
    big = cluster(1, piece(1, 0, 300, number=12, votes=8.0), number=12)
    big.number_conf = 0.9
    small = cluster(2, piece(2, 100, 200, number=12, votes=8.0), number=12)
    small.number_conf = 0.9
    identities.set_confidence([small, big], FPS)
    assert big.confidence > identities.UNCERTAIN > small.confidence
