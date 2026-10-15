import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, UnauthorizedError } from './api'
import { keys } from './queryClient'
import { useToast } from './toast'
import type { AdminTeam } from './types'

export function useMe() {
  return useQuery({ queryKey: keys.me, queryFn: ({ signal }) => api.me(signal), staleTime: 5 * 60_000 })
}

export function useTeams() {
  return useQuery({ queryKey: keys.teams, queryFn: ({ signal }) => api.teams(signal) })
}

export function useTeam(id: number) {
  return useQuery({ queryKey: keys.team(id), queryFn: ({ signal }) => api.team(id, signal) })
}

export function useTeamGames(id: number) {
  return useQuery({ queryKey: keys.teamGames(id), queryFn: ({ signal }) => api.teamGames(id, signal) })
}

export function useTeamTrainings(id: number) {
  return useQuery({ queryKey: keys.teamTrainings(id), queryFn: ({ signal }) => api.teamTrainings(id, signal) })
}

export function useAdminTeams() {
  return useQuery({ queryKey: keys.adminTeams, queryFn: ({ signal }) => api.adminTeams(signal) })
}

export function useSyncStatus() {
  return useQuery({ queryKey: keys.sync, queryFn: ({ signal }) => api.syncStatus(signal) })
}

export function useLogout() {
  const client = useQueryClient()
  const toast = useToast()
  const { t } = useTranslation()
  const loggedOut = () => {
    client.removeQueries({ predicate: (query) => query.queryKey[0] !== keys.me[0] })
    client.setQueryData(keys.me, null)
  }
  return useMutation({
    mutationFn: api.logout,
    onSuccess: loggedOut,
    onError: (error) => {
      // Already logged out on the server: same result.
      if (error instanceof UnauthorizedError) loggedOut()
      else toast.error(t('header.logoutFailed'))
    },
  })
}

function patchTeam(list: AdminTeam[] | undefined, id: number, patch: Partial<AdminTeam>) {
  return list?.map((team) => (team.id === id ? { ...team, ...patch } : team))
}

/** Turns Nomal on or off for a team; the switch flips at once and flips back with a toast on failure. */
export function useSetTeamEnabled() {
  const client = useQueryClient()
  const toast = useToast()
  const { t } = useTranslation()
  return useMutation({
    mutationFn: ({ team, enabled }: { team: AdminTeam; enabled: boolean }) => api.setTeamEnabled(team.id, enabled),
    onMutate: async ({ team, enabled }) => {
      await client.cancelQueries({ queryKey: keys.adminTeams })
      client.setQueryData<AdminTeam[]>(keys.adminTeams, (list) => patchTeam(list, team.id, { enabled }))
    },
    onError: (_error, { team }) => {
      client.setQueryData<AdminTeam[]>(keys.adminTeams, (list) => patchTeam(list, team.id, { enabled: team.enabled }))
      toast.error(t('admin.saveFailed', { team: team.name }))
    },
    onSuccess: (updated) => {
      client.setQueryData<AdminTeam[]>(keys.adminTeams, (list) => patchTeam(list, updated.id, updated))
    },
    onSettled: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.teams }),
        client.invalidateQueries({ queryKey: keys.me }),
      ])
    },
  })
}

export function useRunSync() {
  const client = useQueryClient()
  const toast = useToast()
  const { t } = useTranslation()
  return useMutation({
    mutationFn: api.runSync,
    onSuccess: async (status) => {
      client.setQueryData(keys.sync, status)
      if (status.error) toast.error(t('admin.sync.failed', { error: status.error }))
      else toast.success(t('admin.sync.done'))
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.adminTeams }),
        client.invalidateQueries({ queryKey: keys.teams }),
        client.invalidateQueries({ queryKey: keys.me }),
      ])
    },
    onError: () => toast.error(t('admin.sync.requestFailed')),
  })
}
