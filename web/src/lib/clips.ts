// Clips are time ranges (docs/PLAN.md §6.8, §7.7): watching one seeks to its start, plays to its
// end and stops, or moves on to the next one in a list. Pure helpers; the hook that drives the
// <video> element is useRangePlayback.ts.

import type { Clip, Visibility } from './types'

/** The API's limit. */
export const MAX_CLIP_MS = 10 * 60_000
/** Length of a clip when only one end is marked and the playhead is on the wrong side of it. */
export const DEFAULT_CLIP_MS = 10_000
/** Seeking this far outside a playing range leaves it (the viewer went elsewhere). */
const LEAVE_SLACK_MS = 1000

export interface PlayRange {
  start_ms: number
  end_ms: number
}

/**
 * The clip made of the marked in and out points. A missing end comes from the playhead (or is
 * 10 s away from the marked one); points marked the wrong way round are swapped.
 */
export function clipRange(
  clipIn: number | null,
  clipOut: number | null,
  now: number,
  duration_ms: number | null = null,
): PlayRange | null {
  let start: number
  let end: number
  if (clipIn !== null && clipOut !== null) {
    start = Math.min(clipIn, clipOut)
    end = Math.max(clipIn, clipOut)
  } else if (clipIn !== null) {
    start = clipIn
    end = now > clipIn ? now : clipIn + DEFAULT_CLIP_MS
  } else if (clipOut !== null) {
    end = clipOut
    start = now < clipOut ? now : clipOut - DEFAULT_CLIP_MS
  } else {
    return null
  }
  start = Math.max(0, Math.round(start))
  end = Math.round(duration_ms === null ? end : Math.min(end, duration_ms))
  return end > start ? { start_ms: start, end_ms: end } : null
}

export type ClipProblem = 'order' | 'tooLong'

export function clipProblem(range: PlayRange): ClipProblem | null {
  if (range.end_ms <= range.start_ms) return 'order'
  if (range.end_ms - range.start_ms > MAX_CLIP_MS) return 'tooLong'
  return null
}

export type RangeStep = 'play' | 'end' | 'outside'

/** While a range plays: keep playing, it has ended, or the viewer seeked away from it. */
export function rangeStep(t_ms: number, range: PlayRange): RangeStep {
  if (t_ms < range.start_ms - LEAVE_SLACK_MS || t_ms > range.end_ms + LEAVE_SLACK_MS) return 'outside'
  if (t_ms >= range.end_ms) return 'end'
  return 'play'
}

/** Ranges played one after the other ("play all"); `index` is the one playing. */
export interface RangeQueue<T extends PlayRange = PlayRange> {
  ranges: readonly T[]
  index: number
}

/** The queue moved on to the next range, or null after the last one. */
export function nextInQueue<T extends PlayRange>(queue: RangeQueue<T>): RangeQueue<T> | null {
  return queue.index + 1 < queue.ranges.length ? { ranges: queue.ranges, index: queue.index + 1 } : null
}

/** Newest clips first (by when they were made), then by position in the video. */
export function newestFirst(clips: readonly Clip[]): Clip[] {
  return [...clips].sort((a, b) => b.created_at.localeCompare(a.created_at) || a.start_ms - b.start_ms)
}

/** Clips of one video in playing order. */
export function byStart<T extends PlayRange>(ranges: readonly T[]): T[] {
  return [...ranges].sort((a, b) => a.start_ms - b.start_ms || a.end_ms - b.end_ms)
}

/** Who a comment or highlight can name: a member of the team, with a name. */
export interface Person {
  member_id: number
  name: string
  number: number | null
}

/** A clip to save: a new one (id null) or an existing one of mine. */
export interface ClipDraft {
  id: string | null
  video_id: string
  start_ms: number
  end_ms: number
  title: string
  note: string | null
  event_id: string | null
  visibility: Visibility
}

export function draftOf(clip: Clip): ClipDraft {
  return {
    id: clip.id,
    video_id: clip.video_id,
    start_ms: clip.start_ms,
    end_ms: clip.end_ms,
    title: clip.title,
    note: clip.note,
    event_id: clip.event_id,
    visibility: clip.visibility,
  }
}
