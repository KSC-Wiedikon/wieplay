// Players in a video (docs/PLAN.md §10.5, §5.7): the gallery a coach names once (GET
// /api/videos/{id}/players, polled while the job runs), its actions (name, merge, split, not a
// player, swap teams, find again) and "apply", which takes Nomal's players for the coach's tags
// without a number. A card is "a person in this video": no faces, nothing across videos; the
// number a coach picks is the only identity.

import { queryOptions, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ApiError, apiRequest } from './api'
import { keys } from './queryClient'
import { useToast } from './toast'
import type { AnalysisStatus, RosterEntry, Side } from './types'

export interface PlayerTrack {
  id: number
  t0: number
  t1: number
  /** Detections (at the detection rate, 10 a second). */
  n: number
  side: 'near' | 'far'
  number: number | null
  /** [page, cell] in the contact sheets. */
  crop: [number, number] | null
}

export interface PlayerCluster {
  id: string
  key: number
  team: Side
  /** The coach's. */
  number: number | null
  member_id: number | null
  name: string | null
  not_player: boolean
  /** The job's guess: stays beside the court (referee, line judge, bench). */
  referee_guess: boolean
  /** The number reader's suggestion. */
  ocr_number: number | null
  ocr_confidence: number | null
  crops: [number, number][]
  detections: number
  seconds: number
  actions: number
  source: 'model' | 'split'
  tracks: PlayerTrack[]
}

export interface Sheets {
  count: number
  cell_w: number
  cell_h: number
  cols: number
  rows: number
  version: string
}

export interface Attribution {
  actions: number
  identified: number
  identified_share: number | null
  by_coach: number
  suggested: number
  vs_coach: { compared: number; correct: number; accuracy: number | null }
}

export interface PlayersJob {
  id: string
  status: string
  progress: number | null
  message: string | null
  error: string | null
}

export interface Players {
  video_id: string
  available: boolean
  can_edit: boolean
  status: AnalysisStatus
  job: PlayersJob | null
  run: { id: string; created_at: string; stats: Record<string, unknown>; timing: Record<string, unknown> } | null
  sheets: Sheets | null
  clusters: PlayerCluster[]
  roster: RosterEntry[]
  attribution: Attribution | null
  open_suggestions: number
}

/** The players part of GET /api/videos/{id}/analysis. */
export interface PlayersSummary {
  status: AnalysisStatus
  run_id: string | null
  clusters: number
  named: number
  clusters_us: number
  clusters_them: number
  numbers_read: number
  actions: number
  identified: number
  identified_share: number | null
  suggested: number
  compared: number
  accuracy: number | null
}

export const playersKey = (videoId: string) => ['videos', videoId, 'players'] as const

const BUSY = new Set<AnalysisStatus>(['queued', 'running'])

export function playersQuery(videoId: string) {
  return queryOptions({
    queryKey: playersKey(videoId),
    queryFn: ({ signal }) => apiRequest<Players>(`/api/videos/${videoId}/players`, { signal }),
    refetchInterval: (query) => (query.state.data && BUSY.has(query.state.data.status) ? 5_000 : false),
  })
}

export function usePlayers(videoId: string, enabled = true) {
  return useQuery({ ...playersQuery(videoId), enabled })
}

/** Where a crop sits in its sheet, for a CSS background at `scale` of the cell. */
export function cropStyle(sheets: Sheets, crop: readonly [number, number], scale = 1) {
  const [, cell] = crop
  const col = cell % sheets.cols
  const row = Math.floor(cell / sheets.cols)
  const w = sheets.cell_w * scale
  const h = sheets.cell_h * scale
  return {
    width: `${w}px`,
    height: `${h}px`,
    backgroundSize: `${sheets.cols * w}px auto`,
    backgroundPosition: `${-col * w}px ${-row * h}px`,
  }
}

export function sheetUrl(videoId: string, sheets: Sheets, page: number): string {
  return `/api/videos/${videoId}/players/sheets/${page}?v=${sheets.version}`
}

