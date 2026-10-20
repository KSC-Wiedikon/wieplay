// The automatic analysis of a video (docs/PLAN.md §10.1) and the learning loop around it:
// its state (GET /api/videos/{id}/analysis, polled while it runs), "Analyse" (POST
// …/analyse) and "Rallies reviewed" (POST …/rallies-reviewed, which makes the server retrain).

import type { TFunction } from 'i18next'
import { queryOptions, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { api, ApiError } from './api'
import { keys } from './queryClient'
import { useToast } from './toast'
import type { Analysis, AnalysisStatus, ProposalCounts, Tagging, Video } from './types'

const BUSY: ReadonlySet<AnalysisStatus> = new Set(['queued', 'running'])

export function isBusy(analysis: Pick<Analysis, 'status'> | null | undefined): boolean {
  return analysis != null && BUSY.has(analysis.status)
}

/** Every rally WiePlay made for the video, whatever happened to it. */
export function proposalTotal(counts: ProposalCounts): number {
  return (counts.auto ?? 0) + (counts.check ?? 0) + (counts.unreviewed ?? 0) + counts.confirmed + counts.corrected + counts.rejected
}

/** WiePlay's rallies that are still to check (older servers: the unanswered ones). */
export function proposalsToCheck(counts: ProposalCounts): number {
  return (counts.check ?? 0) + (counts.unreviewed ?? 0)
}

/**
 * The chain in one line (PLAN §10): "Analysing: 4 of 7 steps" while a step is queued or running,
 * "Analysed" once all are through (with how many failed). Null from older servers.
 */
export function stepsLine(t: TFunction, data: Pick<Analysis, 'steps'>): string | null {
  const steps = data.steps
  if (!steps || steps.total === 0) return null
  if (steps.running) return t('tagging.analysis.steps', { done: steps.done, total: steps.total })
  const failed = steps.items.filter((s) => s.status === 'failed').length
  return failed > 0 ? t('tagging.analysis.analysedFailed', { count: failed }) : t('tagging.analysis.analysed')
}

/** Refreshes every 5 s while the analysis (or the suggested actions) is queued or running. */
export function analysisQuery(videoId: string) {
  return queryOptions({
    queryKey: keys.analysis(videoId),
    queryFn: ({ signal }) => api.analysis(videoId, signal),
    refetchInterval: (query) => (isBusy(query.state.data) || isBusy(query.state.data?.actions) || query.state.data?.steps?.running ? 5_000 : false),
  })
}

export function useAnalysis(videoId: string, enabled: boolean) {
  return useQuery({ ...analysisQuery(videoId), enabled })
}

/** Queues an analysis; the status shows "queued" at once. */
export function useAnalyse(videoId: string) {
  const client = useQueryClient()
  const toast = useToast()
  const { t } = useTranslation()
  return useMutation({
    mutationFn: () => api.analyse(videoId),
    onSuccess: async () => {
      client.setQueryData<Analysis>(keys.analysis(videoId), (old) => (old ? { ...old, status: 'queued' } : old))
      await client.invalidateQueries({ queryKey: keys.analysis(videoId) })
    },
    onError: (error) =>
      toast.error(t('tagging.analysis.analyseFailed', { detail: error instanceof ApiError ? error.detail : t('common.error') })),
  })
}

function withReviewed<T extends { rallies_reviewed_at?: string | null }>(value: T | undefined, at: string | null): T | undefined {
  return value ? { ...value, rallies_reviewed_at: at } : value
}

/**
 * Marks the video's rallies reviewed (or not). Shown at once on the video and in the tagging
 * data; the server retrains its rally finder with the game.
 */
/**
 * The server refuses "reviewed" while something is still to check: 409 {"detail": {"to_check": n}}
 * (older servers: "open_proposals" / "open_suggestions").
 */
export function toCheckIn(error: unknown): number | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null
  const body = error.body
  if (!body || typeof body !== 'object' || !('detail' in body)) return null
  const detail = body.detail as Record<string, unknown> | null
  if (!detail || typeof detail !== 'object') return null
  for (const key of ['to_check', 'open_proposals', 'open_suggestions']) {
    if (typeof detail[key] === 'number') return detail[key]
  }
  return null
}

export function useSetRalliesReviewed(videoId: string) {
  const client = useQueryClient()
  const toast = useToast()
  const { t } = useTranslation()
  return useMutation({
    mutationFn: (reviewed: boolean) => api.setRalliesReviewed(videoId, reviewed),
    onMutate: async (reviewed) => {
      await client.cancelQueries({ queryKey: keys.video(videoId), exact: true })
      const before = client.getQueryData<Video>(keys.video(videoId))?.rallies_reviewed_at ?? null
      const at = reviewed ? new Date().toISOString() : null
      client.setQueryData<Video>(keys.video(videoId), (v) => withReviewed(v, at))
      client.setQueryData<Tagging>(keys.tagging(videoId), (b) => withReviewed(b, at))
      return { before }
    },
    onError: (error, _reviewed, context) => {
      const at = context?.before ?? null
      client.setQueryData<Video>(keys.video(videoId), (v) => withReviewed(v, at))
      client.setQueryData<Tagging>(keys.tagging(videoId), (b) => withReviewed(b, at))
      const open = toCheckIn(error)
      if (open !== null) toast.error(t('tagging.analysis.openProposals', { count: open }))
      else toast.error(t('tagging.analysis.reviewedFailed', { detail: error instanceof ApiError ? error.detail : t('common.error') }))
    },
    onSuccess: (out, reviewed) => {
      if (out && 'rallies_reviewed_at' in out) {
        const at = out.rallies_reviewed_at ?? null
        client.setQueryData<Video>(keys.video(videoId), (v) => withReviewed(v, at))
        client.setQueryData<Tagging>(keys.tagging(videoId), (b) => withReviewed(b, at))
      }
      toast.success(reviewed ? t('tagging.analysis.reviewedOn') : t('tagging.analysis.reviewedOff'))
    },
    onSettled: async () => {
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.video(videoId), exact: true }),
        client.invalidateQueries({ queryKey: keys.analysis(videoId) }),
      ])
    },
  })
}

/**
 * Marks the video's actions reviewed (or not): its tags then teach WiePlay's action proposer.
 * Shown at once in the tagging data.
 */
export function useSetActionsReviewed(videoId: string) {
  const client = useQueryClient()
  const toast = useToast()
  const { t } = useTranslation()
  return useMutation({
    mutationFn: (reviewed: boolean) => api.setActionsReviewed(videoId, reviewed),
    onSuccess: (out, reviewed) => {
      const at = out && 'actions_reviewed_at' in out ? (out.actions_reviewed_at ?? null) : null
      client.setQueryData<Tagging>(keys.tagging(videoId), (b) => (b ? { ...b, actions_reviewed_at: at } : b))
      toast.success(reviewed ? t('actions.reviewedOn') : t('actions.reviewedOff'))
    },
    onError: (error) => {
      const open = toCheckIn(error)
      if (open !== null) toast.error(t('actions.openSuggestions', { count: open }))
      else toast.error(t('actions.reviewedFailed', { detail: error instanceof ApiError ? error.detail : t('common.error') }))
    },
    onSettled: async () => {
      await client.invalidateQueries({ queryKey: keys.analysis(videoId) })
    },
  })
}
