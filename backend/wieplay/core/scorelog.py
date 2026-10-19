"""Score logs from the club scoreboard (PLAN §10.10), API side.

    Wiedisync bridge GET /score-log?game=  ──import──▶ analysis_track "score_log" (+ model_run)
    scoring ◀── per rally: the point pressed after it (who won, the set's score), "no point",
                presses with no rally (missed); only in sets whose final is the official one

The club's LED scoreboard (point-hub) logs every point with its own clock; Wiedisync keeps it
(`live_match_logs`, else the `live_scores` revisions) and the bridge serves one game's log by
team (a = home, b = away). WiePlay imports it once per video with a Wiedisync game: right after
the analysis, and in the hourly sync for videos that have none yet (the bridge may not have the
route yet, or the game has no log: both are quiet "no score log" and tried again a day later),
and on demand (`python -m wieplay.cli score-log`). The log is kept as it came (a few tens of KB,
compressed); every scoring lines it up with the video's rallies again (wieplay/ml/scorelog.py:
a tenth of a second), so a rally a coach moved or added gets its press too.

In the scoring it is the strongest evidence after a coach's fact (wieplay/ml/scoring.py C_LOG):
more than the scoreboard read in the picture, far more than the serves. A coach's fact is never
changed: where one disagrees, the coach wins and the rally is flagged ("log"). Where the log
decided against what the video alone suggested (the scoring blind to the log, with the same
sides, was sure of another answer) the rally is flagged too, so the review shows it.

Team names and scores only: the log names nobody.
"""

from __future__ import annotations

import base64
import itertools
import json
import logging
import uuid
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Protocol

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from wieplay.core.models import AnalysisTrack, ModelRun, Segment, Video, WdGame
from wieplay.core.sync import BridgeError
from wieplay.ml import scorelog as sl
from wieplay.ml import scoring as sc

log = logging.getLogger(__name__)

SPORTS = frozenset({"volleyball"})
TRACK_KIND = "score_log"
RUN_KIND = "score_log"
MODEL_NAME = "wieplay-score-log"
MAX_PACKED = 2_000_000  # bytes of the compressed log
RETRY_AFTER = timedelta(hours=24)
MAX_PER_SWEEP = 20
BLIND_SURE = 0.7  # the video's own answer counts as a disagreement from this sure
P_FLOOR = 0.5  # a press that found its rally names the winner at least this surely

Side = Literal["us", "them"]


class ScoreLogClient(Protocol):
    def score_log(self, game_id: int) -> Mapping[str, Any]: ...


# --- storage ------------------------------------------------------------------------------------


def pack(payload: Mapping[str, Any]) -> dict[str, str]:
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    return {"log": base64.b64encode(zlib.compress(data, 9)).decode()}


