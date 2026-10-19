"""Mirror the Wiedisync data WiePlay needs (PLAN §4.3, §6.2).

1. List every team of the current and previous season (metadata only, no personal data)
   and map each row to its lineage, so a team keeps its identity across the 1 June rollover.
2. Ask the bridge for a snapshot of the *enabled* teams only: rosters, games, trainings.
3. Replace the mirror for those teams and purge members no enabled team references.
4. Refresh the "borrowed" games: games of other KSCW teams that a video is linked to (a shared
   cup team's game, filed in Wiedisync under another team), by id through /club-games. They
   carry no personal data. A Wiedisync without that route leaves them as they are.

Guest clubs' teams and people (PLAN §4.5, wd_team.guest, ids from GUEST_ID_BASE up) live in
the same tables and are kept by their coaches: every step here leaves them alone.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from itertools import batched
from typing import Any, Protocol

import httpx
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from wieplay.core.guests import GUEST_ID_BASE
from wieplay.core.models import (
    SyncState,
    TeamLineage,
    Video,
    WdGame,
    WdMember,
    WdTeam,
    WdTeamMember,
    WdTraining,
)

log = logging.getLogger(__name__)

SPORTS = frozenset({"volleyball", "basketball"})
RELS = frozenset({"player", "coach", "team_responsible", "captain"})
MAX_TEAMS_PER_SNAPSHOT = 20
MAX_CLUB_GAME_IDS = 100  # per /club-games call (the bridge's limit)


class BridgeError(Exception):
    pass


class BridgeClient(Protocol):
    def list_teams(self) -> Mapping[str, Any]: ...

    def snapshot(self, team_ids: Sequence[int]) -> Mapping[str, Any]: ...

    def adult_check(self, team_id: int, on: date) -> Mapping[str, Any]: ...

    def score_log(self, game_id: int) -> Mapping[str, Any]: ...

    def club_games(
        self, *, on: date | None = None, ids: Sequence[int] | None = None, sport: str | None = None
    ) -> Mapping[str, Any]: ...


class HttpBridgeClient:
    """Calls the bridge in Wiedisync's kscw-endpoints over the local network."""

    def __init__(self, base_url: str, service_token: str, timeout: float = 30.0) -> None:
        if not base_url or not service_token:
            raise BridgeError("bridge URL and service token must be configured")
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-WiePlay-Service-Token": service_token},
            timeout=timeout,
        )

    def _get(self, path: str, params: Mapping[str, str] | None = None) -> Mapping[str, Any]:
        try:
            response = self._client.get(path, params=params)
        except httpx.HTTPError as exc:
            raise BridgeError(f"bridge unreachable: {exc}") from exc
        if response.status_code != 200:
            raise BridgeError(f"bridge answered {response.status_code} for {path}")
        try:
            body = response.json()
        except ValueError as exc:
            raise BridgeError(f"bridge answered no JSON for {path}") from exc
        if not isinstance(body, dict) or body.get("version") != 1:
            raise BridgeError(f"unexpected bridge payload for {path}")
        return body

    def list_teams(self) -> Mapping[str, Any]:
        return self._get("/teams")

    def snapshot(self, team_ids: Sequence[int]) -> Mapping[str, Any]:
        return self._get("/sync", {"teams": ",".join(str(i) for i in team_ids)})

    def adult_check(self, team_id: int, on: date) -> Mapping[str, Any]:
        return self._get("/adult-check", {"team": str(team_id), "date": on.isoformat()})

    def score_log(self, game_id: int) -> Mapping[str, Any]:
        """The club scoreboard's log of a game (PLAN §10.10); 404 from a Wiedisync without
        the route, or for a game it does not know: a BridgeError like any other."""
        return self._get("/score-log", {"game": str(game_id)})

    def club_games(
        self, *, on: date | None = None, ids: Sequence[int] | None = None, sport: str | None = None
    ) -> Mapping[str, Any]:
        """Every KSCW team's games of a day, or these games by id: no rosters. 404 from a
        Wiedisync without the route (before 19.10.2026): a BridgeError like any other."""
        params: dict[str, str] = {}
        if on is not None:
            params["date"] = on.isoformat()
        if ids is not None:
            params["ids"] = ",".join(str(i) for i in ids)
        if sport is not None:
            params["sport"] = sport
        return self._get("/club-games", params)


