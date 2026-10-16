// Drives two <video> elements for a reel (docs/PLAN.md §7.7): one plays, the other loads the
// next piece when it is in another video, so the switch is quick. The sequencing itself is
// the pure reelStep() in reelPlayer.ts; this class only does what it says.

import type Hls from 'hls.js'
import { moveTo, preloadTarget, reelStep, type ReelPiece, type ReelStep } from './reelPlayer'

type HlsClass = typeof Hls
type SlotNo = 0 | 1

const HLS_TYPE = 'application/vnd.apple.mpegurl'
/** A seek the engine made lands within this of its target (the user's seeks don't). */
const SEEK_MATCH_MS = 400

export interface ReelState {
  index: number
  /** The slot on screen. */
  active: SlotNo
  playing: boolean
  /** Loading another video. */
  waiting: boolean
  /** Played to the end of the last piece. */
  ended: boolean
  error: boolean
  /** Position in the active video. */
  t_ms: number
}

interface Slot {
  element: HTMLVideoElement | null
  videoId: string | null
  hls: Hls | null
  ready: Promise<boolean> | null
  /** Increases on every load: an answer for an older load is ignored. */
  token: number
}

const INITIAL: ReelState = { index: 0, active: 0, playing: false, waiting: false, ended: false, error: false, t_ms: 0 }

function loadHls(): Promise<HlsClass | null> {
  return import('hls.js/light').then((m) => m.default).catch(() => null)
}

export class ReelEngine {
  private pieces: readonly ReelPiece[] = []
  private readonly slots: [Slot, Slot] = [
    { element: null, videoId: null, hls: null, ready: null, token: 0 },
    { element: null, videoId: null, hls: null, ready: null, token: 0 },
  ]
  private state: ReelState = INITIAL
  private readonly listeners = new Set<() => void>()
  private frame = 0
  private moveToken = 0
  private expectSeek: number | null = null
  private readonly hls: () => Promise<HlsClass | null>
  private readonly detachers: [(() => void) | null, (() => void) | null] = [null, null]

  constructor(hls: () => Promise<HlsClass | null> = loadHls) {
    this.hls = hls
  }

  getState = (): ReelState => this.state

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  private set(patch: Partial<ReelState>): void {
    const next = { ...this.state, ...patch }
    if ((Object.keys(patch) as (keyof ReelState)[]).every((k) => next[k] === this.state[k])) return
    this.state = next
    for (const listener of [...this.listeners]) listener()
  }

  private get active(): Slot {
    return this.slots[this.state.active]
  }

  // Elements

  attach(no: SlotNo, element: HTMLVideoElement | null): void {
    const slot = this.slots[no]
    if (slot.element === element) return
    this.detachers[no]?.()
    this.detachers[no] = null
    this.unload(slot)
    slot.element = element
    if (!element) return
    const on = (type: string, handler: () => void) => element.addEventListener(type, handler)
    const handlers: [string, () => void][] = [
      ['play', () => this.onPlay(no)],
      ['pause', () => this.onPause(no)],
      ['timeupdate', () => this.onTime(no)],
      ['seeked', () => this.onSeeked(no)],
      ['ended', () => this.onEnded(no)],
    ]
    for (const [type, handler] of handlers) on(type, handler)
    this.detachers[no] = () => {
      for (const [type, handler] of handlers) element.removeEventListener(type, handler)
    }
    // Pieces given before the elements existed: show the first one now.
    if (no === this.state.active && this.pieces.length > 0 && !slot.videoId) void this.show(this.state.index, false)
  }

  /** A new list: back to the first piece, paused. */
  setPieces(pieces: readonly ReelPiece[]): void {
    this.reset()
    this.pieces = pieces
    if (pieces.length > 0) void this.show(0, false)
  }

  /** Stops and unloads both players (the elements stay attached). */
  reset(): void {
    this.moveToken += 1
    this.stopLoop()
    for (const slot of this.slots) {
      slot.element?.pause()
      this.unload(slot)
    }
    this.state = { ...INITIAL }
    for (const listener of [...this.listeners]) listener()
  }

