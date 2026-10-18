// The regrade queue (docs/PLAN.md §9.7, Hudl-style): after a quick first tagging pass, go through
// the tags of one skill (ungraded only or all, one player, one set) and grade them fast. Each
// tag's moment plays in a loop (its clip window: the tag's own, else the skill's pre/post roll),
// a key sets the grade and the list moves on by itself:
//
//   1–6 (or # + ! - / =) the grade, in the order of the grades table · 0 no grade
//   S or Enter skip · ← ↑ previous · → ↓ next · U undo · Space play/pause · Esc back to the setup
//
// Grades are ordinary tag changes through the tagging store and its outbox (offline queue, base
// versions, conflicts shown like everywhere else, undo). The queue is fixed when the pass starts,
// so a tag graded in an "ungraded only" pass stays in the list (to go back to it). It never ends
// a rally or sets who won it: winners stay the coach's call in the tagging mode. Plain TypeScript
// like the other controllers.

import { actionChange } from '../../lib/actions'
import { playerKey } from '../../lib/condensed'
import { factTags } from '../../lib/facts'
import type { Playhead } from '../../lib/playhead'
import { periodAt } from '../../lib/rallies'
import { tagWindow } from '../../lib/stats'
import type { TaggingStore } from '../../lib/taggingStore'
import type { Tag } from '../../lib/types'
import { ownsKeys, type TagController } from './controller'

export type RegradeWhich = 'ungraded' | 'all'

export interface RegradeSetup {
  /** The skill's code (volleyball: S R E A B D F). */
  code: string
  which: RegradeWhich
  /** `us:7`, `them:12` (src/lib/condensed.ts playerKey); null: everyone. */
  player: string | null
  /** A set's id; null: every set. */
  set: string | null
}

export type RegradeNoticeKey = 'graded' | 'cleared' | 'kept' | 'skipped' | 'undone' | 'redone' | 'nothingToUndo' | 'nothingToRedo' | 'empty'

export interface RegradeNotice {
  id: number
  key: RegradeNoticeKey
  tone: 'info' | 'error'
  params?: Record<string, string | number>
}

export interface RegradeState {
  /** The regrade mode is on. */
  active: boolean
  /** A pass is running (else the setup shows). */
  running: boolean
  setup: RegradeSetup
  /** The tags of the pass, fixed when it starts. */
  queue: readonly string[]
  /** Where the pass is; queue.length when it is through. */
  index: number
  /** Tags graded in this pass. */
  graded: ReadonlySet<string>
  notice: RegradeNotice | null
}

const NONE: ReadonlySet<string> = new Set()

/** The tags a setup goes through, in time order: the facts only (no open suggestions). */
export function regradeTags(tags: readonly Tag[], periods: Parameters<typeof periodAt>[0], setup: RegradeSetup): Tag[] {
  return factTags(tags)
    .filter((tag) => {
      if (tag.code !== setup.code) return false
      if (setup.which === 'ungraded' && tag.attributes.grade) return false
      if (setup.player !== null && playerKey(tag.side, tag.number) !== setup.player) return false
      if (setup.set !== null && periodAt(periods, tag.t_ms)?.id !== setup.set) return false
      return true
    })
    .sort((a, b) => a.t_ms - b.t_ms || a.id.localeCompare(b.id))
}

export class RegradeController {
  private state: RegradeState
  private readonly listeners = new Set<() => void>()
  private noticeId = 0
  /** Where the pass was before each of its undo steps (by store step id). */
  private readonly marks = new Map<number, number>()
  private readonly store: TaggingStore
  private readonly playhead: Playhead
  private readonly tag: TagController
  /** Esc on the setup: leaves the mode (setLeave: the workspace decides where to). */
  private onLeave: () => void = () => this.stop()

  constructor(store: TaggingStore, playhead: Playhead, tag: TagController) {
    this.store = store
    this.playhead = playhead
    this.tag = tag
    // Attacks by default: the skill whose grade makes the most of the numbers.
    const codes = store.taxonomy?.skills.map((s) => s.code) ?? []
    const code = codes.includes('A') ? 'A' : (codes[0] ?? 'A')
    this.state = { active: false, running: false, setup: { code, which: 'ungraded', player: null, set: null }, queue: [], index: 0, graded: NONE, notice: null }
  }

  getState = (): RegradeState => this.state

