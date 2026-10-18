"""May this team be filmed on this day? (PLAN §5.3, §5.4)

Two rules, enforced here and nowhere else:

1. The team's policy: an admin can switch a team's matches off (team_policy), e.g. when
   its opponents may be under 18.
2. Adults only: every rostered person is 18 or older on the day. Wiedisync answers this
   (`adult-check`); birthdates never reach WiePlay. Missing birthdate = fails. A guest club's
   team (PLAN §4.5) has no birthdates anywhere WiePlay can ask: its coach declares once that
   everyone filmed is 18 or older, and that declaration is the check. WiePlay never asks
   Wiedisync about a guest team.

No per-player consent: Swiss Volley allows anyone to record a match (Volleyballreglement
2026/27, Art. 86), and the club decided on 16.10.2026 not to ask for it (docs/PRIVACY.md).
WiePlay handles matches only (decided 16.10.2026, PLAN §5.3): every video is a match.

The check runs fresh before anything is stored (`fresh=True`) and from a short cache for
the "filming allowed / blocked" hints on the team page.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from wieplay.core.models import AdultCheck, TeamPolicy, WdTeam
from wieplay.core.sync import BridgeClient, BridgeError, run_sync

ADULT_CHECK_MAX_AGE = timedelta(hours=6)
CLUB_TZ = ZoneInfo("Europe/Zurich")
# A camera clock further off than this was never set; its date proves nothing.
CAMERA_DATE_WINDOW_DAYS = 60


@dataclass(frozen=True)
class Blocker:
    # policy_matches | under_18 | no_birthdate | no_roster | adult_check_unavailable |
    # roster_unavailable | adults_not_declared (a guest team)
    code: str
    count: int | None = None
    detail: str | None = None


def club_today() -> date:
    return datetime.now(CLUB_TZ).date()


def camera_day(capture: Mapping[str, Any]) -> date | None:
    """The recording day according to the camera's metadata (creation_time), club time."""
    raw = capture.get("creation_time")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        stamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=UTC)
    return stamp.astimezone(CLUB_TZ).date()


def refresh_rosters(db: Session, bridge: BridgeClient | None, team: WdTeam | None = None) -> bool:
    """Sync rosters right before an upload is accepted (PLAN §4.3). Commits. A guest team's
    roster is WiePlay's own: nothing to fetch, it is always fresh."""
    if team is not None and team.guest:
        return True
    if bridge is None:
        return False
    try:
        run_sync(db, bridge)
    except BridgeError:
        return False
    return True


@dataclass(frozen=True)
class FilmingCheck:
    allowed: bool
    blockers: tuple[Blocker, ...] = field(default_factory=tuple)
    adult_checked_at: datetime | None = None


def _adult_blockers(
    db: Session, bridge: BridgeClient | None, team: WdTeam, on: date, fresh: bool, now: datetime
) -> tuple[list[Blocker], datetime | None]:
    cached = db.get(AdultCheck, (team.id, on))
    stale = cached is None or now - cached.checked_at > ADULT_CHECK_MAX_AGE
    if fresh or stale:
        try:
            if bridge is None:
                raise BridgeError("no bridge configured")
            answer = bridge.adult_check(team.id, on)
            reasons = Counter(
                str(p.get("reason")) for p in answer.get("people", []) if not p.get("ok")
            )
            cached = cached or AdultCheck(team_id=team.id, on_date=on)
            cached.passed = bool(answer.get("passed"))
            cached.failing_count = int(answer.get("failing_count", sum(reasons.values())))
            cached.reasons = dict(reasons)
            cached.checked_at = now
            if not answer.get("people"):
                cached.passed = False  # nobody on the roster proves nothing
                cached.reasons = {"no_roster": 1}
            db.merge(cached)
            db.flush()
        except (BridgeError, ValueError, TypeError):
            # Before storing anything, an unanswered check blocks. For hints, an older
            # answer is better than none.
            if fresh or cached is None:
                return [Blocker("adult_check_unavailable")], None
    assert cached is not None
    if cached.passed:
        return [], cached.checked_at
    blockers = [Blocker(code, count) for code, count in sorted(cached.reasons.items())]
    return blockers or [Blocker("under_18", cached.failing_count)], cached.checked_at


def check_filming(
    db: Session,
    bridge: BridgeClient | None,
    team: WdTeam,
    on: date,
    *,
    fresh: bool,
    now: datetime | None = None,
    roster_fresh: bool = True,
) -> FilmingCheck:
    now = now or datetime.now(UTC)
    blockers: list[Blocker] = []
    if not roster_fresh and not team.guest:
        blockers.append(Blocker("roster_unavailable"))

    policy = db.get(TeamPolicy, team.lineage_id)
    if policy is not None and not policy.matches_allowed:
        blockers.append(Blocker("policy_matches", detail=policy.reason))

    if team.guest:
        declared = team.guest_adults_declared_at  # per season (PLAN §4.5)
        if declared is None:
            blockers.append(Blocker("adults_not_declared"))
        return FilmingCheck(not blockers, tuple(blockers), declared)

    adult, checked_at = _adult_blockers(db, bridge, team, on, fresh, now)
    blockers += adult

    return FilmingCheck(not blockers, tuple(blockers), checked_at)
