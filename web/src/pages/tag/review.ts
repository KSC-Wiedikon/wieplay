// "To check" (docs/PLAN.md §10, §10.1, §11), the rallies and breaks: WiePlay tags every rally,
// its winner and the breaks by itself; what it is sure of counts at once (review "auto"), what
// it is not sure of counts too and waits here (review "check", or a winner it is unsure of).
// Modelled on Hudl's review: the list goes through what is still to check in time order, the
// player loops the one on screen (1 s before it to just after it), and every answer is one key:
//
//   Enter accept (WiePlay's answer is right: the rally, and its winner when nobody set one)
//   X reject · ←/→ move the start · Shift+←/→ move the end (0.25 s; with Alt one frame)
//   M merge with the next rally · S split at the playhead · U undo
//   1 we serve · 2 they serve (again: no call) · 3 we won · 4 they won (again: no call;
//   the winner is a fact the scoring keeps to) · W B T D O label a break (§10.3); on a
//   rally they say "no rally, a break": the rally is rejected and the dead time around it
//   (from the rally before to the rally after) becomes that break
//
// Each item says why WiePlay is unsure (the scoring's reasons) and offers its other answer
// (`model_alternative`). The sure ones are not in the walk (↑/↓ still reach them). A decision
// (who won, the serve, reject, a break) is scored again at once: the rallies around it can
// become sure. When nothing is left here, the walk goes on into the actions (`onEnd`).
//
// The break labels of the automatic scoring (warm-up, set break, timeout, stoppage, after the
// match) are in the list too, in time order with the rallies; they are accepted, rejected,
// relabelled and moved like rallies, but not merged or split.
//
// Answers are ordinary segment changes through the tagging store and its outbox (offline
// queue, base versions, undo): accept sets review "confirmed", a moved boundary, another label
// or another winner "corrected", reject deletes the segment with review "rejected" (the server
// keeps it as a negative). A serve call sets `serving_manual`, a winner `won_by`. Plain
// TypeScript like the tagging controller, so a burst of keys always sees the last state.

import type { Playhead } from '../../lib/playhead'
import {
  allProgress,
  isGap,
  isModel,
  isOpen,
  isToCheck,
  isWiePlays,
  LOOP_PRE_MS,
  nextOpen,
  reviewItems,
  toCheckRallies,
  type ReviewProgress,
} from '../../lib/suggestions'
import { newId, segmentToIn, type TaggingStore } from '../../lib/taggingStore'
import type { GapKind, Review, Segment, SegmentIn, Side } from '../../lib/types'
import type { Change } from '../../lib/outbox'
import { ownsKeys, type TagController } from './controller'

/** ←/→ move a boundary by this much; with Alt by one frame. */
export const NUDGE_MS = 250
/** Moving a boundary while playing shows this much before the end. */
const END_PREVIEW_MS = 1500
/** Nothing shorter is left by a split or a moved boundary. */
export const MIN_RALLY_MS = 200

/** The label keys for a break. */
export const GAP_KEYS: Readonly<Record<string, GapKind>> = { w: 'warmup', b: 'set_break', t: 'timeout', d: 'stoppage', o: 'other_dead' }

/**
 * The breaks as buttons under Accept and Reject, the most frequent first: on a rally they say
 * "not a rally, it's that break", on a break which one it is. "Sub" (a substitution, a coach's
 * label only) has no key.
 */
export const BREAK_BUTTONS: readonly GapKind[] = ['timeout', 'substitution', 'set_break', 'warmup', 'stoppage', 'other_dead']

export type ReviewNoticeKey =
  | 'accepted'
  | 'rejected'
  | 'gapAccepted'
  | 'gapRejected'
  | 'relabelled'
  | 'toBreak'
  | 'toRally'
  | 'rallyAlready'
  | 'serve'
  | 'serveCleared'
  | 'won'
  | 'wonCleared'
  | 'ralliesOnly'
  | 'gapsOnly'
  | 'moved'
  | 'merged'
  | 'split'
  | 'undone'
  | 'redone'
  | 'nothingToUndo'
  | 'nothingToRedo'
  | 'noNext'
  | 'cannotSplit'
  | 'tooShort'
  | 'done'
  | 'nothingSelected'

