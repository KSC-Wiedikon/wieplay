import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, ApiError, UnauthorizedError } from './api'
import { keys } from './queryClient'
import { useToast } from './toast'
import type { AdminTeam, ImportChannel, Job, Policy, PolicyUpdate, Video, Worker } from './types'

export function useMe() {
  return useQuery({ queryKey: keys.me, queryFn: ({ signal }) => api.me(signal), staleTime: 5 * 60_000 })
}

export function useTeams() {
  return useQuery({ queryKey: keys.teams, queryFn: ({ signal }) => api.teams(signal) })
}

export function useTeam(id: number) {
  return useQuery({ queryKey: keys.team(id), queryFn: ({ signal }) => api.team(id, signal) })
}

/** The seasons of the team's lineage I can see, newest first. */
export function useTeamSeasons(id: number, enabled = true) {
  return useQuery({ queryKey: keys.teamSeasons(id), queryFn: ({ signal }) => api.teamSeasons(id, signal), enabled })
}

export function useTeamGames(id: number) {
  return useQuery({ queryKey: keys.teamGames(id), queryFn: ({ signal }) => api.teamGames(id, signal) })
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

// Videos

const BUSY: ReadonlySet<Video['status']> = new Set(['importing', 'queued', 'processing'])

export function useVideoConfig() {
  return useQuery({ queryKey: keys.videoConfig, queryFn: ({ signal }) => api.videoConfig(signal), staleTime: 5 * 60_000 })
}

/** Refreshes every 10 s while a video of the team is being imported or processed. */
export function useTeamVideos(teamId: number) {
  return useQuery({
    queryKey: keys.teamVideos(teamId),
    queryFn: ({ signal }) => api.teamVideos(teamId, signal),
    refetchInterval: (query) => (query.state.data?.some((v) => BUSY.has(v.status)) ? 10_000 : false),
  })
}

/** Refreshes every 5 s while the video is importing, queued or processing. */
export function useVideo(id: string) {
  return useQuery({
    queryKey: keys.video(id),
    queryFn: ({ signal }) => api.video(id, signal),
    refetchInterval: (query) => (query.state.data && BUSY.has(query.state.data.status) ? 5_000 : false),
  })
}

/** Tags, rallies, sets and the roster snapshot of a ready video (players can read it too). */
export function useTagging(id: string, enabled: boolean) {
  return useQuery({
    queryKey: keys.tagging(id),
    queryFn: ({ signal }) => api.tagging(id, signal),
    enabled,
  })
}

/** The team's players (any season), for the roster editor (coaches, sport admins). */
export function useRosterCandidates(videoId: string) {
  return useQuery({
    queryKey: keys.rosterCandidates(videoId),
    queryFn: ({ signal }) => api.rosterCandidates(videoId, signal),
    staleTime: 0,
  })
}

export function useFilming(teamId: number, on: string | null) {
  return useQuery({
    queryKey: keys.teamFilming(teamId, on ?? ''),
    queryFn: ({ signal }) => api.filming(teamId, on ?? '', signal),
    enabled: on !== null,
    staleTime: 0,
  })
}

export function useUpcomingFilming(teamId: number) {
  return useQuery({ queryKey: keys.upcomingFilming(teamId), queryFn: ({ signal }) => api.upcomingFilming(teamId, signal) })
}

export function useInbox(enabled: boolean) {
  return useQuery({ queryKey: keys.inbox, queryFn: ({ signal }) => api.inbox(signal), enabled })
}

export function useDeleteVideo() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (video: Video) => api.deleteVideo(video.id),
    onSuccess: async (_data, video) => {
      await client.invalidateQueries({ queryKey: keys.teamVideos(video.team_id) })
    },
  })
}

// Admin: workers, jobs, filming policies

export function useWorkers() {
  return useQuery({ queryKey: keys.workers, queryFn: ({ signal }) => api.workers(signal) })
}

