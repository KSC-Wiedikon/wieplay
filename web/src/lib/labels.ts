// Display helpers for tagging: skill and grade names from the taxonomy's i18n keys, clock times.

import type { TFunction } from 'i18next'
import type { SideNames } from './sides'
import type { RosterEntry, ScoreNote, ScoreReason, Segment, SegmentKind, Side, TaxonomySkill } from './types'

/** Volleyball's skills; their names double as the categories that have grade meanings. */
const VOLLEYBALL = ['serve', 'reception', 'set', 'attack', 'block', 'dig', 'freeball'] as const
const SKILLS = [
  ...VOLLEYBALL,
  'two_pointer',
  'three_pointer',
  'free_throw',
  'offensive_rebound',
  'defensive_rebound',
  'assist',
  'turnover',
  'steal',
  'foul',
] as const
type SkillName = (typeof SKILLS)[number]
type GradedCategory = (typeof VOLLEYBALL)[number]

/** Grade symbols as i18n key parts (`#` can't be part of a key on its own). */
export const GRADE_KEYS = { '#': 'hash', '+': 'plus', '!': 'excl', '-': 'minus', '/': 'slash', '=': 'equal' } as const
type GradeSymbol = keyof typeof GRADE_KEYS

function isSkillName(value: string): value is SkillName {
  return (SKILLS as readonly string[]).includes(value)
}

function isGrade(value: string): value is GradeSymbol {
  return value in GRADE_KEYS
}

function isGradedCategory(value: string): value is GradedCategory {
  return (VOLLEYBALL as readonly string[]).includes(value)
}

/** "Attack" for name_key 'skill.attack'; the code when the key is unknown. */
export function skillLabel(t: TFunction, skill: Pick<TaxonomySkill, 'name_key' | 'code'> | null | undefined, code = ''): string {
  if (!skill) return code
  const name = skill.name_key.replace(/^skill\./, '')
  return isSkillName(name) ? t(`skill.${name}`) : skill.code
}

/** What a grade means for a skill, e.g. attack # = "Kill", a shot + = "Made". The symbol when unknown. */
export function gradeLabel(t: TFunction, skill: Pick<TaxonomySkill, 'category'> | null | undefined, grade: string | null | undefined): string {
  if (!grade) return ''
  if (skill?.category === 'shot' && (grade === '+' || grade === '-')) return t(`grade.shot.${GRADE_KEYS[grade]}`)
  if (!skill || !isGrade(grade) || !isGradedCategory(skill.category)) return grade
  return t(`grade.${skill.category}.${GRADE_KEYS[grade]}`)
}

/** "Set 2" (volleyball); "Q2", "OT", "OT2" (basketball: four quarters, then overtimes). */
export function periodLabel(t: TFunction, sport: string, no: number): string {
  if (sport !== 'basketball') return t('tagging.setNo', { no })
  if (no <= 4) return t('tagging.basketball.quarterNo', { no })
  return no === 5 ? t('tagging.basketball.overtime') : t('tagging.basketball.overtimeNo', { n: no - 4 })
}

/** "Timeout", "Set break"; basketball's breaks are between quarters. */
export function segmentKindLabel(t: TFunction, kind: SegmentKind, sport: string): string {
  if (sport === 'basketball' && kind === 'set_break') return t('tagging.basketball.quarterBreak')
  return t(`tagging.segment.kind.${kind}`)
}

/**
 * A segment's kind in words; a timeout from the official scoresheet names the team that called
 * it ("Timeout (Volero)"; docs/PLAN.md §10.3a).
 */
export function segmentLabel(
  t: TFunction,
  segment: Pick<Segment, 'kind' | 'called_by'>,
  sport: string,
  names?: SideNames | null,
): string {
  const kind = segmentKindLabel(t, segment.kind, sport)
  return segment.kind === 'timeout' && segment.called_by ? `${kind} (${sideLabel(t, segment.called_by, names)})` : kind
}

/** Why the automatic scoring flags a rally, in words (needs the video strings, src/i18n/pages.ts). */
export function scoreNoteText(t: TFunction, note: ScoreNote | null | undefined): string {
  return note ? t(`tagging.scoring.notes.${note}`) : t('tagging.scoring.notes.unsure')
}

/** The score as written ("14:12"). */
function scoreText(score: readonly number[] | null | undefined): string {
  return score && score.length === 2 ? `${score[0]}:${score[1]}` : '–'
}

/**
 * Why the joint decode needs the coach for a rally, in words, with what the sources say
 * (docs/PLAN.md §10.1): "The sheet’s box here is unclear (17 or 11).", "A timeout (Volero) at
 * 14:12 doesn’t fit here: the score here is 13:12.".
 */
