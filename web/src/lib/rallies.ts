// Rallies, sets and the score (docs/PLAN.md §6.6, §9.4).
//
// The server computes who served, which set a rally belongs to and the running score
// (backend/nomal/core/tagging.py: rescore). While changes are still in the outbox, the
// workspace shows the same computation done locally, marked as provisional; after each sync
// the server's values replace it.
//
// Basketball has no rallies: its score is a running total of the made shots across the
// quarters (backend: _rescore_basketball), done here the same way.

import { rallyWinner, skillByCode } from './grammar'
import type { Period, Segment, Side, Tag, Taxonomy } from './types'

/** A serve tag may sit slightly before the rally marker (the toss). Same as the server. */
export const SERVE_SLACK_MS = 2000
/** A serve tag opens the rally this long before the serve. */
export const RALLY_LEAD_MS = 2000
/** A rally-ending tag proposes the rally's end this long after the tag. */
export const RALLY_TAIL_MS = 1500

export function byStart<T extends { start_ms: number }>(a: T, b: T): number {
  return a.start_ms - b.start_ms
}

export function sortedPeriods(periods: readonly Period[]): Period[] {
  return [...periods].sort((a, b) => a.start_ms - b.start_ms || a.no - b.no)
}

/** The set a moment belongs to: the last one started at or before it and not yet ended. */
export function periodAt(periods: readonly Period[], t_ms: number): Period | null {
  let found: Period | null = null
  for (const period of sortedPeriods(periods)) {
    if (period.start_ms <= t_ms && (period.end_ms === null || t_ms < period.end_ms)) found = period
  }
  return found
}

export function ralliesOf(segments: readonly Segment[]): Segment[] {
  return segments.filter((s) => s.kind === 'rally').sort(byStart)
}

/** The serve codes of a taxonomy (volleyball: S). */
export function serveCodes(taxonomy: Taxonomy | null): Set<string> {
  return new Set((taxonomy?.skills ?? []).filter((s) => s.starts_rally).map((s) => s.code))
}

/**
 * The server's rescore, done locally: per set from its starting score; who served is a serve
 * tag in the rally, else the previous rally's winner, else the set's first server.
 * Returns new arrays; the inputs are not changed.
 */
export function rescore(
  periods: readonly Period[],
  segments: readonly Segment[],
  tags: readonly Tag[],
  serves: ReadonlySet<string>,
): { periods: Period[]; segments: Segment[] } {
  const ordered = sortedPeriods(periods)
  const out = new Map(segments.map((s) => [s.id, { ...s }]))
  const rallies = ralliesOf([...out.values()])
  const serveTags = tags.filter((t) => serves.has(t.code)).sort((a, b) => a.t_ms - b.t_ms)

  const byPeriod = new Map<string | null, Segment[]>()
  for (const rally of rallies) {
    const period = periodAt(ordered, rally.start_ms)
    rally.period_id = period?.id ?? null
    const list = byPeriod.get(rally.period_id) ?? []
    list.push(rally)
    byPeriod.set(rally.period_id, list)
  }
  for (const rally of byPeriod.get(null) ?? []) {
    rally.score_us_after = rally.score_them_after = null
    rally.score_source = null
  }

  const servedBy = (rally: Segment): Side | null =>
    serveTags.find((t) => rally.start_ms - SERVE_SLACK_MS <= t.t_ms && t.t_ms <= rally.end_ms)?.side ?? null

  const finished = ordered.map((period) => {
    let us = period.start_us
    let them = period.start_them
    let serving = period.first_serve
    const played = byPeriod.get(period.id) ?? []
    for (const rally of played) {
      const derived = servedBy(rally) ?? serving
      if (derived !== null) rally.serving = derived
      if (rally.won_by === 'us') us += 1
      else if (rally.won_by === 'them') them += 1
      if (rally.won_by === null) {
        rally.score_us_after = rally.score_them_after = null
        rally.score_source = null
      } else {
        rally.score_us_after = us
        rally.score_them_after = them
        rally.score_source = 'tags'
        serving = rally.won_by
      }
    }
    return played.length > 0
      ? { ...period, final_us: us, final_them: them }
      : { ...period, final_us: null, final_them: null }
  })
  return { periods: finished, segments: [...out.values()].sort(byStart) }
}

/** What a tag adds to its side's score: a made shot's points (basketball), else 0. Same as the server. */
export function shotPoints(taxonomy: Taxonomy, code: string, grade: string | null | undefined): number {
  return grade === '+' ? (skillByCode(taxonomy, code)?.points ?? 0) : 0
}

/**
 * Basketball's rescore, done locally: a running score across the quarters, seeded by the first
 * quarter's starting score (a recording may start mid-game); every made shot inside a quarter
 * adds its points, and a quarter's final is the running score at its end. Shots before the first
 * quarter, between quarters and after the last one count nothing. Returns new periods.
 */
export function rescoreShots(periods: readonly Period[], tags: readonly Tag[], taxonomy: Taxonomy): Period[] {
  const ordered = sortedPeriods(periods)
  const shots = [...tags].sort((a, b) => a.t_ms - b.t_ms)
  let us = ordered[0]?.start_us ?? 0
  let them = ordered[0]?.start_them ?? 0
  let at = 0
  return ordered.map((period, n) => {
    // A quarter left open ends where the next one starts.
    const end = period.end_ms ?? ordered[n + 1]?.start_ms ?? null
    for (; at < shots.length; at += 1) {
      const shot = shots[at] as Tag
      if (end !== null && shot.t_ms >= end) break
      const points = shotPoints(taxonomy, shot.code, shot.attributes.grade)
      if (shot.t_ms < period.start_ms || !points) continue
      if (shot.side === 'us') us += points
      else them += points
    }
    return { ...period, final_us: us, final_them: them }
  })
}