export interface ReviewNotice {
  id: number
  key: ReviewNoticeKey
  tone: 'info' | 'error'
  params?: Record<string, string | number>
}

export interface ReviewState {
  active: boolean
  /** The item on screen; null when everything is checked (or nothing is to check). */
  currentId: string | null
  /** Rallies split off in this session: manual rallies that wait for an answer like suggestions. */
  pending: ReadonlySet<string>
  /** Rallies seen to check in this session (also once rejected or checked), for the progress. */
  seen: ReadonlySet<string>
  /** Break labels seen to check in this session (also once rejected or checked). */
  seenGaps: ReadonlySet<string>
  /** Rallies rejected before this session (the tagging data no longer lists them). */
  rejectedBefore: number
  notice: ReviewNotice | null
}

const EMPTY: ReadonlySet<string> = new Set()

interface Mark {
  currentId: string | null
  pending: ReadonlySet<string>
}

/** A break WiePlay labelled that the coach says is play: up to this long, the rally takes it all. */
export const RALLY_FROM_BREAK_MS = 20_000
/** A longer one: a rally from this long before the playhead … */
const RALLY_LEAD_MS = 2000
/** … this long (the coach then moves its start and end). */
const RALLY_LENGTH_MS = 8000

export class ReviewController {
  private state: ReviewState
  private readonly listeners = new Set<() => void>()
  private noticeId = 0
  /** Where the review was before each of its undo steps (by store step id). */
  private readonly marks = new Map<number, Mark>()
  private readonly store: TaggingStore
  private readonly playhead: Playhead
  private readonly tag: TagController
  private unsubscribe: () => void = () => {}
  /** Something was changed since the review started (the scoring is then run again on leaving). */
  private changed = false
  /** Inside one of the review's own commits: an item it answers leaves the view on purpose. */
  private committing = false
  /** The item on screen as it was last seen, to find the walk again when the item disappears. */
  private shown: Pick<Segment, 'id' | 'kind' | 'start_ms' | 'end_ms'> | null = null
  /** Gap labels this session answered (a rejected one leaves the view, but it counts as done). */
  private readonly answeredGaps = new Set<string>()
  private readonly onLeave: () => void
  /** Nothing left to check here: the walk goes on (into the actions). */
  private onEnd: () => void = () => {}

  /** `onLeave`: the review ends after changes (the page asks the server to score again). */
  constructor(store: TaggingStore, playhead: Playhead, tag: TagController, { rejectedBefore = 0, onLeave = () => {} } = {}) {
    this.store = store
    this.playhead = playhead
    this.tag = tag
    this.onLeave = onLeave
    const segments = store.getView().segments
    this.state = {
      active: false,
      currentId: null,
      pending: EMPTY,
      seen: modelIds(segments, EMPTY, 'rally'),
      seenGaps: modelIds(segments, EMPTY, 'gap'),
      rejectedBefore,
      notice: null,
    }
  }

  /** What happens when nothing is left to check here (the page chains the actions). */
  setOnEnd(onEnd: () => void): void {
    this.onEnd = onEnd
  }

  /**
   * Follows the tagging data: items to check that show up count for the progress, labels the
   * server took away unanswered no longer do, and when the item on screen disappears (the
   * server's scoring replaced it, someone else answered it) the walk goes on from where it was.
   */
  attach(): () => void {
    this.unsubscribe()
    this.unsubscribe = this.store.subscribe(() => {
      const segments = this.store.getView().segments
      const seen = modelIds(segments, this.state.seen, 'rally')
      const seenGaps = withoutVanished(modelIds(segments, this.state.seenGaps, 'gap'), segments, this.answeredGaps)
      if (seen !== this.state.seen || seenGaps !== this.state.seenGaps) this.set({ seen, seenGaps })
      if (!this.committing) this.findAgain()
    })
    return () => {
      this.unsubscribe()
      this.unsubscribe = () => {}
    }
  }

