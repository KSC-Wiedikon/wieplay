"""The official scoresheet for the players, without a database (PLAN §10.3a, §10.5): who is on
court per point, the server behind the end line or stepping onto the court, the receiving
team's order as soft evidence only (the serving team has none since the 2025 rules), who may
be read, a piece of track given to the team whose number it reads, the sheet's numbers per
player, and the sheet's timeouts, substitutions and sanctions checked at their breaks. Made-up
shirt numbers and positions only."""

from __future__ import annotations

import random

import pytest

from wieplay.core import court, sheet_notes
from wieplay.ml import identities, on_court
from wieplay.ml import players as pl
from wieplay.ml import serve_labels as slb
from wieplay.ml import sheet as sh
from wieplay.worker import players as job

US = (7, 3, 11, 5, 9, 1)
THEM = (4, 8, 12, 14, 2, 6)


def a_set(winners: list[str], first: str = "us", **notes: object) -> tuple[sh.SheetSet, sh.Decoded]:
    grids, circled = sh.encode(first, winners)  # type: ignore[arg-type]
    s = sh.SheetSet(
        1, first, grids, False, circled, {"us": list(US), "them": list(THEM)},  # type: ignore[arg-type]
        **notes,  # type: ignore[arg-type]
    )  # fmt: skip
    return s, sh.decode(s)


def to_25(start: str = "us") -> list[str]:
    """A set to 25:20 with side-outs and a run (made up)."""
    out = ["us", "us", "them", "them", "us"] * 4 + ["them"] * 4 + ["us"] * 5
    assert out.count("us") == 17
    out += ["us"] * 8
    return out


# --- who is on court ---------------------------------------------------------------------------


def test_every_point_has_its_server_in_zone_one_and_substitutions_apply() -> None:
    winners = to_25()
    sub = sh.Sub("us", 2, 17, (5, 4))  # #17 in for #3 (position II) at 5:4
    s, d = a_set(winners, subs=[sub])
    assert d.consistent
    lineups = on_court.point_lineups(s, d)
    assert len(lineups) == len(winners)
    first = lineups[0]
    assert first.serving == "us" and first.server == 7 and first.zones["us"] == US
    assert first.zones["them"] == THEM  # they received first: not rotated yet
    for lu in lineups:
        assert lu.server == lu.zones[lu.serving][0]  # zone I serves
    k = d.sub_at[0][0]
    assert k is not None
    assert 3 in lineups[k - 1].numbers("us") and 17 not in lineups[k - 1].numbers("us")
    assert 17 in lineups[k].numbers("us") and 3 not in lineups[k].numbers("us")
    # Their first side-out rotates them: their position II player serves.
    them_serves = next(lu for lu in lineups if lu.serving == "them")
    assert them_serves.server == THEM[1]


def test_who_may_be_read_is_the_six_and_the_liberos() -> None:
    s, d = a_set(to_25())
    lu = on_court.point_lineups(s, d)[0]
    assert on_court.allowed(lu, "us", [10]) == {*US, 10}
    assert on_court.allowed(lu, "them") == set(THEM)
    assert on_court.allowed(None, "us") is None


# --- the server ---------------------------------------------------------------------------------


def seen(key: int, x: float, y: float) -> on_court.Seen:
    return on_court.Seen(key, (x, y), 0.5, 0.1)


def test_the_server_is_the_one_behind_the_end_line() -> None:
    people = [seen(1, 8.0, -2.0), seen(2, 4.0, 3.0), seen(3, 2.0, 6.0)]
    assert on_court.server_calibrated(people, near_end=True) == 1
    assert on_court.server_calibrated(people, near_end=False) is None  # nobody behind the far
    far = [seen(5, 1.0, 20.5), seen(6, 4.0, 15.0)]
    assert on_court.server_calibrated(far, near_end=False) == 5
    # Two at about the same depth behind the line: no telling which serves.
    two = [seen(1, 8.0, -2.0), seen(2, 1.0, -1.6)]
    assert on_court.server_calibrated(two, near_end=True) is None
    # Off to the side (a line judge at the corner, past the side line): not the server.
    assert on_court.server_calibrated([seen(1, -3.0, -1.0)], near_end=True) is None


def test_without_a_calibration_the_server_stands_clearly_apart() -> None:
    low = [on_court.Seen(1, None, 0.95, 0.2), on_court.Seen(2, None, 0.7, 0.15)]
    assert on_court.server_uncalibrated(low, near_end=True) == 1
    close = [on_court.Seen(1, None, 0.75, 0.2), on_court.Seen(2, None, 0.72, 0.2)]
    assert on_court.server_uncalibrated(close, near_end=True) is None


