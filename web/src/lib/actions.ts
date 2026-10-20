// Actions WiePlay tags per rally (docs/PLAN.md §10.4a): serve, reception, set, attack, block,
// dig and freeball at the ball's contacts, as tags with source "model": review "auto" when
// WiePlay is sure, "check" when not (to check). Every one of them counts, like the server's stats;
// the unsure ones wait in "To check". Which tags are WiePlay's, the review list and its
// progress, and the change a review answer sends. Pure, so the review controller and the pages
// share one reading of the data.

import type { TFunction } from 'i18next'
import { isToCheckTag } from './facts'
import { formatPercent } from './suggestions'
import { eventToIn } from './taggingStore'
import type { Change } from './outbox'
import type { ActionGate, EventIn, Review, Side, Tag } from './types'

export { countedTags, isAutoTag, isToCheckTag, isWiePlaysTag, personTags, toCheckTagCount } from './facts'

/** The skill keys of the action review: DataVolley's letters. */
export const SKILL_KEYS: readonly string[] = ['S', 'R', 'E', 'A', 'B', 'D', 'F']

/** The grade keys: DataVolley's grades. */
export const GRADE_KEYS: readonly string[] = ['#', '+', '!', '-', '/', '=']

type Kind = Pick<Tag, 'source' | 'review'>

/** Made by a model (any review state). */
export function isModelTag(tag: Pick<Tag, 'source'>): boolean {
  return tag.source === 'model'
}

/** WiePlay's action a person accepted or corrected: a tag like any other, with a marker. */
export function isReviewedSuggestion(tag: Kind): boolean {
  return tag.source === 'model' && (tag.review === 'confirmed' || tag.review === 'corrected')
}

/** The review list: every action WiePlay tagged (checked or not), in time order. */
export function actionItems(tags: readonly Tag[]): Tag[] {
  return tags.filter(isModelTag).sort((a, b) => a.t_ms - b.t_ms || a.id.localeCompare(b.id))
}

/** The actions still to check, in time order. */
export function toCheckActions(tags: readonly Tag[]): Tag[] {
  return actionItems(tags).filter(isToCheckTag)
}

/**
 * The next action to check after `from`: the first one to check that comes later, else the
 * first one from the start (one skipped earlier). Null when everything is checked.
 */
export function nextOpenAction(items: readonly Tag[], from: Pick<Tag, 'id' | 't_ms'> | null): Tag | null {
  const open = items.filter((t) => isToCheckTag(t) && t.id !== from?.id)
  if (!from) return open[0] ?? null
  return open.find((t) => t.t_ms > from.t_ms) ?? open[0] ?? null
}

/** The model's likeliest other answer: another skill and side, or no contact at all. */
export function alternativeOf(tag: Pick<Tag, 'model_alternative'>): { code: string; side: Side } | 'none' | null {
  const value = tag.model_alternative
  if (!value) return null
  if (value === 'none') return 'none'
  const [code, side] = value.split(':')
  if (!code || (side !== 'us' && side !== 'them')) return null
  return { code, side }
}

export interface ActionProgress {
  done: number
  total: number
}

/**
 * Progress over the actions to check of this session: those seen to check (live or rejected
 * since) and those rejected before it, which the tagging data no longer lists.
 */
export function actionProgress(tags: readonly Tag[], seen: ReadonlySet<string>, rejectedBefore: number): ActionProgress {
  const open = tags.filter((t) => isToCheckTag(t) && seen.has(t.id)).length
  const total = seen.size + rejectedBefore
  return { done: Math.max(0, total - open), total }
}

/** The actions to check of one rally (by its link, else by time). */
export function openInRally(tags: readonly Tag[], rally: { id: string; start_ms: number; end_ms: number } | null): Tag[] {
  if (!rally) return []
  return tags.filter(
    (t) => isToCheckTag(t) && (t.segment_id === rally.id || (t.segment_id === null && t.t_ms >= rally.start_ms && t.t_ms <= rally.end_ms)),
  )
}

/**
 * A change to WiePlay's action (PLAN §11: corrections are data): another skill, side, grade
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
  if (isModelTag(tag) && edited && (review === 'auto' || review === 'check' || review === 'unreviewed' || review === 'confirmed')) review = 'corrected'
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

/** Why WiePlay tags no actions (PLAN §10.4a): only when that is switched off. */
export function gateText(t: TFunction, gate: ActionGate | null | undefined): string | null {
  if (!gate || gate.open) return null
  return t('actions.gate.off')
}

/**
 * What is measured, plainly (PLAN §10.4a): on hand-checked rallies, how many of WiePlay's actions
 * are fully right, how many contacts get one, and how many sure ones were wrong. Null when unknown.
 */
export function measuredText(t: TFunction, gate: ActionGate | null | undefined): string | null {
  const m = gate?.measured
  if (!m || !m.rallies || m.precision == null || m.found == null) return null
  return t('actions.measured', {
    count: m.rallies,
    precision: formatPercent(m.precision),
    found: formatPercent(m.found),
    sure: m.sure ?? 0,
    wrong: m.sure_wrong ?? 0,
  })
}