  /** A new analysis replaced WiePlay's rallies: count afresh. */
  reset(rejectedBefore: number): void {
    this.marks.clear()
    const segments = this.store.getView().segments
    this.set({
      seen: modelIds(segments, EMPTY, 'rally'),
      seenGaps: modelIds(segments, EMPTY, 'gap'),
      rejectedBefore,
      pending: EMPTY,
      currentId: null,
    })
    if (this.state.active) this.go(nextOpen(this.items(), this.state.pending, null))
  }

  getState = (): ReviewState => this.state

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  private set(patch: Partial<ReviewState>): void {
    this.state = { ...this.state, ...patch }
    for (const listener of [...this.listeners]) listener()
  }

  private notify(key: ReviewNoticeKey, params?: ReviewNotice['params'], tone: ReviewNotice['tone'] = 'info'): ReviewNotice {
    this.noticeId += 1
    return { id: this.noticeId, key, tone, params }
  }

  // What is on screen

  /** The review list: WiePlay's rallies and break labels, the rallies to check and this session's split-off parts, in time order. */
  items(): Segment[] {
    return reviewItems(this.store.getView().segments, this.state.pending)
  }

  current(): Segment | null {
    const id = this.state.currentId
    return id === null ? null : (this.store.getView().segments.find((s) => s.id === id) ?? null)
  }

  /** Rallies and gap labels together. */
  progress(): ReviewProgress {
    return allProgress(this.store.getView().segments, this.state.seen, this.state.seenGaps, this.state.rejectedBefore)
  }

  /** The rallies still to check, in time order. */
  toCheck(): Segment[] {
    return toCheckRallies(this.store.getView().segments)
  }

  // Mode

  /**
   * Review mode on: the first item to check in time order, unless one is on screen already.
   * Nothing to check here: the walk goes on (`onEnd`, the actions).
   */
  start = (): void => {
    const current = this.current()
    if (!this.state.active) this.changed = false
    this.set({ active: true, notice: null })
    const next = current && this.items().some((s) => s.id === current.id) ? current : nextOpen(this.items(), this.state.pending, null)
    this.go(next)
    if (!next) this.onEnd()
  }

  stop = (): void => {
    const leaving = this.state.active && this.changed
    this.changed = false
    this.set({ active: false })
    if (leaving) this.onLeave()
  }

  /** Shows an item: the player jumps to 1 s before it and plays (the loop is useLoopPlayback). */
  private go(segment: Segment | null, { play = true } = {}): void {
    this.shown = segment
    this.set({ currentId: segment?.id ?? null })
    if (!segment) return
    this.tag.seek(segment.start_ms - LOOP_PRE_MS)
    if (play) this.playhead.play()
  }

  /**
   * The item on screen left the view without an answer from here: the open item over the same
   * stretch (the scoring's new label for it), else the next open one from its start.
   */
  private findAgain(): void {
    const gone = this.shown
    if (!this.state.active || gone === null || this.state.currentId !== gone.id) return
    const items = this.items()
    const still = items.find((s) => s.id === gone.id)
    if (still) {
      this.shown = still
      return
    }
    const open = items.filter((s) => isOpen(s, this.state.pending))
    const same = open.find((s) => s.kind === gone.kind && s.start_ms < gone.end_ms && s.end_ms > gone.start_ms)
    if (same) {
      // The same place: the loop goes on as it was.
      this.shown = same
      this.set({ currentId: same.id })
      return
    }
    const playing = this.playhead.video ? !this.playhead.video.paused : false
    this.go(nextOpen(items, this.state.pending, { id: gone.id, start_ms: gone.start_ms - 1 }), { play: playing })
  }

  /** "Next" when nothing is on screen but items are still open (none skipped for good). */
  resume = (): void => {
    this.go(nextOpen(this.items(), this.state.pending, null))
  }

  select = (id: string): void => {
    const segment = this.store.getView().segments.find((s) => s.id === id)
    if (segment) this.go(segment)
  }

