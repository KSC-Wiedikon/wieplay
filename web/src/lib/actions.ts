// Actions Nomal suggests per rally (docs/PLAN.md §10.4a): serve, reception, set, attack, block,
// dig and freeball at the ball's contacts, as tags with source "model" and review "unreviewed".
// Which tags are suggestions, the review list and its progress, and the change a review
// answer sends. Pure, so the review controller and the pages share one reading of the data.
//
// Nothing a model suggests is a fact until a coach accepts it: the score, the stats and the
// plays behind a number use `factTags` (the server's stats do the same).

import { isSuggestedTag } from './facts'
import { eventToIn } from './taggingStore'
import type { Change } from './outbox'
import type { EventIn, Review, Tag } from './types'

export { factTags, isSuggestedTag } from './facts'

/** The skill keys of the action review: DataVolley's letters. */
export const SKILL_KEYS: readonly string[] = ['S', 'R', 'E', 'A', 'B', 'D', 'F']

/** The grade keys: DataVolley's grades. */
export const GRADE_KEYS: readonly string[] = ['#', '+', '!', '-', '/', '=']

type Kind = Pick<Tag, 'source' | 'review'>

/** Made by a model (any review state). */
export function isModelTag(tag: Pick<Tag, 'source'>): boolean {
  return tag.source === 'model'
}

/** A suggested action a person accepted or corrected: a tag like any other, with a marker. */
export function isReviewedSuggestion(tag: Kind): boolean {
  return tag.source === 'model' && (tag.review === 'confirmed' || tag.review === 'corrected')
}

export function suggestedCount(tags: readonly Kind[]): number {
  return tags.filter(isSuggestedTag).length
}

/** The review list: every suggested action (answered or not), in time order. */
export function actionItems(tags: readonly Tag[]): Tag[] {
  return tags.filter(isModelTag).sort((a, b) => a.t_ms - b.t_ms || a.id.localeCompare(b.id))
}

/**
 * The next action to answer after `from`: the first open one that comes later, else the first
 * open one from the start (one skipped earlier). Null when everything is answered.
 */
export function nextOpenAction(items: readonly Tag[], from: Pick<Tag, 'id' | 't_ms'> | null): Tag | null {
  const open = items.filter((t) => isSuggestedTag(t) && t.id !== from?.id)
  if (!from) return open[0] ?? null
  return open.find((t) => t.t_ms > from.t_ms) ?? open[0] ?? null
}

export interface ActionProgress {
  done: number
  total: number
}

/**
 * Progress over every suggestion of the video: those seen in this session (live or rejected
 * since) and those rejected before it, which the tagging data no longer lists.
 */
export function actionProgress(tags: readonly Tag[], seen: ReadonlySet<string>, rejectedBefore: number): ActionProgress {
  const open = tags.filter((t) => isSuggestedTag(t) && seen.has(t.id)).length
  const total = seen.size + rejectedBefore
  return { done: Math.max(0, total - open), total }
}

/** The open suggestions of one rally (by its link, else by time). */
export function openInRally(tags: readonly Tag[], rally: { id: string; start_ms: number; end_ms: number } | null): Tag[] {
  if (!rally) return []
  return tags.filter(
    (t) => isSuggestedTag(t) && (t.segment_id === rally.id || (t.segment_id === null && t.t_ms >= rally.start_ms && t.t_ms <= rally.end_ms)),
  )
}

/**
 * A change to a suggested action (PLAN §11: corrections are data): another skill, side, grade
 * or time corrects it; naming the player (the model leaves it empty) or accepting it does not.
 * The server reads the change the same way.
 */
export function actionChange(tag: Tag, patch: Partial<EventIn>): Change {
  const edited =
    (patch.code !== undefined && patch.code !== tag.code) ||
    (patch.side !== undefined && patch.side !== tag.side) ||
    (patch.t_ms !== undefined && patch.t_ms !== tag.t_ms) ||
    (patch.attributes !== undefined && JSON.stringify(patch.attributes) !== JSON.stringify(tag.attributes)) ||
    (patch.number !== undefined && tag.number !== null && patch.number !== tag.number)
  let review: Review = patch.review ?? tag.review
  if (isModelTag(tag) && edited && (review === 'unreviewed' || review === 'confirmed')) review = 'corrected'
  if (isModelTag(tag) && tag.review === 'corrected' && review === 'confirmed') review = 'corrected'
  return { kind: 'event', payload: { ...eventToIn(tag), ...patch, review } }
}

/** Accept as it is (a corrected one stays corrected). */
export function acceptChange(tag: Tag, patch: Partial<EventIn> = {}): Change {
  return actionChange(tag, { ...patch, review: tag.review === 'corrected' ? 'corrected' : 'confirmed' })
}

/** Not an action: rejected and deleted (the server keeps it as a negative). */
export function rejectChange(tag: Tag): Change {
  return { kind: 'event', payload: { ...eventToIn(tag), review: isModelTag(tag) ? 'rejected' : tag.review, deleted: true } }
}