export interface Grouped {
  us: PlayerCluster[]
  them: PlayerCluster[]
  others: PlayerCluster[]
  /** Seen less than this many seconds: shown folded (a substitute's moment, a stray track). */
  brief: PlayerCluster[]
}

export const BRIEF_SECONDS = 15

/** Cards by team, the most seen first; not-players and brief appearances apart. */
export function groupClusters(clusters: readonly PlayerCluster[]): Grouped {
  const out: Grouped = { us: [], them: [], others: [], brief: [] }
  const sorted = [...clusters].sort((a, b) => b.seconds - a.seconds || a.key - b.key)
  for (const c of sorted) {
    if (c.not_player) out.others.push(c)
    else if (c.seconds < BRIEF_SECONDS && c.number === null) out.brief.push(c)
    else out[c.team].push(c)
  }
  return out
}

/** "#7 Alex Muster", "#12", or "Player 4" until it has a number. */
export function clusterTitle(c: Pick<PlayerCluster, 'number' | 'name' | 'key'>, unnamed: (key: number) => string): string {
  if (c.number === null) return unnamed(c.key)
  return c.name ? `#${c.number} ${c.name}` : `#${c.number}`
}

/** Parses what a coach typed as a number: 0–99, or null for "none"; undefined when invalid. */
export function parseNumber(text: string): number | null | undefined {
  const value = text.trim().replace(/^#/, '')
  if (value === '') return null
  if (!/^\d{1,2}$/.test(value)) return undefined
  return Number(value)
}

function useGalleryMutation<T>(videoId: string, run: (body: T) => Promise<Players>, failKey: 'saveFailed' | 'runFailed') {
  const client = useQueryClient()
  const toast = useToast()
  const { t } = useTranslation()
  return useMutation({
    mutationFn: run,
    onSuccess: (data) => {
      client.setQueryData(playersKey(videoId), data)
      void client.invalidateQueries({ queryKey: keys.analysis(videoId) })
      void client.invalidateQueries({ queryKey: keys.tagging(videoId) })
    },
    onError: (error) =>
      toast.error(t(`players.${failKey}`, { detail: error instanceof ApiError ? error.detail : t('common.error') })),
  })
}

export function useAssignPlayer(videoId: string) {
  return useGalleryMutation(
    videoId,
    ({ id, number, not_player }: { id: string; number: number | null; not_player?: boolean }) =>
      apiRequest<Players>(`/api/videos/${videoId}/players/clusters/${id}`, {
        method: 'PATCH',
        body: { number, not_player: not_player ?? false },
      }),
    'saveFailed',
  )
}

export function useMergePlayers(videoId: string) {
  return useGalleryMutation(
    videoId,
    ({ into, sources }: { into: string; sources: string[] }) =>
      apiRequest<Players>(`/api/videos/${videoId}/players/merge`, { method: 'POST', body: { into, sources } }),
    'saveFailed',
  )
}

export function useSplitPlayer(videoId: string) {
  return useGalleryMutation(
    videoId,
    ({ id, tracks }: { id: string; tracks: number[] }) =>
      apiRequest<Players>(`/api/videos/${videoId}/players/clusters/${id}/split`, { method: 'POST', body: { tracks } }),
    'saveFailed',
  )
}

export function useSwapTeams(videoId: string) {
  return useGalleryMutation(
    videoId,
    () => apiRequest<Players>(`/api/videos/${videoId}/players/swap-teams`, { method: 'POST' }),
    'saveFailed',
  )
}

export function useFindPlayers(videoId: string) {
  return useGalleryMutation(
    videoId,
    () => apiRequest<Players>(`/api/videos/${videoId}/players/run`, { method: 'POST' }),
    'runFailed',
  )
}

export function useApplySuggestions(videoId: string) {
  return useGalleryMutation(
    videoId,
    async () => (await apiRequest<{ applied: number; players: Players }>(`/api/videos/${videoId}/players/apply`, { method: 'POST', body: {} })).players,
    'saveFailed',
  )
}
