"""May this team be filmed on this day? (PLAN §5.3, §5.4)

Three rules, enforced here and nowhere else:

1. The team's policy: DU23-1 films trainings only (team_policy).
2. Adults only: every rostered person is 18 or older on the day. Wiedisync answers this
   (`adult-check`); birthdates never reach Nomal. Missing birthdate = fails.
3. Consent: every current roster player has opted in.

The check runs fresh before anything is stored (`fresh=True`) and from a short cache for
the "filming allowed / blocked" hints on the team page.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from nomal.core.models import AdultCheck, Consent, TeamPolicy, WdTeam, WdTeamMember
from nomal.core.sync import BridgeClient, BridgeError

# Version of the information notice members agree to (docs/PRIVACY.md). Draft until the
# club's privacy contact has reviewed §5 of the plan.
NOTICE_VERSION = "2026-10-draft"
ADULT_CHECK_MAX_AGE = timedelta(hours=6)


@dataclass(frozen=True)
class Blocker:
    # policy_matches | policy_trainings | under_18 | no_birthdate | no_roster |
    # adult_check_unavailable | consent_missing
    code: str
    count: int | None = None
    detail: str | None = None


@dataclass(frozen=True)
class FilmingCheck:
    allowed: bool
    blockers: tuple[Blocker, ...] = field(default_factory=tuple)
    adult_checked_at: datetime | None = None


def current_consent(db: Session, member_ids: Iterable[int]) -> dict[int, Consent]:
    """Each member's newest consent row (append-only table)."""
    ids = sorted(set(member_ids))
    if not ids:
        return {}
    rows = db.scalars(
        select(Consent)
        .where(Consent.member_id.in_(ids))
        .order_by(Consent.member_id, Consent.at.desc(), Consent.id.desc())
        .ext(distinct_on(Consent.member_id))
    )
    return {row.member_id: row for row in rows}


def roster_players(db: Session, team_id: int) -> list[int]:
    return sorted(
        set(
            db.scalars(
                select(WdTeamMember.member_id).where(
                    WdTeamMember.team_id == team_id, WdTeamMember.rel == "player"
                )
            )
        )
    )


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
    kind: str,
    on: date,
    *,
    fresh: bool,
    now: datetime | None = None,
) -> FilmingCheck:
    now = now or datetime.now(UTC)
    blockers: list[Blocker] = []

    policy = db.get(TeamPolicy, team.lineage_id)
    if policy is not None:
        if kind == "match" and not policy.matches_allowed:
            blockers.append(Blocker("policy_matches", detail=policy.reason))
        if kind == "training" and not policy.trainings_allowed:
            blockers.append(Blocker("policy_trainings", detail=policy.reason))

    adult, checked_at = _adult_blockers(db, bridge, team, on, fresh, now)
    blockers += adult

    players = roster_players(db, team.id)
    consents = current_consent(db, players)
    missing = [m for m in players if (c := consents.get(m)) is None or c.status != "accepted"]
    if missing:
        blockers.append(Blocker("consent_missing", len(missing)))

    return FilmingCheck(not blockers, tuple(blockers), checked_at)