# --- the receiving team: soft order, the serving team: none ---------------------------------------


def receivers(near_half: bool, jitter: float = 0.0, seed: int = 1) -> list[on_court.Seen]:
    """Six receivers in zone order (IV III II front, V VI I back) on one half, as the team
    looks at the net: zone k gets key k."""
    rng = random.Random(seed)  # noqa: S311 - made-up positions
    spots = {
        4: (1.5, 7.0),
        3: (4.5, 7.0),
        2: (7.5, 7.0),
        5: (1.5, 3.0),
        6: (4.5, 3.0),
        1: (7.5, 3.0),
    }
    out = []
    for zone, (lat, depth) in spots.items():
        x = lat if near_half else court.WIDTH_M - lat  # the far team looks at the camera
        y = depth if near_half else court.LENGTH_M - depth
        out.append(seen(zone, x + rng.uniform(-jitter, jitter), y + rng.uniform(-jitter, jitter)))
    return out


@pytest.mark.parametrize("near_half", [True, False])
def test_receivers_in_order_get_their_zones_softly(near_half: bool) -> None:
    probs = on_court.receiving_zones(receivers(near_half), near_half)
    for zone in range(1, 7):
        best = max(probs[zone].items(), key=lambda kv: kv[1])
        assert best[0] == zone
        assert 0.5 < best[1] < 1.0  # evidence, never a certainty
        assert abs(sum(probs[zone].values()) - 1.0) < 1e-9


def test_a_stacked_reception_says_little() -> None:
    """Five receivers in one line across the court (a stack) and the setter at the net: the
    order cannot tell the line's people apart in depth, only left from right."""
    line = [seen(k, 1.0 + 1.7 * k, 4.0) for k in range(5)] + [seen(9, 7.0, 8.0)]
    probs = on_court.receiving_zones(line, near_half=True)
    top = [max(p.values()) for k, p in probs.items() if k != 9]
    assert max(top) < 0.6  # nobody's zone is anywhere near sure


def test_order_is_soft_a_pair_slightly_the_wrong_way_round_stays_possible() -> None:
    """IV and III stand 0.3 m the wrong way round: both answers stay likely (people step and
    overlap), never ruled out."""
    people = [seen(4, 4.6, 7.0), seen(3, 4.3, 7.0)]
    people += [p for p in receivers(True) if p.key not in (3, 4)]
    probs = on_court.receiving_zones(people, near_half=True)
    assert 0.15 < probs[4][4] < 0.6 and 0.15 < probs[4][3] < 0.6


# --- the job's labels on a made-up match --------------------------------------------------------


def collector(frames: int) -> pl.Collector:
    geom = pl.Geometry(
        roi=(0.0, 0.0, 1.0, 1.0), to_court=court.from_lists([[1, 0, 0], [0, 1, 0], [0, 0, 1]])
    )
    col = pl.Collector(geom, 1000, 500, (0, 0, 1000, 500))
    col.times = [100 * k for k in range(frames)]
    col.windows = [0] * frames
    col.spans = [0] * frames
    return col


def walk(col: pl.Collector, tid: int, frames: range, feet: list[tuple[float, float]]) -> None:
    tr = col.tracks.setdefault(tid, pl.TrackData(tid, window=0))
    for f, foot in zip(frames, feet, strict=True):
        tr.frames.append(f)
        side = pl.NEAR if foot[1] < court.NET_Y else pl.FAR
        tr.sides.append(side)
        v = 0.9 - foot[1] / 30
        tr.foot_v.append(v)
        tr.heights_px.append(120.0)
        tr.boxes.append((foot[0] / 10, v - 0.2, foot[0] / 10 + 0.05, v))
        tr.foot_at.append(foot)
        col.rows.append((f, tid, foot[0] / 10, v - 0.2, foot[0] / 10 + 0.05, v))


def rally(start_ms: int, serving: str, server: int, *, first: bool = False) -> slb.RallySheet:
    return slb.RallySheet(
        start_ms, start_ms + 8000, serving, server, {"us": US, "them": THEM},  # type: ignore[arg-type]
        set_no=1, first=first,
    )  # fmt: skip


