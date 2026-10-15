"""Tagging vocabularies, loaded from nomal/taxonomies/<sport>.yaml (PLAN §6.5).

The YAML is the source; the `taxonomy` table mirrors it for SQL reports and datasets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from typing import Any, Literal

import yaml
from sqlalchemy.orm import Session

from nomal.core.models import TaxonomyEntry

Side = Literal["us", "them"]


@dataclass(frozen=True)
class Skill:
    code: str
    name_key: str
    category: str
    lead_ms: int
    pre_ms: int
    post_ms: int
    starts_rally: bool = False
    ends_rally_on: dict[str, str] = field(default_factory=dict)  # grade -> self | other
    points: int = 0  # basketball: what a made shot adds
    graded: bool = True  # whether the skill takes a grade at all


@dataclass(frozen=True)
class Taxonomy:
    sport: str
    version: int
    grades: tuple[str, ...]
    zones: tuple[int, ...]
    skills: dict[str, Skill]

    def rally_winner(self, code: str, grade: str | None, side: Side) -> Side | None:
        """Who wins the rally when this tag ends it (a kill, an ace, an error), else None."""
        skill = self.skills.get(code)
        if skill is None or grade is None:
            return None
        outcome = skill.ends_rally_on.get(grade)
        if outcome is None:
            return None
        other: Side = "them" if side == "us" else "us"
        return side if outcome == "self" else other

    def points(self, code: str, grade: str | None) -> int:
        """Points a tag adds to its side's score (basketball: a made shot)."""
        skill = self.skills.get(code)
        return skill.points if skill is not None and grade == "+" else 0

    def as_json(self) -> dict[str, Any]:
        return {
            "sport": self.sport,
            "version": self.version,
            "grades": list(self.grades),
            "zones": list(self.zones),
            "skills": [vars(s) for s in self.skills.values()],
        }


SPORTS_WITH_TAXONOMY = ("volleyball", "basketball")


@cache
def load(sport: str) -> Taxonomy:
    text = resources.files("nomal.taxonomies").joinpath(f"{sport}.yaml").read_text()
    raw = yaml.safe_load(text)
    skills = {
        s["code"]: Skill(
            code=s["code"],
            name_key=s["name_key"],
            category=s["category"],
            lead_ms=int(s["lead_ms"]),
            pre_ms=int(s["pre_ms"]),
            post_ms=int(s["post_ms"]),
            starts_rally=bool(s.get("starts_rally", False)),
            ends_rally_on=dict(s.get("ends_rally_on") or {}),
            points=int(s.get("points", 0)),
            graded=bool(s.get("graded", raw["sport"] == "volleyball")),
        )
        for s in raw["skills"]
    }
    return Taxonomy(
        sport=raw["sport"],
        version=int(raw["version"]),
        grades=tuple(raw["grades"]),
        zones=tuple(int(z) for z in raw["zones"]),
        skills=skills,
    )


def seed(db: Session) -> None:
    """Mirror the YAML vocabularies into the taxonomy table (idempotent)."""
    for sport in SPORTS_WITH_TAXONOMY:
        tax = load(sport)
        properties: dict[str, Any] = {"grade": {"enum": list(tax.grades)}}
        if tax.zones:
            properties |= {
                "zone_from": {"enum": list(tax.zones)},
                "zone_to": {"enum": list(tax.zones)},
            }
        schema = {"type": "object", "properties": properties, "additionalProperties": False}
        for skill in tax.skills.values():
            db.merge(
                TaxonomyEntry(
                    sport=sport,
                    version=tax.version,
                    code=skill.code,
                    name_key=skill.name_key,
                    category=skill.category,
                    hotkey=skill.code,
                    default_pre_ms=skill.pre_ms,
                    default_post_ms=skill.post_ms,
                    lead_ms=skill.lead_ms,
                    attributes_schema=schema,
                    starts_rally=skill.starts_rally,
                    ends_rally_on=skill.ends_rally_on,
                    active=True,
                )
            )
    db.commit()
