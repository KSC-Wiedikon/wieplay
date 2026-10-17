import dataclasses
import uuid

from nomal.core.permissions import TeamRef, Viewer, can_manage_nomal, visible_team_ids

H3 = uuid.uuid4()
D1 = uuid.uuid4()
HERREN1 = uuid.uuid4()
OFF = uuid.uuid4()
TEAMS = [
    TeamRef(92, "volleyball", H3, True),  # H3, current season
    TeamRef(12, "volleyball", H3, True),  # H3, previous season
    TeamRef(80, "volleyball", D1, True),  # D1
    TeamRef(86, "basketball", HERREN1, True),
    TeamRef(99, "volleyball", OFF, False),  # not enabled for Nomal
]


def viewer(roles=("user",), sport_admin=(), teams=None) -> Viewer:
    return Viewer.from_claims(
        {
            "mid": 1,
            "sub": "u",
            "name": "Test",
            "roles": list(roles),
            "sport_admin": list(sport_admin),
            "teams": teams or [],
        }
    )


def test_admin_sees_every_enabled_team_and_manages_nomal() -> None:
    admin = viewer(roles=("admin",))
    assert visible_team_ids(admin, TEAMS) == {92, 12, 80, 86}
    assert can_manage_nomal(admin)
    assert can_manage_nomal(viewer(roles=("superuser",)))


def test_sport_admin_sees_their_sport_only() -> None:
    assert visible_team_ids(viewer(sport_admin=("basketball",)), TEAMS) == {86}
    assert not can_manage_nomal(viewer(roles=("vb_admin",), sport_admin=("volleyball",)))


def test_player_sees_only_their_rostered_seasons() -> None:
    player = viewer(teams=[{"team": 92, "season": "2026/27", "rel": ["player"]}])
    assert visible_team_ids(player, TEAMS) == {92}


def test_coach_sees_the_whole_lineage() -> None:
    coach = viewer(teams=[{"team": 92, "season": "2026/27", "rel": ["coach"]}])
    assert visible_team_ids(coach, TEAMS) == {92, 12}
    manager = viewer(teams=[{"team": 86, "season": "2026/27", "rel": ["team_responsible"]}])
    assert visible_team_ids(manager, TEAMS) == {86}


def test_disabled_teams_stay_hidden_even_for_their_members() -> None:
    member = viewer(teams=[{"team": 99, "season": "2026/27", "rel": ["coach"]}])
    assert visible_team_ids(member, TEAMS) == set()


def test_board_member_without_admin_role_sees_nothing() -> None:
    assert visible_team_ids(viewer(roles=("vorstand",)), TEAMS) == set()


# M1: videos (PLAN §5.1) — who may upload and delete.
from nomal.core.permissions import (  # noqa: E402
    can_delete_video,
    can_upload,
    can_use_inbox,
    lead_lineages,
)

H3_NOW, H3_PREV, D1_NOW, HERREN1_NOW, OFF_TEAM = TEAMS

VIDEO_MATRIX = [
    # (who, team, can upload, can delete)
    (viewer(roles=("admin",)), H3_NOW, False, True),
    (viewer(sport_admin=("volleyball",)), H3_NOW, True, True),
    (viewer(sport_admin=("volleyball",)), HERREN1_NOW, False, False),
    (viewer(teams=[{"team": 92, "rel": ["coach"]}]), H3_NOW, True, True),
    (viewer(teams=[{"team": 92, "rel": ["coach"]}]), H3_PREV, False, True),  # past season
    (viewer(teams=[{"team": 12, "rel": ["coach"]}]), H3_NOW, False, True),  # former coach
    (viewer(teams=[{"team": 86, "rel": ["team_responsible"]}]), HERREN1_NOW, True, True),
    (viewer(teams=[{"team": 92, "rel": ["player", "captain"]}]), H3_NOW, False, False),
    (viewer(roles=("vorstand",)), H3_NOW, False, False),
    (viewer(teams=[{"team": 99, "rel": ["coach"]}]), OFF_TEAM, False, False),  # not enabled
]


