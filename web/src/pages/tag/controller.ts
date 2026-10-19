// The tagging workspace's keyboard and actions (docs/PLAN.md §9.2–§9.4). Plain TypeScript so
// that a burst of keystrokes always sees the state the previous key left (no stale closures);
// React renders a snapshot of it through subscribe/getState.

import { clipRange } from '../../lib/clips'
import { factTags } from '../../lib/facts'
import { formatEntry, isEntryChar, parseEntry, toggleSide, type EntryError } from '../../lib/grammar'
import type { Playhead } from '../../lib/playhead'
import { proposeByKey, proposeByTag, rallyStartForServe, ralliesOf, type RallyProposal } from '../../lib/rallies'
import { sheetServer, type SheetCourt } from '../../lib/sheetCourt'
import { eventToIn, newId, periodToIn, segmentToIn, type TaggingStore } from '../../lib/taggingStore'
import type { EventIn, PeriodIn, Review, Segment, SegmentIn, SegmentKind, Side, Tag, Taxonomy } from '../../lib/types'
import type { Change } from '../../lib/outbox'

export const RATES = [0.25, 0.5, 0.75, 1, 1.25, 1.5, 2] as const
export const SEGMENT_LENGTH_MS: Partial<Record<SegmentKind, number>> = { timeout: 30_000, set_break: 180_000 }
/** Basketball's break between quarters (B): 2 minutes, editable like any break. */
export const QUARTER_BREAK_MS = 120_000
const MAX_CLIP_MS = 60_000

export type NoticeKey =
  | 'saved'
  | 'invalid'
  | 'rallyStarted'
  | 'rallyEnded'
  | 'rallyKept'
  | 'segmentAdded'
  | 'cannotAdd'
  | 'undone'
  | 'redone'
  | 'nothingToUndo'
  | 'nothingToRedo'
  | 'noSelection'
  | 'clipIn'
  | 'clipOut'
  | 'clipSet'
  | 'clipOrder'
  | 'noClip'
  | 'snapped'
  | 'deleted'

export interface Notice {
  id: number
  key: NoticeKey
  tone: 'info' | 'error'
  params?: Record<string, string | number>
  /** Show an Undo button next to it. */
  undo?: boolean
}

export interface ClipRequest {
  start_ms: number
  end_ms: number
  /** The selected tag, when it lies inside the clip. */
  event_id: string | null
}

export interface UiState {
  /** The entry being typed and the playhead at its first keystroke. */
  entry: { text: string; t_ms: number } | null
  /** Ending the open rally, waiting for Enter, u/t or Esc. */
  proposal: RallyProposal | null
  selectedId: string | null
  editingId: string | null
  segmentId: string | null
  clipIn: number | null
  clipOut: number | null
  /** C was pressed: the clip to save (the dialog asks for its title). */
  clipRequest: ClipRequest | null
  skip: boolean
  cheatsheet: boolean
  notice: Notice | null
}

const INITIAL: UiState = {
  entry: null,
  proposal: null,
  selectedId: null,
  editingId: null,
  segmentId: null,
  clipIn: null,
  clipOut: null,
  clipRequest: null,
  skip: false,
  cheatsheet: false,
  notice: null,
}

/** Keys typed into these keep their own meaning. */
export function ownsKeys(target: EventTarget | null): boolean {
  return (
    target instanceof Element &&
    target.closest('input, textarea, select, [contenteditable="true"], [role="dialog"], [role="alertdialog"]') !== null
  )
}

/**
 * The review state a segment gets with an edit: a model's proposal whose boundaries, kind or
 * point change is corrected; anything else keeps its state.
 */
export function reviewAfterEdit(before: Segment, after: SegmentIn): Review {
  if (before.source !== 'model') return after.review ?? before.review
  const edited =
    before.kind !== after.kind ||
    before.start_ms !== after.start_ms ||
    before.end_ms !== after.end_ms ||
    (before.won_by ?? null) !== (after.won_by ?? null)
  return edited ? 'corrected' : (after.review ?? before.review)
}

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value))
}

export interface ControllerOptions {
  /** From the API; the element's own duration wins once it is known. */
  duration_ms: number | null
}

export class TagController {
  private ui: UiState = INITIAL
  private readonly listeners = new Set<() => void>()
  private fps = 25
  private noticeId = 0
  private readonly store: TaggingStore
  private readonly playhead: Playhead
  private readonly taxonomy: Taxonomy
  private readonly options: ControllerOptions
  /** The official scoresheet's lineups per rally (docs/PLAN.md §10.5): a serve's server. */
  private court: SheetCourt | undefined = undefined

