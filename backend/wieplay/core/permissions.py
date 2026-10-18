"""Who may see what (PLAN §5.1): team visibility, WiePlay administration (M0), videos (M1),
tagging and sharing (M2, M3), team helpers (analysts), guest clubs (PLAN §4.5), saved filters
(PLAN §9.7), official scoresheets (PLAN §10.3a).

Guest clubs are walled off both ways: a guest sees only the guest team rows they are on,
and KSCW members (players, coaches, sport admins, helpers) never see a guest team. Only
full admins see both sides.

Every rule lives here and is tested in tests/test_permissions.py; routes only call these.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

ADMIN_ROLES = frozenset({"admin", "superuser"})
LEAD_RELS = frozenset({"coach", "team_responsible"})


@dataclass(frozen=True)
class Viewer:
    member_id: int
    member_uuid: str
    name: str
    roles: frozenset[str]
    sport_admin: frozenset[str]
    # Wiedisync team row id -> relations of this member on that row.
    team_rels: Mapping[int, frozenset[str]]
    # Lineages this member helps (open analyst grants in WiePlay's team_grant). Loaded from the
    # database on every request, never from the login token: a revoked grant ends at once.
    analyst_lineages: frozenset[uuid.UUID] = frozenset()
    # A guest club's member (a WiePlay login, not Wiedisync; PLAN §4.5): no roles, and their
    # team rows come from the database on every request. `club` is their club's name.
    guest: bool = False
    club: str | None = None

    @property
    def is_admin(self) -> bool:
        return not self.guest and bool(self.roles & ADMIN_ROLES)

    def with_grants(self, analyst_lineages: Iterable[uuid.UUID]) -> Viewer:
        return dataclasses.replace(self, analyst_lineages=frozenset(analyst_lineages))

    @classmethod
    def from_claims(cls, claims: Mapping[str, Any]) -> Viewer:
        rels: dict[int, frozenset[str]] = {}
        for entry in claims.get("teams", []):
            rels[int(entry["team"])] = frozenset(entry.get("rel", [])) | rels.get(
                int(entry["team"]), frozenset()
            )
        return cls(
            member_id=int(claims["mid"]),
            member_uuid=str(claims["sub"]),
            name=str(claims["name"]),
            roles=frozenset(claims.get("roles", [])),
            sport_admin=frozenset(claims.get("sport_admin", [])),
            team_rels=rels,
        )


@dataclass(frozen=True)
class TeamRef:
    id: int
    sport: str
    lineage_id: uuid.UUID
    enabled: bool
    # A guest club's team (wd_team.guest).
    guest: bool = False


def _same_side(viewer: Viewer, team: TeamRef) -> bool:
    """Guests reach guest teams only, KSCW members KSCW teams only; admins both."""
    return viewer.is_admin or team.guest == viewer.guest


def _reachable(viewer: Viewer, teams: Iterable[TeamRef]) -> list[TeamRef]:
    return [t for t in teams if t.enabled and _same_side(viewer, t)]


def can_manage_wieplay(viewer: Viewer) -> bool:
    """Enable teams, run the sync: full Wiedisync admins only."""
    return viewer.is_admin


def visible_team_ids(viewer: Viewer, teams: Iterable[TeamRef]) -> set[int]:
    """Team rows the viewer may see.

    - Only teams whose lineage is enabled for WiePlay.
    - Admins: all. Sport admins: all teams of their sport.
    - Members of a team row (player, guest, captain, coach, team manager) see that row:
      players see the seasons they were rostered in.
    - Coaches and team managers see every season of their team's lineage.
    - Helpers (analysts) see every season of the lineages they were granted.
    - Guest clubs: a guest sees their own guest team rows (a guest coach every season of
      the team); KSCW members, sport admins included, never see a guest team.
    """
    teams = _reachable(viewer, teams)
    if viewer.is_admin:
        return {t.id for t in teams}
    lead_lineages = {
        t.lineage_id for t in teams if viewer.team_rels.get(t.id, frozenset()) & LEAD_RELS
    }
    return {
        t.id
        for t in teams
        if t.sport in viewer.sport_admin
        or t.id in viewer.team_rels
        or t.lineage_id in lead_lineages
        or (t.lineage_id in viewer.analyst_lineages and not t.guest)
    }


def lead_lineages(viewer: Viewer, teams: Iterable[TeamRef]) -> set[uuid.UUID]:
    """Lineages the viewer coaches or manages in any season."""
    return {
        t.lineage_id
        for t in teams
        if _same_side(viewer, t) and viewer.team_rels.get(t.id, frozenset()) & LEAD_RELS
    }


def is_analyst(viewer: Viewer, team: TeamRef) -> bool:
    """A helper of this team: an open analyst grant on its lineage (any season). Guest teams
    have no helpers (for now)."""
    return team.enabled and not team.guest and team.lineage_id in viewer.analyst_lineages


def _sport_admin_of(viewer: Viewer, team: TeamRef) -> bool:
    """A sport admin of the team's sport: Wiedisync's, so KSCW teams only."""
    return not team.guest and team.sport in viewer.sport_admin


def _leads_team(viewer: Viewer, team: TeamRef, leads: set[uuid.UUID]) -> bool:
    """Coaches and team managers of the team (any season), sport admins, admins."""
    if not team.enabled or not _same_side(viewer, team):
        return False
    return viewer.is_admin or _sport_admin_of(viewer, team) or team.lineage_id in leads


def can_upload(viewer: Viewer, team: TeamRef) -> bool:
    """Upload or link a video for this team row, edit a video's roster (§5.1).

    Coaches and team managers of that season's team, sport admins of its sport, and the
    team's helpers. Admins without one of those roles watch and delete but do not upload.
    """
    if not team.enabled or not _same_side(viewer, team):
        return False
    return (
        _sport_admin_of(viewer, team)
        or bool(viewer.team_rels.get(team.id, frozenset()) & LEAD_RELS)
        or is_analyst(viewer, team)
    )


def can_delete_video(viewer: Viewer, team: TeamRef, leads: set[uuid.UUID]) -> bool:
    """Coaches and team managers of the team (any season), sport admins, admins. Not
    helpers."""
    return _leads_team(viewer, team, leads)


def can_use_inbox(viewer: Viewer, teams: Iterable[TeamRef]) -> bool:
    """See the NAS inbox: anyone who may upload for at least one enabled KSCW team. The inbox
    is the club's NAS share: never for guest clubs."""
    return not viewer.guest and any(can_upload(viewer, t) for t in teams if not t.guest)