  /** ↑/↓: the previous or next item, answered or not. */
  move = (direction: -1 | 1): void => {
    const items = this.items()
    if (items.length === 0) return
    const index = items.findIndex((s) => s.id === this.state.currentId)
    const next = index === -1 ? items[direction > 0 ? 0 : items.length - 1] : items[Math.min(items.length - 1, Math.max(0, index + direction))]
    if (next && next.id !== this.state.currentId) this.go(next)
  }

  /** After an answer: the next item to check, or the end of the list (then on to the actions). */
  private advance(from: Segment): void {
    const next = nextOpen(this.items(), this.state.pending, from)
    if (next) {
      this.go(next)
      return
    }
    this.set({ currentId: null })
    this.playhead.video?.pause()
    this.onEnd()
  }

  // Answers

  /** One undo step, remembering where the review was. */
  private commit(changes: readonly Change[], pending: ReadonlySet<string> = this.state.pending): void {
    const mark: Mark = { currentId: this.state.currentId, pending: this.state.pending }
    for (const change of changes) {
      if (change.kind === 'segment' && isGap(change.payload.kind)) this.answeredGaps.add(change.payload.id)
    }
    if (changes.length > 0) {
      this.changed = true
      const step = this.quietly(() => this.store.commit(changes))
      this.marks.set(step, mark)
      if (this.marks.size > 200) this.marks.delete(this.marks.keys().next().value as number)
    }
    if (pending !== this.state.pending) this.set({ pending })
  }

  /** The review's own change to the store: it moves on by itself (no `findAgain`). */
  private quietly<T>(run: () => T): T {
    this.committing = true
    try {
      return run()
    } finally {
      this.committing = false
    }
  }

  /**
   * Enter: WiePlay's answer is right, and on. A rally to check (WiePlay's, or a person's whose
   * winner the scoring is unsure of) is confirmed with WiePlay's winner when nobody set one: the
   * winner is then a fact the scoring keeps to. A corrected one stays corrected.
   */
  accept = (): void => {
    const segment = this.current()
    if (!segment) {
      this.set({ notice: this.notify('nothingSelected', undefined, 'error') })
      return
    }
    const changes: Change[] = isToCheck(segment) || isWiePlays(segment) ? [acceptance(segment)] : []
    this.commit(changes, without(this.state.pending, segment.id))
    this.set({ notice: this.answered(segment, 'accepted') })
    this.advance(segment)
  }

  /** The notice of an answer: "Accepted the rally at …", "Accepted the timeout at …". */
  private answered(segment: Segment, key: 'accepted' | 'rejected'): ReviewNotice {
    if (!isGap(segment.kind)) return this.notify(key, { time: segment.start_ms })
    return this.notify(key === 'accepted' ? 'gapAccepted' : 'gapRejected', { time: segment.start_ms, kind: segment.kind })
  }

  /** X: not a rally. A proposal is rejected (kept by the server as a negative); a split-off part is deleted. */
  reject = (): void => {
    const segment = this.current()
    if (!segment) {
      this.set({ notice: this.notify('nothingSelected', undefined, 'error') })
      return
    }
    this.commit([rejection(segment)], without(this.state.pending, segment.id))
    this.set({ notice: this.answered(segment, 'rejected') })
    this.advance(segment)
  }

  /**
   * W B T D O: the break on screen is a warm-up, set break, timeout, stoppage or other break.
   * Its own label accepts it; another one corrects it. Either way it is answered.
   */
  relabel = (kind: GapKind): void => {
    const segment = this.current()
    if (!segment) {
      this.set({ notice: this.notify('nothingSelected', undefined, 'error') })
      return
    }
    if (!isGap(segment.kind)) {
      this.toBreak(segment, kind)
      return
    }
    if (kind === segment.kind) {
      this.accept()
      return
    }
    this.commit([segmentChange(segment, { kind })])
    this.set({ notice: this.notify('relabelled', { time: segment.start_ms, kind }) })
    this.advance(segment)
  }

