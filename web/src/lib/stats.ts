// Statistics (docs/PLAN.md §9.7): the box score of a match or a season, computed by the server
// (backend/wieplay/core/stats.py), and which plays each number is made of.
//
// A box-score number's plays are the tags matching its row's `filter`, its column's `codes`
// and `grades`, and (per set) the set. On the video page they are played right there, from
// the tagging data the page already has; on the team page a number opens the smart reel
// builder with the same query. The matching here mirrors the server's counting, so a "5"
// always plays five moments.

import { useQuery } from '@tanstack/react-query'
import type { TFunction } from 'i18next'
import { apiRequest } from './api'
import { POST_ROLL_MS, PRE_ROLL_MS } from './condensed'
import { keys } from './queryClient'
import { periodAt, ralliesOf } from './rallies'
import type { SideNames } from './sides'
import type { ActionGate, Period, Segment, Side, Tag, Taxonomy } from './types'

export type StatFormat = 'count' | 'pct' | 'rating'
export type StatValue = number | null

export interface StatColumn {
  key: string
  /** serve | reception | attack | block | other; null: a column on its own (points). */
  group: string | null
  format: StatFormat
  codes: string[]
  /** null: any grade. A null inside the list stands for an ungraded tag. */
  grades: (string | null)[] | null
}

/** Which tags are a row's: a key that is present must match (null: must be empty). */
export interface RowFilter {
  side: Side
  number?: number | null
  member_id?: number | null
}

export type RowKind = 'player' | 'no_number' | 'other' | 'team' | 'opponent'

export interface StatRow {
  kind: RowKind
  side: Side
  number: number | null
  member_id: number | null
  name: string | null
  filter: RowFilter
  values: Record<string, StatValue>
  /** Season: matches with a tag of this row. */
  matches: number | null
}

export interface TeamLine {
  rallies: number
  won: number
  lost: number
  side_out_won: number
  side_out_of: number
  side_out_pct: number | null
  break_point_won: number
  break_point_of: number
  break_point_pct: number | null
}

/** A rally the rotation walk put in a rotation, with the server and winner it used. */
export interface RotationRally {
  id: string
  serving: Side
  won_by: Side
}

/** Our points per rotation (our setter's zone, 1..6), walked from each set's starting rotation. */
export interface RotationLine extends TeamLine {
  rotation: number
  // Since step 5a; absent from older servers.
  /** Our tags in its rallies: the numbers of `rotation_columns` (serve, reception, attack). */
  values?: Record<string, StatValue>
  /** Its rallies (the season: none, its numbers are not played). */
  plays?: RotationRally[]
  /** Our six in zones I..VI in this rotation (the most common when the sets differ). */
  lineup?: (number | null)[] | null
  lineup_varies?: boolean
}

/** Where an attack after one of our sets started: front row 4 3 2, back row (pipe). */
export const SETTER_ZONES = ['left', 'middle', 'right', 'back'] as const
export type SetterZone = (typeof SETTER_ZONES)[number]
/** The first contact before a set: our reception's quality, or transition. */
export const QUALITIES = ['perfect', 'good', 'poor', 'transition', 'unknown'] as const
export type Quality = (typeof QUALITIES)[number]
export type SetOutcome = 'kill' | 'error' | 'blocked' | 'play'

/** One option of the setter's distribution; its plays are the sets matching `filter`. */
export interface SetterLine {
  key: string
  filter: SetFilter
  number: number | null
  member_id: number | null
  name: string | null
  sets: number
  attacks: number
  /** Of the attacks after a set in this scope. */
  share: number | null
  /** Attacks per zone (left, middle, right, back, unknown). */
  zones: Partial<Record<SetterZone | 'unknown', number>>
  kills: number
  errors: number
  blocked: number
  kill_pct: number | null
  hit_pct: number | null
}

/** One of our sets (a match only). */
export interface SetPlay {
  set_id: string
  attack_id: string | null
  rally_id: string | null
  set_no: number | null
  t_ms: number
  attacker: number | null
  attacker_member: number | null
  setter: number | null
  setter_member: number | null
  zone: SetterZone | null
  rotation: number | null
  quality: Quality
  outcome: SetOutcome | null
  /** Followed by our attack. */
  attacked: boolean
}

/** Which sets a setter number is made of: every key present must match. */
export type SetFilter = Partial<Pick<SetPlay, 'attacker' | 'attacker_member' | 'setter' | 'setter_member' | 'zone' | 'rotation' | 'quality' | 'outcome' | 'attacked'>>