def can_import_link(team: TeamRef) -> bool:
    """Import a YouTube or Twitch video (PLAN §7.2.2): the allow-list holds KSCW's and its
    members' channels, so links are for KSCW teams only. Guest clubs upload their files."""
    return not team.guest


def can_tag(viewer: Viewer, team: TeamRef, leads: set[uuid.UUID]) -> bool:
    """Tag, mark rallies and sets, review and correct WiePlay's suggestions and scores (§5.1):
    the team's coaches and team managers (every season of the team), sport admins, admins
    (owner, 16.10.2026: the admins do the first tagging), the team's helpers, and (owner,
    17.10.2026: "have everyone be able to tag") every member of that season's team: players
    tag and review their own games. Every change is in the revision log."""
    if _leads_team(viewer, team, leads) or is_analyst(viewer, team):
        return True
    return team.enabled and _same_side(viewer, team) and team.id in viewer.team_rels


def is_team_manager(viewer: Viewer, team: TeamRef, leads: set[uuid.UUID]) -> bool:
    """Coaches and team managers (any season), sport admins, admins: they see coach-only
    comments and manage the team's shared clips and reels. Not helpers."""
    return _leads_team(viewer, team, leads)


def can_share_with_team(viewer: Viewer, team: TeamRef, leads: set[uuid.UUID]) -> bool:
    """Share a clip or reel with the team (§5.1): coaches, team managers, sport admins,
    admins and helpers. Players tag (since 17.10.2026) but keep their reels to themselves."""
    return _leads_team(viewer, team, leads) or is_analyst(viewer, team)


