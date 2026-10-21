// "To check", the actions (docs/PLAN.md §10.4a, §11): WiePlay tags every action it finds by
// itself; the sure ones count at once (review "auto"), the unsure ones count too and wait here
// (review "check"). Like the rallies: the list goes through what is still to check in time
// order, the player loops the one on screen (1 s before the contact to 0.5 s after it), and
// every answer is a key:
//
//   Enter accept · X reject · S R E A B D F the skill · # + ! - / = the grade (again: none)
//   a number, then Enter: the player (the action is accepted with it) · Tab the other side
//   ←/→ one frame earlier or later · Shift+Enter accept the rest of this rally · U undo
//
// Each action shows WiePlay's other likely answer (`model_alternative`), which one button takes.
// The sure ones are not in the walk (↑/↓ still reach them).
//
// Answers are ordinary tag changes through the tagging store and its outbox (offline queue,
// base versions, undo): accept sets review "confirmed", another skill, side, grade or time
// "corrected" (the server reads it the same way), reject deletes the tag with review
// "rejected" (the server keeps it as a negative). Plain TypeScript like the tagging controller.

import {
  acceptChange,
  actionChange,
  actionItems,
  actionProgress,
  GRADE_KEYS,
  isModelTag,
  nextOpenAction,
  openInRally,
  rejectChange,
  SKILL_KEYS,
  alternativeOf,
  isToCheckTag,
  type ActionProgress,
} from '../../lib/actions'
import type { Change } from '../../lib/outbox'
import type { Playhead } from '../../lib/playhead'
import { ralliesOf } from '../../lib/rallies'
import { LOOP_PRE_MS } from '../../lib/suggestions'
import type { TaggingStore } from '../../lib/taggingStore'
import type { Side, Tag } from '../../lib/types'
import { ownsKeys, type TagController } from './controller'

export type ActionNoticeKey =
  | 'accepted'
  | 'rejected'
  | 'skill'
  | 'sameSkill'
  | 'grade'
  | 'gradeCleared'
  | 'side'
  | 'moved'
  | 'acceptedRally'
  | 'alternative'
  | 'noneInRally'
  | 'undone'
  | 'redone'
  | 'nothingToUndo'
  | 'nothingToRedo'
  | 'nothingSelected'
  | 'unknownSkill'

export interface ActionNotice {
  id: number
  key: ActionNoticeKey
  tone: 'info' | 'error'
  params?: Record<string, string | number>
}

export interface ActionReviewState {
  /** The actions queue is on (inside the review mode). */
  active: boolean
  /** The action on screen; null when everything is checked (or nothing is to check). */
  currentId: string | null
  /** Digits typed for the player's number (Enter sets it). */
  number: string
  /** Actions seen to check in this session (also once rejected or checked), for the progress. */
  seen: ReadonlySet<string>
  /** WiePlay's actions rejected before this session (the tagging data no longer lists them). */
  rejectedBefore: number
  notice: ActionNotice | null
}

const EMPTY: ReadonlySet<string> = new Set()

export class ActionReviewController {
  private state: ActionReviewState
  private readonly listeners = new Set<() => void>()
  private noticeId = 0
  /** Where the review was before each of its undo steps (by store step id). */
  private readonly marks = new Map<number, string | null>()
  private readonly store: TaggingStore
  private readonly playhead: Playhead
  private readonly tag: TagController
  private readonly onLeave: () => void
  private unsubscribe: () => void = () => {}

  /** `onLeave`: Esc with nothing typed leaves the review altogether. */
  constructor(store: TaggingStore, playhead: Playhead, tag: TagController, { rejectedBefore = 0, onLeave = () => {} } = {}) {
    this.store = store
    this.playhead = playhead
    this.tag = tag
    this.onLeave = onLeave
    this.state = {
      active: false,
      currentId: null,
      number: '',
      seen: modelIds(store.getView().events, EMPTY),
      rejectedBefore,
      notice: null,
    }
  }