@dataclass(frozen=True)
class SyncResult:
    teams: int
    enabled: int
    members: int
    games: int
    trainings: int
    borrowed: int = 0  # other teams' games that videos are linked to, refreshed

    def counts(self) -> dict[str, int]:
        return {
            "teams": self.teams,
            "enabled": self.enabled,
            "members": self.members,
            "games": self.games,
            "trainings": self.trainings,
            "borrowed": self.borrowed,
        }


def _date(value: Any) -> date | None:
    return date.fromisoformat(value) if isinstance(value, str) and value else None


def _time(value: Any) -> time | None:
    return time.fromisoformat(value) if isinstance(value, str) and value else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _game_values(g: Mapping[str, Any]) -> dict[str, Any]:
    """A bridge game's columns of wd_game (the same shape from /sync and /club-games)."""
    league = g.get("league")
    return {
        "date": _date(g.get("date")),
        "time": _time(g.get("time")),
        "home_name": g.get("home_name"),
        "away_name": g.get("away_name"),
        "our_side": g.get("our_side") if g.get("our_side") in ("home", "away") else None,
        "league": league[:64] if isinstance(league, str) else None,
        "hall_name": g.get("hall_name"),
        "home_score": _int(g.get("home_score")),
        "away_score": _int(g.get("away_score")),
        "sets": g.get("sets") if isinstance(g.get("sets"), list) else None,
    }


def _lineage_for(session: Session, team: Mapping[str, Any]) -> TeamLineage:
    existing = session.get(WdTeam, team["id"])
    if existing is not None:
        return existing.lineage
    sport, key, name = team["sport"], team.get("team_key") or None, team["name"]
    # A guest club's lineage may carry the same name ("H1"): never a Wiedisync team's.
    query = select(TeamLineage).where(TeamLineage.sport == sport, TeamLineage.guest_club.is_(None))
    query = (
        query.where(TeamLineage.team_key == key)
        if key
        else query.where(TeamLineage.label == name, TeamLineage.team_key.is_(None))
    )
    lineage = session.scalars(query.limit(1)).first()
    if lineage is None:
        lineage = TeamLineage(sport=sport, label=name, team_key=key)
        session.add(lineage)
        session.flush()
    return lineage


def _upsert_teams(session: Session, listing: Mapping[str, Any], now: datetime) -> list[WdTeam]:
    teams = listing.get("teams")
    if not isinstance(teams, list):
        raise BridgeError("team listing has no teams")
    listed_ids = set()
    rows = []
    for team in teams:
        if team.get("sport") not in SPORTS or not isinstance(team.get("id"), int):
            continue
        if team["id"] >= GUEST_ID_BASE:
            raise BridgeError(f"team id {team['id']} is in the guest clubs' range")
        lineage = _lineage_for(session, team)
        row = session.get(WdTeam, team["id"]) or WdTeam(id=team["id"])
        row.lineage = lineage
        row.name = team["name"]
        row.sport = team["sport"]
        row.season = team["season"]
        row.league = team.get("league")
        row.team_key = team.get("team_key") or None
        row.listed = True
        row.synced_at = now
        session.add(row)
        rows.append(row)
        listed_ids.add(team["id"])
    for stale in session.scalars(
        select(WdTeam).where(WdTeam.id.not_in(listed_ids), WdTeam.guest.is_(False))
    ):
        stale.listed = False
    session.flush()
    return rows