/** The server's rescore for the taxonomy's sport (rallies, or basketball's made shots). */
export function rescoreFor(
  taxonomy: Taxonomy | null,
  periods: readonly Period[],
  segments: readonly Segment[],
  tags: readonly Tag[],
): { periods: Period[]; segments: Segment[] } {
  if (taxonomy?.sport === 'basketball') {
    return { periods: rescoreShots(periods, tags, taxonomy), segments: [...segments].sort(byStart) }
  }
  return rescore(periods, segments, tags, serveCodes(taxonomy))
}

export interface ScoreState {
  period: Period | null
  us: number
  them: number
  /** Who serves next (or serves in the rally being played). */
  serving: Side | null
}

/**
 * The score of the set at a moment: from the last scored rally of that set that ended by
 * then, else the set's starting score. During a rally, that is the score before it.
 */
export function scoreBefore(
  t_ms: number,
  periods: readonly Period[],
  segments: readonly Segment[],
  exclude: string | null = null,
): ScoreState {
  const period = periodAt(periods, t_ms)
  if (!period) return { period: null, us: 0, them: 0, serving: null }
  let us = period.start_us
  let them = period.start_them
  let serving = period.first_serve
  for (const rally of ralliesOf(segments)) {
    if (rally.id === exclude || rally.end_ms > t_ms) continue
    if (periodAt(periods, rally.start_ms)?.id !== period.id) continue
    if (rally.won_by && rally.score_us_after !== null && rally.score_them_after !== null) {
      us = rally.score_us_after
      them = rally.score_them_after
      serving = rally.won_by
    } else if (rally.won_by) {
      // Not scored yet (e.g. sent but not rescored): count it.
      if (rally.won_by === 'us') us += 1
      else them += 1
      serving = rally.won_by
    }
  }
  return { period, us, them, serving }
}

/**
 * Basketball's score at a moment: the previous quarter's final (the server's, once synced) plus
 * the made shots of the quarter up to then. `period` is the quarter the score counts in, the last
 * one started by then (it may have ended: a break shows the score at its end). Before the first
 * quarter there is no score.
 */
export function shotScoreAt(t_ms: number, periods: readonly Period[], tags: readonly Tag[], taxonomy: Taxonomy): ScoreState {
  const ordered = sortedPeriods(periods)
  const n = ordered.findLastIndex((p) => p.start_ms <= t_ms)
  const period = ordered[n]
  if (!period) return { period: null, us: 0, them: 0, serving: null }
  let before = n > 0 ? (ordered[n - 1] as Period) : null
  if (before && (before.final_us === null || before.final_them === null)) {
    before = rescoreShots(ordered, tags, taxonomy)[n - 1] as Period
  }
  let us = before ? (before.final_us ?? 0) : period.start_us
  let them = before ? (before.final_them ?? 0) : period.start_them
  const end = Math.min(t_ms + 1, period.end_ms ?? Infinity)
  for (const tag of tags) {
    if (tag.t_ms < period.start_ms || tag.t_ms >= end) continue
    const points = shotPoints(taxonomy, tag.code, tag.attributes.grade)
    if (tag.side === 'us') us += points
    else them += points
  }
  return { period, us, them, serving: null }
}

/** The score after a rally won by `winner`, from the score before it. */
export function addPoint(state: { us: number; them: number }, winner: Side | null): { us: number; them: number } {
  if (winner === 'us') return { us: state.us + 1, them: state.them }
  if (winner === 'them') return { us: state.us, them: state.them + 1 }
  return { us: state.us, them: state.them }
}

/** The last tag in [from, to] whose grade ends the rally decides the proposed winner. */
export function winnerFromTags(tags: readonly Tag[], taxonomy: Taxonomy, from_ms: number, to_ms: number): Side | null {
  let winner: Side | null = null
  let at = -Infinity
  for (const tag of tags) {
    if (tag.t_ms < from_ms || tag.t_ms > to_ms || tag.t_ms < at) continue
    const won = rallyWinner(skillByCode(taxonomy, tag.code), tag.attributes.grade, tag.side)
    if (won) {
      winner = won
      at = tag.t_ms
    }
  }
  return winner
}

export interface OpenRally {
  id: string
  start_ms: number
}

export interface RallyProposal {
  rally: OpenRally
  end_ms: number
  winner: Side | null
  /** Why: 'key' (R pressed) or 'tag' (a rally-ending tag was saved). */
  reason: 'key' | 'tag'
}

/** R pressed while a rally is open: end it at the playhead, winner from the last ending tag. */
export function proposeByKey(
  rally: OpenRally,
  playhead_ms: number,
  tags: readonly Tag[],
  taxonomy: Taxonomy,
): RallyProposal {
  const end_ms = Math.max(playhead_ms, rally.start_ms + 1)
  return { rally, end_ms, winner: winnerFromTags(tags, taxonomy, rally.start_ms - SERVE_SLACK_MS, end_ms), reason: 'key' }
}

/** A saved tag that ends the rally: propose its end 1.5 s later. Null when it doesn't end it. */
export function proposeByTag(
  rally: OpenRally | null,
  tag: Pick<Tag, 't_ms' | 'code' | 'side' | 'attributes'>,
  taxonomy: Taxonomy,
): RallyProposal | null {
  if (!rally) return null
  const winner = rallyWinner(skillByCode(taxonomy, tag.code), tag.attributes.grade, tag.side)
  if (!winner) return null
  return { rally, end_ms: Math.max(tag.t_ms + RALLY_TAIL_MS, rally.start_ms + 1), winner, reason: 'tag' }
}

/** The rally a serve opens when none is open. */
export function rallyStartForServe(serve_ms: number): number {
  return Math.max(0, serve_ms - RALLY_LEAD_MS)
}