  /**
   * W B T D O on a rally: no rally but a timeout, set break, warm-up, stoppage or other break.
   * The rally is rejected (a negative the rally finder learns from) and the dead time around
   * it, from the end of the rally before to the start of the rally after, becomes that break:
   * a break already there is relabelled, else a new one is made (its edges can be moved later).
   */
  private toBreak(segment: Segment, kind: GapKind): void {
    const segments = this.store.getView().segments
    const live = (s: Segment) => !s.deleted_at && s.review !== 'rejected' && s.review !== 'merged'
    const rallies = segments.filter((s) => s.kind === 'rally' && s.id !== segment.id && live(s))
    const before = rallies.filter((s) => s.end_ms <= segment.start_ms).reduce((m, s) => Math.max(m, s.end_ms), -1)
    const after = rallies.filter((s) => s.start_ms >= segment.end_ms).reduce((m, s) => Math.min(m, s.start_ms), Number.POSITIVE_INFINITY)
    const start = before >= 0 ? before : segment.start_ms
    const end = Number.isFinite(after) ? after : segment.end_ms
    const gap = segments.find((s) => isGap(s.kind) && live(s) && s.start_ms < end && s.end_ms > start) ?? null
    const pending = without(this.state.pending, segment.id)
    if (gap) {
      const changes: Change[] = [rejection(segment)]
      if (gap.kind !== kind || isWiePlays(gap)) changes.push(segmentChange(gap, { kind, review: 'confirmed' }))
      this.commit(changes, without(pending, gap.id))
    } else {
      const created: SegmentIn = { id: newId(), base_version: 0, kind, start_ms: start, end_ms: end, won_by: null }
      this.commit([rejection(segment), { kind: 'segment', payload: created }], pending)
    }
    this.set({ notice: this.notify('toBreak', { time: segment.start_ms, kind }) })
    this.advance(segment)
  }

  /**
   * R on a break WiePlay labelled: no break, play. The label is rejected and a rally made in one
   * step: over the whole break when it is short (up to 20 s), else 8 s from 2 s before the
   * playhead. It comes on screen to answer like a split-off part: move its start and end, accept.
   */
  toRally = (): void => {
    const segment = this.current()
    if (!segment) {
      this.set({ notice: this.notify('nothingSelected', undefined, 'error') })
      return
    }
    if (!isGap(segment.kind)) {
      this.set({ notice: this.notify('rallyAlready', undefined, 'error') })
      return
    }
    let start_ms = segment.start_ms
    let end_ms = segment.end_ms
    if (end_ms - start_ms > RALLY_FROM_BREAK_MS) {
      const at = Math.min(Math.max(this.tag.now(), segment.start_ms), segment.end_ms)
      start_ms = Math.max(segment.start_ms, Math.min(at - RALLY_LEAD_MS, segment.end_ms - RALLY_LENGTH_MS))
      end_ms = Math.min(segment.end_ms, start_ms + RALLY_LENGTH_MS)
    }
    const rally: SegmentIn = { id: newId(), base_version: 0, kind: 'rally', start_ms, end_ms, won_by: null }
    const pending = new Set(this.state.pending)
    pending.add(rally.id)
    this.commit([rejection(segment), { kind: 'segment', payload: rally }], pending)
    this.set({ notice: this.notify('toRally', { start: start_ms, end: end_ms }) })
    this.go(this.store.getView().segments.find((s) => s.id === rally.id) ?? null)
  }

  /** 1 / 2: we or they served this rally (the coach's call); the same key again takes the call back. */
  serve = (side: Side): void => {
    const segment = this.current()
    if (!segment) {
      this.set({ notice: this.notify('nothingSelected', undefined, 'error') })
      return
    }
    if (segment.kind !== 'rally') {
      this.set({ notice: this.notify('ralliesOnly', undefined, 'error') })
      return
    }
    const call = segment.serving_manual === side ? null : side
    this.commit([segmentChange(segment, { serving_manual: call })])
    this.set({ notice: call === null ? this.notify('serveCleared') : this.notify('serve', { side: call }) })
  }

