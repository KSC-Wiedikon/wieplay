import { useEffect } from 'react'
import { nextPlayback, type PlayWindow } from './condensed'

/**
 * While `enabled`, a playing video only plays inside `windows`: it jumps over the gaps and
 * stops after the last window. Checks on every frame while playing, and on play/seek.
 */
export function useCondensedPlayback(video: HTMLVideoElement | null, windows: readonly PlayWindow[], enabled: boolean): void {
  useEffect(() => {
    if (!video || !enabled || windows.length === 0) return
    let frame = 0
    let interval = 0

    const check = () => {
      if (video.paused || video.seeking) return
      const step = nextPlayback(video.currentTime * 1000, windows)
      if (step.kind === 'seek') video.currentTime = step.to_ms / 1000
      else if (step.kind === 'end') video.pause()
    }
    const loop = () => {
      check()
      frame = requestAnimationFrame(loop)
    }
    const stop = () => {
      if (frame) cancelAnimationFrame(frame)
      if (interval) window.clearInterval(interval)
      frame = interval = 0
    }
    const start = () => {
      stop()
      check()
      if (typeof requestAnimationFrame === 'function') frame = requestAnimationFrame(loop)
      else interval = window.setInterval(check, 50)
    }

    video.addEventListener('play', start)
    video.addEventListener('pause', stop)
    video.addEventListener('timeupdate', check)
    video.addEventListener('seeked', check)
    if (!video.paused) start()
    return () => {
      stop()
      video.removeEventListener('play', start)
      video.removeEventListener('pause', stop)
      video.removeEventListener('timeupdate', check)
      video.removeEventListener('seeked', check)
    }
  }, [video, windows, enabled])
}
