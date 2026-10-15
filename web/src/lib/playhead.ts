import { useSyncExternalStore } from 'react'

export interface PlayheadState {
  /** Current position, ms. */
  t_ms: number
  playing: boolean
  rate: number
  /** Null until the metadata is loaded. */
  duration_ms: number | null
}

const IDLE: PlayheadState = { t_ms: 0, playing: false, rate: 1, duration_ms: null }
/** While playing, the position is published at most this often (the video itself is not slowed). */
const PUBLISH_MS = 66

/**
 * The <video> element's position as a store, so the timeline, the table and the score can
 * follow it without re-rendering the whole workspace on every frame.
 */
export class Playhead {
  private element: HTMLVideoElement | null = null
  private state: PlayheadState = IDLE
  private readonly listeners = new Set<() => void>()
  private frame = 0
  private last = 0

  attach(element: HTMLVideoElement | null): void {
    if (element === this.element) return
    this.detach()
    this.element = element
    if (!element) return
    for (const type of EVENTS) element.addEventListener(type, this.sync)
    this.sync()
  }

  detach(): void {
    const element = this.element
    if (!element) return
    for (const type of EVENTS) element.removeEventListener(type, this.sync)
    this.stopLoop()
    this.element = null
  }

  get video(): HTMLVideoElement | null {
    return this.element
  }

  getState = (): PlayheadState => this.state

  /** Moves the video to a position (ms), clamped to the video. */
  seek(ms: number): void {
    const el = this.element
    if (!el) return
    const end = Number.isFinite(el.duration) && el.duration > 0 ? el.duration : Infinity
    el.currentTime = Math.min(end, Math.max(0, ms / 1000))
    this.sync()
  }

  play(): void {
    // play() returns a promise in browsers (rejected when autoplay is refused); not in jsdom.
    const playing = this.element?.play() as Promise<void> | undefined
    playing?.catch?.(() => {})
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  /** Reads the element now (e.g. right after a seek). */
  sync = (): void => {
    const el = this.element
    if (!el) return
    const next: PlayheadState = {
      t_ms: Math.round(el.currentTime * 1000),
      playing: !el.paused && !el.ended,
      rate: el.playbackRate || 1,
      duration_ms: Number.isFinite(el.duration) ? Math.round(el.duration * 1000) : null,
    }
    if (next.playing) this.startLoop()
    else this.stopLoop()
    this.publish(next)
  }

  private publish(next: PlayheadState): void {
    const prev = this.state
    if (
      prev.t_ms === next.t_ms &&
      prev.playing === next.playing &&
      prev.rate === next.rate &&
      prev.duration_ms === next.duration_ms
    ) {
      return
    }
    this.state = next
    for (const listener of [...this.listeners]) listener()
  }

  private startLoop(): void {
    if (this.frame || typeof requestAnimationFrame !== 'function') return
    const tick = (now: number) => {
      this.frame = requestAnimationFrame(tick)
      if (now - this.last < PUBLISH_MS) return
      this.last = now
      const el = this.element
      if (el) this.publish({ ...this.state, t_ms: Math.round(el.currentTime * 1000) })
    }
    this.frame = requestAnimationFrame(tick)
  }

  private stopLoop(): void {
    if (this.frame) cancelAnimationFrame(this.frame)
    this.frame = 0
  }
}

const EVENTS = ['timeupdate', 'seeked', 'seeking', 'play', 'pause', 'ended', 'ratechange', 'durationchange', 'loadedmetadata'] as const

/** A value derived from the playhead; the component re-renders only when the value changes. */
export function usePlayhead<T>(playhead: Playhead, select: (state: PlayheadState) => T): T {
  return useSyncExternalStore(playhead.subscribe, () => select(playhead.getState()))
}
