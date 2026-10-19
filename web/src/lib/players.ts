// Players in a video (docs/PLAN.md §10.5, §5.7): the gallery a coach names once (GET
// /api/videos/{id}/players, polled while the job runs), its actions (name, merge, split, not a
// player, swap teams, find again) and "apply", which takes WiePlay's players for the coach's tags
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
  /** Players v2: how sure WiePlay is that the card is one whole person (0..1); null for older runs. */
  confidence?: number | null
  /** The card asks to be checked (unsure, no number yet). Older servers: absent. */
  uncertain?: boolean
  /**
   * Players v3: `person` (one person), or `pieces`: look-alike pieces without a number, most
   * often of several teammates (they share the kit); the coach names one person's at a time.
   * Older servers: absent (a person).
   */
  kind?: 'person' | 'pieces'
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
  /** Start finding the players: coaches and helpers. Older servers: absent. */
  can_find?: boolean
  /** What the search takes, in minutes (~120 on the club server, ~20 on the GPU). */
  find_minutes?: number
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
  /** Cards WiePlay is unsure of. Older servers: absent. */
  uncertain?: number
  find_minutes?: number
}

/** "2 hours", "20 min": how long finding the players takes (older servers: 2 hours). */
export function useSearchTime(minutes: number | undefined): string {
  const { t } = useTranslation()
  const m = minutes ?? 120
  return m >= 90 ? t('players.hours', { count: Math.round(m / 60) }) : t('players.minutes', { count: Math.max(1, Math.round(m)) })
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
  /** Cards WiePlay is unsure of (players v2): shown folded, by team, after the teams. */
  check: PlayerCluster[]
}

export const BRIEF_SECONDS = 15

/** Cards by team, the most seen first; not-players and brief appearances apart. */
export function groupClusters(clusters: readonly PlayerCluster[]): Grouped {
  const out: Grouped = { us: [], them: [], others: [], brief: [], check: [] }
  const sorted = [...clusters].sort((a, b) => b.seconds - a.seconds || a.key - b.key)
  for (const c of sorted) {
    if (c.not_player) out.others.push(c)
    else if (isPieces(c)) out.check.push(c)
    else if (c.seconds < BRIEF_SECONDS && c.number === null) out.brief.push(c)
    else if (c.uncertain) out.check.push(c)
    else out[c.team].push(c)
  }
  out.check.sort((a, b) => Number(a.team === 'them') - Number(b.team === 'them') || b.seconds - a.seconds)
  return out
}

/** A pieces card: look-alike pieces of (most often) several people, not one person. */
export function isPieces(c: Pick<PlayerCluster, 'kind' | 'number'>): boolean {
  return c.kind === 'pieces' && c.number === null
}

/** "#7 Alex Muster", "#12", or "Player 4" until it has a number ("Pieces 9" for a pieces card). */
export function clusterTitle(
  c: Pick<PlayerCluster, 'number' | 'name' | 'key'> & Partial<Pick<PlayerCluster, 'kind'>>,
  unnamed: (key: number) => string,
  pieces?: (key: number) => string,
): string {
  if (c.number === null) return pieces && c.kind === 'pieces' ? pieces(c.key) : unnamed(c.key)
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
    () => apiRequest<Players>(`/api/videos/${videoId}/players/find`, { method: 'POST' }),
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