def test_the_server_steps_onto_the_court_after_the_serve() -> None:
    """The near server below the picture during the serve: first seen at the end line a
    second later (in two pieces: the longer one is labelled); the receivers far, untouched."""
    col = collector(200)
    walk(col, 1, range(110, 112), [(7.9, -0.8), (7.9, -0.7)])  # a fragment
    walk(col, 2, range(112, 180), [(7.8, -0.6 + 0.05 * k) for k in range(68)])
    for k, x in enumerate((1.5, 4.5, 7.5)):  # their front row, far
        walk(col, 10 + k, range(80, 180), [(x, 11.0)] * 100)
    tracks = pl.finish_tracks(col)
    for t in tracks:
        t.team = "us" if t.track_id < 10 else "them"
    found = slb.serve_labels(col, tracks, [rally(10_000, "us", 7)], ["us"], calibrated=True)
    assert [(lab.track, lab.number, lab.kind) for lab in found.labels] == [(2, 7, "server")]
    assert found.info["servers_entering"] == 1
    # The far end's server is not labelled by default (USE), nor the receivers.
    far = slb.serve_labels(col, tracks, [rally(10_000, "us", 7)], ["them"], calibrated=True)
    assert far.labels == []
    added = slb.add_labels(tracks, found.labels)
    assert added == 1
    [t2] = [t for t in tracks if t.track_id == 2]
    assert t2.reads == [(112, 7, slb.SERVER_WEIGHT)]


def test_two_people_stepping_on_apart_give_no_server() -> None:
    col = collector(200)
    walk(col, 1, range(112, 160), [(1.0, -0.6)] * 48)
    walk(col, 2, range(112, 160), [(8.0, -0.6)] * 48)
    tracks = pl.finish_tracks(col)
    assert slb.entering_server(col, tracks, 10_000, serving_near=True, calibrated=True) is None


def test_the_serving_team_s_other_five_get_no_label_whatever_their_order() -> None:
    col = collector(200)
    walk(col, 1, range(50, 120), [(8.0, -2.0)] * 70)  # the server, seen behind the line
    for k, (x, y) in enumerate(((1.5, 7.0), (4.5, 7.0), (7.5, 7.0), (1.5, 3.0), (4.5, 3.0))):
        walk(col, 10 + k, range(50, 150), [(x, y)] * 100)
    tracks = pl.finish_tracks(col)
    for t in tracks:
        t.team = "us"
    use = frozenset({"server", "receivers"})
    found = slb.serve_labels(col, tracks, [rally(10_000, "us", 7)], ["us"], True, use=use)
    assert [(lab.track, lab.number) for lab in found.labels] == [(1, 7)]
    assert found.info["servers_behind"] == 1


def test_who_may_be_read_during_and_between_rallies() -> None:
    col = collector(400)
    first = rally(1_000, "us", 7, first=True)
    later = slb.RallySheet(20_000, 28_000, "them", 4, {"us": (17, 3, 11, 5, 9, 1), "them": THEM})  # type: ignore[arg-type]
    sheet = slb.SheetInputs([first, later], {"us": {10}}, {}, {"us": {*US, 17}, "them": set(THEM)})
    ok = slb.Allowed(col, sheet, {"us": {*US, 10, 17, 22}}).ok
    assert ok("us", 50, 7) and ok("us", 50, 10) and not ok("us", 50, 17)  # in the first rally
    assert ok("us", 150, 7) and ok("us", 150, 17)  # between the two: either's
    assert ok("us", 230, 17) and not ok("us", 230, 7)  # after the substitution
    assert not ok("us", 50, 22)  # on the roster, on the bench
    # Their liberos are unknown: a number their sheet never has could be one; a number their
    # sheet has (on the bench now) cannot.
    bench = slb.SheetInputs([first], {}, {}, {"them": {*THEM, 13}})
    ok_them = slb.Allowed(col, bench).ok
    assert ok_them("them", 50, 15) and ok_them("them", 50, 4) and not ok_them("them", 50, 13)


def test_a_piece_reading_a_number_only_the_other_team_has_on_court_is_theirs() -> None:
    col = collector(300)
    walk(col, 1, range(10, 90), [(4.0, 8.5)] * 80)  # our blocker at the net
    walk(col, 2, range(10, 90), [(4.0, 12.0)] * 80)
    tracks = pl.finish_tracks(col)
    by = {t.track_id: t for t in tracks}
    by[1].team = "them"  # the jumps put the feet past the net
    by[1].reads = [(20, 11, 0.9), (40, 11, 0.9)]  # #11: ours on court, not theirs
    by[2].team = "them"
    by[2].reads = [(20, 12, 0.9), (40, 12, 0.9)]  # #12: theirs on court
    sheet = slb.SheetInputs([rally(1_000, "us", 7)], {}, {}, {"us": set(US), "them": set(THEM)})
    changed = slb.fix_teams(tracks, slb.Allowed(col, sheet))
    assert changed == 1 and by[1].team == "us" and by[2].team == "them"
    # A number both teams have on court decides nothing.
    both = slb.RallySheet(1_000, 9_000, "us", 7, {"us": US, "them": (7, 8, 12, 14, 2, 6)})  # type: ignore[arg-type]
    by[1].team = "them"
    by[1].reads = [(20, 7, 0.9), (40, 7, 0.9)]
    sheet2 = slb.SheetInputs([both], {}, {}, {"us": set(US), "them": {7, 8, 12, 14, 2, 6}})
    assert slb.fix_teams(tracks, slb.Allowed(col, sheet2)) == 0 and by[1].team == "them"


