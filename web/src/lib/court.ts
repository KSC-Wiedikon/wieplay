// The court of a video (docs/PLAN.md §10.6, §10.7): its calibration (GET/PUT/DELETE
// /api/videos/{id}/court, POST …/court/solve), set by a coach or by the court job (from the
// hall's earlier videos or found in the picture; `checked` false until a coach looks), with
// the camera's setups when it moved during the match (`segments`: a time range each, with its
// own homography; `calibrationAt` picks the one for a moment), the court's geometry, and the
// arrows of the court diagram ("Tendencies"): serves and attacks from where they started to
// where they landed, from the ball's bounces when the court is set up, else from the tags' zones.

import { queryOptions, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiRequest } from './api'
import type { BallTrack, ContactKind } from './ballTrack'
import type { Tag, Taxonomy } from './types'

export const LANDMARKS = [
  'near_left',
  'near_right',
  'far_right',
  'far_left',
  'net_left',
  'net_right',
  'attack_near_left',
  'attack_near_right',
  'attack_far_left',
  'attack_far_right',
] as const
export type Landmark = (typeof LANDMARKS)[number]
export const CORNERS: readonly Landmark[] = ['near_left', 'near_right', 'far_right', 'far_left']

export const WIDTH_M = 9
export const LENGTH_M = 18
export const NET_Y = 9

/** Court metres of each landmark: x across (0 left seen from the camera), y along (0 near). */
export const LANDMARK_AT: Record<Landmark, [number, number]> = {
  near_left: [0, 0],
  near_right: [9, 0],
  far_right: [9, 18],
  far_left: [0, 18],
  net_left: [0, 9],
  net_right: [9, 9],
  attack_near_left: [0, 6],
  attack_near_right: [9, 6],
  attack_far_left: [0, 12],
  attack_far_right: [9, 12],
}

export const LINES: readonly [number, number, number, number][] = [
  [0, 0, 0, 18],
  [9, 0, 9, 18],
  [0, 0, 9, 0],
  [0, 18, 9, 18],
  [0, 9, 9, 9],
  [0, 6, 9, 6],
  [0, 12, 9, 12],
]

export type Mat3 = number[][]

export interface CourtPoint {
  landmark: Landmark
  u: number
  v: number
}

export interface Camera {
  center: [number, number, number]
  focal: number
  rays: Mat3
  aspect: number
}

export interface Solution {
  image_to_court: Mat3
  court_to_image: Mat3
  camera: Camera | null
  error_cm: number | null
  max_error_cm: number | null
  residuals_cm: Record<string, number>
  /** The court's lines in the image, polylines of [u, v]. */
  lines: [number, number][][]
}

/** How a court came about: clicked, found in the picture, taken from the team's last video, or carried over from the hall's earlier videos. */
export type CourtMethod = 'manual' | 'auto' | 'copied' | 'auto-hall'

/** One of the camera's setups: from_ms (inclusive) to to_ms (exclusive; null: the end). */
export interface CalibrationSegment {
  index: number
  from_ms: number
  to_ms: number | null
  /** How it began: knocked (bump), sagged slowly (drift), carried to another view (moved). */
  start: 'start' | 'bump' | 'drift' | 'moved'
  shift_px: number
  view: number
  points: CourtPoint[]
  /** derived: carried over from another setup; none: the camera showed no court. */
  method: CourtMethod | 'derived' | 'none'
  checked: boolean
  main: boolean
  image_to_court: Mat3 | null
  court_to_image: Mat3 | null
  camera: Camera | null
  error_cm: number | null
  max_error_cm: number | null
  lines: [number, number][][]
}

export interface Calibration extends Solution {
  id: string
  video_id: string
  status: 'confirmed' | 'proposed'
  method: CourtMethod
  points: CourtPoint[]
  confidence: number | null
  copied_from: string | null
  updated_at: string
  /** False: the court job set it and no coach has checked it yet. */
  checked?: boolean
  /** The court job was sure of it: WiePlay uses it without a coach's look. */
  sure?: boolean
  /** Checked by a coach or sure: the analysis uses it. */
  usable?: boolean
  at_ms?: number | null
  /** The camera's setups when it moved during the match; absent or null: one for the whole video. */
  segments?: CalibrationSegment[] | null
}

