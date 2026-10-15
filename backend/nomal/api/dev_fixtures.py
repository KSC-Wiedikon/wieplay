"""Made-up club data for local development. No real people."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from typing import Any


def season_start_year(today: date) -> int:
    # Same cutover as Wiedisync: the new season starts on 1 June.
    return today.year if today.month >= 6 else today.year - 1


def seasons(today: date) -> tuple[str, str]:
    y = season_start_year(today)
    return f"{y}/{str(y + 1)[2:]}", f"{y - 1}/{str(y)[2:]}"


# (name, sport, league, team_key); ids: 9100+n current season, 9000+n previous season
TEAMS = [
    ("H3", "volleyball", "2L", "vb-h3"),
    ("D1", "volleyball", "2L", "vb-d1"),
    ("DU23-1", "volleyball", "U23", "vb-du23-1"),
    ("Herren 1", "basketball", "H1LRA", "bb-herren-1"),
    ("H1", "volleyball", "2L", "vb-h1"),
]

MEMBERS = [
    (101, "Alex Muster", 7, ["outside"]),
    (102, "Sam Beispiel", 3, ["setter"]),
    (103, "Robin Test", 11, ["middle"]),
    (104, "Kim Probe", 1, ["libero"]),
    (105, "Noa Fiktiv", 9, ["opposite"]),
    (106, "Jo Trainer", None, []),
    (107, "Mika Korb", 23, ["guard"]),
    (108, "Lou Dunk", 5, ["center"]),
    (109, "Ada Admin", None, []),
]


def _member_uuid(member_id: int) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"nomal-dev-member-{member_id}"))


def team_listing(today: date) -> dict[str, Any]:
    current, previous = seasons(today)
    teams = []
    for n, (name, sport, league, key) in enumerate(TEAMS, start=1):
        teams.append(
            {
                "id": 9100 + n,
                "name": name,
                "sport": sport,
                "season": current,
                "league": league,
                "team_key": key,
            }
        )
        teams.append(
            {
                "id": 9000 + n,
                "name": name,
                "sport": sport,
                "season": previous,
                "league": league,
                "team_key": key,
            }
        )
    return {"version": 1, "seasons": [current, previous], "teams": teams}


ROSTERS: dict[int, list[tuple[int, str]]] = {
    1: [
        (101, "player"),
        (102, "player"),
        (103, "player"),
        (104, "player"),
        (101, "captain"),
        (106, "coach"),
    ],
    2: [(105, "player"), (102, "player")],
    3: [(105, "player")],
    4: [(107, "player"), (108, "player"), (106, "team_responsible")],
}


def snapshot(team_ids: Sequence[int], today: date) -> dict[str, Any]:
    members: dict[int, Mapping[str, Any]] = {}
    memberships, games, trainings = [], [], []
    by_id = {m[0]: m for m in MEMBERS}
    for team_id in team_ids:
        n = team_id % 100
        name, sport, league, _ = TEAMS[n - 1]
        for member_id, rel in ROSTERS.get(n, []):
            mid, mname, number, positions = by_id[member_id]
            members[mid] = {
                "id": mid,
                "uuid": _member_uuid(mid),
                "name": mname,
                "number": number,
                "positions": positions,
                "active": True,
            }
            memberships.append({"team": team_id, "member": mid, "rel": rel, "guest": False})
        base = today - timedelta(days=21) if team_id >= 9100 else today - timedelta(days=300)
        for k in range(6):
            day = base + timedelta(days=7 * k)
            played = day < today
            home = k % 2 == 0
            ours = f"KSC Wiedikon {name}"
            other = f"Testclub {chr(65 + k)}"
            games.append(
                {
                    "id": team_id * 100 + k,
                    "team": team_id,
                    "date": day.isoformat(),
                    "time": "14:00" if home else "19:30",
                    "home_name": ours if home else other,
                    "away_name": other if home else ours,
                    "our_side": "home" if home else "away",
                    "league": league,
                    "hall_name": "Halle Test" if home else "Auswärtshalle",
                    "home_score": (3 if home else 1) if played and sport == "volleyball" else None,
                    "away_score": (1 if home else 3) if played and sport == "volleyball" else None,
                    "sets": [[25, 20], [23, 25], [25, 18], [25, 22]]
                    if played and sport == "volleyball"
                    else None,
                }
            )
            trainings.append(
                {
                    "id": team_id * 100 + 50 + k,
                    "team": team_id,
                    "date": (day + timedelta(days=3)).isoformat(),
                    "start_time": "20:00",
                    "end_time": "22:00",
                    "hall_name": "Halle Test",
                }
            )
    return {
        "version": 1,
        "members": list(members.values()),
        "memberships": memberships,
        "games": games,
        "trainings": trainings,
    }


def personas(today: date) -> dict[str, dict[str, Any]]:
    """Who you can log in as on the fake bridge."""
    current, previous = seasons(today)
    h3, h3_prev, herren1 = 9101, 9001, 9104

    def claims(
        mid: int,
        roles: list[str],
        teams: list[dict[str, Any]],
        sport_admin: list[str] | None = None,
    ) -> dict[str, Any]:
        name = next(m[1] for m in MEMBERS if m[0] == mid)
        return {
            "mid": mid,
            "sub": _member_uuid(mid),
            "name": name,
            "roles": roles,
            "sport_admin": sport_admin or [],
            "teams": teams,
        }

    return {
        "admin": claims(109, ["admin"], []),
        "coach-h3": claims(
            106,
            ["user"],
            [
                {"team": h3, "season": current, "rel": ["coach"]},
                {"team": herren1, "season": current, "rel": ["team_responsible"]},
            ],
        ),
        "player-h3": claims(
            101,
            ["user"],
            [
                {"team": h3, "season": current, "rel": ["player", "captain"]},
                {"team": h3_prev, "season": previous, "rel": ["player"]},
            ],
        ),
        "basketball-player": claims(
            107,
            ["user"],
            [
                {"team": herren1, "season": current, "rel": ["player"]},
            ],
        ),
        "vb-sport-admin": claims(105, ["vb_admin"], [], ["volleyball"]),
    }
