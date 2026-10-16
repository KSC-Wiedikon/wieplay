// Reviewing the rallies Nomal suggested (docs/PLAN.md §10, §10.1, §11), modelled on Hudl's
// review: the list goes through the open suggestions in time order, the player loops the one
// on screen (1 s before it to just after it), and every answer is one key:
//
//   Enter accept · X reject · ←/→ move the start · Shift+←/→ move the end (0.25 s; with Alt
//   one frame) · M merge with the next suggestion · S split at the playhead · U undo
//
// Answers are ordinary segment changes through the tagging store and its outbox (offline
// queue, base versions, undo): accept sets review "confirmed", a moved boundary "corrected",
// reject deletes the segment with review "rejected" (the server keeps it as a negative). Plain
// TypeScript like the tagging controller, so a burst of keys always sees the last state.

import type { Playhead } from '../../lib/playhead'
import {
  isModel,
  isSuggestion,
  LOOP_PRE_MS,
  nextOpen,
  reviewItems,
  reviewProgress,
  sureSuggestions,
  type ReviewProgress,
} from '../../lib/suggestions'
import { newId, segmentToIn, type TaggingStore } from '../../lib/taggingStore'
import type { Review, Segment, SegmentIn } from '../../lib/types'
import type { Change } from '../../lib/outbox'
import { ownsKeys, type TagController } from './controller'

/** ←/→ move a boundary by this much; with Alt by one frame. */
export const NUDGE_MS = 250
/** Moving a boundary while playing shows this much before the end. */
const END_PREVIEW_MS = 1500
/** Nothing shorter is left by a split or a moved boundary. */
export const MIN_RALLY_MS = 200

export type ReviewNoticeKey =
  | 'accepted'
  | 'rejected'
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
  | 'acceptedAll'
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
  /** The suggestion on screen; null when everything is answered (or nothing was suggested). */
  currentId: string | null
  /** Rallies split off in this session: manual rallies that wait for an answer like suggestions. */
  pending: ReadonlySet<string>
  /** Proposals of this video seen in this session (also once rejected), for the progress. */
  seen: ReadonlySet<string>
  /** Proposals rejected before this session (the tagging data no longer lists them). */
  rejectedBefore: number
  /** "Accept all remaining with confidence ≥ 90%" waits for its confirmation. */
  confirmAll: boolean
  notice: ReviewNotice | null
}

const EMPTY: ReadonlySet<string> = new Set()