def test_the_job_takes_the_sheet_from_its_inputs() -> None:
    inputs = {
        "rallies": [
            {"start_ms": 0, "end_ms": 8000, "near_team": "us"},
            {"start_ms": 20_000, "end_ms": 28_000, "near_team": "us",
             "sheet": {"set": 1, "point": 2, "serving": "us", "server": 7, "us": list(US),
                       "them": list(THEM)}},
        ],
        "sheet": {"liberos": {"us": [10]}, "middles": {"us": [11]}, "numbers": {"us": [7, 3]}},
    }  # fmt: skip
    sheet = slb.sheet_inputs(inputs)
    assert sheet is not None and sheet.rallies[0] is None
    assert sheet.rallies[1] is not None and sheet.rallies[1].server == 7
    assert sheet.liberos == {"us": {10}} and sheet.middles == {"us": {11}}
    assert slb.sheet_inputs({"rallies": [{"start_ms": 0, "end_ms": 1}]}) is None


def test_the_tracker_can_start_before_a_set_s_first_rally() -> None:
    rallies = [
        {"start_ms": 60_000, "end_ms": 70_000, "near_team": "us", "lead_ms": 45_000},
        {"start_ms": 90_000, "end_ms": 99_000, "near_team": "us"},
    ]
    windows = job.windows_from(rallies, 200_000)
    assert windows[0]["lead_ms"] == 45_000 and windows[0]["rallies"] == [0]
    spans = job.spans_of(windows, 40_000)
    assert spans == [{"start_ms": 14_000, "end_ms": 100_000, "windows": [0, 1]}]


def test_identify_with_the_sheet_records_its_labels() -> None:
    col = collector(300)
    walk(col, 2, range(112, 260), [(7.8, -0.6 + 0.02 * k) for k in range(148)])
    for k, x in enumerate((1.5, 4.5, 7.5)):
        walk(col, 10 + k, range(80, 280), [(x, 11.0)] * 200)
    windows = [{"start_ms": 9000, "end_ms": 28_000, "near_team": "us", "rallies": [0]}]
    sheet = slb.SheetInputs([rally(10_000, "us", 7)], {}, {}, {"us": set(US), "them": set(THEM)})
    out = job.identify(col, windows, None, {"us": list(US)}, 10.0, sheet=sheet)
    assert out.stats["sheet_labels"] == 1 and out.stats["sheet_servers"] == 1
    assert out.stats["sheet_use"] == sorted(slb.USE)
    without = job.identify(collector(10), windows, None, {}, 10.0)
    assert "sheet_labels" not in without.stats


# --- the sheet's numbers per player -----------------------------------------------------------


def test_serves_runs_aces_and_plus_minus_per_player() -> None:
    winners = to_25()
    s, d = a_set(winners)
    lineups = on_court.point_lineups(s, d)
    grades = {(1, 0): "#", (1, 1): "="}  # an ace and an error tagged on the first two serves
    lines = on_court.player_lines([lineups], grades)
    served = sum(line.served for line in lines.values())
    assert served == len(winners)
    seven = lines[("us", 7)]
    # #7 served the first rallies: won two (a run of two), then lost the serve.
    assert seven.served >= 3 and seven.won_on_serve >= 2 and seven.longest_run >= 2
    assert seven.aces == 1 and seven.errors == 1 and seven.tagged == 2
    # On court for every point (no substitution): +/- is the set's difference.
    assert seven.on_court == len(winners)
    assert seven.plus_minus == winners.count("us") - winners.count("them")
    assert sum(seven.by_rotation) == seven.plus_minus
    them = lines[("them", THEM[0])]
    assert them.plus_minus == -seven.plus_minus


def test_the_longest_run_is_one_server_s_turn() -> None:
    s, d = a_set(["us"] * 5 + ["them"] + ["us"] * 2 + ["them"] * 3 + ["us"] * 20)
    lines = on_court.player_lines([on_court.point_lineups(s, d)])
    assert lines[("us", 7)].longest_run == 5  # 5 straight on #7's first turn


# --- the notes at their breaks -----------------------------------------------------------------


