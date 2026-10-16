// The ball's path over the video (docs/PLAN.md §10.4): GET /api/videos/{id}/ball-track, a
// compact track the server stores per analysis (backend/nomal/ml/trajectory.py), decoded
// here, and the maths of the "Trajectories" overlay: which part of the path to draw at a
// moment, and where the picture sits inside the <video> box (letterboxing).

import { queryOptions, useQuery } from '@tanstack/react-query'
import { apiRequest } from './api'
import { readStorage, writeStorage } from './storage'

/** As served: positions in 1/scale of the frame, delta-encoded per track. */
export interface BallTrackPayload {
  schema: number
  fps: number
  scale: number
  /** Samples in the video (sample i is at i / fps seconds). */
  frames: number
  duration_s: number
  /** [first sample, x0, y0, dx1, dy1, …] per track; inside a track every sample has the ball. */
  tracks: number[][]
  /** [t_ms, x, y, kind]: 0 bounce (the floor), 1 low contact (dig, reception), 2 high (set, attack, block). */
  contacts: [number, number, number, number][]
  stats?: Record<string, number>
}

export type ContactKind = 'bounce' | 'low' | 'high'

export interface Contact {
  /** Seconds of the video. */
  t: number
  /** Normalised to the frame (0..1, y down). */
  x: number
  y: number
  kind: ContactKind
}

export interface BallTrack {
  fps: number
  /** Normalised positions per sample; NaN where there is no ball. */
  x: Float32Array
  y: Float32Array
  contacts: Contact[]
}

const KINDS: readonly ContactKind[] = ['bounce', 'low', 'high']

export function decodeTrack(payload: BallTrackPayload): BallTrack {
  const n = Math.max(0, payload.frames)
  const x = new Float32Array(n).fill(Number.NaN)
  const y = new Float32Array(n).fill(Number.NaN)
  const scale = payload.scale || 1000
  for (const item of payload.tracks) {
    let qx = 0
    let qy = 0
    const first = item[0] ?? 0
    for (let k = 1; k + 1 < item.length; k += 2) {
      qx += item[k] ?? 0
      qy += item[k + 1] ?? 0
      const i = first + (k - 1) / 2
      if (i >= 0 && i < n) {
        x[i] = qx / scale
        y[i] = qy / scale
      }
    }
  }
  const contacts = payload.contacts
    .map(([t_ms, cx, cy, kind]) => ({ t: t_ms / 1000, x: cx / scale, y: cy / scale, kind: KINDS[kind] ?? 'high' }))
    .sort((a, b) => a.t - b.t)
  return { fps: payload.fps, x, y, contacts }
}

export interface TrailPoint {
  x: number
  y: number
  /** 0 now … 1 at the far end of the trail. */
  age: number
  contact?: ContactKind
}

/** Trails shorter than this read as noise. */
const MIN_POINTS = 2

/**
 * The path of the last `windowS` seconds before `t` (seconds), as polylines (a gap ends one),
 * oldest point first, ending at the ball's position now (interpolated between samples).
 * Contacts in the window are put in at their exact time, so a bounce stays a sharp corner.
 */