def _apply_snapshot(
    session: Session, enabled_ids: set[int], snap: Mapping[str, Any], now: datetime
) -> tuple[int, int, int]:
    members = {
        m["id"]: m
        for m in snap.get("members", [])
        if isinstance(m.get("id"), int) and m["id"] < GUEST_ID_BASE
    }
    memberships = [
        m
        for m in snap.get("memberships", [])
        if m.get("team") in enabled_ids and m.get("member") in members and m.get("rel") in RELS
    ]
    for team_id in {m["team"] for m in snap.get("memberships", [])} - enabled_ids:
        raise BridgeError(f"snapshot contains team {team_id}, which was not requested")

    # Members: upsert the referenced ones; rosters are replaced wholesale below.
    referenced = {m["member"] for m in memberships}
    for member_id in referenced:
        m = members[member_id]
        row = session.get(WdMember, member_id) or WdMember(id=member_id)
        row.uuid = m["uuid"]
        row.display_name = m.get("name") or ""
        row.number = _int(m.get("number"))
        row.positions = [p for p in m.get("positions") or [] if isinstance(p, str)]
        row.active = m.get("active") is not False
        row.synced_at = now
        session.add(row)
    session.flush()

    # Every KSCW roster is replaced; the guest clubs' rosters are not Wiedisync's.
    session.execute(
        delete(WdTeamMember).where(
            WdTeamMember.team_id.not_in(select(WdTeam.id).where(WdTeam.guest.is_(True)))
        )
    )
    seen = set()
    for m in memberships:
        key = (m["team"], m["member"], m["rel"])
        if key in seen:
            continue
        seen.add(key)
        session.add(
            WdTeamMember(
                team_id=m["team"],
                member_id=m["member"],
                rel=m["rel"],
                is_guest=m.get("guest") is True,
            )
        )

    games = [g for g in snap.get("games", []) if g.get("team") in enabled_ids]
    # The enabled teams' games are replaced, and so is a row of the same game kept under another
    # team (a borrowed game Wiedisync has since moved to an enabled team): no duplicate key.
    session.execute(
        delete(WdGame).where(
            or_(
                WdGame.team_id.in_(enabled_ids),
                WdGame.id.in_([g["id"] for g in games if isinstance(g.get("id"), int)]),
            ),
            WdGame.team_id < GUEST_ID_BASE,
        )
    )
    for g in games:
        session.add(WdGame(id=g["id"], team_id=g["team"], **_game_values(g), synced_at=now))

    session.execute(
        delete(WdTraining).where(
            WdTraining.team_id.in_(enabled_ids), WdTraining.team_id < GUEST_ID_BASE
        )
    )
    trainings = [t for t in snap.get("trainings", []) if t.get("team") in enabled_ids]
    for t in trainings:
        session.add(
            WdTraining(
                id=t["id"],
                team_id=t["team"],
                date=_date(t.get("date")),
                start_time=_time(t.get("start_time")),
                end_time=_time(t.get("end_time")),
                hall_name=t.get("hall_name"),
                synced_at=now,
            )
        )
    session.flush()

    # Data minimisation: nobody stays in the mirror without an enabled team. Guest clubs'
    # people are not the mirror's: their coaches and admins remove them.
    session.execute(
        delete(WdMember).where(WdMember.id.not_in(referenced), WdMember.id < GUEST_ID_BASE)
    )
    return len(referenced), len(games), len(trainings)


# --- other KSCW teams' games ("borrowed"): /club-games -----------------------------------------


@dataclass(frozen=True)
class ClubTeam:
    """The team row a club game is filed under (metadata only, as /teams gives it)."""

    id: int
    name: str
    sport: str
    season: str
    league: str | None
    team_key: str | None


@dataclass(frozen=True)
class ClubGame:
    """A KSCW game from /club-games: any team's, not only the enabled ones'."""

    id: int
    team: ClubTeam
    values: Mapping[str, Any]  # wd_game's columns (_game_values)

    @property
    def date(self) -> date | None:
        value: date | None = self.values["date"]
        return value


def parse_club_games(payload: Mapping[str, Any]) -> list[ClubGame]:
    """/club-games → games with their team. A game whose team is not described, is in the
    guest clubs' range or of another sport is left out; a malformed answer is a BridgeError."""
    teams_in, games_in = payload.get("teams"), payload.get("games")
    if not isinstance(teams_in, list) or not isinstance(games_in, list):
        raise BridgeError("club games answer has no teams or games")
    teams: dict[int, ClubTeam] = {}
    for t in teams_in:
        if not isinstance(t, Mapping):
            continue
        tid, name, season = _int(t.get("id")), t.get("name"), t.get("season")
        if tid is None or tid >= GUEST_ID_BASE or t.get("sport") not in SPORTS:
            continue
        if not isinstance(name, str) or not name or not isinstance(season, str):
            continue
        league = t.get("league")
        teams[tid] = ClubTeam(
            id=tid,
            name=name,
            sport=t["sport"],
            season=season,
            league=league if isinstance(league, str) else None,
            team_key=t.get("team_key") or None,
        )
    out: list[ClubGame] = []
    for g in games_in:
        if not isinstance(g, Mapping):
            continue
        gid, team = _int(g.get("id")), teams.get(_int(g.get("team")) or -1)
        if gid is None or team is None:
            continue
        try:
            values = _game_values(g)
        except (TypeError, ValueError) as exc:
            raise BridgeError(f"club game {gid} is malformed: {exc}") from exc
        out.append(ClubGame(gid, team, values))
    return out


