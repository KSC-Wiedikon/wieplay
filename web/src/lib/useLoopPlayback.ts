import { useEffect } from 'react'
import { LOOP_POST_MS, LOOP_PRE_MS, loopRestart } from './suggestions'

/**
 * While a range is set (the suggestion under review), playing through its end starts it over
 * from 1 s before it; a seek elsewhere is left alone. If the range was picked before the video
 * could seek (metadata not loaded yet), the video jumps to it once it can.
 */
export function useLoopPlayback(video: HTMLVideoElement | null, start_ms: number | null, end_ms: number | null): void {
  useEffect(() => {
    if (!video || start_ms === null || end_ms === null) return
    const range = { start_ms, end_ms }
    const from = Math.max(0, start_ms - LOOP_PRE_MS) / 1000
    let frame = 0
    let interval = 0

    const check = () => {
      if (video.paused || video.seeking) return
      if (loopRestart(video.currentTime * 1000, range)) video.currentTime = from
    }
    const onLoaded = () => {
      const t = video.currentTime * 1000
      if (t < start_ms - LOOP_PRE_MS - 250 || t > end_ms + LOOP_POST_MS) video.currentTime = from
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
    video.addEventListener('loadedmetadata', onLoaded)
    if (!video.paused) start()
    return () => {
      stop()
      video.removeEventListener('play', start)
      video.removeEventListener('pause', stop)
      video.removeEventListener('timeupdate', check)
      video.removeEventListener('loadedmetadata', onLoaded)
    }
  }, [video, start_ms, end_ms])
}
