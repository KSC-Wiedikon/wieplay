// Display helpers for tagging: skill and grade names from the taxonomy's i18n keys, clock times.

import type { TFunction } from 'i18next'
import type { RosterEntry, Side, TaxonomySkill } from './types'

const SKILLS = ['serve', 'reception', 'set', 'attack', 'block', 'dig', 'freeball'] as const
type SkillName = (typeof SKILLS)[number]

/** Grade symbols as i18n key parts (`#` can't be part of a key on its own). */
export const GRADE_KEYS = { '#': 'hash', '+': 'plus', '!': 'excl', '-': 'minus', '/': 'slash', '=': 'equal' } as const
type GradeSymbol = keyof typeof GRADE_KEYS

function isSkillName(value: string): value is SkillName {
  return (SKILLS as readonly string[]).includes(value)
}

function isGrade(value: string): value is GradeSymbol {
  return value in GRADE_KEYS
}

/** "Attack" for name_key 'skill.attack'; the code when the key is unknown. */
export function skillLabel(t: TFunction, skill: Pick<TaxonomySkill, 'name_key' | 'code'> | null | undefined, code = ''): string {
  if (!skill) return code
  const name = skill.name_key.replace(/^skill\./, '')
  return isSkillName(name) ? t(`skill.${name}`) : skill.code
}

/** What a grade means for a skill, e.g. attack # = "Kill". The symbol when unknown. */
export function gradeLabel(t: TFunction, skill: Pick<TaxonomySkill, 'category'> | null | undefined, grade: string | null | undefined): string {
  if (!grade) return ''
  if (!skill || !isGrade(grade) || !isSkillName(skill.category)) return grade
  return t(`grade.${skill.category}.${GRADE_KEYS[grade]}`)
}

export function sideLabel(t: TFunction, side: Side | null | undefined): string {
  if (side === 'us') return t('tagging.us')
  if (side === 'them') return t('tagging.them')
  return '–'
}

/**
 * A player's name. For a tag pass its member_id: the name then comes from the roster entry of
 * that member, so a tag whose link was removed shows no name.
 */
export function playerName(
  roster: readonly RosterEntry[],
  side: Side,
  number: number | null,
  memberId?: number | null,
): string | null {
  if (side !== 'us' || number === null) return null
  if (memberId === null) return null
  const entry =
    memberId === undefined
      ? roster.find((r) => r.side === 'us' && r.number === number)
      : roster.find((r) => r.member_id === memberId)
  return entry?.name ?? null
}

/** "#7 Luca Brunner", "#7" (no name known), "Them #12", "Them". */
export function playerLabel(
  t: TFunction,
  side: Side,
  number: number | null,
  roster: readonly RosterEntry[],
  memberId?: number | null,
): string {
  if (side === 'them') return number === null ? t('tagging.them') : t('tagging.themNumber', { number })
  if (number === null) return t('tagging.us')
  const name = playerName(roster, side, number, memberId)
  return name ? `#${number} ${name}` : `#${number}`
}

const pad2 = (n: number) => String(n).padStart(2, '0')

/** 754_300 → "12:34.3"; with hours "1:02:34.3". Tenths, rounded down. */
export function formatClock(ms: number | null | undefined, { tenths = true } = {}): string {
  if (ms == null || !Number.isFinite(ms)) return ''
  const total = Math.max(0, Math.floor(ms / 100))
  const tenth = total % 10
  const seconds = Math.floor(total / 10)
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  const base = h > 0 ? `${h}:${pad2(m)}:${pad2(s)}` : `${m}:${pad2(s)}`
  return tenths ? `${base}.${tenth}` : base
}