/** The camera's setups as the newest court job saw them. */
export interface CameraSetup {
  from_ms: number
  to_ms: number
  start: CalibrationSegment['start']
  shift_px: number
  view?: number
}

export interface Suggestion {
  source: 'auto' | 'auto-hall' | 'team'
  points: CourtPoint[]
  confidence: number | null
  /** The court job's own court, and it was sure. */
  sure?: boolean
  video_id: string
  video_title: string | null
  recorded_on: string | null
  calibration_id: string
}

export interface BallTrackState {
  available: boolean
  fps: number | null
  bytes: number | null
  contacts: number | null
  bounces: number | null
  created_at: string | null
  job: 'none' | 'queued' | 'running' | 'done' | 'failed'
}

export interface CourtState {
  video_id: string
  can_edit: boolean
  aspect: number
  calibration: Calibration | null
  suggestions: Suggestion[]
  ball_track: BallTrackState
  court_job?: 'none' | 'queued' | 'running' | 'done' | 'failed'
  setups?: CameraSetup[]
}

export const courtKey = (videoId: string) => ['videos', videoId, 'court'] as const

export function courtQuery(videoId: string) {
  return queryOptions({
    queryKey: courtKey(videoId),
    queryFn: ({ signal }) => apiRequest<CourtState>(`/api/videos/${videoId}/court`, { signal }),
    // While the ball track is being made, look again now and then.
    refetchInterval: (query) =>
      ['queued', 'running'].includes(query.state.data?.ball_track.job ?? '') || ['queued', 'running'].includes(query.state.data?.court_job ?? '') ? 15_000 : false,
  })
}

export function useCourt(videoId: string, enabled = true) {
  return useQuery({ ...courtQuery(videoId), enabled, retry: false })
}

export function solveCourt(videoId: string, points: CourtPoint[], signal?: AbortSignal): Promise<Solution> {
  return apiRequest<Solution>(`/api/videos/${videoId}/court/solve`, { method: 'POST', body: { points }, signal })
}

export interface SaveCourt {
  points: CourtPoint[]
  method: CourtMethod
  copied_from?: string | null
  /** One of the camera's setups only; the setups nobody set follow it. */
  segment?: number | null
  /** Where the video was paused while setting it up (ms). */
  at_ms?: number | null
}

export function useSaveCourt(videoId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: SaveCourt) => apiRequest<Calibration>(`/api/videos/${videoId}/court`, { method: 'PUT', body }),
    onSuccess: (calibration) => {
      client.setQueryData<CourtState>(courtKey(videoId), (old) => (old ? { ...old, calibration, suggestions: [] } : old))
      void client.invalidateQueries({ queryKey: courtKey(videoId) })
    },
  })
}

/** POST …/court/confirm: the court WiePlay found is right (its own court becomes checked, or its proposal the video's court). */
export function useConfirmCourt(videoId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => apiRequest<Calibration>(`/api/videos/${videoId}/court/confirm`, { method: 'POST' }),
    onSuccess: (calibration) => {
      client.setQueryData<CourtState>(courtKey(videoId), (old) => (old ? { ...old, calibration, suggestions: [] } : old))
      void client.invalidateQueries({ queryKey: courtKey(videoId) })
    },
  })
}

/** The court WiePlay found and nobody has checked yet: the one it set by itself (sure), else its proposal (not sure). */
export interface FoundCourt {
  points: CourtPoint[]
  /** Already drawn by the server (a set court); a proposal's lines come from …/court/solve. */
  lines: [number, number][][] | null
  sure: boolean
  confidence: number | null
  method: 'auto' | 'auto-hall'
  /** set: the video's court already (to confirm); proposal: not used until a coach confirms it. */
  kind: 'set' | 'proposal'
}

