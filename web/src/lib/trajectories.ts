// The "Trajectories" overlay's state and drawing (docs/PLAN.md §10.4): whether it is on for a
// video, the track when it is, how the trail is painted, and full screen of the player's box.

import { useEffect, useState } from 'react'
import { rememberTrajectories, toBox, trajectoriesDefault, useBallTrack, type BallTrack, type Rect, type TrailPoint } from './ballTrack'
import { useCourt } from './court'

/** Seconds of the ball's path drawn behind it. */
export const TRAIL_S = 1.2
const TRAIL = '#ffc72c' // brand yellow: reads on wooden, blue and green floors
const SHADE = 'rgb(19 32 58 / 0.65)'

export interface TrajectoryState {
  /** The video has a ball track (else the toggle stays hidden). */
  available: boolean
  /** The track is being made. */
  pending: boolean
  on: boolean
  setOn: (on: boolean) => void
  track: BallTrack | null
}

/** The "Trajectories" choice of a video page, and the track when it is on. */
export function useTrajectories(videoId: string, enabled = true): TrajectoryState {
  const court = useCourt(videoId, enabled)
  const available = court.data?.ball_track.available === true
  const [on, setOnState] = useState(trajectoriesDefault)
  const track = useBallTrack(videoId, available && on)
  return {
    available,
    pending: !available && ['queued', 'running'].includes(court.data?.ball_track.job ?? ''),
    on,
    setOn: (value) => {
      setOnState(value)
      rememberTrajectories(value)
    },
    track: track.data ?? null,
  }
}

/** Draws the trail: older parts fade, contacts stay corners, the ball now is a dot. */
export function paintTrail(ctx: CanvasRenderingContext2D, lines: TrailPoint[][], rect: Rect): void {
  const width = Math.min(6, Math.max(2, rect.height * 0.007))
  ctx.lineCap = 'round'
  ctx.lineJoin = 'round'
  for (const line of lines) {
    for (const [colour, extra] of [
      [SHADE, 2],
      [TRAIL, 0],
    ] as const) {
      ctx.strokeStyle = colour
      ctx.lineWidth = width + extra
      for (let i = 1; i < line.length; i++) {
        const a = line[i - 1]
        const b = line[i]
        if (!a || !b) continue
        ctx.globalAlpha = 0.95 * (1 - b.age) ** 1.4
        const pa = toBox(rect, a)
        const pb = toBox(rect, b)
        ctx.beginPath()
        ctx.moveTo(pa.x, pa.y)
        ctx.lineTo(pb.x, pb.y)
        ctx.stroke()
      }
    }
    for (const p of line) {
      if (p.contact !== 'bounce') continue
      const at = toBox(rect, p)
      ctx.globalAlpha = 0.9 * (1 - p.age)
      ctx.strokeStyle = '#ffffff'
      ctx.lineWidth = Math.max(1.5, width * 0.6)
      ctx.beginPath()
      ctx.arc(at.x, at.y, width * 2, 0, 2 * Math.PI)
      ctx.stroke()
    }
    const head = line[line.length - 1]
    if (head && head.age === 0) {
      const at = toBox(rect, head)
      ctx.globalAlpha = 1
      ctx.fillStyle = TRAIL
      ctx.strokeStyle = SHADE
      ctx.lineWidth = 2
      ctx.beginPath()
      ctx.arc(at.x, at.y, width * 1.4, 0, 2 * Math.PI)
      ctx.fill()
      ctx.stroke()
    }
  }
  ctx.globalAlpha = 1
}

/** Whether the player's box is in full screen now. */
export function useFullscreen(box: HTMLElement | null): boolean {
  const [full, setFull] = useState(false)
  useEffect(() => {
    const update = () => setFull(box !== null && document.fullscreenElement === box)
    update()
    document.addEventListener('fullscreenchange', update)
    return () => document.removeEventListener('fullscreenchange', update)
  }, [box])
  return full
}