export interface SetterStats {
  sets: number
  attacks: number
  by_attacker: SetterLine[]
  by_zone: SetterLine[]
  by_rotation: SetterLine[]
  by_reception: SetterLine[]
  by_setter: SetterLine[]
  /** The match only: one per set tag, in time order. */
  plays: SetPlay[] | null
}

export interface BoxScore {
  players: StatRow[]
  team: StatRow
  opponent: StatRow
  summary: TeamLine
  /**
   * One line per rotation 1..6; null when no set has a starting rotation (or, for a set,
   * when it has none). Absent from servers before the automatic scoring.
   */
  rotations?: RotationLine[] | null
  /** Our sets and the attacks after them (absent from older servers). */
  setter?: SetterStats | null
}

export interface SetBox extends BoxScore {
  no: number
  score_us: number | null
  score_them: number | null
  /** Our setter's zone and our six in zones I..VI at the set's first serve. */
  start_rotation?: number | null
  start_lineup?: (number | null)[] | null
}

/** Rallies behind a team number: a present key must match; won_by 'any' means decided. */
export interface RallyFilter {
  serving?: Side
  won_by?: Side | 'any'
}

export interface VideoStats {
  video_id: string
  team_id: number
  title: string
  recorded_on: string
  tags: number
  rallies: number
  columns: StatColumn[]
  team_filters: Record<string, RallyFilter>
  match: BoxScore
  sets: SetBox[]
  /** The numbers of our tags shown per rotation (absent from older servers). */
  rotation_columns?: StatColumn[]
  /** Suggested actions nobody has answered yet: not counted (absent from older servers). */
  suggested?: number
  /** Whether WiePlay suggests actions yet. */
  actions_gate?: ActionGate | null
}

export interface MatchLine {
  video_id: string
  title: string
  recorded_on: string
  result_us: number | null
  result_them: number | null
  tagged: boolean
  summary: TeamLine
}

export interface TeamStats {
  team_id: number
  team_name: string
  season: string
  lineage_id: string
  /** Ready matches of the team row, and how many of them have tags. */
  videos: number
  tagged: number
  columns: StatColumn[]
  season_box: BoxScore
  matches: MatchLine[]
  rotation_columns?: StatColumn[]
  /** Suggested actions nobody has answered yet, over the matches: not counted. */
  suggested?: number
  actions_gate?: ActionGate | null
}

export type CsvLanguage = 'en' | 'de'
/** Which table a CSV holds: the box score, the rotations or the setter analysis. */
export type CsvPart = 'box' | 'rotations' | 'setter'

export const statsApi = {
  video: (id: string, signal?: AbortSignal) => apiRequest<VideoStats>(`/api/videos/${id}/stats`, { signal }),
  team: (id: number, signal?: AbortSignal) => apiRequest<TeamStats>(`/api/teams/${id}/stats`, { signal }),
}

const partQuery = (part: CsvPart) => (part === 'box' ? '' : `&part=${part}`)

/** The CSV download is a plain link (the session cookie goes along). */
export function videoCsvUrl(id: string, lang: CsvLanguage, part: CsvPart = 'box'): string {
  return `/api/videos/${id}/stats.csv?lang=${lang}${partQuery(part)}`
}

export function teamCsvUrl(id: number, lang: CsvLanguage, part: CsvPart = 'box'): string {
  return `/api/teams/${id}/stats.csv?lang=${lang}${partQuery(part)}`
}

/** Stats follow the tagging: always asked again when the page opens. */
export function useVideoStats(id: string, enabled: boolean) {
  return useQuery({ queryKey: keys.videoStats(id), queryFn: ({ signal }) => statsApi.video(id, signal), enabled, staleTime: 0 })
}

export function useTeamStats(id: number, enabled: boolean) {
  return useQuery({ queryKey: keys.teamStats(id), queryFn: ({ signal }) => statsApi.team(id, signal), enabled, staleTime: 0 })
}

// Formats (Swiss: 33.3%, 2.15).

const PERCENT = new Intl.NumberFormat('de-CH', { style: 'percent', minimumFractionDigits: 1, maximumFractionDigits: 1 })
const RATING = new Intl.NumberFormat('de-CH', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const COUNT = new Intl.NumberFormat('de-CH')
export const EMPTY = '–'

export function formatStat(value: StatValue | undefined, format: StatFormat): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return EMPTY
  if (format === 'pct') return PERCENT.format(value)
  if (format === 'rating') return RATING.format(value)
  return COUNT.format(value)
}