def unpack(channels: Mapping[str, str]) -> dict[str, Any]:
    """Raises ValueError on anything that is not a packed score log."""
    try:
        raw = json.loads(zlib.decompress(base64.b64decode(channels["log"], validate=True)))
    except (KeyError, TypeError, ValueError, zlib.error) as exc:
        raise ValueError(f"not a score log track: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("not a score log track")
    return raw


@dataclass(frozen=True)
class Stored:
    track: AnalysisTrack
    payload: dict[str, Any]
    log: sl.ScoreLog


def stored(db: Session, video: Video) -> Stored | None:
    """The newest log kept for the video, when it is its current game's."""
    if video.game_id is None:
        return None
    track = db.scalars(
        select(AnalysisTrack)
        .where(AnalysisTrack.video_id == video.id, AnalysisTrack.kind == TRACK_KIND)
        .order_by(AnalysisTrack.created_at.desc())
        .limit(1)
    ).first()
    if track is None:
        return None
    try:
        payload = unpack(track.channels)
        parsed = sl.parse(payload)
    except ValueError:
        return None
    game = payload.get("game") or {}
    if parsed is None or game.get("id") != video.game_id:
        return None
    return Stored(track, payload, parsed)


# --- import -------------------------------------------------------------------------------------


Status = Literal["stored", "none", "unavailable", "no_game", "not_volleyball"]


@dataclass(frozen=True)
class Imported:
    status: Status
    detail: str = ""
    run: ModelRun | None = None


def fetch(db: Session, video: Video, client: ScoreLogClient, *, rescore: bool = True) -> Imported:
    """Ask the bridge for the video's game's log and keep it; score the video again with it.
    "none" when the game has no log, "unavailable" when the bridge could not say (an older
    Wiedisync without the route, unreachable): both quiet."""
    from wieplay.core import scoring

    if video.sport not in SPORTS:
        return Imported("not_volleyball")
    if video.game_id is None:
        return Imported("no_game")
    try:
        payload = dict(client.score_log(video.game_id))
        parsed = sl.parse(payload)
    except BridgeError as exc:
        return Imported("unavailable", str(exc)[:200])
    except ValueError as exc:
        log.warning("score log of game %s refused: %s", video.game_id, exc)
        return Imported("unavailable", f"bad score log: {exc}"[:200])
    if parsed is None or not parsed.points:
        return Imported("none", "the game has no score log")
    channels = pack(payload)
    if len(channels["log"]) > MAX_PACKED:
        return Imported("unavailable", "score log too large")
    run = ModelRun(
        id=uuid.uuid4(),
        kind=RUN_KIND,
        video_id=video.id,
        model_name=MODEL_NAME,
        model_version=scoring.code_version(),
        params={
            "game_id": video.game_id,
            "source": parsed.source,
            "channel": parsed.channel,
            "match_key": payload.get("match_key"),
            "events": len(parsed.events),
            "points": len(parsed.points),
            "finals": {str(k): list(v) for k, v in sorted(parsed.finals.items())},
        },
    )
    db.add(run)
    db.flush()
    db.execute(
        delete(AnalysisTrack).where(
            AnalysisTrack.video_id == video.id, AnalysisTrack.kind == TRACK_KIND
        )
    )
    db.add(
        AnalysisTrack(
            video_id=video.id, model_run_id=run.id, kind=TRACK_KIND, step_ms=0, channels=channels
        )
    )
    db.flush()
    if rescore and video.status == "ready":
        score_run = scoring.score_video(db, video, reason="score_log")
        if score_run is not None:
            run.metrics = dict(score_run.metrics.get("log") or {})
    return Imported("stored", parsed.source, run)


_tried: dict[uuid.UUID, datetime] = {}


def needs_log(db: Session, video: Video) -> bool:
    return video.game_id is not None and video.sport in SPORTS and stored(db, video) is None


def after_analysis(db: Session, video: Video | None, client: ScoreLogClient | None) -> None:
    """Right after an analysis: one bridge call for a video with a game and no log yet. Never
    fails the analysis."""
    if video is None or client is None or video.status != "ready" or not needs_log(db, video):
        return
    try:
        with db.begin_nested():
            done = fetch(db, video, client)
        _tried[video.id] = datetime.now(UTC)
        if done.status != "stored":
            log.info("no score log for video %s: %s %s", video.id, done.status, done.detail)
    except Exception:
        log.exception("score log import of %s failed", video.id)


def import_missing(db: Session, client: ScoreLogClient, now: datetime | None = None) -> int:
    """The hourly sweep: videos with a game and no log, each tried at most once a day.
    Returns how many got one."""
    now = now or datetime.now(UTC)
    found = 0
    candidates = db.scalars(
        select(Video)
        .where(Video.status == "ready", Video.game_id.is_not(None), Video.sport.in_(SPORTS))
        .order_by(Video.recorded_on.desc())
    )
    tried = 0
    for video in candidates:
        if tried >= MAX_PER_SWEEP:
            break
        last = _tried.get(video.id)
        if (last is not None and now - last < RETRY_AFTER) or not needs_log(db, video):
            continue
        tried += 1
        _tried[video.id] = now
        try:
            with db.begin_nested():
                done = fetch(db, video, client)
        except Exception:
            log.exception("score log import of %s failed", video.id)
            continue
        if done.status == "stored":
            found += 1
        elif done.status == "unavailable":
            break  # the bridge cannot say for any video now: the next sweep tries again
    db.commit()
    return found


# --- the log in the scoring ----------------------------------------------------------------------


def _side(says: str, us: str) -> Literal["us", "them", "none"]:
    if says == "none":
        return "none"
    return "us" if says == us else "them"


def ours(pair: tuple[int, int], us: str) -> tuple[int, int]:
    """A score of the log (a, b) as (us, them)."""
    return pair if us == "a" else (pair[1], pair[0])


_ours = ours


def _ours_list(pair: list[int] | None, us: str) -> list[int] | None:
    return None if not pair else list(_ours((pair[0], pair[1]), us))


@dataclass
class Evidence:
    """The log against this scoring's rallies, oriented to us/them, trusted sets only."""

    stored: Stored
    alignment: sl.Alignment
    sets: dict[int, dict[str, Any]]  # the log's set no -> trust (wieplay/ml/scorelog.py)
    us: Literal["a", "b"]

    def trusted(self, period: int | None) -> bool:
        return period is not None and bool(self.sets.get(period, {}).get("trusted"))

    @property
    def any_trusted(self) -> bool:
        return any(s["trusted"] for s in self.sets.values())

    def verdict(self, key: str) -> sl.Verdict | None:
        v = self.alignment.verdicts.get(key)
        return v if v is not None and self.trusted(v.period) else None

    def says(self, key: str) -> tuple[Literal["us", "them", "none"], float] | None:
        v = self.verdict(key)
        if v is None:
            return None
        if v.says == "none":
            return "none", v.p
        return _side(v.says, self.us), P_FLOOR + (1 - P_FLOOR) * v.p

    def score_after(self, key: str) -> tuple[int, int] | None:
        """The set's score after this rally by the log (us, them), where it names a winner."""
        v = self.verdict(key)
        if v is None or v.says == "none" or v.score is None:
            return None
        return ours(v.score, self.us)

    def missed_before(self, key: str) -> tuple[int, int] | None:
        """Points (us, them) the log saw right before this rally with no rally of their own;
        None when the log says nothing about that gap (no press of its own here)."""
        v = self.verdict(key)
        if v is None or v.says == "none":
            return None
        return _ours(self.alignment.missed.get(key, (0, 0)), self.us)

    def missed_after(self, last_key: str | None, period: int) -> tuple[int, int] | None:
        v = self.verdict(last_key) if last_key else None
        if v is None or v.says == "none" or v.period != period:
            return None
        return _ours(self.alignment.missed_after.get(period, (0, 0)), self.us)


def evidence_for(db: Session, video: Video, rallies: Sequence[Segment]) -> Evidence | None:
    """The video's log lined up with its rallies, or None (no log, no side, no rallies)."""
    found = stored(db, video)
    if found is None or not rallies or video.game_id is None:
        return None
    game = db.get(WdGame, video.game_id)
    if game is None or game.our_side not in ("home", "away"):
        return None
    alignment = sl.align(
        found.log, [sl.RallySpan(str(r.id), r.start_ms, r.end_ms) for r in rallies]
    )
    official: dict[int, tuple[int, int]] = {}
    for no, pair in enumerate(game.sets or [], start=1):
        if isinstance(pair, list | tuple) and len(pair) == 2:
            official[no] = (int(pair[0]), int(pair[1]))  # home, away = a, b
    return Evidence(found, alignment, sl.trust(found.log, official, alignment), _us(game))


def _us(game: WdGame) -> Literal["a", "b"]:
    return "a" if game.our_side == "home" else "b"


def set_spans(ev: Evidence, rallies: Sequence[Segment]) -> list[tuple[int, int, int]] | None:
    """Sets from the log: (no, first rally, last rally) by the presses that found a rally, the
    rallies between two sets split at the longest break, the ones next to a set's ends kept
    while the break to them is short. None when the log's sets do not follow each other."""
    labels: list[int | None] = []
    for r in rallies:
        v = ev.alignment.verdicts.get(str(r.id))
        labels.append(v.period if v is not None and v.says != "none" else None)
    known = [(i, p) for i, p in enumerate(labels) if p is not None]
    if not known or any(b[1] < a[1] for a, b in itertools.pairwise(known)):
        return None
    sets = sorted({p for _, p in known})
    first = {p: min(i for i, q in known if q == p) for p in sets}
    last = {p: max(i for i, q in known if q == p) for p in sets}
    if any(last[a] >= first[b] for a, b in itertools.pairwise(sets)):
        return None

    def gap(i: int) -> int:  # the break before rally i
        return rallies[i].start_ms - rallies[i - 1].end_ms

    spans = {p: [first[p], last[p]] for p in sets}
    for a, b in itertools.pairwise(sets):
        cut = max(range(last[a] + 1, first[b] + 1), key=gap)
        spans[a][1], spans[b][0] = cut - 1, cut
    s0 = spans[sets[0]]
    while s0[0] > 0 and gap(s0[0]) < sc.MIN_BREAK_S * 1000:
        s0[0] -= 1
    s1 = spans[sets[-1]]
    while s1[1] + 1 < len(rallies) and gap(s1[1] + 1) < sc.MIN_BREAK_S * 1000:
        s1[1] += 1
    return [(p, spans[p][0], spans[p][1]) for p in sets]


def apply(ev: Evidence, set_ins: list[sc.SetIn]) -> list[sc.SetIn]:
    """The sets with what the log says about each rally and gap."""
    out = []
    for st in set_ins:
        rallies = []
        for r in st.rallies:
            said = ev.says(r.key)
            rallies.append(
                replace(
                    r,
                    log=said[0] if said else None,
                    log_p=round(said[1], 3) if said else 0.0,
                    log_missed=ev.missed_before(r.key),
                    log_score=ev.score_after(r.key),
                )
            )
        last = st.rallies[-1].key if st.rallies else None
        out.append(replace(st, rallies=rallies, log_missed_after=ev.missed_after(last, st.no)))
    return out


def flag_disagreements(with_log: list[sc.SetIn], match: sc.MatchOut) -> tuple[sc.MatchOut, int]:
    """Rallies where the log decided against the video: the scoring blind to the log, with the
    same sides and first servers, was sure of another answer. Flagged "log"."""
    blind_sets = [
        replace(
            st,
            rallies=[
                replace(r, log=None, log_p=0.0, log_missed=None, log_score=None) for r in st.rallies
            ],
            log_missed_after=None,
            our_side=so.our_side,
            first_serve=so.first_serve,
        )
        for st, so in zip(with_log, match.sets, strict=True)
    ]
    blind = sc.score_match(blind_sets)
    blind_out = {o.key: o for so in blind.sets for o in so.rallies}
    told = {r.key for st in with_log for r in st.rallies if r.log is not None}
    disagree = 0
    for so in match.sets:
        rallies = []
        for out in so.rallies:
            other = blind_out.get(out.key)
            if (
                out.key in told
                and other is not None
                and other.confidence >= BLIND_SURE
                and (other.point, other.won_by) != (out.point, out.won_by)
            ):
                disagree += 1
                if out.note is None or out.note in ("unsure", "serve", "board"):
                    out = replace(out, note="log")
            rallies.append(out)
        so.rallies = rallies
    return match, disagree


def write(ev: Evidence | None, rallies: Sequence[Segment]) -> None:
    """The log's answer on each rally (cleared first)."""
    for rally in rallies:
        rally.log_won_by = None
        rally.log_score_us = rally.log_score_them = rally.log_at_ms = None
        if ev is None:
            continue
        v = ev.verdict(str(rally.id))
        if v is None:
            continue
        rally.log_won_by = _side(v.says, ev.us)
        if v.score is not None:
            rally.log_score_us, rally.log_score_them = _ours(v.score, ev.us)
        rally.log_at_ms = v.at_ms


def metrics(
    ev: Evidence, with_log: list[sc.SetIn], match: sc.MatchOut, disagree: int
) -> dict[str, Any]:
    """What the log did in this scoring: the alignment, the trust per set, how often the score
    line follows it, how often the video alone disagreed, and the coach's winners against it."""
    al = ev.alignment
    path = {o.key: o for so in match.sets for o in so.rallies}
    told = agree = right = known = serve_ok = serve_n = 0
    for st in with_log:
        for r in st.rallies:
            if r.log is None:
                continue
            told += 1
            out = path.get(r.key)
            if out is not None:
                agree += int((out.won_by if out.point else "none") == r.log)
                # An independent check of the alignment: the serve read in the video against
                # the server the log implies (a shift a rally off reads about half right).
                if out.point and out.serving and r.p_near is not None:
                    serve_n += 1
                    near = (out.serving == "us") == (out.our_side == "near")
                    serve_ok += int((r.p_near >= 0.5) == near)
            if r.won_fact and r.log in ("us", "them"):
                known += 1
                right += int(r.log == r.won_fact)
    delays = sorted(al.delays)
    sets = {
        str(no): {
            **s,
            "final": _ours_list(s["final"], ev.us),
            "official": _ours_list(s["official"], ev.us),
        }
        for no, s in ev.sets.items()
    }
    trusted = sum(1 for s in ev.sets.values() if s["trusted"])
    with_official = sum(1 for s in ev.sets.values() if s["official"] is not None)
    return {
        "source": ev.stored.log.source,
        "imported_at": ev.stored.track.created_at.isoformat()
        if ev.stored.track.created_at
        else None,
        "us": ev.us,
        "shift_ms": al.shift_ms,
        "stretches": [
            {
                "set": s.period,
                "shift_ms": s.shift_ms,
                "own": s.own,
                "points": s.points,
                "matched": s.matched,
                "anchors": s.anchors,
            }
            for s in al.stretches
        ],
        "points": al.points,
        "matched": al.matched,
        "matched_share": round(al.matched_share, 4),
        "delay_ms": {
            "p10": delays[len(delays) // 10] if delays else None,
            "median": delays[len(delays) // 2] if delays else None,
            "p90": delays[9 * len(delays) // 10] if delays else None,
        },
        "no_point": sum(1 for v in al.verdicts.values() if v.says == "none"),
        "missed": sum(a + b for a, b in al.missed.values())
        + sum(a + b for a, b in al.missed_after.values()),
        "sets": sets,
        "sets_trusted": trusted,
        "sets_with_official": with_official,
        "trusted": trusted > 0 and all(s["agrees"] for s in ev.sets.values() if s["official"]),
        "rallies_told": told,
        "path_agrees": agree,
        "serve_agreement": round(serve_ok / serve_n, 4) if serve_n else None,
        "disagree_with_video": disagree,
        "vs_coach": {
            "winner_accuracy": round(right / known, 4) if known else None,
            "winners_known": known,
        },
    }


def summary(db: Session, video: Video) -> dict[str, Any] | None:
    """For the video page: the newest scoring's log metrics, when the video has a log."""
    from wieplay.core import scoring

    if stored(db, video) is None:
        return None
    run = scoring.latest_run(db, video.id)
    found = (run.metrics or {}).get("log") if run is not None else None
    return found if isinstance(found, dict) else None
