// What WiePlay knows about a team (docs/PLAN.md §10.5b): our players with their numbers and
// how many games named each, the usual service orders and starting rotations, the models'
// scores for the team. Coaches fix a wrong number ↔ player link. Shirt numbers only: no faces,
// no looks, nothing about the opponents.

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { ApiError, apiRequest } from './api'
import { keys } from './queryClient'
import { useToast } from './toast'

export interface MemoryPlayer {
  number: number
  member_id: number | null
  name: string | null
  libero: boolean
  /** The team's games that named the number this season. */
  games: number
  answers: number
  /** Sets in the starting six (a substitute counts half). */
  sets: number
  last_on: string | null
  source: 'roster' | 'learned' | 'coach'
  /** Several roster members wear it and nobody said who plays. */
  ambiguous: boolean
  fixed: boolean
  hidden: boolean
}

export interface MemoryMember {
  id: number
  name: string
  number: number | null
}

export interface MemoryOrder {
  /** The service order, the setter first. */
  order: number[]
  share: number
}

export interface MemoryRotations {
  sets: number
  orders: MemoryOrder[]
  /** The setter's zone at the first serve: share per zone "1"-"6". */
  start_zones: Record<string, number>
  setters: number[]
  liberos: number[]
  /** Our server named right at our serves on the team's own sheets (each game left out). */
  check: { sets?: number; serves?: number; right?: number; sure?: number; sure_right?: number }
}

export interface ModelScore {
  trained_at: string | null
  games: number
  v0: number | null
  v1: number | null
  team: { variant: string; games: number; model?: boolean } | null
}

export interface TeamMemory {
  team_id: number
  available: boolean
  games: number
  refreshed_at: string | null
  players: MemoryPlayer[]
  rotations: MemoryRotations | null
  models: {
    rallies?: ModelScore
    actions?: ModelScore
    players?: { games: number; sure_share: number | null }
  }
  can_fix: boolean
  members: MemoryMember[]
}

export const memoryKey = (teamId: number) => [...keys.team(teamId), 'memory'] as const

export function useTeamMemory(teamId: number) {
  return useQuery({
    queryKey: memoryKey(teamId),
    queryFn: ({ signal }) => apiRequest<TeamMemory>(`/api/teams/${teamId}/memory`, { signal }),
    retry: false,
  })
}

export type Fix = { number: number; member_id: number | null; hidden: boolean } | { number: number; reset: true }

export function useFixNumber(teamId: number) {
  const client = useQueryClient()
  const toast = useToast()
  const { t } = useTranslation()
  return useMutation({
    mutationFn: (fix: Fix) =>
      'reset' in fix
        ? apiRequest<TeamMemory>(`/api/teams/${teamId}/memory/players/${fix.number}`, { method: 'DELETE' })
        : apiRequest<TeamMemory>(`/api/teams/${teamId}/memory/players/${fix.number}`, {
            method: 'PUT',
            body: { member_id: fix.member_id, hidden: fix.hidden },
          }),
    onSuccess: (data, fix) => {
      client.setQueryData(memoryKey(teamId), data)
      toast.success(t('memory.fixed', { number: fix.number }))
    },
    onError: (error) => toast.error(t('memory.fixFailed', { detail: error instanceof ApiError ? error.detail : t('common.error') })),
  })
}

/** The players worth showing: named in a game, fixed, or a roster number with a member. */
export function shownPlayers(players: readonly MemoryPlayer[]): MemoryPlayer[] {
  return [...players].sort((a, b) => b.games - a.games || b.sets - a.sets || a.number - b.number)
}

/** "Zone I 50 %" … for the starting rotations, the most common first. */
export function startZones(zones: Record<string, number>): { zone: number; share: number }[] {
  return Object.entries(zones)
    .map(([z, share]) => ({ zone: Number(z), share }))
    .filter((z) => z.zone >= 1 && z.zone <= 6)
    .sort((a, b) => b.share - a.share || a.zone - b.zone)
}

const ROMAN = ['I', 'II', 'III', 'IV', 'V', 'VI']

export function romanZone(zone: number): string {
  return ROMAN[zone - 1] ?? String(zone)
}