  constructor(store: TaggingStore, playhead: Playhead, taxonomy: Taxonomy, options: ControllerOptions) {
    this.store = store
    this.playhead = playhead
    this.taxonomy = taxonomy
    this.options = options
  }

  getState = (): UiState => this.ui

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  setFps = (fps: number): void => {
    if (fps > 0) this.fps = fps
  }

  /** The scoresheet's lineups, once loaded: a serve typed without a number gets its server. */
  setSheetCourt = (court: SheetCourt | undefined): void => {
    this.court = court
  }

  /** Volleyball is played in rallies (R, the point, skip dead time); basketball's score comes from its shots. */
  get playsRallies(): boolean {
    return this.taxonomy.sport !== 'basketball'
  }

  private set(patch: Partial<UiState>): void {
    this.ui = { ...this.ui, ...patch }
    for (const listener of [...this.listeners]) listener()
  }

  /** Applies and queues changes; an Undo button shown for an earlier change no longer fits. */
  private commit(changes: readonly Change[], openRally?: { id: string; start_ms: number } | null): void {
    this.store.commit(changes, openRally)
    if (this.ui.notice?.undo) this.set({ notice: { ...this.ui.notice, undo: false } })
  }

  private notify(key: NoticeKey, params?: Notice['params'], extra: Partial<Notice> = {}): Notice {
    this.noticeId += 1
    return { id: this.noticeId, key, tone: 'info', params, ...extra }
  }

  // Time

  private get video(): HTMLVideoElement | null {
    return this.playhead.video
  }

  now(): number {
    return Math.round((this.video?.currentTime ?? 0) * 1000)
  }

  duration(): number | null {
    const el = this.video
    if (el && Number.isFinite(el.duration) && el.duration > 0) return Math.round(el.duration * 1000)
    return this.options.duration_ms
  }

  seek(ms: number): void {
    const el = this.video
    if (!el) return
    const end = this.duration()
    el.currentTime = clamp(ms, 0, end ?? Infinity) / 1000
    this.playhead.sync()
  }

  togglePlay = (): void => {
    const el = this.video
    if (!el) return
    if (el.paused) {
      const playing = el.play() as Promise<void> | undefined
      playing?.catch?.(() => {})
    } else {
      el.pause()
    }
    this.playhead.sync()
  }

  /** ←/→: one frame when paused, 2 s while playing. */
  step = (direction: -1 | 1): void => {
    const el = this.video
    if (!el) return
    if (el.paused) this.seek((el.currentTime + direction / this.fps) * 1000)
    else this.seek(this.now() + direction * 2000)
  }

  jump = (ms: number): void => this.seek(this.now() + ms)

  /** Ctrl+←/→: start of the previous or next rally. */
  rally = (direction: -1 | 1): void => {
    const now = this.now()
    const rallies = ralliesOf(this.store.getView().segments)
    const target =
      direction < 0 ? rallies.filter((r) => r.start_ms < now - 500).at(-1) : rallies.find((r) => r.start_ms > now + 100)
    if (target) this.seek(target.start_ms)
  }

  speed = (direction: -1 | 1): void => {
    const el = this.video
    if (!el) return
    const current = RATES.reduce((best, rate, i) =>
      Math.abs(rate - el.playbackRate) < Math.abs((RATES[best] as number) - el.playbackRate) ? i : best,
    0)
    el.playbackRate = RATES[clamp(current + direction, 0, RATES.length - 1)] as number
    this.playhead.sync()
  }

  toggleSkip = (on = !this.ui.skip): void => this.set({ skip: on })

  toggleCheatsheet = (open = !this.ui.cheatsheet): void => this.set({ cheatsheet: open })

  // Entry

  startEntry = (text: string): void => {
    this.set({ entry: { text, t_ms: this.now() }, editingId: null })
  }

  private type(char: string): void {
    if (!this.ui.entry) return
    this.set({ entry: { ...this.ui.entry, text: this.ui.entry.text + char } })
  }

  private backspace(): void {
    const entry = this.ui.entry
    if (!entry) return
    const text = entry.text.slice(0, -1)
    this.set({ entry: text ? { ...entry, text } : null })
  }