export function reasonText(t: TFunction, reason: ScoreReason, names?: SideNames | null): string {
  const team = (side: Side | 'none' | null | undefined) => (side === 'us' || side === 'them' ? sideLabel(t, side, names) : '–')
  switch (reason.code) {
    case 'log':
    case 'board':
      return t(`tagging.scoring.reason.${reason.code}`, { team: team(reason.team) })
    case 'log_score':
      return t('tagging.scoring.reason.log_score', { score: scoreText(reason.score) })
    case 'sheet_log': {
      // The typed sheet and the board log disagree on the winner, or on the score after it; the
      // video takes a side or cannot tell.
      const sameWinner = reason.log === reason.sheet && reason.log_score && reason.sheet_score
      const what = sameWinner
        ? t('tagging.scoring.reason.sheet_log_score', { log: scoreText(reason.log_score), sheet: scoreText(reason.sheet_score) })
        : t('tagging.scoring.reason.sheet_log', { log: team(reason.log), sheet: team(reason.sheet) })
      if (reason.video === undefined) return what
      return `${what} ${t(`tagging.scoring.reason.sheet_video.${reason.video ?? 'none'}`)}`
    }
    case 'sheet':
      return reason.team
        ? t('tagging.scoring.reason.sheet_departs', { team: team(reason.team), score: scoreText(reason.score) })
        : t('tagging.scoring.reason.sheet')
    case 'video':
      return t(`tagging.scoring.reason.video.${reason.vs ?? 'score'}`)
    case 'sheet_box': {
      const values = reason.values ?? []
      const or = t('tagging.scoring.reason.or')
      return values.length > 1
        ? t('tagging.scoring.reason.sheet_box', { values: values.join(` ${or} `) })
        : t('tagging.scoring.reason.sheet_box_one', { values: values[0] ?? '?' })
    }
    case 'note': {
      const what = noteWhat(t, reason, names)
      const why = t(`tagging.scoring.reason.noteIssue.${reason.issue ?? 'off_path'}`, { here: scoreText(reason.here) })
      return t('tagging.scoring.reason.note', { what, why })
    }
    case 'missing_before':
    case 'missing_after':
    case 'extra':
    case 'log_none':
    case 'coach':
    case 'serve':
    case 'sides':
    case 'no_set':
    case 'unsure':
      return t(`tagging.scoring.reason.${reason.code}`)
  }
}

/** A sheet's timeout, substitution or card in words ("Timeout (Volero) at 14:12"). */
function noteWhat(t: TFunction, reason: ScoreReason, names?: SideNames | null): string {
  const who = sideLabel(t, reason.side ?? null, names)
  const other = reason.side === 'us' ? 'them' : 'us'
  const score = scoreText(reason.score)
  switch (reason.kind) {
    case 'sub':
    case 'sub_back':
      return t(`tagging.scoring.reason.what.${reason.kind}`, { team: who, score })
    case 'sanction': {
      const kind = (['W', 'DW', 'P', 'DP', 'E', 'D'] as const).find((k) => k === reason.sanction) ?? 'W'
      return t(`sheetCourt.card.${kind}`, { team: who, to: sideLabel(t, other, names), score })
    }
    default:
      return t('tagging.scoring.reason.what.timeout', { team: who, score })
  }
}

/** The first reason a rally needs the coach, in words; else the flag's. */
export function firstReasonText(
  t: TFunction,
  rally: { score_reasons?: ScoreReason[] | null; score_note?: ScoreNote | null },
  names?: SideNames | null,
): string {
  const first = rally.score_reasons?.[0]
  return first ? reasonText(t, first, names) : scoreNoteText(t, rally.score_note)
}

/** "KSCW H3", "Volero" with the video's names (src/lib/sides.ts); else "Us", "Them". */
export function sideLabel(t: TFunction, side: Side | null | undefined, names?: SideNames | null): string {
  if (side === 'us') return names?.us ?? t('tagging.us')
  if (side === 'them') return names?.them ?? t('tagging.them')
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

/** "#7 Luca Brunner", "#7" (no name known), "Volero #12", "Volero" ("Them #12" without names). */
export function playerLabel(
  t: TFunction,
  side: Side,
  number: number | null,
  roster: readonly RosterEntry[],
  memberId?: number | null,
  names?: SideNames | null,
): string {
  const them = names?.them ?? t('tagging.them')
  if (side === 'them') return number === null ? them : t('tagging.themNumber', { number, team: them })
  if (number === null) return names?.us ?? t('tagging.us')
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

interface PieceText {
  caption: string | null
  code: string | null
  grade: string | null
  side: Side | null
  number: number | null
  player_name: string | null
  video_title: string | null
}

/**
 * What a reel piece shows: its caption, else the tag it came from ("#7 Luca Brunner · Attack ·
 * # Kill"), else the video's title.
 */
export function pieceLabel(t: TFunction, piece: PieceText, skill: Pick<TaxonomySkill, 'name_key' | 'code' | 'category'> | null): string {
  if (piece.caption) return piece.caption
  if (!piece.code) return piece.video_title ?? ''
  const side = piece.side ?? 'us'
  const player =
    side === 'them'
      ? piece.number === null
        ? t('tagging.them')
        : t('tagging.themNumber', { number: piece.number, team: t('tagging.them') })
      : piece.number === null
        ? t('tagging.us')
        : piece.player_name
          ? `#${piece.number} ${piece.player_name}`
          : `#${piece.number}`
  const meaning = skill && piece.grade ? gradeLabel(t, skill, piece.grade) : ''
  const grade = piece.grade && meaning !== piece.grade ? `${piece.grade} ${meaning}`.trim() : (piece.grade ?? '')
  return [player, skillLabel(t, skill, piece.code), grade].filter(Boolean).join(' · ')
}

/**
 * The line under a piece's label: the player when the label doesn't name them (a captioned
 * piece) and the score after the rally, e.g. "Alex Muster · 1:0". Empty when neither is known.
 */
export function pieceFacts(piece: PieceText & { score_us: number | null; score_them: number | null }): string {
  const shown = !piece.caption && piece.code !== null && piece.side !== 'them' && piece.number !== null
  const score = piece.score_us !== null && piece.score_them !== null ? `${piece.score_us}:${piece.score_them}` : null
  return [shown ? null : piece.player_name, score].filter(Boolean).join(' · ')
}
