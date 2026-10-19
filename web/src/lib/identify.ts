// Identifying the players with the coach (docs/PLAN.md §10.5): a queue of the cards WiePlay is
// not sure of, the biggest share of the footage first, answered from pictures (the video zoomed
// onto the person at several moments); "which team is ours" when the teams are uncertain; and a
// tap on a person in the paused video. Every answer comes back with `undo` (the cards as they
// were) and the cards linked anew around it. No faces: the coach's eyes do the matching.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ApiError, apiRequest } from './api'
import { playersKey, type PlayerCluster, type Players } from './players'
import { keys } from './queryClient'
import { useToast } from './toast'
import type { RosterEntry, Side } from './types'

export interface QueueItem {
  card: string
  /** On court for its team in its rallies (the scoresheet), the most often first; then liberos. */
  on_court: number[]
  /** Cards that may be the same person. */
  same_as: string[]
}

export interface TeamsSide {
  card: string
  crops: [number, number][]
  team: Side
}

export interface TeamsQuestion {
  set: number
  near: TeamsSide[]
  far: TeamsSide[]
  near_team: Side | null
  far_team: Side | null
}

export interface Queue {
  items: QueueItem[]
  named: number
  sure_share: number
  teams: TeamsQuestion | null
}

export type CardState = Record<string, unknown> & { id: string }

export interface Answered {
  players: Players
  queue: Queue
  undo: CardState[]
  relinked: { card: string; into: string; why: string }[]
}

export type Answer =
  | { kind: 'number'; number: number }
  | { kind: 'not_player' }
  | { kind: 'team'; team: Side }
  | { kind: 'same_as'; into: string }
  | { kind: 'split'; tracks: number[] }
  | { kind: 'pieces'; tracks: number[]; number: number | null; not_player: boolean }

export interface Moment {
  t_ms: number
  track: number
  /** u1 v1 u2 v2, 0..1 of the frame. */
  box: [number, number, number, number]
  /** [t_ms, u1, v1, u2, v2] around it. */
  clip: [number, number, number, number, number][]
}

export interface FrameBox {
  track: number
  box: [number, number, number, number]
  card: string
  key: number
  team: Side
  number: number | null
  ocr_number: number | null
  not_player: boolean
  sure: boolean
}

export const queueKey = (videoId: string) => ['videos', videoId, 'players', 'queue'] as const

export function useIdentifyQueue(videoId: string, enabled = true) {
  return useQuery({
    queryKey: queueKey(videoId),
    queryFn: ({ signal }) => apiRequest<Queue>(`/api/videos/${videoId}/players/queue`, { signal }),
    enabled,
  })
}

export function useMoments(videoId: string, cardId: string | null) {
  return useQuery({
    queryKey: ['videos', videoId, 'players', 'moments', cardId] as const,
    queryFn: ({ signal }) => apiRequest<Moment[]>(`/api/videos/${videoId}/players/clusters/${cardId}/moments`, { signal }),
    enabled: cardId !== null,
    staleTime: 5 * 60_000,
  })
}

export function usePlayersFrame(videoId: string, t_ms: number | null) {
  return useQuery({
    queryKey: ['videos', videoId, 'players', 'frame', t_ms] as const,
    queryFn: ({ signal }) => apiRequest<{ t_ms: number | null; boxes: FrameBox[] }>(`/api/videos/${videoId}/players/frame?t_ms=${t_ms}`, { signal }),
    enabled: t_ms !== null,
    staleTime: 60_000,
  })
}

function useIdentifyMutation<T>(videoId: string, run: (body: T) => Promise<Answered>) {
  const client = useQueryClient()
  const toast = useToast()
  const { t } = useTranslation()
  return useMutation({
    mutationFn: run,
    onSuccess: (data) => {
      client.setQueryData(playersKey(videoId), data.players)
      client.setQueryData(queueKey(videoId), data.queue)
      void client.invalidateQueries({ queryKey: keys.analysis(videoId) })
      void client.invalidateQueries({ queryKey: keys.tagging(videoId) })
    },
    onError: (error) => toast.error(t('players.saveFailed', { detail: error instanceof ApiError ? error.detail : t('common.error') })),
  })
}