export function trailAt(track: BallTrack, t: number, windowS: number): TrailPoint[][] {
  const { fps, x, y } = track
  if (!(fps > 0) || !Number.isFinite(t) || x.length === 0) return []
  const now = t * fps
  const last = Math.min(x.length - 1, Math.floor(now))
  const first = Math.max(0, Math.ceil(now - windowS * fps))
  if (last < 0 || first > last + 1) return []
  const age = (sample: number) => Math.min(1, Math.max(0, (now - sample) / (windowS * fps)))
  const lines: TrailPoint[][] = []
  let line: TrailPoint[] = []
  const flush = () => {
    if (line.length >= MIN_POINTS) lines.push(line)
    line = []
  }
  for (let i = first; i <= last; i++) {
    const xi = x[i] ?? Number.NaN
    const yi = y[i] ?? Number.NaN
    if (Number.isNaN(xi) || Number.isNaN(yi)) {
      flush()
      continue
    }
    line.push({ x: xi, y: yi, age: age(i) })
  }
  // The ball now: between the last sample and the next one, when both have it.
  const next = last + 1
  const xn = x[next] ?? Number.NaN
  const yn = y[next] ?? Number.NaN
  const xl = x[last] ?? Number.NaN
  const yl = y[last] ?? Number.NaN
  if (line.length > 0 && next < x.length && !Number.isNaN(xn) && !Number.isNaN(xl) && now > last) {
    const f = now - last
    line.push({ x: xl + f * (xn - xl), y: yl + f * (yn - yl), age: 0 })
  }
  flush()
  // Contacts inside a line's span, at their place in time.
  const from = t - windowS
  for (const c of track.contacts) {
    if (c.t <= from || c.t > t) continue
    const sample = c.t * fps
    for (const l of lines) {
      const start = now - (l[0]?.age ?? 0) * windowS * fps
      const end = now - (l[l.length - 1]?.age ?? 0) * windowS * fps
      if (sample < start || sample > end) continue
      const a = age(sample)
      const at = l.findIndex((p) => p.age < a)
      l.splice(at === -1 ? l.length : at, 0, { x: c.x, y: c.y, age: a, contact: c.kind })
      break
    }
  }
  return lines
}

/** The ball at `t` (seconds), when the track has it then. */
export function ballAt(track: BallTrack, t: number): { x: number; y: number } | null {
  const i = Math.round(t * track.fps)
  const x = track.x[i] ?? Number.NaN
  const y = track.y[i] ?? Number.NaN
  return Number.isNaN(x) || Number.isNaN(y) ? null : { x, y }
}

export interface Rect {
  x: number
  y: number
  width: number
  height: number
}

/**
 * Where the picture sits inside a box showing it with object-fit: contain (the <video>
 * default): bars left and right or above and below. Null before the video's size is known.
 */
export function contentRect(boxWidth: number, boxHeight: number, videoWidth: number, videoHeight: number): Rect | null {
  if (!(boxWidth > 0 && boxHeight > 0 && videoWidth > 0 && videoHeight > 0)) return null
  const scale = Math.min(boxWidth / videoWidth, boxHeight / videoHeight)
  const width = videoWidth * scale
  const height = videoHeight * scale
  return { x: (boxWidth - width) / 2, y: (boxHeight - height) / 2, width, height }
}

/** A normalised point (0..1) of the picture in the box's pixels. */
export function toBox(rect: Rect, p: { x: number; y: number }): { x: number; y: number } {
  return { x: rect.x + p.x * rect.width, y: rect.y + p.y * rect.height }
}

export function ballTrackQuery(videoId: string) {
  return queryOptions({
    queryKey: ['videos', videoId, 'ball-track'] as const,
    queryFn: async ({ signal }) => decodeTrack(await apiRequest<BallTrackPayload>(`/api/videos/${videoId}/ball-track`, { signal })),
    // A track changes only with a new analysis; the server revalidates by ETag.
    staleTime: 10 * 60_000,
    gcTime: 30 * 60_000,
  })
}

/** Only fetched when wanted (the overlay is on, or a court diagram needs the bounces). */
export function useBallTrack(videoId: string, enabled: boolean) {
  return useQuery({ ...ballTrackQuery(videoId), enabled })
}

const PREFERENCE = 'nomal-trajectories'

/** On by default on wider screens, off on phones; the last choice is remembered. */
export function trajectoriesDefault(): boolean {
  const saved = readStorage(PREFERENCE)
  if (saved === '1') return true
  if (saved === '0') return false
  if (typeof window.matchMedia !== 'function') return true
  return !window.matchMedia('(max-width: 639px)').matches
}

export function rememberTrajectories(on: boolean): void {
  writeStorage(PREFERENCE, on ? '1' : '0')
}
