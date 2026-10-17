import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api } from './api'
import { keys } from './queryClient'
import { useToast } from './toast'
import type { Helper } from './types'

/** The search needs this many letters (the API answers [] below it). */
export const MIN_SEARCH = 2

/** A team's helpers (for those who may pick them: `can_grant`). */
export function useTeamHelpers(teamId: number, enabled: boolean) {
  return useQuery({
    queryKey: keys.teamHelpers(teamId),
    queryFn: ({ signal }) => api.teamHelpers(teamId, signal),
    enabled,
  })
}

/** Club members matching `q` who could help: name and teams. Keeps the last answer while typing. */
export function useHelperCandidates(teamId: number, q: string) {
  const query = q.trim()
  return useQuery({
    queryKey: keys.helperCandidates(teamId, query),
    queryFn: ({ signal }) => api.helperCandidates(teamId, query, signal),
    enabled: query.replace(/\s/g, '').length >= MIN_SEARCH,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  })
}

async function refresh(client: ReturnType<typeof useQueryClient>, teamId: number) {
  await client.invalidateQueries({ queryKey: keys.teamHelpers(teamId) })
}

export function useAddHelper(teamId: number) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ memberId, note }: { memberId: number; note: string | null }) => api.addHelper(teamId, memberId, note),
    onSuccess: () => refresh(client, teamId),
  })
}

export function useRemoveHelper(teamId: number) {
  const client = useQueryClient()
  const toast = useToast()
  const { t } = useTranslation()
  return useMutation({
    mutationFn: (helper: Helper) => api.removeHelper(teamId, helper.id),
    onSuccess: async (_data, helper) => {
      toast.success(t('team.helpers.removed', { name: helper.name }))
      await refresh(client, teamId)
    },
    onError: (_error, helper) => toast.error(t('team.helpers.removeFailed', { name: helper.name })),
  })
}