export function useAnswer(videoId: string) {
  return useIdentifyMutation(videoId, ({ card, answer }: { card: string; answer: Answer }) =>
    apiRequest<Answered>(`/api/videos/${videoId}/players/clusters/${card}/answer`, { method: 'POST', body: answer }),
  )
}

export function useRestore(videoId: string) {
  return useIdentifyMutation(videoId, (cards: CardState[]) => apiRequest<Answered>(`/api/videos/${videoId}/players/restore`, { method: 'POST', body: { cards } }))
}

export function useTap(videoId: string) {
  return useIdentifyMutation(videoId, (body: { t_ms: number; track: number; number: number | null; not_player: boolean }) =>
    apiRequest<Answered>(`/api/videos/${videoId}/players/tap`, { method: 'POST', body }),
  )
}

export function useTeamsAnswer(videoId: string) {
  return useIdentifyMutation(videoId, (near_is_ours: boolean) => apiRequest<Answered>(`/api/videos/${videoId}/players/teams`, { method: 'POST', body: { near_is_ours } }))
}

/**
 * The numbers to offer for a card, in order: those on court in its rallies first (the sheet),
 * then (ours) the rest of the roster. Theirs are typed when not offered.
 */
export function numbersToOffer(team: Side, onCourt: readonly number[], roster: readonly RosterEntry[]): { number: number; name: string | null; onCourt: boolean }[] {
  const names = new Map(roster.filter((r) => r.side === team).map((r) => [r.number, r.name ?? null]))
  const out = onCourt.map((n) => ({ number: n, name: names.get(n) ?? null, onCourt: true }))
  if (team === 'us') {
    const seen = new Set(onCourt)
    for (const r of [...roster].filter((r) => r.side === 'us').sort((a, b) => a.number - b.number)) {
      if (!seen.has(r.number)) out.push({ number: r.number, name: r.name ?? null, onCourt: false })
    }
  }
  return out
}

/** The box of a moment's clip at a time (the nearest sample), else the moment's own. */
export function boxAt(moment: Moment, t_ms: number): [number, number, number, number] {
  let best: [number, number, number, number] = moment.box
  let gap = Infinity
  for (const [t, u1, v1, u2, v2] of moment.clip) {
    const d = Math.abs(t - t_ms)
    if (d < gap) {
      gap = d
      best = [u1, v1, u2, v2]
    }
  }
  return best
}

/**
 * CSS for a video element (frame `aspect` = width / height) inside a view of `viewAspect`, so
 * that the box fills the view's height with some room around it: the person, large.
 */
export function zoomOnto(box: readonly [number, number, number, number], aspect: number, viewAspect: number, room = 1.35) {
  const [u1, v1, u2, v2] = box
  const h = Math.max(0.02, (v2 - v1) * room)
  const w = Math.max(0.02, (u2 - u1) * room)
  // The zoom: the box's height (or width, whichever is tighter) fills the view.
  const scaleH = 1 / h
  const scaleW = viewAspect / (w * aspect)
  const scale = Math.max(1, Math.min(scaleH, scaleW))
  const cu = (u1 + u2) / 2
  const cv = (v1 + v2) / 2
  // The video is `scale` view-heights tall and `scale * aspect / viewAspect` view-widths wide.
  const width = (scale * aspect) / viewAspect
  const height = scale
  const left = 0.5 - cu * width
  const top = 0.5 - cv * height
  return {
    width: `${width * 100}%`,
    height: `${height * 100}%`,
    left: `${left * 100}%`,
    top: `${top * 100}%`,
  }
}

/** WiePlay is sure of the card, or the coach answered it (a number, or not a player). */
export function isSure(c: Pick<PlayerCluster, 'number' | 'not_player' | 'uncertain'>): boolean {
  return c.not_player || c.number !== null || !c.uncertain
}
