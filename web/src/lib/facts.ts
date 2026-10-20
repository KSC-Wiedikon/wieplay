// Which tags count and which are still to check (docs/PLAN.md §10, §10.4a): every tag WiePlay
// writes counts like a person's, sure ones (review "auto") and unsure ones (review "check", to
// check) alike, as the server's stats do (tagging.event_stands). Only the scoring's facts, the
// serves a person tagged, leave WiePlay's own tags out (the server does the same). No imports,
// so the tagging store can use it.

import type { Review, Tag } from './types'

type Kind = Pick<Tag, 'source' | 'review'>

/** WiePlay's own review states: nobody has answered (`unreviewed` from older data). */
const UNTOUCHED: readonly Review[] = ['auto', 'check', 'unreviewed']

/** WiePlay's tag that nobody has answered yet (sure or to check). */
export function isWiePlaysTag(tag: Kind): boolean {
  return tag.source === 'model' && UNTOUCHED.includes(tag.review)
}

/** WiePlay's tag that is sure: it counts, nobody needs to check it. */
export function isAutoTag(tag: Kind): boolean {
  return tag.source === 'model' && tag.review === 'auto'
}

/** WiePlay's tag that is not sure: it counts, and waits in "To check". */
export function isToCheckTag(tag: Kind & { deleted_at?: string | null }): boolean {
  return tag.source === 'model' && tag.review === 'check' && !tag.deleted_at
}

/** Tags that count: all but those a person rejected (rejected ones are deleted already). */
export function countedTags<T extends Kind>(tags: readonly T[]): T[] {
  const out = (t: T) => t.review === 'rejected' || t.review === 'merged'
  return tags.some(out) ? tags.filter((t) => !out(t)) : [...tags]
}

/**
 * A person's tags: made or imported by hand, or WiePlay's that a person accepted or corrected.
 * The score's facts (a serve tag names the server) come from these only: WiePlay's serves come
 * out of the scoring itself.
 */
export function personTags<T extends Kind>(tags: readonly T[]): T[] {
  return tags.some(isWiePlaysTag) ? tags.filter((t) => !isWiePlaysTag(t)) : [...tags]
}

/** How many tags are still to check. */
export function toCheckTagCount(tags: readonly (Kind & { deleted_at?: string | null })[]): number {
  return tags.filter(isToCheckTag).length
}