export function foundCourt(state: CourtState | undefined): FoundCourt | null {
  const calibration = state?.calibration
  if (calibration) {
    if (calibration.checked !== false || (calibration.method !== 'auto' && calibration.method !== 'auto-hall')) return null
    return { points: calibration.points, lines: calibration.lines, sure: calibration.sure === true, confidence: calibration.confidence, method: calibration.method, kind: 'set' }
  }
  const proposal = state?.suggestions.find((s) => s.source === 'auto' || s.source === 'auto-hall')
  if (!proposal || proposal.source === 'team') return null
  return { points: proposal.points, lines: null, sure: proposal.sure === true, confidence: proposal.confidence, method: proposal.source, kind: 'proposal' }
}

export function useRemoveCourt(videoId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => apiRequest<undefined>(`/api/videos/${videoId}/court`, { method: 'DELETE' }),
    onSuccess: () => void client.invalidateQueries({ queryKey: courtKey(videoId) }),
  })
}

// --- the set-up dialog's points ----------------------------------------------------------------

export type SetupPoints = Partial<Record<Landmark, [number, number]>>

export function pointsOf(list: readonly CourtPoint[]): SetupPoints {
  const out: SetupPoints = {}
  for (const p of list) out[p.landmark] = [p.u, p.v]
  return out
}

export function listOf(points: SetupPoints): CourtPoint[] {
  return LANDMARKS.flatMap((landmark) => {
    const p = points[landmark]
    return p ? [{ landmark, u: p[0], v: p[1] }] : []
  })
}

export function withoutPoint(points: SetupPoints, name: Landmark): SetupPoints {
  return Object.fromEntries(Object.entries(points).filter(([key]) => key !== name)) as SetupPoints
}

/** The next landmark to place after `after`: corners first, in the list's order. */
export function nextLandmark(points: SetupPoints, after: Landmark | null = null): Landmark | null {
  const start = after ? LANDMARKS.indexOf(after) + 1 : 0
  for (let k = 0; k < LANDMARKS.length; k++) {
    const name = LANDMARKS[(start + k) % LANDMARKS.length]
    if (name && !points[name]) return name
  }
  return null
}

/** Fits within this are good (PLAN §10.6: under 15 cm on held-out landmarks). */
export const GOOD_FIT_CM = 15

// --- geometry ------------------------------------------------------------------------------

export function applyH(h: Mat3, x: number, y: number): [number, number] | null {
  const w = (h[2]?.[0] ?? 0) * x + (h[2]?.[1] ?? 0) * y + (h[2]?.[2] ?? 0)
  if (Math.abs(w) < 1e-12) return null
  return [
    ((h[0]?.[0] ?? 0) * x + (h[0]?.[1] ?? 0) * y + (h[0]?.[2] ?? 0)) / w,
    ((h[1]?.[0] ?? 0) * x + (h[1]?.[1] ?? 0) * y + (h[1]?.[2] ?? 0)) / w,
  ]
}

/**
 * Court metres under an image point (u, v) seen at `height` metres above the floor: with the
 * camera, along its viewing ray; without it only the floor (height 0) maps, by the homography.
 */
export function toCourt(cal: Pick<Solution, 'image_to_court' | 'camera'>, u: number, v: number, height = 0): [number, number] | null {
  const cam = cal.camera
  if (!cam) return height === 0 ? applyH(cal.image_to_court, u, v) : null
  const X = (u - 0.5) * cam.aspect
  const Y = v - 0.5
  const d = cam.rays.map((row) => (row[0] ?? 0) * X + (row[1] ?? 0) * Y + (row[2] ?? 0))
  const dz = d[2] ?? 0
  if (dz >= -1e-9) return null
  const s = (height - cam.center[2]) / dz
  return [cam.center[0] + s * (d[0] ?? 0), cam.center[1] + s * (d[1] ?? 0)]
}

/** What maps a moment of the video onto the court: a calibration, with its camera setups if any. */
export type CourtMapping = Pick<Solution, 'image_to_court' | 'camera'> & { segments?: CalibrationSegment[] | null }

/**
 * The calibration for a moment of the video (ms): the camera's setup then when it moved during the
 * match, else the calibration itself. Null when the camera showed no court then.
 */
export function calibrationAt(cal: CourtMapping, tMs: number): Pick<Solution, 'image_to_court' | 'camera'> | null {
  const segments = cal.segments
  if (!segments || segments.length === 0) return cal
  const seg =
    segments.find((s) => s.from_ms <= tMs && (s.to_ms === null || tMs < s.to_ms)) ??
    ((segments[0]?.from_ms ?? 0) > tMs ? segments[0] : segments[segments.length - 1])
  if (!seg?.image_to_court) return null
  return { image_to_court: seg.image_to_court, camera: seg.camera }
}