export function useCreateWorker() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ name, caps }: { name: string; caps: string[] }) => api.createWorker(name, caps),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: keys.workers })
    },
  })
}

function patchWorker(client: ReturnType<typeof useQueryClient>, updated: Worker) {
  client.setQueryData<Worker[]>(keys.workers, (list) => list?.map((w) => (w.id === updated.id ? updated : w)))
}

export function useSetWorkerPaused() {
  const client = useQueryClient()
  const toast = useToast()
  const { t } = useTranslation()
  return useMutation({
    mutationFn: ({ worker, paused }: { worker: Worker; paused: boolean }) => api.setWorkerPaused(worker.id, paused),
    onSuccess: (updated) => patchWorker(client, updated),
    onError: (_error, { worker }) => toast.error(t('admin.workers.saveFailed', { name: worker.name })),
  })
}

export function useRevokeWorker() {
  const client = useQueryClient()
  const toast = useToast()
  const { t } = useTranslation()
  return useMutation({
    mutationFn: (worker: Worker) => api.revokeWorker(worker.id),
    onSuccess: async (_data, worker) => {
      toast.success(t('admin.workers.revoked', { name: worker.name }))
      await client.invalidateQueries({ queryKey: keys.workers })
    },
    onError: (_error, worker) => toast.error(t('admin.workers.saveFailed', { name: worker.name })),
  })
}

/** Recent jobs; refreshes every 10 s. */
export function useJobs(status: string | null) {
  return useQuery({
    queryKey: keys.jobs(status),
    queryFn: ({ signal }) => api.jobs(status, signal),
    refetchInterval: 10_000,
  })
}

export function useRetryJob() {
  const client = useQueryClient()
  const toast = useToast()
  const { t } = useTranslation()
  return useMutation({
    mutationFn: (job: Job) => api.retryJob(job.id),
    onSuccess: async () => {
      toast.success(t('admin.jobs.retried'))
      await client.invalidateQueries({ queryKey: ['admin', 'jobs'] })
    },
    onError: (error) =>
      toast.error(t('admin.jobs.retryFailed', { detail: error instanceof ApiError ? error.detail : t('common.error') })),
  })
}

export function usePolicies() {
  return useQuery({ queryKey: keys.policies, queryFn: ({ signal }) => api.policies(signal) })
}

export function useSetPolicy() {
  const client = useQueryClient()
  const toast = useToast()
  const { t } = useTranslation()
  return useMutation({
    mutationFn: ({ policy, update }: { policy: Policy; update: PolicyUpdate }) =>
      api.setPolicy(policy.lineage_id, update),
    onSuccess: async (updated) => {
      client.setQueryData<Policy[]>(keys.policies, (list) =>
        list?.map((p) => (p.lineage_id === updated.lineage_id ? updated : p)),
      )
      toast.success(t('admin.policies.saved', { team: updated.label }))
      // Filming hints on team pages depend on the policy.
      await client.invalidateQueries({ queryKey: keys.teams })
    },
    onError: (_error, { policy }) => toast.error(t('admin.saveFailed', { team: policy.label })),
  })
}

// Admin: the import allow-list

export function useImportChannels() {
  return useQuery({ queryKey: keys.importChannels, queryFn: ({ signal }) => api.importChannels(signal) })
}

export function useAddImportChannel() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ channel, label }: { channel: string; label: string }) => api.addImportChannel(channel, label),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: keys.importChannels })
    },
  })
}

export function useRemoveImportChannel() {
  const client = useQueryClient()
  const toast = useToast()
  const { t } = useTranslation()
  return useMutation({
    mutationFn: (channel: ImportChannel) => api.removeImportChannel(channel.id),
    onSuccess: async (_data, channel) => {
      toast.success(t('admin.importChannels.removed', { label: channel.label }))
      await client.invalidateQueries({ queryKey: keys.importChannels })
    },
    onError: (_error, channel) => toast.error(t('admin.importChannels.removeFailed', { label: channel.label })),
  })
}