/** Columns in their groups, in order, for the two header rows. */
export function columnGroups(columns: readonly StatColumn[]): { group: string | null; columns: StatColumn[] }[] {
  const out: { group: string | null; columns: StatColumn[] }[] = []
  for (const column of columns) {
    const last = out.at(-1)
    if (last && last.group === column.group && column.group !== null) last.columns.push(column)
    else out.push({ group: column.group, columns: [column] })
  }
  return out
}

/** "#7 Alex Muster", "#11", "Team", "Opponent" (needs the stats strings, src/i18n/pages.ts). */
/** "#7 Alex Muster"; the team's and the opponent's rows by the teams' names on a match ("KSCW H3", "Volero"). */
export function rowLabel(t: TFunction, row: Pick<StatRow, 'kind' | 'number' | 'name'>, names?: SideNames | null): string {
  if (names && row.kind === 'team') return names.us
  if (names && row.kind === 'opponent') return names.them
  if (row.kind !== 'player') return t(`stats.rows.${row.kind}`)
  return [row.number !== null ? `#${row.number}` : null, row.name].filter(Boolean).join(' ')
}

/** A stable key for a row of a box score. */
export function rowKey(row: Pick<StatRow, 'kind' | 'number' | 'member_id'>): string {
  return `${row.kind}:${row.member_id ?? row.number ?? ''}`
}

// Which plays a number is made of.

export function rowHas(tag: Pick<Tag, 'side' | 'number' | 'member_id'>, filter: RowFilter): boolean {
  if (tag.side !== filter.side) return false
  if ('number' in filter && tag.number !== (filter.number ?? null)) return false
  const member = tag.side === 'us' ? tag.member_id : null
  if ('member_id' in filter && member !== (filter.member_id ?? null)) return false
  return true
}

export function columnHas(tag: Pick<Tag, 'code' | 'attributes'>, column: Pick<StatColumn, 'codes' | 'grades'>): boolean {
  if (!column.codes.includes(tag.code)) return false
  return column.grades === null || column.grades.includes(tag.attributes.grade ?? null)
}

/** A tag up to this long before a rally belongs to it (the serve toss). Same as the server. */
const RALLY_LEAD_MS = PRE_ROLL_MS

/**
 * Each tag's set (period id), as the server counts it: the set of its rally (the linked one,
 * or one starting up to 2 s after an unlinked tag), else the set running at its time.
 */
export function tagSets(tags: readonly Tag[], segments: readonly Segment[], periods: readonly Period[]): Map<string, string | null> {
  const rallies = ralliesOf(segments)
  const byId = new Map(rallies.map((r) => [r.id, r]))
  const out = new Map<string, string | null>()
  for (const tag of tags) {
    const rally =
      tag.segment_id !== null
        ? byId.get(tag.segment_id)
        : rallies.find((r) => r.start_ms - RALLY_LEAD_MS <= tag.t_ms && tag.t_ms <= r.end_ms)
    out.set(tag.id, rally?.period_id ?? periodAt(periods, tag.t_ms)?.id ?? null)
  }
  return out
}

export interface TagSource {
  events: readonly Tag[]
  segments: readonly Segment[]
  periods: readonly Period[]
}

/** The tags behind one number of the box score, in time order. `setNo` null: the whole match. */
export function statTags(source: TagSource, filter: RowFilter, column: Pick<StatColumn, 'codes' | 'grades'>, setNo: number | null): Tag[] {
  const sets = setNo === null ? null : tagSets(source.events, source.segments, source.periods)
  const numbers = new Map(source.periods.map((p) => [p.id, p.no]))
  return source.events
    .filter((tag) => {
      if (!rowHas(tag, filter) || !columnHas(tag, column)) return false
      if (sets === null) return true
      const set = sets.get(tag.id)
      return set != null && numbers.get(set) === setNo
    })
    .sort((a, b) => a.t_ms - b.t_ms)
}

/** The rallies behind a team number, in time order. `periodId` null: the whole match. */
export function statRallies(segments: readonly Segment[], filter: RallyFilter, periodId: string | null): Segment[] {
  return ralliesOf(segments).filter((r) => {
    if (periodId !== null && r.period_id !== periodId) return false
    if (filter.serving !== undefined && r.serving !== filter.serving) return false
    if (filter.won_by === 'any') return r.won_by !== null
    return filter.won_by === undefined || r.won_by === filter.won_by
  })
}