  setLeave(leave: () => void): void {
    this.onLeave = leave
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  private set(patch: Partial<RegradeState>): void {
    this.state = { ...this.state, ...patch }
    for (const listener of [...this.listeners]) listener()
  }

  private notify(key: RegradeNoticeKey, params?: RegradeNotice['params'], tone: RegradeNotice['tone'] = 'info'): RegradeNotice {
    this.noticeId += 1
    return { id: this.noticeId, key, tone, params }
  }

  // What is on screen

  /** The tags the setup would go through now (before the pass starts). */
  candidates(): Tag[] {
    const view = this.store.getView()
    return regradeTags(view.events, view.periods, this.state.setup)
  }

  /** The tag on screen, or null (setup, the end, or it was deleted meanwhile). */
  current(): Tag | null {
    if (!this.state.running) return null
    const id = this.state.queue[this.state.index]
    return id === undefined ? null : (this.store.getView().events.find((e) => e.id === id) ?? null)
  }

  /** The window of the tag on screen: what plays (in a loop). */
  window(): { start_ms: number; end_ms: number } | null {
    const tag = this.current()
    return tag ? tagWindow(tag, this.store.taxonomy, this.tag.duration()) : null
  }

  // Mode

  start = (): void => {
    if (!this.state.active) this.set({ active: true, notice: null })
  }

  stop = (): void => {
    if (this.state.active) this.set({ active: false, running: false })
    this.playhead.video?.pause()
  }

  setSetup = (patch: Partial<RegradeSetup>): void => {
    this.set({ setup: { ...this.state.setup, ...patch } })
  }

  /** Starts a pass over the tags the setup names. */
  begin = (): void => {
    const queue = this.candidates().map((t) => t.id)
    if (queue.length === 0) {
      this.set({ notice: this.notify('empty', undefined, 'error') })
      return
    }
    this.marks.clear()
    this.set({ running: true, queue, index: 0, graded: NONE, notice: null })
    this.go(this.liveFrom(0, 1))
  }

  /** Back to the setup (Esc during a pass). */
  setupAgain = (): void => {
    this.set({ running: false, notice: null })
    this.playhead.video?.pause()
  }

  /** The first position from `index` on (in `direction`) whose tag still exists; else the end. */
  private liveFrom(index: number, direction: -1 | 1): number {
    const events = this.store.getView().events
    const { queue } = this.state
    for (let i = index; i >= 0 && i < queue.length; i += direction) {
      if (events.some((e) => e.id === queue[i])) return i
    }
    return direction > 0 ? queue.length : Math.max(0, Math.min(index, queue.length))
  }

  /** Shows the tag at a position: the player jumps to the start of its window and plays. */
  private go(index: number, { play = true } = {}): void {
    this.set({ index })
    const range = this.window()
    if (!range) {
      this.playhead.video?.pause()
      return
    }
    this.tag.seek(range.start_ms)
    if (play) this.playhead.play()
  }

  /** ←/→: the previous or next tag of the pass. */
  move = (direction: -1 | 1): void => {
    if (!this.state.running) return
    const from = this.state.index + direction
    if (from < 0) return
    const next = this.liveFrom(from, direction)
    if (direction < 0 && !this.store.getView().events.some((e) => e.id === this.state.queue[next])) return
    this.go(next)
  }

  /** S or Enter: on to the next one, unchanged. */
  skip = (): void => {
    const tag = this.current()
    if (!tag) return
    this.set({ notice: this.notify('skipped', { code: tag.code, time: tag.t_ms }) })
    this.go(this.liveFrom(this.state.index + 1, 1))
  }

  /** A grade (null: none) for the tag on screen, then the next one. The same grade again keeps it. */
  grade = (grade: string | null): void => {
    const tag = this.current()
    if (!tag) return
    const before = tag.attributes.grade ?? null
    const graded = new Set(this.state.graded)
    graded.add(tag.id)
    if (before === grade) {
      this.set({ graded, notice: this.notify('kept', { code: tag.code, time: tag.t_ms, grade: grade ?? '' }) })
    } else {
      const attributes = { ...tag.attributes }
      if (grade === null) delete attributes.grade
      else attributes.grade = grade
      const step = this.store.commit([actionChange(tag, { attributes })])
      this.marks.set(step, this.state.index)
      if (this.marks.size > 200) this.marks.delete(this.marks.keys().next().value as number)
      const key = grade === null ? 'cleared' : 'graded'
      this.set({ graded, notice: this.notify(key, { code: tag.code, time: tag.t_ms, grade: grade ?? '' }) })
    }
    this.go(this.liveFrom(this.state.index + 1, 1))
  }

  /** U (and Ctrl+Z here): takes back the last grade and goes back to its tag. */
  undo = (): void => {
    const step = this.store.topStep()
    if (!this.store.undo()) {
      this.set({ notice: this.notify('nothingToUndo', undefined, 'error') })
      return
    }
    const mark = step === null ? undefined : this.marks.get(step)
    if (mark !== undefined && this.state.running) {
      const id = this.state.queue[mark]
      const graded = new Set(this.state.graded)
      if (id) graded.delete(id)
      this.set({ graded })
      this.go(mark, { play: !(this.playhead.video?.paused ?? true) })
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
    if (altKey) return false
    switch (key) {
      case ' ':
        this.tag.togglePlay()
        return true
      case ',':
      case '.':
        this.tag.speed(key === ',' ? -1 : 1)
        return true
      case '?':
        this.tag.toggleCheatsheet(true)
        return true
      case 'Escape':
        if (this.state.running) this.setupAgain()
        else this.onLeave()
        return true
    }
    if (!this.state.running) {
      if (key === 'Enter') {
        this.begin()
        return true
      }
      return false
    }
    const grades = this.store.taxonomy?.grades ?? []
    if (/^[1-9]$/.test(key)) {
      const grade = grades[Number(key) - 1]
      if (grade === undefined) return false
      this.grade(grade)
      return true
    }
    if (key === '0') {
      this.grade(null)
      return true
    }
    if (grades.includes(key)) {
      this.grade(key)
      return true
    }
    switch (key) {
      case 'Enter':
        this.skip()
        return true
      case 'ArrowLeft':
      case 'ArrowUp':
        this.move(-1)
        return true
      case 'ArrowRight':
      case 'ArrowDown':
        this.move(1)
        return true
    }
    const letter = key.toUpperCase()
    if (letter === 'S') {
      this.skip()
      return true
    }
    if (letter === 'U') {
      this.undo()
      return true
    }
    return false
  }
}

function pressable(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest('button, a[href], [role="button"], [role="switch"], summary') !== null
}