def test_video_permission_matrix() -> None:
    for who, team, upload, delete in VIDEO_MATRIX:
        leads = lead_lineages(who, TEAMS)
        assert can_upload(who, team) is upload, (who, team)
        assert can_delete_video(who, team, leads) is delete, (who, team)


def test_inbox_is_for_people_who_upload_somewhere() -> None:
    assert can_use_inbox(viewer(teams=[{"team": 92, "rel": ["coach"]}]), TEAMS)
    assert not can_use_inbox(viewer(roles=("admin",)), TEAMS)
    assert not can_use_inbox(viewer(teams=[{"team": 92, "rel": ["player"]}]), TEAMS)


# Tagging (M2), sharing and export (M3): admins too since 16.10.2026.
from nomal.core.permissions import can_export, can_share_with_team, can_tag  # noqa: E402

TAG_MATRIX = [
    # (who, team, may tag, may share with the team and export)
    (viewer(roles=("superuser",)), H3_NOW, True, True),
    (viewer(roles=("admin",)), HERREN1_NOW, True, True),
    (viewer(roles=("admin",)), OFF_TEAM, False, False),  # not enabled
    (viewer(sport_admin=("volleyball",)), H3_PREV, True, True),
    (viewer(sport_admin=("volleyball",)), HERREN1_NOW, False, False),
    (viewer(teams=[{"team": 12, "rel": ["coach"]}]), H3_NOW, True, True),  # any season
    # Players tag their own season's games (owner, 17.10.2026), not other seasons.
    (viewer(teams=[{"team": 92, "rel": ["player", "captain"]}]), H3_NOW, True, False),
    (viewer(teams=[{"team": 92, "rel": ["player"]}]), H3_PREV, False, False),
    (viewer(teams=[{"team": 99, "rel": ["player"]}]), OFF_TEAM, False, False),
    (viewer(roles=("vorstand",)), H3_NOW, False, False),
]


def test_tag_permission_matrix() -> None:
    for who, team, tag, share_export in TAG_MATRIX:
        leads = lead_lineages(who, TEAMS)
        assert can_tag(who, team, leads) is tag, (who, team)
        assert can_share_with_team(who, team, leads) is share_export, (who, team)
        assert can_export(who, team, leads) is share_export, (who, team)


# Team helpers (PLAN §5.1 "analyst"): a Nomal grant per lineage, loaded per request.
from nomal.core.permissions import (  # noqa: E402
    can_grant_analyst,
    is_analyst,
    is_team_manager,
)

ACTIONS = ("see", "upload", "tag", "share", "export", "delete", "grant", "manage")


def allowed(who: Viewer, team: TeamRef) -> set[str]:
    leads = lead_lineages(who, TEAMS)
    checks = {
        "see": team.id in visible_team_ids(who, TEAMS),
        "upload": can_upload(who, team),
        "tag": can_tag(who, team, leads),
        "share": can_share_with_team(who, team, leads),
        "export": can_export(who, team, leads),
        "delete": can_delete_video(who, team, leads),
        "grant": can_grant_analyst(who, team, leads),
        "manage": is_team_manager(who, team, leads),  # coach-only comments, shared clips
    }
    return {action for action, ok in checks.items() if ok}


D1_PLAYER = viewer(teams=[{"team": 80, "rel": ["player"]}])
H3_HELPER = D1_PLAYER.with_grants({H3})  # a D1 player helping H3
H3_PLAYER_HELPER = viewer(teams=[{"team": 92, "rel": ["player"]}]).with_grants({H3})
HELPS = {"see", "upload", "tag", "share"}
LEADS = set(ACTIONS)

