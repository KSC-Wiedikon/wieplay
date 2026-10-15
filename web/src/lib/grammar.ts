// The tag entry grammar (docs/PLAN.md §9.3), volleyball, DataVolley-flavoured:
//
//   [a]<number><skill>[grade][zone_from][zone_to]     7A#  a12S=  5R+  3A#46
//
// `a` (or `A`) as the first character means the opponent ("them"); without it, us. The skill
// letter is case-insensitive and must be one of the taxonomy's codes. Grade, zones and even the
// number are optional, so a half-typed entry still parses into something the live preview can
// show. Pure functions only: the workspace and the event table share them.

import type { RosterEntry, Side, Taxonomy, TaxonomySkill } from './types'

/** Why an entry can't be saved yet. */
export type EntryError =
  /** A letter that is not a skill of the taxonomy. */
  | 'unknownSkill'
  /** More than two digits for the number. */
  | 'numberTooLong'
  /** A zone digit the taxonomy doesn't have (volleyball: 1–9). */
  | 'invalidZone'
  /** Anything after the skill that is not a grade or a zone, or a third zone. */
  | 'unexpected'
  /** A grade or zone typed before the skill letter. */
  | 'skillMissing'

export interface ParsedEntry {
  text: string
  side: Side
  number: number | null
  skill: TaxonomySkill | null
  /** The skill letter as typed, upper case (also when it is not a known skill). */
  skillCode: string | null
  grade: string | null
  zoneFrom: number | null
  zoneTo: number | null
  /** The roster snapshot's entry for this number on this side. */
  player: RosterEntry | null
  /**
   * The number is not on that side's roster. Allowed (the snapshot may be incomplete) but
   * flagged. Not raised for a side whose roster is empty (opponents are often not entered).
   */
  unknownNumber: boolean
  error: EntryError | null
  /** A known skill and no error: Enter saves it. */
  complete: boolean
  /** What the next character would be, for the preview's hint. */
  expects: 'number' | 'skill' | 'grade' | 'zone' | 'none'
}

const DIGIT = /^[0-9]$/
const LETTER = /^[a-z]$/i

export function skillByCode(taxonomy: Taxonomy, code: string): TaxonomySkill | null {
  const upper = code.toUpperCase()
  return taxonomy.skills.find((s) => s.code.toUpperCase() === upper) ?? null
}

export function rosterEntry(roster: readonly RosterEntry[], side: Side, number: number | null): RosterEntry | null {
  if (number === null) return null
  return roster.find((r) => r.side === side && r.number === number) ?? null
}

/** Parses an entry as typed so far. Never throws. */
export function parseEntry(text: string, taxonomy: Taxonomy, roster: readonly RosterEntry[] = []): ParsedEntry {
  const chars = [...text]
  const result: ParsedEntry = {
    text,
    side: 'us',
    number: null,
    skill: null,
    skillCode: null,
    grade: null,
    zoneFrom: null,
    zoneTo: null,
    player: null,
    unknownNumber: false,
    error: null,
    complete: false,
    expects: 'number',
  }
  let i = 0
  if (chars[0] === 'a' || chars[0] === 'A') {
    result.side = 'them'
    i = 1
  }

  let digits = ''
  while (i < chars.length && DIGIT.test(chars[i] as string)) {
    digits += chars[i]
    i += 1
  }
  if (digits.length > 2) result.error = 'numberTooLong'
  if (digits) result.number = Number(digits)
  finishPlayer(result, roster)

  if (i >= chars.length) {
    result.expects = digits ? 'skill' : 'number'
    return result
  }

  const letter = chars[i] as string
  if (!LETTER.test(letter)) {
    result.error ??= 'skillMissing'
    result.expects = 'skill'
    return result
  }
  result.skillCode = letter.toUpperCase()
  result.skill = skillByCode(taxonomy, letter)
  if (!result.skill) result.error ??= 'unknownSkill'
  i += 1
  result.expects = 'grade'

  if (i < chars.length && taxonomy.grades.includes(chars[i] as string)) {
    result.grade = chars[i] as string
    i += 1
  }
  if (i < chars.length || result.grade) result.expects = 'zone'

  while (i < chars.length) {
    const c = chars[i] as string
    if (DIGIT.test(c) && result.zoneTo === null) {
      const zone = Number(c)
      if (!taxonomy.zones.includes(zone)) result.error ??= 'invalidZone'
      if (result.zoneFrom === null) result.zoneFrom = zone
      else result.zoneTo = zone
      i += 1
      continue
    }
    result.error ??= 'unexpected'
    break
  }
  if (result.zoneTo !== null) result.expects = 'none'

  result.complete = result.skill !== null && result.error === null
  return result
}

function finishPlayer(result: ParsedEntry, roster: readonly RosterEntry[]): void {
  result.player = rosterEntry(roster, result.side, result.number)
  const sideHasRoster = roster.some((r) => r.side === result.side)
  result.unknownNumber = result.number !== null && result.player === null && sideHasRoster
}

/** Tab while typing: adds or removes the `a` prefix. */
export function toggleSide(text: string): string {
  if (text.startsWith('a') || text.startsWith('A')) return text.slice(1)
  return `a${text}`
}

/** Characters the entry box accepts at all (anything else is ignored while typing). */
export function isEntryChar(key: string, taxonomy: Taxonomy): boolean {
  return key.length === 1 && (DIGIT.test(key) || LETTER.test(key) || taxonomy.grades.includes(key))
}

/** The entry text for a tag, e.g. `a12S=` or `3A#46`. */
export function formatEntry(tag: {
  side: Side
  number: number | null
  code: string
  attributes: { grade?: string; zone_from?: number; zone_to?: number }
}): string {
  const { grade, zone_from: from, zone_to: to } = tag.attributes
  return `${tag.side === 'them' ? 'a' : ''}${tag.number ?? ''}${tag.code}${grade ?? ''}${from ?? ''}${to ?? ''}`
}

/** Who wins the rally when this tag ends it (a kill, an ace, an error); null when it doesn't. */
export function rallyWinner(skill: TaxonomySkill | null, grade: string | null | undefined, side: Side): Side | null {
  if (!skill || !grade) return null
  const outcome = skill.ends_rally_on[grade]
  if (!outcome) return null
  return outcome === 'self' ? side : otherSide(side)
}

export function otherSide(side: Side): Side {
  return side === 'us' ? 'them' : 'us'
}
