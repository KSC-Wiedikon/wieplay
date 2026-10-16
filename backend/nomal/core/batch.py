"""Queue many imports at once: `nomal.cli import-batch` (PLAN §7.2.2).

One line per video, tab separated: team id, date, link, and a title only for a match that is
not in Wiedisync (friendly, tournament, cup). `#` starts a comment. Each line meets the
rules of the upload page: a YouTube or Twitch video link with channels on the allow-list
(the worker checks the video's own channel before downloading), the Wiedisync game of that
day or else a title and a date in the team row's season, the filming rules with fresh
rosters, and no second import of a video the team already has.

The imports start `spacing` apart so YouTube's bot check is not woken; if it is, the worker
reports it and every queued import waits an hour (worker API, `retry_after_s`).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from nomal.core import imports, pipeline
from nomal.core.filming import check_filming, club_today, refresh_rosters
from nomal.core.models import AuditLog, TeamLineage, Video, VideoSourceFile, WdGame, WdTeam
from nomal.core.sync import BridgeClient

DASH = "\u2013"


@dataclass(frozen=True)
class Line:
    number: int
    team_id: int
    on: date
    link: str
    title: str | None


@dataclass
class Planned:
    line: Line
    title: str = ""
    team: WdTeam | None = None
    source: imports.ImportSource | None = None
    game_id: int | None = None
    problem: str | None = None  # why the line is skipped


def _day(text: str) -> date:
    if re.fullmatch(r"\d{1,2}\.\d{1,2}\.\d{4}", text):
        d, m, y = (int(x) for x in text.split("."))
        return date(y, m, d)
    return date.fromisoformat(text)


def parse(text: str) -> list[Line]:
    """The batch file → lines. Raises ValueError naming the line."""
    lines: list[Line] = []
    for number, raw in enumerate(text.splitlines(), start=1):
        row = raw.rstrip()
        if not row.strip() or row.lstrip().startswith("#"):
            continue
        fields = [f.strip() for f in row.split("\t")]
        if len(fields) not in (3, 4):
            raise ValueError(f"line {number}: give team id, date, link [, title], tab separated")
        try:
            team_id, on = int(fields[0]), _day(fields[1])
        except ValueError as exc:
            raise ValueError(f"line {number}: {exc}") from exc
        title = fields[3] if len(fields) == 4 and fields[3] else None
        if title is not None and len(title) > 200:
            raise ValueError(f"line {number}: the title is longer than 200 characters")
        lines.append(Line(number, team_id, on, fields[2], title))
    return lines


def _already_imported(db: Session, lineage_id: uuid.UUID, external_id: str) -> Video | None:
    return db.scalar(
        select(Video).where(
            Video.lineage_id == lineage_id,
            Video.source == "import",
            Video.status.not_in(("deleted", "failed", "refused")),
            Video.capture["import"]["id"].astext == external_id,
        )
    )


def plan(db: Session, bridge: BridgeClient | None, lines: list[Line]) -> list[Planned]:
    """Check every line; nothing is written except refreshed rosters."""
    synced = refresh_rosters(db, bridge)
    today = club_today()
    allowed: dict[str, set[str]] = {}
    seen: set[tuple[uuid.UUID, str]] = set()
    out: list[Planned] = []
    for line in lines:
        p = Planned(line)
        out.append(p)
        team = db.get(WdTeam, line.team_id)
        lineage = db.get(TeamLineage, team.lineage_id) if team else None
        if team is None or lineage is None or not lineage.enabled:
            p.problem = "no such team, or the team is not enabled for Nomal"
            continue
        p.team = team
        try:
            p.source = imports.parse_link(line.link)
        except imports.NotImportable as exc:
            p.problem = str(exc)
            continue
        refs = allowed.setdefault(p.source.platform, imports.allowed_refs(db, p.source.platform))
        if not refs:
            p.problem = f"no {p.source.platform} channel is on the import allow-list"
            continue
        key = (team.lineage_id, p.source.external_id)
        existing = _already_imported(db, team.lineage_id, p.source.external_id)
        if key in seen or existing is not None:
            p.problem = "already imported" + (f" (video {existing.id})" if existing else "")
            continue
        seen.add(key)
        if line.on > today:
            p.problem = "the date is in the future"
            continue
        games = list(
            db.scalars(select(WdGame).where(WdGame.team_id == team.id, WdGame.date == line.on))
        )
        if len(games) > 1:
            p.problem = "several Wiedisync games on that day"
            continue
        if games:
            game = games[0]
            p.game_id = game.id
            p.title = line.title or f"{game.home_name or '?'} {DASH} {game.away_name or '?'}"
        elif line.title is None:
            p.problem = "no Wiedisync game on that day: add a title (friendly, tournament, cup)"
            continue
        elif pipeline.season_of(line.on) != team.season:
            p.problem = (
                f"the date belongs to {pipeline.season_of(line.on)}, the team to {team.season}"
            )
            continue
        else:
            p.title = line.title
        check = check_filming(db, bridge, team, line.on, fresh=True, roster_fresh=synced)
        if not check.allowed:
            p.problem = "filming not allowed: " + ", ".join(b.code for b in check.blockers)
    return out


def queue(
    db: Session,
    planned: list[Planned],
    *,
    created_by: int,
    spacing: timedelta,
    now: datetime | None = None,
) -> list[tuple[Planned, Video, datetime]]:
    """Create the videos and their import jobs, `spacing` apart. Commits."""
    now = now or datetime.now(UTC)
    done: list[tuple[Planned, Video, datetime]] = []
    for p in planned:
        if p.problem or p.team is None or p.source is None:
            continue
        start = now + spacing * len(done)
        team, source = p.team, p.source
        video = Video(
            id=uuid.uuid4(),
            team_id=team.id,
            lineage_id=team.lineage_id,
            kind="match",
            game_id=p.game_id,
            title=p.title[:255],
            sport=team.sport,
            recorded_on=p.line.on,
            source="import",
            status="importing",
            created_by=created_by,
            capture={
                "import": {"platform": source.platform, "id": source.external_id, "url": source.url}
            },
        )
        db.add(video)
        video.files.append(
            VideoSourceFile(
                part_no=1, file_name=source.file_name, bytes=0, content_type="video/mp4"
            )
        )
        db.flush()
        pipeline.start_import(
            db,
            video,
            team,
            source,
            imports.allowed_refs(db, source.platform),
            not_before=start if start > now else None,
        )
        db.add(
            AuditLog(
                actor_member_id=created_by,
                action="video_created",
                object_type="video",
                object_id=str(video.id),
                details={"team": team.id, "source": "import", "link": source.url, "via": "batch"},
            )
        )
        done.append((p, video, start))
    db.commit()
    return done