HELPER_MATRIX = [
    # (who, team, what they may do)
    (H3_HELPER, H3_NOW, HELPS),
    (H3_HELPER, H3_PREV, HELPS),  # every season of the team, like a coach
    (H3_HELPER, D1_NOW, {"see", "tag"}),  # their own team: a player there
    (H3_HELPER, HERREN1_NOW, set()),  # another team
    (D1_PLAYER, H3_NOW, set()),  # the grant revoked: nothing loaded for the request
    (D1_PLAYER.with_grants({OFF}), OFF_TEAM, set()),  # a team that does not use Nomal
    (H3_PLAYER_HELPER, H3_PREV, HELPS),  # a player picked to help sees earlier seasons too
    (viewer(teams=[{"team": 92, "rel": ["player"]}]), H3_NOW, {"see", "tag"}),
    (viewer(teams=[{"team": 92, "rel": ["coach"]}]), H3_NOW, LEADS),
    (viewer(teams=[{"team": 12, "rel": ["team_responsible"]}]), H3_NOW, LEADS - {"upload"}),
    (viewer(sport_admin=("volleyball",)), H3_NOW, LEADS),
    (viewer(roles=("admin",)), H3_NOW, LEADS - {"upload"}),
    (viewer(roles=("admin",)), OFF_TEAM, set()),
    (viewer(roles=("vorstand",)), H3_NOW, set()),
]


def test_helper_permission_matrix() -> None:
    for who, team, expected in HELPER_MATRIX:
        assert allowed(who, team) == expected, (who, team)


def test_helpers_see_their_teams_every_season_and_upload_there() -> None:
    assert visible_team_ids(H3_HELPER, TEAMS) == {92, 12, 80}
    assert is_analyst(H3_HELPER, H3_NOW) and not is_analyst(H3_HELPER, D1_NOW)
    assert not is_analyst(D1_PLAYER.with_grants({OFF}), OFF_TEAM)
    assert can_use_inbox(H3_HELPER, TEAMS)
    assert not can_use_inbox(D1_PLAYER, TEAMS)
    # Grants never come from the login token: the API reads them for every request.
    claims = {"mid": 1, "sub": "u", "name": "x", "analyst_lineages": [str(H3)]}
    assert Viewer.from_claims(claims).analyst_lineages == frozenset()


# Guest clubs (PLAN §4.5): walled off both ways; only full admins see both sides.
from nomal.core.permissions import (  # noqa: E402
    can_import_link,
    can_manage_people,
    can_run_analysis,
)

GAST = uuid.uuid4()  # a guest club's team (two seasons)
GAST_NOW = TeamRef(1_000_000_001, "volleyball", GAST, True, guest=True)
GAST_PREV = TeamRef(1_000_000_002, "volleyball", GAST, True, guest=True)
OTHER_GAST = TeamRef(1_000_000_003, "volleyball", uuid.uuid4(), True, guest=True)
GAST_OFF = TeamRef(1_000_000_004, "volleyball", uuid.uuid4(), False, guest=True)
ALL_TEAMS = [*TEAMS, GAST_NOW, GAST_PREV, OTHER_GAST, GAST_OFF]


def guest(teams: list[dict[str, object]], roles: tuple[str, ...] = ()) -> Viewer:
    """A guest session: team rows from the database, never roles."""
    base = viewer(roles=roles, teams=teams)
    return dataclasses.replace(base, guest=True, club="VBC Gast")


def allowed_all(who: Viewer, team: TeamRef) -> set[str]:
    leads = lead_lineages(who, ALL_TEAMS)
    checks = {
        "see": team.id in visible_team_ids(who, ALL_TEAMS),
        "upload": can_upload(who, team),
        "tag": can_tag(who, team, leads),
        "share": can_share_with_team(who, team, leads),
        "export": can_export(who, team, leads),
        "delete": can_delete_video(who, team, leads),
        "grant": can_grant_analyst(who, team, leads),
        "manage": is_team_manager(who, team, leads),
        "people": can_manage_people(who, team, leads),
        "run": can_run_analysis(who, team, leads),  # the long jobs, by hand
    }
    return {action for action, ok in checks.items() if ok}