  /** Follows the tagging data: actions to check that show up count for the progress. */
  attach(): () => void {
    this.unsubscribe()
    this.unsubscribe = this.store.subscribe(() => {
      const seen = modelIds(this.store.getView().events, this.state.seen)
      if (seen !== this.state.seen) this.set({ seen })
      this.keepOneOnScreen()
    })
    return () => {
      this.unsubscribe()
      this.unsubscribe = () => {}
    }
  }

  /** A new run replaced WiePlay's actions: count afresh. */
  reset(rejectedBefore: number): void {
    this.marks.clear()
    this.set({ seen: modelIds(this.store.getView().events, EMPTY), rejectedBefore, currentId: null, number: '' })
    if (this.state.active) this.go(nextOpenAction(this.items(), null))
  }

  getState = (): ActionReviewState => this.state

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  private set(patch: Partial<ActionReviewState>): void {
    this.state = { ...this.state, ...patch }
    for (const listener of [...this.listeners]) listener()
  }

  private notify(key: ActionNoticeKey, params?: ActionNotice['params'], tone: ActionNotice['tone'] = 'info'): ActionNotice {
    this.noticeId += 1
    return { id: this.noticeId, key, tone, params }
  }

  // What is on screen

  items(): Tag[] {
    return actionItems(this.store.getView().events)
  }

  current(): Tag | null {
    const id = this.state.currentId
    return id === null ? null : (this.store.getView().events.find((e) => e.id === id) ?? null)
  }

  progress(): ActionProgress {
    return actionProgress(this.store.getView().events, this.state.seen, this.state.rejectedBefore)
  }

  /** The rally of the action on screen (by its link, else by time). */
  rallyOf(tag: Tag | null): { id: string; start_ms: number; end_ms: number } | null {
    if (!tag) return null
    const rallies = ralliesOf(this.store.getView().segments)
    return rallies.find((r) => r.id === tag.segment_id) ?? rallies.find((r) => r.start_ms - 2000 <= tag.t_ms && tag.t_ms <= r.end_ms) ?? null
  }

  /** The actions to check of the current action's rally ("accept the rest of this rally"). */
  openInCurrentRally(): Tag[] {
    return openInRally(this.store.getView().events, this.rallyOf(this.current()))
  }

  // Mode

  start = (): void => {
    const current = this.current()
    this.set({ active: true, notice: null, number: '' })
    this.go(current && this.items().some((t) => t.id === current.id) ? current : nextOpenAction(this.items(), null))
  }

  stop = (): void => {
    if (this.state.active) this.set({ active: false, number: '' })
  }

  /**
   * While actions are still to check one of them is on screen: when the one shown is gone from
   * the data (a reload, a new run), the next one to check from where the video is takes its place.
   */
  private keepOneOnScreen(): void {
    if (!this.state.active || this.current()) return
    const now = this.playhead.video ? Math.round(this.playhead.video.currentTime * 1000) : 0
    const next = nextOpenAction(this.items(), { id: '', t_ms: now })
    if (next) this.go(next, { play: !(this.playhead.video?.paused ?? true) })
  }

  /** Shows an action: the player jumps to 1 s before its contact and plays (the loop is useLoopPlayback). */
  private go(tag: Tag | null, { play = true } = {}): void {
    this.set({ currentId: tag?.id ?? null, number: '' })
    if (!tag) return
    this.tag.seek(tag.t_ms - LOOP_PRE_MS)
    if (play) this.playhead.play()
  }

  select = (id: string): void => {
    const tag = this.store.getView().events.find((e) => e.id === id)
    if (tag && isModelTag(tag)) this.go(tag)
  }