  private switchSide(): void {
    const entry = this.ui.entry
    if (!entry) return
    const text = toggleSide(entry.text)
    this.set({ entry: text ? { ...entry, text } : null })
  }

  cancelEntry = (): void => this.set({ entry: null })

  /** Enter: saves the entry as a tag at the first keystroke's time minus the skill's lead. */
  saveEntry = (): boolean => {
    const entry = this.ui.entry
    if (!entry) return false
    const view = this.store.getView()
    const parsed = parseEntry(entry.text, this.taxonomy, view.roster)
    if (!parsed.complete || !parsed.skill) {
      const reason: EntryError | 'incomplete' = parsed.error ?? 'incomplete'
      this.set({ notice: this.notify('invalid', { reason, entry: entry.text, code: parsed.skillCode ?? '' }, { tone: 'error' }) })
      return false
    }
    const skill = parsed.skill
    const end = this.duration()
    const t_ms = clamp(entry.t_ms - skill.lead_ms, 0, end ?? Infinity)

    let open = view.openRally
    let opened: { id: string; start_ms: number } | undefined
    if (skill.starts_rally && !open) {
      opened = { id: newId(), start_ms: rallyStartForServe(t_ms) }
      open = opened
    }
    const attributes: EventIn['attributes'] = {}
    if (parsed.grade) attributes.grade = parsed.grade
    if (parsed.zoneFrom !== null) attributes.zone_from = parsed.zoneFrom
    if (parsed.zoneTo !== null) attributes.zone_to = parsed.zoneTo
    // A serve without a number in a rally the scoresheet was lined up with: its server (the
    // number the coach types always wins; editable like any tag's).
    const number =
      parsed.number === null && skill.code === 'S' && this.playsRallies ? sheetServer(this.court, view.segments, t_ms, parsed.side) : parsed.number
    const event: EventIn = {
      id: newId(),
      base_version: 0,
      code: skill.code,
      t_ms,
      frame: null,
      time_precision: 'live',
      side: parsed.side,
      number,
      attributes,
      // The server links tags to rallies by time (the open rally exists only here until it ends).
      segment_id: null,
    }
    this.commit([{ kind: 'event', payload: event }], opened)
    const proposal = proposeByTag(open, event, this.taxonomy)
    this.set({
      entry: null,
      selectedId: event.id,
      proposal: proposal ?? this.ui.proposal,
      notice: this.notify('saved', { entry: formatEntry({ ...event, number: event.number ?? null }) }),
    })
    return true
  }

  // Rallies

  /** R: start a rally at the playhead, or propose ending the open one. */
  rallyKey = (): void => {
    const view = this.store.getView()
    if (view.openRally) {
      // A suggested action's grade is no fact: the winner comes from the coach's tags.
      this.set({ proposal: proposeByKey(view.openRally, this.now(), factTags(view.events), this.taxonomy) })
      return
    }
    const start_ms = this.now()
    this.commit([], { id: newId(), start_ms })
    this.set({ notice: this.notify('rallyStarted', { time: start_ms }) })
  }

  setWinner = (winner: Side | null): void => {
    if (this.ui.proposal) this.set({ proposal: { ...this.ui.proposal, winner } })
  }

  confirmProposal = (): void => {
    const proposal = this.ui.proposal
    if (!proposal) return
    const segment: SegmentIn = {
      id: proposal.rally.id,
      base_version: 0,
      kind: 'rally',
      start_ms: proposal.rally.start_ms,
      end_ms: proposal.end_ms,
      won_by: proposal.winner,
    }
    this.commit([{ kind: 'segment', payload: segment }], null)
    const saved = this.store.getView().segments.find((s) => s.id === segment.id)
    const score =
      saved && saved.score_us_after !== null && saved.score_them_after !== null
        ? `${saved.score_us_after}:${saved.score_them_after}`
        : ''
    this.set({ proposal: null, segmentId: null, notice: this.notify('rallyEnded', { score }) })
  }

  /** Esc on a proposal: the rally stays open. */
  keepRallyOpen = (): void => {
    if (!this.ui.proposal) return
    this.set({ proposal: null, notice: this.notify('rallyKept') })
  }

