import { useEffect, useRef } from 'react'
import { rangeStep, type PlayRange } from './clips'

interface Handlers<T extends PlayRange> {
  /** The range played to its end. */
  onEnd: (range: T) => void
  /** The viewer seeked away from the range. */
  onLeave: (range: T) => void
}

/**
 * While `range` is set, a playing video stops (or moves on, see onEnd) at the range's end.
 * Checks on every frame while playing; a seek far outside the range leaves it. Keep the range
 * object stable (state): a new object starts the range over.
 */
export function useRangePlayback<T extends PlayRange>(video: HTMLVideoElement | null, range: T | null, handlers: Handlers<T>): void {
  const latest = useRef(handlers)
  useEffect(() => {
    latest.current = handlers
  })

  // Keyed on the range object: playing the same clip again starts over.
  useEffect(() => {
    if (!video || !range) return
    const span = { start_ms: range.start_ms, end_ms: range.end_ms }
    let frame = 0
    let done = false

    const finish = (step: 'end' | 'outside') => {
      if (done) return
      done = true
      stop()
      if (step === 'end') latest.current.onEnd(range)
      else latest.current.onLeave(range)
    }
    const check = () => {
      if (video.paused || video.seeking) return
      const step = rangeStep(video.currentTime * 1000, span)
      if (step !== 'play') finish(step)
    }
    const onSeeked = () => {
      const step = rangeStep(video.currentTime * 1000, span)
      if (step === 'outside') finish(step)
    }
    const loop = () => {
      check()
      frame = requestAnimationFrame(loop)
    }
    const stop = () => {
      if (frame) cancelAnimationFrame(frame)
      frame = 0
    }
    const onPlay = () => {
      stop()
      check()
      if (!done && typeof requestAnimationFrame === 'function') frame = requestAnimationFrame(loop)
    }

    video.addEventListener('play', onPlay)
    video.addEventListener('pause', stop)
    video.addEventListener('timeupdate', check)
    video.addEventListener('seeked', onSeeked)
    if (!video.paused) onPlay()
    return () => {
      stop()
      video.removeEventListener('play', onPlay)
      video.removeEventListener('pause', stop)
      video.removeEventListener('timeupdate', check)
      video.removeEventListener('seeked', onSeeked)
    }
  }, [video, range])
}
