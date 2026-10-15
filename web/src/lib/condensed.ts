// Skip dead time (docs/PLAN.md §9.5): play only the rallies, with a little before and after.
// Pure scheduling plus the filters that narrow the rally list; the hook that drives the
// <video> element is in src/lib/useCondensedPlayback.ts.

import { periodAt } from './rallies'
import type { Period, Segment, Side, Tag } from './types'

export const PRE_ROLL_MS = 2000
export const POST_ROLL_MS = 1500
/** A position this close before a window's start counts as inside (seeking isn't exact). */
const EPSILON_MS = 250

export interface PlayWindow {
  start_ms: number
  end_ms: number
}

/** The stretches to play: each rally with pre- and post-roll, clamped, overlaps merged. */
export function playWindows(
  rallies: readonly Pick<Segment, 'start_ms' | 'end_ms'>[],
  { pre = PRE_ROLL_MS, post = POST_ROLL_MS, duration_ms = null }: { pre?: number; post?: number; duration_ms?: number | null } = {},
): PlayWindow[] {
  const end = duration_ms ?? Infinity
  const windows = rallies
    .map((r) => ({ start_ms: Math.max(0, r.start_ms - pre), end_ms: Math.min(end, r.end_ms + post) }))
    .filter((w) => w.end_ms > w.start_ms)
    .sort((a, b) => a.start_ms - b.start_ms)
  const merged: PlayWindow[] = []
  for (const w of windows) {
    const last = merged.at(-1)
    if (last && w.start_ms <= last.end_ms) last.end_ms = Math.max(last.end_ms, w.end_ms)
    else merged.push({ ...w })
  }
  return merged
}

export type PlaybackStep = { kind: 'play' } | { kind: 'seek'; to_ms: number } | { kind: 'end' }

/** What condensed playback does at this position: keep playing, jump to the next rally, or stop. */
export function nextPlayback(t_ms: number, windows: readonly PlayWindow[]): PlaybackStep {
  for (const w of windows) {
    if (t_ms < w.start_ms - EPSILON_MS) return { kind: 'seek', to_ms: w.start_ms }
    if (t_ms < w.end_ms) return { kind: 'play' }
  }
  return { kind: 'end' }
}

/** Total length of the windows, for "34 min instead of 1 h 32 min". */
export function playLength(windows: readonly PlayWindow[]): number {
  return windows.reduce((sum, w) => sum + (w.end_ms - w.start_ms), 0)
}

/** The rally playing at a moment (with its pre/post roll), else null. */
export function rallyAt<T extends Pick<Segment, 'start_ms' | 'end_ms'>>(
  rallies: readonly T[],
  t_ms: number,
  { pre = PRE_ROLL_MS, post = POST_ROLL_MS } = {},
): T | null {
  let found: T | null = null
  for (const r of rallies) if (r.start_ms - pre <= t_ms && t_ms < r.end_ms + post) found = r
  return found
}

export interface TagFilter {
  /** `us:7`, `them:12`. */
  player: string | null
  skill: string | null
  grade: string | null
  /** Period id. */
  set: string | null
}

export const NO_FILTER: TagFilter = { player: null, skill: null, grade: null, set: null }

export function playerKey(side: Side, number: number | null): string {
  return `${side}:${number ?? ''}`
}

export function hasTagFilter(filter: TagFilter): boolean {
  return filter.player !== null || filter.skill !== null || filter.grade !== null
}

export function isFiltered(filter: TagFilter): boolean {
  return hasTagFilter(filter) || filter.set !== null
}

/** Player, skill and grade (the set is checked by the caller, which knows the periods). */
export function matchesTag(tag: Pick<Tag, 'side' | 'number' | 'code' | 'attributes'>, filter: TagFilter): boolean {
  if (filter.player !== null && playerKey(tag.side, tag.number) !== filter.player) return false
  if (filter.skill !== null && tag.code !== filter.skill) return false
  if (filter.grade !== null && tag.attributes.grade !== filter.grade) return false
  return true
}

/** A tag belongs to a rally when it says so, or when it lies within it (a serve up to 2 s before). */
export function tagInRally(tag: Pick<Tag, 't_ms' | 'segment_id'>, rally: Pick<Segment, 'id' | 'start_ms' | 'end_ms'>): boolean {
  if (tag.segment_id === rally.id) return true
  return tag.segment_id === null && rally.start_ms - PRE_ROLL_MS <= tag.t_ms && tag.t_ms <= rally.end_ms
}

/** The set of a segment: the server's period_id, else by time. */
export function setOf(segment: Pick<Segment, 'period_id' | 'start_ms'>, periods: readonly Period[]): string | null {
  return segment.period_id ?? periodAt(periods, segment.start_ms)?.id ?? null
}

/** Rallies of the chosen set that contain at least one tag matching player, skill and grade. */
export function filterRallies(
  rallies: readonly Segment[],
  tags: readonly Tag[],
  periods: readonly Period[],
  filter: TagFilter,
): Segment[] {
  const inSet = filter.set === null ? [...rallies] : rallies.filter((r) => setOf(r, periods) === filter.set)
  if (!hasTagFilter(filter)) return inSet
  const matching = tags.filter((t) => matchesTag(t, filter))
  return inSet.filter((r) => matching.some((t) => tagInRally(t, r)))
}

/** Tags matching all four filters. */
export function filterTags(tags: readonly Tag[], periods: readonly Period[], filter: TagFilter): Tag[] {
  return tags.filter(
    (t) => matchesTag(t, filter) && (filter.set === null || periodAt(periods, t.t_ms)?.id === filter.set),
  )
}

/** The last tag at or before a moment (tags sorted by time). */
export function lastAtOrBefore<T extends { t_ms: number }>(tags: readonly T[], t_ms: number): T | null {
  let lo = 0
  let hi = tags.length - 1
  let found: T | null = null
  while (lo <= hi) {
    const mid = (lo + hi) >> 1
    const tag = tags[mid] as T
    if (tag.t_ms <= t_ms) {
      found = tag
      lo = mid + 1
    } else {
      hi = mid - 1
    }
  }
  return found
}