  /** T / B: a timeout or set break at the playhead (30 s / 3 min, a quarter break 2 min; editable afterwards). */
  addSegment = (kind: SegmentKind): void => {
    const start_ms = this.now()
    const end = this.duration()
    const length = kind === 'set_break' && !this.playsRallies ? QUARTER_BREAK_MS : (SEGMENT_LENGTH_MS[kind] ?? 10_000)
    let end_ms = start_ms + length
    if (end !== null) end_ms = Math.min(end_ms, end)
    if (end_ms <= start_ms) {
      this.set({ notice: this.notify('cannotAdd', undefined, { tone: 'error' }) })
      return
    }
    const id = newId()
    this.commit([{ kind: 'segment', payload: { id, base_version: 0, kind, start_ms, end_ms } }])
    this.set({ segmentId: id, notice: this.notify('segmentAdded', { kind }) })
  }

  selectSegment = (id: string | null): void => this.set({ segmentId: id })

  /** Edits a segment; editing a model's proposal makes it a correction (PLAN §11). */
  updateSegment = (id: string, patch: Partial<SegmentIn>): boolean => {
    const segment = this.store.getView().segments.find((s) => s.id === id)
    if (!segment) return false
    const payload = { ...segmentToIn(segment), ...patch }
    if (payload.end_ms <= payload.start_ms) return false
    if (patch.review === undefined) payload.review = reviewAfterEdit(segment, payload)
    this.commit([{ kind: 'segment', payload }])
    return true
  }

  /** Deletes a segment; a model's proposal is rejected (the server keeps it as a negative). */
  deleteSegment = (id: string): void => {
    const segment = this.store.getView().segments.find((s) => s.id === id)
    if (!segment) return
    const review = segment.source === 'model' ? 'rejected' : segment.review
    this.commit([{ kind: 'segment', payload: { ...segmentToIn(segment), review, deleted: true } }])
    this.set({ segmentId: null })
  }

  /** Accepts a model's proposal as it is. */
  acceptSegment = (id: string): void => {
    const segment = this.store.getView().segments.find((s) => s.id === id)
    if (!segment || segment.source !== 'model' || segment.review !== 'unreviewed') return
    this.commit([{ kind: 'segment', payload: { ...segmentToIn(segment), review: 'confirmed' } }])
  }

  /** One frame of the rendition playing, in ms. */
  get frameMs(): number {
    return 1000 / this.fps
  }

  // Sets

  /** Saves a set; a new set also ends the one before it if that one was left open. */
  savePeriod = (period: PeriodIn): void => {
    const view = this.store.getView()
    const changes: Change[] = [{ kind: 'period', payload: period }]
    const isNew = !view.periods.some((p) => p.id === period.id)
    const before = view.periods.find((p) => p.no === period.no - 1)
    if (isNew && before && before.end_ms === null && before.start_ms < period.start_ms) {
      changes.unshift({ kind: 'period', payload: { ...periodToIn(before), end_ms: period.start_ms } })
    }
    this.commit(changes)
  }

  endPeriod = (id: string): void => {
    const period = this.store.getView().periods.find((p) => p.id === id)
    if (!period) return
    const end_ms = this.now()
    if (end_ms <= period.start_ms) return
    this.commit([{ kind: 'period', payload: { ...periodToIn(period), end_ms } }])
  }

  /** Deletes a set; a model's proposal is rejected (the server keeps it as a negative). */
  deletePeriod = (id: string): void => {
    const period = this.store.getView().periods.find((p) => p.id === id)
    if (!period) return
    const review = period.source === 'model' ? 'rejected' : period.review
    this.commit([{ kind: 'period', payload: { ...periodToIn(period), review, deleted: true } }])
  }

  /**
   * Changes a set as it is (first serve, our side, starting rotation; a model's set that a coach
   * edits is marked corrected by the server, which also scores the match again).
   */
  updatePeriod = (id: string, patch: Partial<PeriodIn>): void => {
    const period = this.store.getView().periods.find((p) => p.id === id)
    if (period) this.commit([{ kind: 'period', payload: { ...periodToIn(period), ...patch } }])
  }

  /** Accepts a set WiePlay suggested as it is. */
  acceptPeriod = (id: string): void => {
    const period = this.store.getView().periods.find((p) => p.id === id)
    if (!period || period.source !== 'model' || period.review !== 'unreviewed') return
    this.commit([{ kind: 'period', payload: { ...periodToIn(period), review: 'confirmed' } }])
  }

  // Tags

  private tag(id: string | null): Tag | null {
    if (id === null) return null
    return this.store.getView().events.find((e) => e.id === id) ?? null
  }

  select = (id: string | null, { seek = false } = {}): void => {
    const tag = this.tag(id)
    this.set({ selectedId: tag?.id ?? null })
    if (tag && seek) this.seek(tag.t_ms)
  }