  /** ↑/↓: the previous or next action, answered or not. */
  move = (direction: -1 | 1): void => {
    const items = this.items()
    if (items.length === 0) return
    const index = items.findIndex((t) => t.id === this.state.currentId)
    const next = index === -1 ? items[direction > 0 ? 0 : items.length - 1] : items[Math.min(items.length - 1, Math.max(0, index + direction))]
    if (next && next.id !== this.state.currentId) this.go(next)
  }

  private advance(from: Tag): void {
    const next = nextOpenAction(this.items(), from)
    if (next) {
      this.go(next)
      return
    }
    this.set({ currentId: null, number: '' })
    this.playhead.video?.pause()
  }

  // Answers

  private commit(changes: readonly Change[]): void {
    if (changes.length === 0) return
    const before = this.state.currentId
    const step = this.store.commit(changes)
    this.marks.set(step, before)
    if (this.marks.size > 200) this.marks.delete(this.marks.keys().next().value as number)
  }

  private need(): Tag | null {
    const tag = this.current()
    if (!tag) this.set({ notice: this.notify('nothingSelected', undefined, 'error') })
    return tag
  }

  /** Enter: accept as it is, with the number typed (the player), and go on. */
  accept = (): void => {
    const tag = this.need()
    if (!tag) return
    const typed = this.state.number
    const patch = typed === '' ? {} : { number: Number(typed) }
    this.commit([acceptChange(tag, patch)])
    this.set({ notice: this.notify('accepted', { code: tag.code, time: tag.t_ms, number: typed }) })
    this.advance(tag)
  }

  /** X: not an action. */
  reject = (): void => {
    const tag = this.need()
    if (!tag) return
    this.commit([rejectChange(tag)])
    this.set({ notice: this.notify('rejected', { code: tag.code, time: tag.t_ms }) })
    this.advance(tag)
  }

  /** S R E A B D F: the skill. The same one again changes nothing. */
  setSkill = (code: string): void => {
    const tag = this.need()
    if (!tag) return
    if (!this.store.taxonomy?.skills.some((s) => s.code === code)) {
      this.set({ notice: this.notify('unknownSkill', { code }, 'error') })
      return
    }
    if (code === tag.code) {
      this.set({ notice: this.notify('sameSkill', { code }) })
      return
    }
    this.commit([actionChange(tag, { code })])
    this.set({ notice: this.notify('skill', { code }) })
  }

  /** # + ! - / =: the grade; the same one again takes it away. */
  setGrade = (grade: string): void => {
    const tag = this.need()
    if (!tag) return
    const attributes = { ...tag.attributes }
    const clearing = attributes.grade === grade
    if (clearing) delete attributes.grade
    else attributes.grade = grade
    this.commit([actionChange(tag, { attributes })])
    this.set({ notice: clearing ? this.notify('gradeCleared') : this.notify('grade', { grade }) })
  }

  /** Tab: the other side. */
  toggleSide = (): void => {
    const tag = this.need()
    if (!tag) return
    const side: Side = tag.side === 'us' ? 'them' : 'us'
    this.commit([actionChange(tag, { side })])
    this.set({ notice: this.notify('side', { side }) })
  }

  /** ←/→: one frame earlier or later; the time is then frame-precise. */
  nudge = (direction: -1 | 1): void => {
    const tag = this.need()
    if (!tag) return
    const step = this.tag.frameMs
    const t_ms = Math.round(Math.max(0, tag.t_ms + direction * step))
    if (t_ms === tag.t_ms) return
    const frame = Math.round(t_ms / step)
    this.commit([actionChange(tag, { t_ms, frame, time_precision: 'frame' })])
    this.set({ notice: this.notify('moved', { time: t_ms }) })
    // Show the new frame when paused; while playing the loop goes on around it.
    const playing = this.playhead.video ? !this.playhead.video.paused : false
    if (!playing) this.tag.seek(t_ms)
  }

  /** Digits: the player's number (two at most); Backspace takes one back. */
  typeDigit = (digit: string): void => {
    if (!this.current()) {
      this.set({ notice: this.notify('nothingSelected', undefined, 'error') })
      return
    }
    const number = (this.state.number + digit).slice(-2)
    this.set({ number })
  }