GUEST_COACH = guest([{"team": GAST_NOW.id, "rel": ["coach"]}])
GUEST_PLAYER = guest([{"team": GAST_NOW.id, "rel": ["player"]}])
COACHES = {"see", "upload", "tag", "share", "export", "delete", "manage", "people", "run"}
ADMIN_ON_GUESTS = COACHES - {"upload"}

GUEST_MATRIX = [
    # (who, team, what they may do)
    (GUEST_COACH, GAST_NOW, COACHES),
    (GUEST_COACH, GAST_PREV, COACHES - {"upload"}),  # every season, like a coach
    (GUEST_COACH, OTHER_GAST, set()),  # another guest club
    (GUEST_COACH, GAST_OFF, set()),  # switched off
    (GUEST_COACH, H3_NOW, set()),  # no KSCW team, ever
    (GUEST_PLAYER, GAST_NOW, {"see", "tag"}),  # players tag their own games (17.10.2026)
    (GUEST_PLAYER, GAST_PREV, set()),  # players: the seasons they were on
    (GUEST_PLAYER, H3_NOW, set()),
    # A guest viewer with Wiedisync-looking roles or rows gets nothing from them.
    (guest([{"team": 92, "rel": ["coach"]}], roles=("admin",)), H3_NOW, set()),
    (guest([], roles=("admin",)), GAST_NOW, set()),
    # KSCW members never see a guest team: players, coaches, sport admins, helpers.
    (viewer(teams=[{"team": 92, "rel": ["player"]}]), GAST_NOW, set()),
    (viewer(teams=[{"team": 92, "rel": ["coach"]}]), GAST_NOW, set()),
    (viewer(sport_admin=("volleyball",)), GAST_NOW, set()),
    (D1_PLAYER.with_grants({GAST}), GAST_NOW, set()),  # a grant on a guest team counts not
    (viewer(teams=[{"team": GAST_NOW.id, "rel": ["coach"]}]), GAST_NOW, set()),  # forged row
    # Full admins see both sides; they manage a guest team's people but pick no helpers.
    (viewer(roles=("admin",)), GAST_NOW, ADMIN_ON_GUESTS),
    (viewer(roles=("admin",)), GAST_OFF, set()),
    # KSCW coaches keep their teams; their rosters are Wiedisync's, never edited in Nomal.
    (viewer(teams=[{"team": 92, "rel": ["coach"]}]), H3_NOW, LEADS | {"run"}),
    (viewer(teams=[{"team": 92, "rel": ["player"]}]), H3_NOW, {"see", "tag"}),
]


def test_guest_permission_matrix() -> None:
    for who, team, expected in GUEST_MATRIX:
        assert allowed_all(who, team) == expected, (who, team)


def test_guests_see_only_their_own_teams() -> None:
    assert visible_team_ids(GUEST_COACH, ALL_TEAMS) == {GAST_NOW.id, GAST_PREV.id}
    assert visible_team_ids(GUEST_PLAYER, ALL_TEAMS) == {GAST_NOW.id}
    assert visible_team_ids(viewer(sport_admin=("volleyball",)), ALL_TEAMS) == {92, 12, 80}
    admin = viewer(roles=("admin",))
    assert visible_team_ids(admin, ALL_TEAMS) == {92, 12, 80, 86, GAST_NOW.id, GAST_PREV.id,
                                                  OTHER_GAST.id}  # fmt: skip
    assert not GUEST_COACH.is_admin and not guest([], roles=("superuser",)).is_admin
    assert not can_manage_nomal(guest([], roles=("admin",)))


def test_guests_neither_use_the_inbox_nor_import_links() -> None:
    assert not can_use_inbox(GUEST_COACH, ALL_TEAMS)
    assert can_use_inbox(viewer(teams=[{"team": 92, "rel": ["coach"]}]), ALL_TEAMS)
    assert not can_import_link(GAST_NOW)
    assert can_import_link(H3_NOW)