/** When the camera was knocked or carried elsewhere (not its slow drift), in ms. */
export function cameraMoves(setups: readonly Pick<CameraSetup, 'from_ms' | 'start'>[]): number[] {
  return setups.filter((s) => s.start === 'bump' || s.start === 'moved').map((s) => s.from_ms)
}

/** Turns the court round (the other half's view): the acting team is drawn at the bottom. */
export function turn([x, y]: [number, number]): [number, number] {
  return [WIDTH_M - x, LENGTH_M - y]
}

// --- zones (DataVolley: 1–9 on each half, seen from the team on that half) --------------------

/** Column (0 left … 2 right) and row (0 front at the net … 2 back) of a zone, for its team. */
const ZONE: Record<number, [number, number]> = {
  4: [0, 0],
  3: [1, 0],
  2: [2, 0],
  7: [0, 1],
  8: [1, 1],
  9: [2, 1],
  5: [0, 2],
  6: [1, 2],
  1: [2, 2],
}

/** Centre of a zone on the acting team's half (the bottom half, facing up to the net). */
export function ownZone(zone: number): [number, number] | null {
  const z = ZONE[zone]
  if (!z) return null
  return [1.5 + 3 * z[0], NET_Y - 1.5 - 3 * z[1]]
}

/** Centre of a zone on the other half (top), as its team sees it: their left is our right. */
export function otherZone(zone: number): [number, number] | null {
  const z = ZONE[zone]
  if (!z) return null
  return [WIDTH_M - 1.5 - 3 * z[0], NET_Y + 1.5 + 3 * z[1]]
}

/** Where a serve from a zone starts: behind the end line, in that zone's column. */
export function serveStart(zone: number | undefined): [number, number] {
  const z = zone !== undefined ? ZONE[zone] : undefined
  return [z ? 1.5 + 3 * z[0] : WIDTH_M / 2, -1.5]
}

// --- the diagram -----------------------------------------------------------------------------

export type Outcome = 'win' | 'error' | 'play'
export type Evidence = 'ball' | 'zones'

export interface Arrow {
  tag: Tag
  /** Diagram metres, the acting team at the bottom: (0, 0) near left … (9, 18) far right. */
  from: [number, number]
  to: [number, number]
  outcome: Outcome
  evidence: Evidence
  /** The ball came down here (a bounce) or was played here (a low contact). */
  landing: ContactKind | null
}

/** Point for the tagged side (an ace, a kill), its error, or neither (the rally goes on). */
export function outcomeOf(tag: Pick<Tag, 'code' | 'attributes'>, taxonomy: Taxonomy | null): Outcome {
  const grade = tag.attributes.grade
  const skill = taxonomy?.skills.find((s) => s.code === tag.code)
  const ends = grade ? skill?.ends_rally_on[grade] : undefined
  if (ends === 'self') return 'win'
  if (ends === 'other') return 'error'
  return 'play'
}

/** Heights (m) at which the ball is played: a reception or dig ~0.7, a serve's toss ~2.6. */
export const LOW_CONTACT_M = 0.7
export const SERVE_CONTACT_M = 2.6
/** After a serve the ball lands or is received within this window (s); after an attack sooner. */
const WINDOW: Record<string, [number, number]> = { serve: [0.35, 3.0], attack: [0.08, 1.6] }
/** A mapped point this far outside the court is no landing on it. */
const MARGIN_M = 3.5

export interface DiagramInput {
  tags: readonly Tag[]
  taxonomy: Taxonomy | null
  calibration: CourtMapping | null
  track: BallTrack | null
}

export function skillCategory(tag: Pick<Tag, 'code'>, taxonomy: Taxonomy | null): string | null {
  return taxonomy?.skills.find((s) => s.code === tag.code)?.category ?? null
}

function inBounds([x, y]: [number, number]): boolean {
  return x >= -MARGIN_M && x <= WIDTH_M + MARGIN_M && y >= -MARGIN_M && y <= LENGTH_M + MARGIN_M
}

