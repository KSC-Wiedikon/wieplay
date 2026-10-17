// Which tags are facts (docs/PLAN.md §10.4a): everything but a model's suggested action nobody
// has answered yet. The score, the stats and the plays behind a number count facts only, like
// the server (tagging.event_stands). No imports, so the tagging store can use it.

import type { Tag } from './types'

type Kind = Pick<Tag, 'source' | 'review'>

/** A model's suggested action nobody has answered yet. */
export function isSuggestedTag(tag: Kind): boolean {
  return tag.source === 'model' && tag.review === 'unreviewed'
}

/** Tags that count: everything but open suggestions (rejected ones are deleted already). */
export function factTags<T extends Kind>(tags: readonly T[]): T[] {
  return tags.some(isSuggestedTag) ? tags.filter((t) => !isSuggestedTag(t)) : [...tags]
}