  backspace = (): void => {
    if (this.state.number) this.set({ number: this.state.number.slice(0, -1) })
  }

  /** Shift+Enter: accept every action to check of this rally as it is. */
  acceptRally = (): void => {
    const tag = this.need()
    if (!tag) return
    const open = this.openInCurrentRally()
    if (open.length === 0) {
      this.set({ notice: this.notify('noneInRally') })
      return
    }
    const typed = this.state.number
    this.commit(open.map((t) => acceptChange(t, t.id === tag.id && typed !== '' ? { number: Number(typed) } : {})))
    this.set({ notice: this.notify('acceptedRally', { count: open.length }) })
    const last = open.reduce((a, b) => (b.t_ms > a.t_ms ? b : a), tag)
    this.advance(last)
  }

  /** WiePlay's other answer (`model_alternative`), taken: another skill and side, or no contact (rejected). */
  takeAlternative = (): void => {
    const tag = this.need()
    if (!tag) return
    const other = alternativeOf(tag)
    if (other === null) return
    if (other === 'none') {
      this.reject()
      return
    }
    this.commit([actionChange(tag, { code: other.code, side: other.side, review: 'corrected' })])
    this.set({ notice: this.notify('alternative', { code: other.code, side: other.side }) })
    this.advance(tag)
  }

  /** U (and Ctrl+Z here): takes back the last change and goes back to where it was made. */
  undo = (): void => {
    const step = this.store.topStep()
    if (!this.store.undo()) {
      this.set({ notice: this.notify('nothingToUndo', undefined, 'error') })
      return
    }
    const mark = step === null ? undefined : this.marks.get(step)
    if (mark !== undefined) {
      const tag = mark ? this.store.getView().events.find((e) => e.id === mark) : undefined
      this.go(tag ?? this.current(), { play: !(this.playhead.video?.paused ?? true) })
    }
    this.set({ notice: this.notify('undone') })
  }

  redo = (): void => {
    this.set({ notice: this.notify(this.store.redo() ? 'redone' : 'nothingToRedo') })
  }

  // Keyboard

  onKeyDown = (event: KeyboardEvent): void => {
    if (event.defaultPrevented || event.isComposing || ownsKeys(event.target)) return
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
      this.nudge(key === 'ArrowLeft' ? -1 : 1)
      return true
    }
    if (altKey) return false
    if (/^[0-9]$/.test(key)) {
      this.typeDigit(key)
      return true
    }
    if (GRADE_KEYS.includes(key)) {
      this.setGrade(key)
      return true
    }
    switch (key) {
      case ' ':
        this.tag.togglePlay()
        return true
      case 'Enter':
        if (shiftKey) this.acceptRally()
        else this.accept()
        return true
      case 'Tab':
        this.toggleSide()
        return true
      case 'Backspace':
        this.backspace()
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
        if (this.state.number) this.set({ number: '' })
        else this.onLeave()
        return true
    }
    const letter = key.toUpperCase()
    if (letter === 'X') {
      this.reject()
      return true
    }
    if (letter === 'U') {
      this.undo()
      return true
    }
    if (key.length === 1 && SKILL_KEYS.includes(letter)) {
      this.setSkill(letter)
      return true
    }
    return false
  }
}

function pressable(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest('button, a[href], [role="button"], [role="switch"], summary') !== null
}

/** The ids of the actions to check, added to `seen` (the same set when nothing is new). */
function modelIds(events: readonly Tag[], seen: ReadonlySet<string>): ReadonlySet<string> {
  let out: Set<string> | null = null
  for (const e of events) {
    if (!isToCheckTag(e) || seen.has(e.id)) continue
    out ??= new Set(seen)
    out.add(e.id)
  }
  return out ?? seen
}