  private unload(slot: Slot): void {
    slot.token += 1
    slot.hls?.destroy()
    slot.hls = null
    slot.videoId = null
    slot.ready = null
    if (slot.element) {
      slot.element.removeAttribute('src')
      try {
        slot.element.load()
      } catch {
        // jsdom
      }
    }
  }

  /** Loads a video into a slot, positioned at `start_ms`; true once it can seek. */
  private load(no: SlotNo, videoId: string, start_ms: number): Promise<boolean> {
    const slot = this.slots[no]
    if (slot.videoId === videoId && slot.ready) return slot.ready
    this.unload(slot)
    const element = slot.element
    if (!element) return Promise.resolve(false)
    slot.videoId = videoId
    const token = slot.token
    const src = `/api/media/${videoId}/master.m3u8`
    slot.ready = (async () => {
      const Hls = await this.hls()
      if (slot.token !== token) return false
      const loaded = new Promise<boolean>((resolve) => {
        const done = (ok: boolean) => {
          element.removeEventListener('loadedmetadata', yes)
          element.removeEventListener('error', no)
          resolve(ok)
        }
        const yes = () => done(true)
        const no = () => done(false)
        element.addEventListener('loadedmetadata', yes)
        element.addEventListener('error', no)
      })
      if (Hls?.isSupported()) {
        const hls = new Hls({ startPosition: start_ms / 1000 })
        slot.hls = hls
        hls.on(Hls.Events.ERROR, (_event, data) => {
          if (data.fatal && slot.token === token && slot === this.active) this.set({ error: true, waiting: false })
        })
        hls.loadSource(src)
        hls.attachMedia(element)
      } else if (element.canPlayType(HLS_TYPE)) {
        element.src = src
      } else {
        return false
      }
      const ok = await loaded
      if (slot.token !== token) return false
      if (ok) this.seekSlot(slot, start_ms)
      return ok
    })()
    return slot.ready
  }

  private seekSlot(slot: Slot, ms: number): void {
    const element = slot.element
    if (!element) return
    if (Math.abs(element.currentTime * 1000 - ms) < 30) return
    if (slot === this.active) this.expectSeek = ms
    element.currentTime = ms / 1000
  }

  // Moving between pieces

  /** Goes to a piece (from wherever the active player is) and plays it if asked. */
  async show(index: number, play: boolean): Promise<void> {
    const slot = this.active
    const from = slot.videoId && slot.element ? { video_id: slot.videoId, t_ms: slot.element.currentTime * 1000 } : null
    const step = moveTo(this.pieces, index, from)
    // Picking a piece always starts it from its beginning.
    await this.apply(step.kind === 'next' ? { kind: 'seek', index: step.index, to_ms: this.pieces[index]?.start_ms ?? 0 } : step, play)
  }

  private async apply(step: ReelStep, play: boolean): Promise<void> {
    switch (step.kind) {
      case 'play':
        return
      case 'end':
        this.active.element?.pause()
        this.set({ ended: true, playing: false })
        return
      case 'next':
        this.set({ index: step.index, ended: false })
        this.preload()
        return
      case 'seek': {
        this.set({ index: step.index, ended: false })
        this.seekSlot(this.active, step.to_ms)
        if (play) this.playActive()
        this.preload()
        return
      }
      case 'switch': {
        const token = ++this.moveToken
        // The first piece goes into the slot on screen; later ones into the other (maybe preloaded).
        const target: SlotNo = this.active.videoId === null ? this.state.active : this.state.active === 0 ? 1 : 0
        this.set({ index: step.index, ended: false, waiting: true, error: false })
        const ok = await this.load(target, step.video_id, step.to_ms)
        if (token !== this.moveToken) return
        if (!ok) {
          this.set({ waiting: false, error: true })
          return
        }
        const previous = this.active
        this.seekSlot(this.slots[target], step.to_ms)
        if (previous !== this.slots[target]) previous.element?.pause()
        this.set({ active: target, waiting: false })
        if (play) this.playActive()
        this.preload()
        return
      }
    }
  }

  /** The next piece's video goes into the hidden player, positioned at the piece's start. */
  private preload(): void {
    const next = preloadTarget(this.pieces, this.state.index)
    if (!next) return
    const hidden: SlotNo = this.state.active === 0 ? 1 : 0
    void this.load(hidden, next.video_id, next.start_ms)
  }