def facts(
    n: int, gap_s: float = 15.0, long_after: int | None = None
) -> list[sheet_notes.RallyFacts]:
    """n rallies of 8 s, one point each, the gap after rally `long_after` 60 s."""
    out = []
    t = 0
    for k in range(n):
        out.append(sheet_notes.RallyFacts(f"r{k}", t, t + 8000, None, None))
        t += 8000 + int(1000 * (60.0 if k == long_after else gap_s))
    return out


def test_a_timeout_falls_in_the_break_after_its_point_and_is_checked() -> None:
    winners = to_25()
    # Them at 2:5 (their score first; us:them 5:2), us at 7:4.
    s, d = a_set(winners, timeouts=[sh.Timeout("them", 2, 5), sh.Timeout("us", 7, 4)])
    notes = sheet_notes.notes_of(s, d)
    assert [(n.kind, n.side) for n in notes] == [("timeout", "them"), ("timeout", "us")]
    at = notes[0].at_points
    assert at is not None and d.scores()[at] == (5, 2)
    rallies = facts(len(winners), long_after=at - 1)
    scores = d.scores()
    rallies = [
        sheet_notes.RallyFacts(r.id, r.start_ms, r.end_ms, scores[k + 1], scores[k + 1])
        for k, r in enumerate(rallies)
    ]
    rallies[1] = sheet_notes.RallyFacts("r1", rallies[1].start_ms, rallies[1].end_ms, (9, 9), None)
    point_rally = {k: f"r{k}" for k in range(len(winners))}
    placed = sheet_notes.place(notes, point_rally, rallies)
    first, second = placed
    assert first.rally == f"r{at - 1}" and first.issues == []
    assert first.start_ms == rallies[at - 1].end_ms and first.end_ms == rallies[at].start_ms
    assert "short_break" in second.issues  # 15 s: too short for a timeout
    # The score line or the log saying otherwise at the break is flagged.
    k2 = (second.note.at_points or 0) - 1
    rallies[k2] = sheet_notes.RallyFacts(
        rallies[k2].id, rallies[k2].start_ms, rallies[k2].end_ms, (0, 0), (1, 1)
    )
    again = sheet_notes.place(notes, point_rally, rallies)[1]
    assert {"score_line", "log"} <= set(again.issues)
    summary = sheet_notes.summary(placed)
    assert summary["timeout"]["notes"] == 2 and summary["timeout"]["placed"] == 2
    assert summary["timeout"]["flagged"] == 1


def test_a_penalty_point_has_no_rally_and_a_card_names_no_person() -> None:
    winners = to_25()
    s, d = a_set(winners, sanctions=[sh.Sanction("us", "P", 2, 0)])  # their point next
    [note] = sheet_notes.notes_of(s, d)
    assert note.kind == "sanction" and note.sanction == "P" and note.side == "us"
    assert note.number_in is None and note.position is None  # nobody named
    k = note.at_points
    assert k is not None and d.points[k].penalty
    rallies = facts(len(winners))
    point_rally = {i: f"r{i}" for i in range(len(winners))}
    [p] = sheet_notes.place([note], point_rally, rallies)
    assert "penalty_rally" in p.issues  # a rally was lined up with the penalty point
    del point_rally[k]
    [p] = sheet_notes.place([note], point_rally, rallies)
    assert "penalty_rally" not in p.issues
    json_out = p.as_json()
    assert json_out["sanction"] == "P" and "number" not in json_out


def test_a_note_off_the_path_or_without_a_rally() -> None:
    winners = to_25()
    s, d = a_set(winners, timeouts=[sh.Timeout("us", 30, 1)], subs=[sh.Sub("us", 1, 17, (1, 0))])
    notes = sheet_notes.notes_of(s, d)
    placed = sheet_notes.place(notes, {}, facts(len(winners)))
    assert placed[0].issues == ["off_path"]
    assert placed[1].note.kind == "sub" and placed[1].note.number_in == 17
    assert "no_rally" in placed[1].issues


def test_votes_count_only_what_may_be_read_at_each_frame() -> None:
    """The sheet's labels and the readings go through the same votes; with the sheet, who may
    be read is asked per frame instead of the roster."""
    t = pl.Track(1, 0, 10, 11, pl.NEAR, 0, team="us")
    t.reads = [(1, 7, 0.9), (5, 7, 0.9)]
    pl.vote(t, {"us": {3}}, ok=lambda team, frame, n: n == 7)
    assert t.number == 7  # the roster would have dropped it
    pl.vote(t, None, ok=lambda team, frame, n: frame > 3)
    assert t.number is None  # one reading left: not enough
    pl.vote(t, {"us": {3}})
    assert t.number is None and identities.fold_reads(t.reads) == t.reads