  private selectRelative(direction: -1 | 1): void {
    const events = this.store.getView().events
    if (events.length === 0) return
    const index = events.findIndex((e) => e.id === this.ui.selectedId)
    let next: number
    if (index === -1) {
      const now = this.now()
      const after = events.findIndex((e) => e.t_ms > now)
      next = direction > 0 ? (after === -1 ? events.length - 1 : after) : (after === -1 ? events.length : after) - 1
    } else {
      next = index + direction
    }
    const tag = events[clamp(next, 0, events.length - 1)]
    if (tag) this.set({ selectedId: tag.id })
  }

  edit = (id: string | null): void => this.set({ editingId: id, selectedId: id ?? this.ui.selectedId })

  updateTag = (id: string, patch: Partial<EventIn>): void => {
    const tag = this.tag(id)
    if (!tag) return
    this.commit([{ kind: 'event', payload: { ...eventToIn(tag), ...patch } }])
  }

  deleteTag = (id: string): void => {
    const tag = this.tag(id)
    if (!tag) return
    this.commit([{ kind: 'event', payload: { ...eventToIn(tag), deleted: true } }])
    this.set({
      selectedId: this.ui.selectedId === id ? null : this.ui.selectedId,
      editingId: this.ui.editingId === id ? null : this.ui.editingId,
      notice: this.notify('deleted', { entry: formatEntry(tag) }, { undo: true }),
    })
  }

  /** G: to the selected tag. */
  gotoSelected = (): void => {
    const tag = this.tag(this.ui.selectedId)
    if (!tag) this.set({ notice: this.notify('noSelection', undefined, { tone: 'error' }) })
    else this.seek(tag.t_ms)
  }

  /** F: the selected tag moves to the frame on screen. */
  snapSelected = (): void => {
    const tag = this.tag(this.ui.selectedId)
    if (!tag) {
      this.set({ notice: this.notify('noSelection', undefined, { tone: 'error' }) })
      return
    }
    const seconds = this.video?.currentTime ?? 0
    this.updateTag(tag.id, { t_ms: Math.round(seconds * 1000), frame: Math.round(seconds * this.fps), time_precision: 'frame' })
    this.set({ notice: this.notify('snapped', { entry: formatEntry(tag) }) })
  }

  /** C: the marked in and out points (one may come from the playhead) become a clip to save. */
  saveClip = (): void => {
    const { clipIn, clipOut } = this.ui
    if (clipIn === null && clipOut === null) {
      this.set({ notice: this.notify('noClip', undefined, { tone: 'error' }) })
      return
    }
    const range = clipRange(clipIn, clipOut, this.now(), this.duration())
    if (!range) {
      this.set({ notice: this.notify('clipOrder', undefined, { tone: 'error' }) })
      return
    }
    const tag = this.tag(this.ui.selectedId)
    const inside = tag !== null && tag.t_ms >= range.start_ms && tag.t_ms <= range.end_ms
    this.set({ clipRequest: { ...range, event_id: inside ? tag.id : null } })
  }

  /** The clip dialog closed; a saved clip clears the marks. */
  closeClip = (saved: boolean): void => {
    this.set(saved ? { clipRequest: null, clipIn: null, clipOut: null } : { clipRequest: null })
  }

  markClip = (which: 'in' | 'out'): void => {
    const now = this.now()
    if (which === 'in') this.set({ clipIn: now, notice: this.notify('clipIn', { time: now }) })
    else this.set({ clipOut: now, notice: this.notify('clipOut', { time: now }) })
  }

  /** C: the marked in/out points become the selected tag's clip window. */
  applyClip = (): void => {
    const tag = this.tag(this.ui.selectedId)
    if (!tag) {
      this.set({ notice: this.notify('noSelection', undefined, { tone: 'error' }) })
      return
    }
    const { clipIn, clipOut } = this.ui
    if (clipIn === null && clipOut === null) {
      this.set({ notice: this.notify('noClip', undefined, { tone: 'error' }) })
      return
    }
    this.updateTag(tag.id, {
      clip_pre_ms: clipIn === null ? tag.clip_pre_ms : clamp(tag.t_ms - clipIn, 0, MAX_CLIP_MS),
      clip_post_ms: clipOut === null ? tag.clip_post_ms : clamp(clipOut - tag.t_ms, 0, MAX_CLIP_MS),
    })
    this.set({ clipIn: null, clipOut: null, notice: this.notify('clipSet', { entry: formatEntry(tag) }) })
  }