/** Each tag's rally (id), as the server links them: its own, or one starting up to 2 s after it. */
export function tagRallies(tags: readonly Tag[], segments: readonly Segment[]): Map<string, string | null> {
  const rallies = ralliesOf(segments)
  const known = new Set(rallies.map((r) => r.id))
  const out = new Map<string, string | null>()
  for (const tag of tags) {
    const rally =
      tag.segment_id !== null
        ? known.has(tag.segment_id)
          ? tag.segment_id
          : null
        : (rallies.find((r) => r.start_ms - RALLY_LEAD_MS <= tag.t_ms && tag.t_ms <= r.end_ms)?.id ?? null)
    out.set(tag.id, rally)
  }
  return out
}

/** The rallies of a rotation behind a team number, in time order. */
export function rotationRallies(segments: readonly Segment[], plays: readonly RotationRally[], filter: RallyFilter): Segment[] {
  const chosen = new Set(
    plays
      .filter((p) => (filter.serving === undefined || p.serving === filter.serving) && (filter.won_by === undefined || filter.won_by === 'any' || p.won_by === filter.won_by))
      .map((p) => p.id),
  )
  return ralliesOf(segments).filter((r) => chosen.has(r.id))
}

/** Our tags of a column in a rotation's rallies, in time order. */
export function rotationTags(source: Pick<TagSource, 'events' | 'segments'>, plays: readonly RotationRally[], column: Pick<StatColumn, 'codes' | 'grades'>): Tag[] {
  const ids = new Set(plays.map((p) => p.id))
  const rallies = tagRallies(source.events, source.segments)
  return source.events
    .filter((tag) => tag.side === 'us' && columnHas(tag, column) && ids.has(rallies.get(tag.id) ?? ''))
    .sort((a, b) => a.t_ms - b.t_ms)
}

/** The sets behind a setter number: those matching every key of the filter (and the set). */
export function setterPlays(plays: readonly SetPlay[], filter: SetFilter, setNo: number | null): SetPlay[] {
  const keys = Object.keys(filter) as (keyof SetFilter)[]
  return plays.filter((play) => (setNo === null || play.set_no === setNo) && keys.every((key) => play[key] === filter[key]))
}

/** A set's window: from the set (its skill's pre-roll) to the end of the attack after it. */
export function setWindow(set: Tag, attack: Tag | null, taxonomy: Taxonomy | null, duration_ms: number | null): { start_ms: number; end_ms: number } {
  const first = tagWindow(set, taxonomy, duration_ms)
  if (!attack) return first
  return { start_ms: first.start_ms, end_ms: Math.max(first.end_ms, tagWindow(attack, taxonomy, duration_ms).end_ms) }
}

/** What one play shows: its window in the video and a label. */
export interface StatPlay {
  key: string
  label: string
  start_ms: number
  end_ms: number
}

/** A tag's clip window: its own, else its skill's (e.g. attack 3 s before, 2 s after). */
export function tagWindow(tag: Tag, taxonomy: Taxonomy | null, duration_ms: number | null): { start_ms: number; end_ms: number } {
  const skill = taxonomy?.skills.find((s) => s.code === tag.code)
  const pre = tag.clip_pre_ms ?? skill?.pre_ms ?? 3000
  const post = tag.clip_post_ms ?? skill?.post_ms ?? 2000
  const end = tag.t_ms + post
  return { start_ms: Math.max(0, tag.t_ms - pre), end_ms: duration_ms === null ? end : Math.min(end, duration_ms) }
}

/** A rally with the same pre- and post-roll as condensed playback. */
export function rallyWindow(rally: Segment, duration_ms: number | null): { start_ms: number; end_ms: number } {
  const end = rally.end_ms + POST_ROLL_MS
  return { start_ms: Math.max(0, rally.start_ms - PRE_ROLL_MS), end_ms: duration_ms === null ? end : Math.min(end, duration_ms) }
}

/**
 * The smart reel builder with a season number's query, or null when a reel can't express the
 * row (a shirt number without a member).
 */
export function reelLink(team: { id: number; season: string }, filter: RowFilter, column: Pick<StatColumn, 'codes' | 'grades'>): string | null {
  const params = new URLSearchParams({ team: String(team.id), season: team.season })
  if ('number' in filter) return null
  if ('member_id' in filter) {
    if (filter.member_id == null) return null
    params.set('member', String(filter.member_id))
  } else {
    params.set('side', filter.side)
  }
  params.set('codes', column.codes.join(','))
  if (column.grades !== null) params.set('grades', column.grades.map((g) => g ?? NO_GRADE).join(','))
  return `/reels/new?${params.toString()}`
}

/** How the reel builder's address names an ungraded tag. */
export const NO_GRADE = 'none'