function landingOf(
  tag: Tag,
  category: string,
  input: DiagramInput,
): { at: [number, number]; kind: ContactKind } | null {
  const { calibration, track } = input
  const window = WINDOW[category]
  if (!calibration || !track || !window) return null
  const t = tag.t_ms / 1000
  for (const c of track.contacts) {
    if (c.t < t + window[0]) continue
    if (c.t > t + window[1]) break
    if (c.kind === 'high') continue
    const then = calibrationAt(calibration, c.t * 1000)
    if (!then) continue
    const at = toCourt(then, c.x, c.y, c.kind === 'bounce' ? 0 : LOW_CONTACT_M) ?? (c.kind === 'low' ? toCourt(then, c.x, c.y, 0) : null)
    if (at && inBounds(at)) return { at, kind: c.kind }
  }
  return null
}

/** The serve's start from the ball at the serve (toss height), when the camera is known. */
function serveFromTrack(tag: Tag, input: DiagramInput): [number, number] | null {
  const { track } = input
  const calibration = input.calibration ? calibrationAt(input.calibration, tag.t_ms) : null
  if (!calibration?.camera || !track) return null
  const t = tag.t_ms / 1000
  for (const dt of [0, -0.1, 0.1, -0.2, 0.2, 0.3]) {
    const i = Math.round((t + dt) * track.fps)
    const x = track.x[i] ?? Number.NaN
    const y = track.y[i] ?? Number.NaN
    if (Number.isNaN(x) || Number.isNaN(y)) continue
    const at = toCourt(calibration, x, y, SERVE_CONTACT_M)
    if (at && inBounds(at)) return at
  }
  return null
}

/** Where a serve started: the ball at the toss when tracked (kept behind the end line), else its zone. */
function serveOrigin(tag: Tag, zone: number | undefined, far: boolean, input: DiagramInput): [number, number] {
  const tracked = serveFromTrack(tag, input)
  if (!tracked) return serveStart(zone)
  const [x, y] = far ? tracked : turn(tracked)
  return [Math.min(WIDTH_M + 1, Math.max(-1, x)), Math.min(-0.5, y)]
}

/** Where an attack started: its zone, else the middle of the front row. */
function attackOrigin(zone: number | undefined): [number, number] {
  return (zone !== undefined ? ownZone(zone) : null) ?? [WIDTH_M / 2, NET_Y - 1.5]
}

/**
 * The diagram's arrows: serves and attacks with a place they went to, from the ball (a bounce
 * or the reception mapped onto the court) when the video's court is set up, else from the
 * tags' zones (DataVolley "from" and "to"). The acting team is always drawn at the bottom.
 */
export function diagramArrows(input: DiagramInput): Arrow[] {
  const out: Arrow[] = []
  for (const tag of input.tags) {
    if (tag.deleted_at) continue
    const category = skillCategory(tag, input.taxonomy)
    if (category !== 'serve' && category !== 'attack') continue
    const outcome = outcomeOf(tag, input.taxonomy)
    const zoneFrom = tag.attributes.zone_from
    const zoneTo = tag.attributes.zone_to
    const landing = landingOf(tag, category, input)
    if (landing) {
      // Landing on the near half means the far team played it: turn the court round.
      const far = landing.at[1] >= NET_Y
      const to = far ? landing.at : turn(landing.at)
      const from = category === 'serve' ? serveOrigin(tag, zoneFrom, far, input) : attackOrigin(zoneFrom)
      out.push({ tag, from, to, outcome, evidence: 'ball', landing: landing.kind })
      continue
    }
    const to = zoneTo !== undefined ? otherZone(zoneTo) : null
    if (!to) continue
    const from = category === 'serve' ? serveStart(zoneFrom) : attackOrigin(zoneFrom)
    out.push({ tag, from, to, outcome, evidence: 'zones', landing: null })
  }
  return out
}

export function countOutcomes(arrows: readonly Arrow[]): Record<Outcome, number> {
  const counts: Record<Outcome, number> = { win: 0, error: 0, play: 0 }
  for (const a of arrows) counts[a.outcome] += 1
  return counts
}
