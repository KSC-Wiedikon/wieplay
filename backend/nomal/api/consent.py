"""Consent to being filmed (PLAN §5.4): each player opts in; a coach can record a paper form.

Append-only: every change is a new row, so the history is the audit trail.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from nomal.api.access import visible_team
from nomal.api.deps import DbDep, ViewerDep, audit
from nomal.core.filming import NOTICE_VERSION, current_consent, roster_players
from nomal.core.models import Consent, WdMember, WdTeamMember
from nomal.core.permissions import can_record_paper_consent

router = APIRouter(prefix="/api", tags=["consent"])

Status = Literal["accepted", "withdrawn"]


class ConsentOut(BaseModel):
    notice_version: str  # the current notice
    accepted_version: str | None  # the notice the newest answer was given to
    status: Status | None
    via: str | None
    at: datetime | None


class ConsentIn(BaseModel):
    status: Status


class RosterConsentOut(BaseModel):
    member_id: int
    name: str
    number: int | None
    accepted_version: str | None
    status: Status | None
    via: str | None
    at: datetime | None


def _out(row: Consent | None) -> ConsentOut:
    if row is None:
        return ConsentOut(
            notice_version=NOTICE_VERSION, accepted_version=None, status=None, via=None, at=None
        )
    return ConsentOut(
        notice_version=NOTICE_VERSION,
        accepted_version=row.notice_version,
        status=row.status,
        via=row.via,
        at=row.at,
    )


@router.get("/me/consent", response_model=ConsentOut)
def my_consent(viewer: ViewerDep, db: DbDep) -> ConsentOut:
    return _out(current_consent(db, [viewer.member_id]).get(viewer.member_id))


@router.put("/me/consent", response_model=ConsentOut)
def set_my_consent(body: ConsentIn, viewer: ViewerDep, db: DbDep) -> ConsentOut:
    row = Consent(
        member_id=viewer.member_id,
        notice_version=NOTICE_VERSION,
        status=body.status,
        via="nomal",
        recorded_by=viewer.member_id,
    )
    db.add(row)
    audit(
        db,
        viewer.member_id,
        f"consent_{body.status}",
        "member",
        str(viewer.member_id),
        via="nomal",
        notice=NOTICE_VERSION,
    )
    db.commit()
    return _out(row)


@router.get("/teams/{team_id}/consent", response_model=list[RosterConsentOut])
def team_consent(team_id: int, viewer: ViewerDep, db: DbDep) -> list[RosterConsentOut]:
    access = visible_team(db, viewer, team_id)
    if not can_record_paper_consent(viewer, access.ref):
        raise HTTPException(403, "Coaches only")
    players = roster_players(db, team_id)
    consents = current_consent(db, players)
    members = {m.id: m for m in db.scalars(select(WdMember).where(WdMember.id.in_(players)))}
    out = []
    for mid in players:
        member, row = members.get(mid), consents.get(mid)
        out.append(
            RosterConsentOut(
                member_id=mid,
                name=member.display_name if member else f"#{mid}",
                number=member.number if member else None,
                accepted_version=row.notice_version if row else None,
                status=row.status if row else None,
                via=row.via if row else None,
                at=row.at if row else None,
            )
        )
    return sorted(out, key=lambda r: (r.number is None, r.number or 0, r.name))


@router.put("/teams/{team_id}/consent/{member_id}", response_model=RosterConsentOut)
def record_paper_consent(
    team_id: int, member_id: int, body: ConsentIn, viewer: ViewerDep, db: DbDep
) -> RosterConsentOut:
    """A coach records a signed paper form (or its withdrawal) for a player of the team."""
    access = visible_team(db, viewer, team_id)
    if not can_record_paper_consent(viewer, access.ref):
        raise HTTPException(403, "Coaches only")
    on_roster = db.scalar(
        select(WdTeamMember.member_id).where(
            WdTeamMember.team_id == team_id, WdTeamMember.member_id == member_id
        )
    )
    if on_roster is None:
        raise HTTPException(404, "Not on this team's roster")
    row = Consent(
        member_id=member_id,
        notice_version=NOTICE_VERSION,
        status=body.status,
        via="paper",
        recorded_by=viewer.member_id,
    )
    db.add(row)
    audit(
        db,
        viewer.member_id,
        f"consent_{body.status}",
        "member",
        str(member_id),
        via="paper",
        team=team_id,
        notice=NOTICE_VERSION,
    )
    db.commit()
    member = db.get(WdMember, member_id)
    return RosterConsentOut(
        member_id=member_id,
        name=member.display_name if member else f"#{member_id}",
        number=member.number if member else None,
        accepted_version=NOTICE_VERSION,
        status=body.status,
        via="paper",
        at=row.at,
    )