  /**
   * 3 / 4: we or they won this rally (the coach's call, a fact the scoring keeps to; the score
   * line follows at once); the same key again takes the call back.
   */
  won = (side: Side): void => {
    const segment = this.current()
    if (!segment) {
      this.set({ notice: this.notify('nothingSelected', undefined, 'error') })
      return
    }
    if (segment.kind !== 'rally') {
      this.set({ notice: this.notify('ralliesOnly', undefined, 'error') })
      return
    }
    const call = segment.won_by === side ? null : side
    this.commit([segmentChange(segment, { won_by: call })])
    this.set({ notice: call === null ? this.notify('wonCleared') : this.notify('won', { side: call }) })
  }

  /** ←/→ (start) and Shift+←/→ (end): 0.25 s, or one frame with Alt. */
  nudge = (field: 'start_ms' | 'end_ms', direction: -1 | 1, { frame = false } = {}): void => {
    const segment = this.current()
    if (!segment) {
      this.set({ notice: this.notify('nothingSelected', undefined, 'error') })
      return
    }
    const step = frame ? this.tag.frameMs : NUDGE_MS
    const end = this.tag.duration() ?? Infinity
    const value = Math.round(Math.min(end, Math.max(0, segment[field] + direction * step)))
    const start_ms = field === 'start_ms' ? value : segment.start_ms
    const end_ms = field === 'end_ms' ? value : segment.end_ms
    if (end_ms - start_ms < MIN_RALLY_MS) {
      this.set({ notice: this.notify('tooShort', undefined, 'error') })
      return
    }
    if (value === segment[field]) return
    this.commit([segmentChange(segment, { start_ms, end_ms })])
    this.set({ notice: this.notify('moved', { field: field === 'start_ms' ? 'start' : 'end', time: value }) })
    // Show the boundary: paused, its frame; playing, from just before it.
    const playing = this.playhead.video ? !this.playhead.video.paused : false
    if (!playing) this.tag.seek(value)
    else if (field === 'start_ms') this.tag.seek(value - LOOP_PRE_MS)
    else this.tag.seek(Math.max(start_ms - LOOP_PRE_MS, value - END_PREVIEW_MS))
  }

  /** M: this item takes in the next one (to its end); the next one is folded in ("merged": not a
   * rally of its own, but no "not a rally" either for training). */
  merge = (): void => {
    const segment = this.current()
    if (!segment) {
      this.set({ notice: this.notify('nothingSelected', undefined, 'error') })
      return
    }
    if (segment.kind !== 'rally') {
      this.set({ notice: this.notify('ralliesOnly', undefined, 'error') })
      return
    }
    const next = this.items().find((s) => s.kind === 'rally' && s.start_ms > segment.start_ms && s.id !== segment.id)
    if (!next) {
      this.set({ notice: this.notify('noNext', undefined, 'error') })
      return
    }
    const end_ms = Math.max(segment.end_ms, next.end_ms)
    this.commit([segmentChange(segment, { end_ms }), mergedInto(next, segment)], without(this.state.pending, next.id))
    this.set({ notice: this.notify('merged', { time: segment.start_ms }) })
    this.go(this.current())
  }

  /** S: two rallies from one, at the playhead. The second part comes next in the list. */
  split = (): void => {
    const segment = this.current()
    if (!segment) {
      this.set({ notice: this.notify('nothingSelected', undefined, 'error') })
      return
    }
    if (segment.kind !== 'rally') {
      this.set({ notice: this.notify('ralliesOnly', undefined, 'error') })
      return
    }
    const at = this.tag.now()
    if (at - segment.start_ms < MIN_RALLY_MS || segment.end_ms - at < MIN_RALLY_MS) {
      this.set({ notice: this.notify('cannotSplit', undefined, 'error') })
      return
    }
    const part: SegmentIn = { id: newId(), base_version: 0, kind: 'rally', start_ms: at, end_ms: segment.end_ms, won_by: null }
    const pending = new Set(this.state.pending)
    pending.add(part.id)
    this.commit([segmentChange(segment, { end_ms: at }), { kind: 'segment', payload: part }], pending)
    this.set({ notice: this.notify('split', { time: at }) })
  }