def ensure_team(session: Session, team: ClubTeam, now: datetime) -> WdTeam | None:
    """The team row of a club game: the mirror's (the sync lists the current and the previous
    season), else a new unlisted row for an older season's team. None for a guest team."""
    row = session.get(WdTeam, team.id)
    if row is not None:
        return None if row.guest else row
    meta = {"id": team.id, "sport": team.sport, "team_key": team.team_key, "name": team.name}
    row = WdTeam(
        id=team.id,
        lineage=_lineage_for(session, meta),
        name=team.name,
        sport=team.sport,
        season=team.season,
        league=team.league[:64] if team.league else None,
        team_key=team.team_key,
        listed=False,  # the next sync lists it when it is the current or previous season's
        synced_at=now,
    )
    session.add(row)
    session.flush()
    return row


def store_club_games(session: Session, games: Sequence[ClubGame], now: datetime) -> list[WdGame]:
    """Keep these games in the mirror (insert or update), with their teams' rows."""
    rows = []
    for game in games:
        if ensure_team(session, game.team, now) is None:
            continue
        row = session.get(WdGame, game.id) or WdGame(id=game.id)
        row.team_id = game.team.id
        for key, value in game.values.items():
            setattr(row, key, value)
        row.synced_at = now
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


def refresh_borrowed(
    session: Session, client: BridgeClient, enabled_ids: set[int], now: datetime
) -> int:
    """Games that videos are linked to and the snapshot did not just bring (another team's,
    or one that left an enabled team's list): fetched again by id, so late results and
    corrections arrive. A game the bridge no longer knows stays as it was. Quiet when the
    bridge cannot answer (an older Wiedisync): returns how many were refreshed."""
    referenced = set(
        session.scalars(
            select(Video.game_id)
            .where(Video.game_id.is_not(None), Video.status != "deleted")
            .distinct()
        )
    )
    if not referenced:
        return 0
    fresh = set(
        session.scalars(
            select(WdGame.id).where(WdGame.id.in_(referenced), WdGame.team_id.in_(enabled_ids))
        )
    )
    todo = sorted(i for i in referenced - fresh if i is not None)
    if not todo:
        return 0
    found: list[ClubGame] = []
    try:
        for chunk in batched(todo, MAX_CLUB_GAME_IDS):
            found += parse_club_games(client.club_games(ids=chunk))
    except BridgeError as exc:
        log.info("borrowed games not refreshed: %s", exc)
        return 0
    wanted = set(todo)
    return len(store_club_games(session, [g for g in found if g.id in wanted], now))


def run_sync(session: Session, client: BridgeClient, now: datetime | None = None) -> SyncResult:
    """One full sync in one transaction. Records the outcome in sync_state either way."""
    now = now or datetime.now(UTC)
    state = session.get(SyncState, 1) or SyncState(id=1)
    try:
        listed = _upsert_teams(session, client.list_teams(), now)
        enabled_ids = {t.id for t in listed if t.lineage.enabled}
        if len(enabled_ids) > MAX_TEAMS_PER_SNAPSHOT:
            raise BridgeError(f"{len(enabled_ids)} enabled teams; the bridge serves at most 20")
        snap = client.snapshot(sorted(enabled_ids)) if enabled_ids else {}
        members, games, trainings = _apply_snapshot(session, enabled_ids, snap, now)
        borrowed = refresh_borrowed(session, client, enabled_ids, now)
        result = SyncResult(len(listed), len(enabled_ids), members, games, trainings, borrowed)
        state.last_run = state.last_ok = now
        state.error = None
        state.counts = result.counts()
        session.add(state)
        session.commit()
        log.info("sync ok: %s", result.counts())
        return result
    except Exception as exc:
        session.rollback()
        state = session.get(SyncState, 1) or SyncState(id=1)
        state.last_run = now
        state.error = str(exc)[:500]
        session.add(state)
        session.commit()
        log.warning("sync failed: %s", exc)
        raise