  undo = (): void => {
    const done = this.store.undo()
    this.set({ proposal: null, editingId: null, notice: this.notify(done ? 'undone' : 'nothingToUndo') })
  }

  redo = (): void => {
    const done = this.store.redo()
    this.set({ proposal: null, editingId: null, notice: this.notify(done ? 'redone' : 'nothingToRedo') })
  }

  // Keyboard

  onKeyDown = (event: KeyboardEvent): void => {
    if (event.defaultPrevented || event.isComposing || ownsKeys(event.target)) return
    if (this.handle(event)) {
      event.preventDefault()
      event.stopPropagation()
    }
  }

  private handle(event: KeyboardEvent): boolean {
    const { key, shiftKey } = event
    const mod = event.ctrlKey || event.metaKey
    if (event.altKey) return false
    const ui = this.ui

    if (mod) {
      if (key === 'z' || key === 'Z') {
        if (shiftKey) this.redo()
        else this.undo()
        return true
      }
      if (key === 'y' || key === 'Y') {
        this.redo()
        return true
      }
      if (key === 'ArrowLeft' || key === 'ArrowRight') {
        this.rally(key === 'ArrowLeft' ? -1 : 1)
        return true
      }
      return false
    }

    if (ui.cheatsheet) {
      if (key === 'Escape' || key === '?') {
        this.toggleCheatsheet(false)
        return true
      }
      return false
    }

    // Transport works in every mode.
    if (key === ' ') {
      this.togglePlay()
      return true
    }
    if (key === 'ArrowLeft' || key === 'ArrowRight') {
      const direction = key === 'ArrowLeft' ? -1 : 1
      if (shiftKey) this.jump(direction * 5000)
      else this.step(direction)
      return true
    }
    if (key === ',' || key === '.') {
      this.speed(key === ',' ? -1 : 1)
      return true
    }

    if (ui.proposal) {
      if (key === 'Enter') this.confirmProposal()
      else if (key === 'Escape') this.keepRallyOpen()
      else if (key === 'u' || key === 'U') this.setWinner('us')
      else if (key === 't' || key === 'T') this.setWinner('them')
      // Swallow other characters: the proposal waits for an answer.
      return key.length === 1 || key === 'Enter' || key === 'Escape'
    }

    if (ui.entry) {
      if (key === 'Enter') this.saveEntry()
      else if (key === 'Escape') this.cancelEntry()
      else if (key === 'Backspace') this.backspace()
      else if (key === 'Tab') this.switchSide()
      else if (isEntryChar(key, this.taxonomy)) this.type(key)
      else return false
      return true
    }

    // Empty buffer: a digit or `a` starts a tag; single letters are commands.
    if (/^[0-9]$/.test(key) || key === 'a' || key === 'A') {
      this.startEntry(key)
      return true
    }
    switch (key) {
      case '?':
        this.toggleCheatsheet(true)
        return true
      case 'Escape':
        if (ui.editingId) this.edit(null)
        else if (ui.segmentId) this.selectSegment(null)
        else if (ui.selectedId) this.select(null)
        else return false
        return true
      case 'Enter':
        if (!ui.selectedId) return false
        this.edit(ui.selectedId)
        return true
      case 'ArrowUp':
      case 'ArrowDown':
        this.selectRelative(key === 'ArrowUp' ? -1 : 1)
        return true
      case 'Delete':
        if (!ui.selectedId) return false
        this.deleteTag(ui.selectedId)
        return true
      case '[':
        this.markClip('in')
        return true
      case ']':
        this.markClip('out')
        return true
    }
    switch (key.toLowerCase()) {
      case 'd':
        if (!shiftKey || !this.playsRallies) return false
        this.toggleSkip()
        return true
      case 'r':
        // Basketball: no rallies to mark (its score comes from the made shots).
        if (!this.playsRallies) return false
        this.rallyKey()
        return true
      case 't':
        this.addSegment('timeout')
        return true
      case 'b':
        this.addSegment('set_break')
        return true
      case 'g':
        this.gotoSelected()
        return true
      case 'f':
        this.snapSelected()
        return true
      case 'c':
        // C saves a clip; Shift+C sets the selected tag's clip window (M2).
        if (shiftKey) this.applyClip()
        else this.saveClip()
        return true
      default:
        return false
    }
  }
}