  /** U (and Ctrl+Z here): takes back the last change and goes back to where it was made. */
  undo = (): void => {
    const step = this.store.topStep()
    if (!this.quietly(() => this.store.undo())) {
      this.set({ notice: this.notify('nothingToUndo', undefined, 'error') })
      return
    }
    const mark = step === null ? undefined : this.marks.get(step)
    if (mark) {
      this.set({ pending: mark.pending })
      const segment = mark.currentId ? this.store.getView().segments.find((s) => s.id === mark.currentId) : undefined
      this.go(segment ?? this.current(), { play: !(this.playhead.video?.paused ?? true) })
    }
    this.set({ notice: this.notify('undone') })
  }

  redo = (): void => {
    this.set({ notice: this.notify(this.quietly(() => this.store.redo()) ? 'redone' : 'nothingToRedo') })
  }

  /**
   * WiePlay's other answer (`model_alternative`), taken: on a rally the other winner, or "no
   * point" (it is not a rally: rejected); on a break the other label.
   */
  takeAlternative = (): void => {
    const segment = this.current()
    if (!segment) {
      this.set({ notice: this.notify('nothingSelected', undefined, 'error') })
      return
    }
    const other = segment.model_alternative ?? null
    if (other === null) return
    if (isGap(segment.kind)) {
      if (isGapKind(other)) this.relabel(other)
      return
    }
    if (other === 'none') {
      this.reject()
      return
    }
    if (other !== 'us' && other !== 'them') return
    this.commit([segmentChange(segment, { won_by: other, review: 'corrected' })], without(this.state.pending, segment.id))
    this.set({ notice: this.notify('won', { side: other }) })
    this.advance(segment)
  }

  // Keyboard

  onKeyDown = (event: KeyboardEvent): void => {
    if (event.defaultPrevented || event.isComposing || ownsKeys(event.target)) return
    // A button or link focused from the keyboard keeps Enter and Space (pointer clicks give the focus back).
    if ((event.key === 'Enter' || event.key === ' ') && pressable(event.target)) return
    if (this.handle(event)) {
      event.preventDefault()
      event.stopPropagation()
    }
  }

  private handle(event: KeyboardEvent): boolean {
    const { key, shiftKey, altKey } = event
    const mod = event.ctrlKey || event.metaKey
    if (mod) {
      if (altKey) return false
      if (key === 'z' || key === 'Z') {
        if (shiftKey) this.redo()
        else this.undo()
        return true
      }
      if (key === 'y' || key === 'Y') {
        this.redo()
        return true
      }
      return false
    }

    if (this.tag.getState().cheatsheet) {
      if (key === 'Escape' || key === '?') {
        this.tag.toggleCheatsheet(false)
        return true
      }
      return false
    }

    if (key === 'ArrowLeft' || key === 'ArrowRight') {
      this.nudge(shiftKey ? 'end_ms' : 'start_ms', key === 'ArrowLeft' ? -1 : 1, { frame: altKey })
      return true
    }
    // Alt with a letter types a character on some keyboards: leave it alone.
    if (altKey) return false

    switch (key) {
      case ' ':
        this.tag.togglePlay()
        return true
      case 'Enter':
        this.accept()
        return true
      case 'ArrowUp':
      case 'ArrowDown':
        this.move(key === 'ArrowUp' ? -1 : 1)
        return true
      case ',':
      case '.':
        this.tag.speed(key === ',' ? -1 : 1)
        return true
      case '?':
        this.tag.toggleCheatsheet(true)
        return true
      case 'Escape':
        this.stop()
        return true
      case '1':
      case '2':
        this.serve(key === '1' ? 'us' : 'them')
        return true
      case '3':
      case '4':
        this.won(key === '3' ? 'us' : 'them')
        return true
    }
    const label = GAP_KEYS[key.toLowerCase()]
    if (label) {
      this.relabel(label)
      return true
    }
    switch (key.toLowerCase()) {
      case 'x':
        this.reject()
        return true
      case 'm':
        this.merge()
        return true
      case 's':
        this.split()
        return true
      case 'u':
        this.undo()
        return true
      case 'r':
        this.toRally()
        return true
      default:
        return false
    }
  }
}

