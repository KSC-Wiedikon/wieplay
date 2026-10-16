import '../i18n/pages'
import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { contentRect, trailAt, type BallTrack } from '../lib/ballTrack'
import { paintTrail, TRAIL_S, type TrajectoryState } from '../lib/trajectories'
import { Switch } from './Switch'

function context2d(canvas: HTMLCanvasElement): CanvasRenderingContext2D | null {
  try {
    return canvas.getContext('2d')
  } catch {
    return null // no canvas (tests)
  }
}

/** The switch; nothing when the video has no ball track. */
export function TrajectoryToggle({ state }: { state: TrajectoryState }) {
  const { t } = useTranslation()
  if (!state.available) return null
  return (
    <label className="flex cursor-pointer items-center gap-1 text-sm font-semibold" title={t('court.trajectoriesHint')}>
      <Switch checked={state.on} onChange={state.setOn} label={t('court.trajectories')} />
      <span aria-hidden="true">{t('court.trajectories')}</span>
    </label>
  )
}

/**
 * The ball's recent path on a canvas over the player, following the video's own clock on
 * every animation frame. It covers the player's box and finds the picture inside it
 * (letterboxing, resizing, full screen) from the <video> element's box and size.
 */
export function TrajectoryOverlay({ video, track, windowS = TRAIL_S }: { video: HTMLVideoElement | null; track: BallTrack; windowS?: number }) {
  const ref = useRef<HTMLCanvasElement>(null)
  useEffect(() => {
    const canvas = ref.current
    if (!canvas || !video) return
    const paint = context2d(canvas)
    if (!paint) return
    let frame = 0
    let last = ''
    const draw = () => {
      frame = requestAnimationFrame(draw)
      const box = canvas.getBoundingClientRect()
      const vbox = video.getBoundingClientRect()
      const dpr = window.devicePixelRatio || 1
      const w = Math.round(box.width * dpr)
      const h = Math.round(box.height * dpr)
      const key = `${video.currentTime}|${w}|${h}|${vbox.left - box.left}|${vbox.top - box.top}|${vbox.width}|${vbox.height}`
      if (key === last) return
      last = key
      if (canvas.width !== w || canvas.height !== h) {
        canvas.width = w
        canvas.height = h
      }
      paint.setTransform(dpr, 0, 0, dpr, 0, 0)
      paint.clearRect(0, 0, box.width, box.height)
      const rect = contentRect(vbox.width, vbox.height, video.videoWidth, video.videoHeight)
      if (!rect) return
      rect.x += vbox.left - box.left
      rect.y += vbox.top - box.top
      paintTrail(paint, trailAt(track, video.currentTime, windowS), rect)
    }
    frame = requestAnimationFrame(draw)
    return () => cancelAnimationFrame(frame)
  }, [video, track, windowS])
  return <canvas ref={ref} aria-hidden="true" data-testid="trajectory-overlay" className="pointer-events-none absolute inset-0 size-full" />
}

/** The overlay when it is on and the track has loaded. */
export function TrajectoryLayer({ state, video }: { state: TrajectoryState; video: HTMLVideoElement | null }) {
  if (!state.available || !state.on || !state.track) return null
  return <TrajectoryOverlay video={video} track={state.track} />
}