interface Mark {
  currentId: string | null
  pending: ReadonlySet<string>
}

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

  constructor(store: TaggingStore, playhead: Playhead, tag: TagController, { rejectedBefore = 0 } = {}) {
    this.store = store
    this.playhead = playhead
    this.tag = tag
    this.state = {
      active: false,
      currentId: null,
      pending: EMPTY,
      seen: modelIds(store.getView().segments, EMPTY),
      rejectedBefore,
      confirmAll: false,
      notice: null,
    }
  }

  /** Follows the tagging data: proposals that show up count for the progress. */
  attach(): () => void {
    this.unsubscribe()
    this.unsubscribe = this.store.subscribe(() => {
      const seen = modelIds(this.store.getView().segments, this.state.seen)
      if (seen !== this.state.seen) this.set({ seen })
    })
    return () => {
      this.unsubscribe()
      this.unsubscribe = () => {}
    }
  }

  /** A new analysis replaced the proposals: count afresh. */
  reset(rejectedBefore: number): void {
    this.marks.clear()
    this.set({ seen: modelIds(this.store.getView().segments, EMPTY), rejectedBefore, pending: EMPTY, currentId: null })
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

  /** The review list: the model's rallies and this session's split-off parts, in time order. */
  items(): Segment[] {
    return reviewItems(this.store.getView().segments, this.state.pending)
  }

  current(): Segment | null {
    const id = this.state.currentId
    return id === null ? null : (this.store.getView().segments.find((s) => s.id === id) ?? null)
  }

  progress(): ReviewProgress {
    return reviewProgress(this.store.getView().segments, this.state.seen, this.state.rejectedBefore)
  }

  /** The open proposals "accept all" would accept. */
  sure(): Segment[] {
    return sureSuggestions(this.store.getView().segments)
  }

  // Mode

  /** Review mode on: the first open suggestion in time order, unless one is on screen already. */
  start = (): void => {
    const current = this.current()
    this.set({ active: true, notice: null })
    this.go(current && this.items().some((s) => s.id === current.id) ? current : nextOpen(this.items(), this.state.pending, null))
  }

  stop = (): void => {
    this.set({ active: false, confirmAll: false })
  }

  /** Shows an item: the player jumps to 1 s before it and plays (the loop is useLoopPlayback). */
  private go(segment: Segment | null, { play = true } = {}): void {
    this.set({ currentId: segment?.id ?? null })
    if (!segment) return
    this.tag.seek(segment.start_ms - LOOP_PRE_MS)
    if (play) this.playhead.play()
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

  /** After an answer: the next open item, or the end of the list. */
  private advance(from: Segment): void {
    const next = nextOpen(this.items(), this.state.pending, from)
    if (next) {
      this.go(next)
      return
    }
    this.set({ currentId: null })
    this.playhead.video?.pause()
  }

  // Answers

  /** One undo step, remembering where the review was. */
  private commit(changes: readonly Change[], pending: ReadonlySet<string> = this.state.pending): void {
    const mark: Mark = { currentId: this.state.currentId, pending: this.state.pending }
    if (changes.length > 0) {
      const step = this.store.commit(changes)
      this.marks.set(step, mark)
      if (this.marks.size > 200) this.marks.delete(this.marks.keys().next().value as number)
    }
    if (pending !== this.state.pending) this.set({ pending })
  }

  /** Enter: accept the suggestion as it is (a corrected one stays corrected) and go on. */
  accept = (): void => {
    const segment = this.current()
    if (!segment) {
      this.set({ notice: this.notify('nothingSelected', undefined, 'error') })
      return
    }
    const changes: Change[] = isSuggestion(segment) ? [segmentChange(segment, { review: 'confirmed' })] : []
    this.commit(changes, without(this.state.pending, segment.id))
    this.set({ notice: this.notify('accepted', { time: segment.start_ms }) })
    this.advance(segment)
  }

  /** X: not a rally. A proposal is rejected (kept by the server as a negative); a split-off part is deleted. */
  reject = (): void => {
    const segment = this.current()
    if (!segment) {
      this.set({ notice: this.notify('nothingSelected', undefined, 'error') })
      return
    }
    this.commit([rejection(segment)], without(this.state.pending, segment.id))
    this.set({ notice: this.notify('rejected', { time: segment.start_ms }) })
    this.advance(segment)
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
    const next = this.items().find((s) => s.start_ms > segment.start_ms && s.id !== segment.id)
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
    if (!this.store.undo()) {
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
    this.set({ notice: this.notify(this.store.redo() ? 'redone' : 'nothingToRedo') })
  }

  // Accept all remaining with confidence ≥ 90%

  askAcceptAll = (): void => {
    if (this.sure().length > 0) this.set({ confirmAll: true })
  }

  cancelAcceptAll = (): void => this.set({ confirmAll: false })

  confirmAcceptAll = (): void => {
    const sure = this.sure()
    this.set({ confirmAll: false })
    if (sure.length === 0) return
    const current = this.current()
    this.commit(sure.map((s) => segmentChange(s, { review: 'confirmed' })))
    this.set({ notice: this.notify('acceptedAll', { count: sure.length }) })
    // The suggestion on screen was one of them: go on to the next open one.
    if (current && sure.some((s) => s.id === current.id)) this.advance(current)
    else if (!current) this.go(nextOpen(this.items(), this.state.pending, null))
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
    if (this.state.confirmAll) return false

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
      default:
        return false
    }
  }
}

function pressable(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest('button, a[href], [role="button"], [role="switch"], summary') !== null
}

/** The ids of the model's rallies, added to `seen` (the same set when nothing is new). */
function modelIds(segments: readonly Segment[], seen: ReadonlySet<string>): ReadonlySet<string> {
  let out: Set<string> | null = null
  for (const s of segments) {
    if (s.kind !== 'rally' || !isModel(s) || seen.has(s.id)) continue
    out ??= new Set(seen)
    out.add(s.id)
  }
  return out ?? seen
}

function without(set: ReadonlySet<string>, id: string): ReadonlySet<string> {
  if (!set.has(id)) return set
  const out = new Set(set)
  out.delete(id)
  return out
}

/** A change to a segment; a model's proposal with new boundaries is corrected (PLAN §11). */
function segmentChange(segment: Segment, patch: Partial<SegmentIn>): Change {
  const moved =
    (patch.start_ms !== undefined && patch.start_ms !== segment.start_ms) ||
    (patch.end_ms !== undefined && patch.end_ms !== segment.end_ms)
  let review: Review = patch.review ?? segment.review
  if (isModel(segment) && moved) review = 'corrected'
  return { kind: 'segment', payload: { ...segmentToIn(segment), ...patch, review } }
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