  private playActive(): void {
    const playing = this.active.element?.play() as Promise<void> | undefined
    playing?.catch?.(() => {})
  }

  /** A point of the reel: inside the current piece it seeks there, else the piece starts over. */
  jump = (index: number, t_ms: number): void => {
    if (index === this.state.index && this.active.videoId === this.pieces[index]?.video_id) {
      this.seekSlot(this.active, t_ms)
      this.set({ t_ms: Math.floor(t_ms / 250) * 250, ended: false })
    } else {
      void this.show(index, true)
    }
  }

  play = (): void => {
    if (this.state.ended) {
      void this.show(0, true)
      return
    }
    const piece = this.pieces[this.state.index]
    const element = this.active.element
    if (!piece || !element) return
    const t = element.currentTime * 1000
    // Outside the piece (e.g. after it ended): start it again.
    if (t < piece.start_ms - 1000 || t >= piece.end_ms - 40) this.seekSlot(this.active, piece.start_ms)
    this.playActive()
  }

  pause = (): void => {
    this.active.element?.pause()
  }

  toggle = (): void => {
    if (this.state.playing) this.pause()
    else this.play()
  }

  next = (): void => {
    if (this.state.index + 1 < this.pieces.length) void this.show(this.state.index + 1, true)
  }

  previous = (): void => {
    const element = this.active.element
    const piece = this.pieces[this.state.index]
    // Well into a piece, "previous" starts it again (as music players do).
    if (piece && element && element.currentTime * 1000 - piece.start_ms > 3000) void this.show(this.state.index, true)
    else if (this.state.index > 0) void this.show(this.state.index - 1, true)
  }

  // Element events (only the active slot counts)

  private isActive(no: SlotNo): boolean {
    return no === this.state.active && !this.state.waiting
  }

  private onPlay(no: SlotNo): void {
    if (!this.isActive(no)) return
    this.set({ playing: true, ended: false })
    this.startLoop()
  }

  private onPause(no: SlotNo): void {
    if (!this.isActive(no)) return
    this.set({ playing: false })
    this.stopLoop()
  }

  private onTime(no: SlotNo): void {
    if (!this.isActive(no)) return
    this.check()
  }

  private onEnded(no: SlotNo): void {
    if (!this.isActive(no)) return
    const element = this.active.element
    const video = this.active.videoId
    // The video ended inside the piece: go on with the next one.
    const from = element && video ? { video_id: video, t_ms: element.currentTime * 1000 } : null
    void this.apply(moveTo(this.pieces, this.state.index + 1, from), true)
  }

  /** A seek by the viewer (native controls) inside another piece of this video selects it. */
  private onSeeked(no: SlotNo): void {
    if (!this.isActive(no)) return
    const element = this.active.element
    if (!element) return
    const t = element.currentTime * 1000
    const expected = this.expectSeek
    this.expectSeek = null
    this.set({ t_ms: Math.round(t) })
    if (expected !== null && Math.abs(expected - t) < SEEK_MATCH_MS) return
    const video = this.active.videoId
    const current = this.pieces[this.state.index]
    if (current && current.video_id === video && current.start_ms - 500 <= t && t < current.end_ms) return
    const found = this.pieces.findIndex((p) => p.video_id === video && p.start_ms - 500 <= t && t < p.end_ms)
    if (found !== -1) this.set({ index: found, ended: false })
  }

  private check = (): void => {
    const element = this.active.element
    if (!element || this.state.waiting) return
    const t = element.currentTime * 1000
    // For the clock: four times a second is enough.
    this.set({ t_ms: Math.floor(t / 250) * 250 })
    if (element.paused || element.seeking) return
    const step = reelStep(this.pieces, this.state.index, t)
    if (step.kind !== 'play') void this.apply(step, true)
  }

  private startLoop(): void {
    if (this.frame || typeof requestAnimationFrame !== 'function') return
    const tick = () => {
      this.frame = requestAnimationFrame(tick)
      this.check()
    }
    this.frame = requestAnimationFrame(tick)
  }

  private stopLoop(): void {
    if (this.frame) cancelAnimationFrame(this.frame)
    this.frame = 0
  }
}
