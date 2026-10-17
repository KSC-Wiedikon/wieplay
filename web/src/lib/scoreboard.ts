// The scoreboard in the picture (docs/PLAN.md §10.3, step 5): the box a coach draws around the
// score once per hall (GET/PUT/DELETE /api/videos/{id}/scoreboard, POST …/scoreboard/read),
// optionally a second box for the set counts, what the hall's memory offers, the reading job's
// state and what the board showed and when.

import { queryOptions, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiRequest } from './api'

export type BoardKind = 'flip' | 'led' | 'auto'

/** A box in the picture, normalised to the frame (0..1). */
export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

export interface ScoreboardRegion {
  id: string
  rect: Rect
  sets: Rect | null
  kind: BoardKind
  hall_name: string | null
  at_ms: number | null
  copied_from: string | null
  updated_at: string
}

export interface ScoreboardSuggestion {
  /** hall: the last box drawn in this hall; team: the team's last one. */
  source: 'hall' | 'team'
  region: ScoreboardRegion
  video_title: string
  recorded_on: string
  hall_name: string | null
}

export interface BoardSegment {
  first_ms: number
  last_ms: number
  left: number
  right: number
  set_no: number
  confidence: number
  kind: 'start' | 'step' | 'jump' | 'set'
}

export interface ScoreboardState {
  video_id: string
  can_edit: boolean
  aspect: number
  region: ScoreboardRegion | null
  suggestion: ScoreboardSuggestion | null
  job: { status: 'queued' | 'running' | 'done' | 'failed' | 'cancelled'; progress: number | null; error: string | null } | null
  reading: { at: string; summary: Record<string, unknown>; segments: BoardSegment[] } | null
}

export interface SaveScoreboard {
  rect: Rect
  sets?: Rect | null
  kind: BoardKind
  at_ms?: number | null
  copied_from?: string | null
}

export const scoreboardKey = (videoId: string) => ['videos', videoId, 'scoreboard'] as const

export function scoreboardQuery(videoId: string) {
  return queryOptions({
    queryKey: scoreboardKey(videoId),
    queryFn: ({ signal }) => apiRequest<ScoreboardState>(`/api/videos/${videoId}/scoreboard`, { signal }),
    // While the board is being read, look again now and then.
    refetchInterval: (query) => (['queued', 'running'].includes(query.state.data?.job?.status ?? '') ? 10_000 : false),
  })
}

export function useScoreboard(videoId: string, enabled = true) {
  return useQuery({ ...scoreboardQuery(videoId), enabled, retry: false })
}

function useScoreboardMutation<T>(videoId: string, call: (body: T) => Promise<ScoreboardState | undefined>) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: call,
    onSuccess: (state) => {
      if (state) client.setQueryData(scoreboardKey(videoId), state)
      void client.invalidateQueries({ queryKey: scoreboardKey(videoId) })
      // The score line follows the board once it is read.
      void client.invalidateQueries({ queryKey: ['videos', videoId] })
    },
  })
}

export function useSaveScoreboard(videoId: string) {
  return useScoreboardMutation(videoId, (body: SaveScoreboard) =>
    apiRequest<ScoreboardState>(`/api/videos/${videoId}/scoreboard`, { method: 'PUT', body }),
  )
}

export function useReadScoreboard(videoId: string) {
  return useScoreboardMutation(videoId, () => apiRequest<ScoreboardState>(`/api/videos/${videoId}/scoreboard/read`, { method: 'POST' }))
}

export function useRemoveScoreboard(videoId: string) {
  return useScoreboardMutation(videoId, async () => {
    await apiRequest<undefined>(`/api/videos/${videoId}/scoreboard`, { method: 'DELETE' })
    return undefined
  })
}

const clamp01 = (v: number) => Math.min(1, Math.max(0, v))

/** The box between two corners dragged on the picture (any direction), inside the frame. */
export function rectFrom(a: [number, number], b: [number, number]): Rect {
  const x0 = clamp01(Math.min(a[0], b[0]))
  const y0 = clamp01(Math.min(a[1], b[1]))
  const x1 = clamp01(Math.max(a[0], b[0]))
  const y1 = clamp01(Math.max(a[1], b[1]))
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 }
}

/** Big enough to read: a few pixels of a 1080p frame each way. */
export function usable(rect: Rect | null): rect is Rect {
  return rect !== null && rect.w >= 0.008 && rect.h >= 0.008
}

/** "Set 2 · 22:20"-like summary of a reading: how many scores it saw and the last one. */
export function lastScore(state: ScoreboardState | undefined): BoardSegment | null {
  const segments = state?.reading?.segments ?? []
  return segments.length ? (segments[segments.length - 1] ?? null) : null
}