def can_share_filter(viewer: Viewer, team: TeamRef, leads: set[uuid.UUID]) -> bool:
    """Share a saved filter (a search kept by name, PLAN §9.7) with the team: the same people
    who share clips and reels with it (coaches, team managers, sport admins, admins, helpers).
    Everyone else keeps their filters to themselves."""
    return can_share_with_team(viewer, team, leads)


def can_see_filter(viewer: Viewer, owner: int, shared: bool, sees_team: bool) -> bool:
    """Use a saved filter: its owner; one shared with a team also everyone who sees that team
    in some season (`sees_team`, from visible_team_ids: the guest wall holds)."""
    return viewer.member_id == owner or (shared and sees_team)


def can_edit_filter(viewer: Viewer, owner: int) -> bool:
    """Rename a saved filter, change its words or who sees it: its owner only."""
    return viewer.member_id == owner


def can_remove_filter(
    viewer: Viewer, owner: int, team: TeamRef | None, leads: set[uuid.UUID]
) -> bool:
    """Delete a saved filter: its owner; one shared with a team also the team's coaches, team
    managers, sport admins and admins (`team`: a row of the team it is shared with, None for a
    private filter), as for the team's shared clips and reels."""
    if viewer.member_id == owner:
        return True
    return team is not None and is_team_manager(viewer, team, leads)


def can_run_analysis(viewer: Viewer, team: TeamRef, leads: set[uuid.UUID]) -> bool:
    """Start the long jobs on the club's machine by hand (an analysis ~30 min, action
    suggestions, finding the players ~2 h of CPU): coaches, team managers, sport admins,
    admins and helpers. Players tag and review, they don't queue hours of work."""
    return _leads_team(viewer, team, leads) or is_analyst(viewer, team)


def can_export(viewer: Viewer, team: TeamRef, leads: set[uuid.UUID]) -> bool:
    """Download a watermarked MP4 (§5.1, §5.2): coaches and team managers, sport admins,
    admins. Not helpers: they tag and share, nothing leaves WiePlay through them."""
    return _leads_team(viewer, team, leads)


def can_grant_analyst(viewer: Viewer, team: TeamRef, leads: set[uuid.UUID]) -> bool:
    """Add or remove the team's helpers, see who they are (§5.1 "Grant analyst"): its
    coaches and team managers (any season), sport admins and admins. Helpers do not pick
    other helpers. Guest teams have no helpers (for now): the search lists KSCW members."""
    return not team.guest and _leads_team(viewer, team, leads)


def can_manage_people(viewer: Viewer, team: TeamRef, leads: set[uuid.UUID]) -> bool:
    """A guest team's roster and invite links (PLAN §4.5): its coaches (any season) and
    admins. KSCW rosters come from Wiedisync and are never edited in WiePlay."""
    return team.guest and _leads_team(viewer, team, leads)


def can_see_sheet(viewer: Viewer, team: TeamRef, teams: Iterable[TeamRef]) -> bool:
    """See a match's official scoresheet as it was typed in (PLAN §10.3a): whoever may watch
    the video (`teams`: every team row, for the lineages). It holds shirt numbers and scores
    only; a photo of the paper sheet (SS3) gets a narrower rule of its own."""
    return team.id in visible_team_ids(viewer, teams)


def can_edit_sheet(viewer: Viewer, team: TeamRef, leads: set[uuid.UUID]) -> bool:
    """Type in, correct or remove a match's official scoresheet: whoever may tag the match
    (coaches, team managers, sport admins, admins, the team's helpers and the season's players;
    research note §5.2: "anyone who may tag, a helper with the paper copy in hand"). Their
    winners already outrank the sheet; every change of the sheet is kept as a revision."""
    return can_tag(viewer, team, leads)
