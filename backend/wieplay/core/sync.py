"""Mirror the Wiedisync data WiePlay needs (PLAN §4.3, §6.2).

1. List every team of the current and previous season (metadata only, no personal data)
   and map each row to its lineage, so a team keeps its identity across the 1 June rollover.
2. Ask the bridge for a snapshot of the *enabled* teams only: rosters, games, trainings.
3. Replace the mirror for those teams and purge members no enabled team references.

Guest clubs' teams and people (PLAN §4.5, wd_team.guest, ids from GUEST_ID_BASE up) live in
the same tables and are kept by their coaches: every step here leaves them alone.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any, Protocol

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from wieplay.core.guests import GUEST_ID_BASE
from wieplay.core.models import (
    SyncState,
    TeamLineage,
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


class BridgeError(Exception):
    pass


class BridgeClient(Protocol):
    def list_teams(self) -> Mapping[str, Any]: ...

    def snapshot(self, team_ids: Sequence[int]) -> Mapping[str, Any]: ...

    def adult_check(self, team_id: int, on: date) -> Mapping[str, Any]: ...

    def score_log(self, game_id: int) -> Mapping[str, Any]: ...


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
        body = response.json()
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


@dataclass(frozen=True)
class SyncResult:
    teams: int
    enabled: int
    members: int
    games: int
    trainings: int

    def counts(self) -> dict[str, int]:
        return {
            "teams": self.teams,
            "enabled": self.enabled,
            "members": self.members,
            "games": self.games,
            "trainings": self.trainings,
        }


def _date(value: Any) -> date | None:
    return date.fromisoformat(value) if isinstance(value, str) and value else None


def _time(value: Any) -> time | None:
    return time.fromisoformat(value) if isinstance(value, str) and value else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


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

    session.execute(
        delete(WdGame).where(WdGame.team_id.in_(enabled_ids), WdGame.team_id < GUEST_ID_BASE)
    )
    games = [g for g in snap.get("games", []) if g.get("team") in enabled_ids]
    for g in games:
        session.add(
            WdGame(
                id=g["id"],
                team_id=g["team"],
                date=_date(g.get("date")),
                time=_time(g.get("time")),
                home_name=g.get("home_name"),
                away_name=g.get("away_name"),
                our_side=g.get("our_side") if g.get("our_side") in ("home", "away") else None,
                league=g.get("league"),
                hall_name=g.get("hall_name"),
                home_score=_int(g.get("home_score")),
                away_score=_int(g.get("away_score")),
                sets=g.get("sets") if isinstance(g.get("sets"), list) else None,
                synced_at=now,
            )
        )

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
        result = SyncResult(len(listed), len(enabled_ids), members, games, trainings)
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
