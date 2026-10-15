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


# M1: videos (PLAN §5.1) — who may upload, delete, record paper consent.
from nomal.core.permissions import (  # noqa: E402
    can_delete_video,
    can_record_paper_consent,
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
        assert can_record_paper_consent(who, team) is upload, (who, team)
        assert can_delete_video(who, team, leads) is delete, (who, team)


def test_inbox_is_for_people_who_upload_somewhere() -> None:
    assert can_use_inbox(viewer(teams=[{"team": 92, "rel": ["coach"]}]), TEAMS)
    assert not can_use_inbox(viewer(roles=("admin",)), TEAMS)
    assert not can_use_inbox(viewer(teams=[{"team": 92, "rel": ["player"]}]), TEAMS)