function pressable(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest('button, a[href], [role="button"], [role="switch"], summary') !== null
}

/** The ids of the rallies (or break labels) to check, added to `seen` (the same set when nothing is new). */
function modelIds(segments: readonly Segment[], seen: ReadonlySet<string>, which: 'rally' | 'gap'): ReadonlySet<string> {
  let out: Set<string> | null = null
  for (const s of segments) {
    const kind = which === 'rally' ? s.kind === 'rally' : isGap(s.kind)
    if (!kind || !isToCheck(s) || seen.has(s.id)) continue
    out ??= new Set(seen)
    out.add(s.id)
  }
  return out ?? seen
}

/**
 * `seen` without the gap labels that left the view unanswered (the server's scoring replaced or
 * dropped them): they were never done. Those answered here stay (a rejected one leaves too).
 */
function withoutVanished(seen: ReadonlySet<string>, segments: readonly Segment[], answered: ReadonlySet<string>): ReadonlySet<string> {
  let out: Set<string> | null = null
  const live = new Set(segments.map((s) => s.id))
  for (const id of seen) {
    if (live.has(id) || answered.has(id)) continue
    out ??= new Set(seen)
    out.delete(id)
  }
  return out ?? seen
}

function without(set: ReadonlySet<string>, id: string): ReadonlySet<string> {
  if (!set.has(id)) return set
  const out = new Set(set)
  out.delete(id)
  return out
}

/**
 * A change to a segment; WiePlay's rally with new boundaries, another label or another winner
 * than its own is corrected (PLAN §11; the server does the same). Taking WiePlay's own winner
 * is no correction.
 */
function segmentChange(segment: Segment, patch: Partial<SegmentIn>): Change {
  const wonBy = patch.won_by === undefined ? undefined : (patch.won_by ?? null)
  const ownWinner = wonBy !== undefined && wonBy !== null && !segment.won_by && wonBy === (segment.model_won_by ?? null)
  const moved =
    (patch.start_ms !== undefined && patch.start_ms !== segment.start_ms) ||
    (patch.end_ms !== undefined && patch.end_ms !== segment.end_ms) ||
    (patch.kind !== undefined && patch.kind !== segment.kind) ||
    (wonBy !== undefined && wonBy !== (segment.won_by ?? null) && !ownWinner)
  let review: Review = patch.review ?? segment.review
  if (isModel(segment) && moved) review = 'corrected'
  return { kind: 'segment', payload: { ...segmentToIn(segment), ...patch, review } }
}

/**
 * Enter on an item to check: confirmed; a rally without a winner gets WiePlay's (a corrected
 * one stays corrected).
 */
function acceptance(segment: Segment): Change {
  const review: Review = segment.review === 'corrected' ? 'corrected' : 'confirmed'
  const takeWinner = segment.kind === 'rally' && !segment.won_by && (segment.model_won_by ?? null) !== null
  return segmentChange(segment, takeWinner ? { review, won_by: segment.model_won_by ?? null } : { review })
}

function isGapKind(value: string): value is GapKind {
  return (BREAK_BUTTONS as readonly string[]).includes(value)
}

/** Folded into `into` by M: a proposal becomes "merged" (soft-deleted, kept apart from rejections). */
function mergedInto(segment: Segment, into: Segment): Change {
  if (!isModel(segment)) return rejection(segment)
  return {
    kind: 'segment',
    payload: { ...segmentToIn(segment), review: 'merged', merged_into: into.id, deleted: true },
  }
}

/** Not a rally: a proposal is rejected and deleted (soft, the server keeps it); anything else is deleted. */
function rejection(segment: Segment): Change {
  const review: Review = isModel(segment) ? 'rejected' : segment.review
  return { kind: 'segment', payload: { ...segmentToIn(segment), review, deleted: true } }
}
