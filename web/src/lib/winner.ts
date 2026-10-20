// Who won a rally, set by a coach outside the tagging workspace (the video page's rally list):
// one change through POST /api/videos/{id}/changes. The winner is a fact: the server keeps it,
// scores the match again around it and answers with the new score line. Shown at once: the
// score line is computed here first, as the server does (src/lib/rallies.ts).

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import { personTags } from './facts'
import { keys } from './queryClient'
import { rescore, serveCodes } from './rallies'
import { segmentToIn } from './taggingStore'
import type { Review, Segment, SegmentIn, Side, Tagging } from './types'

/**
 * The change: the rally as it is with the winner. WiePlay's rally with another winner than its
 * own is corrected; with WiePlay's own winner it is confirmed (as the server has it).
 */
export function winnerChange(rally: Segment, won_by: Side | null): SegmentIn {
  let review: Review = rally.review
  if (rally.source === 'model' && (rally.won_by ?? null) !== won_by && review !== 'rejected' && review !== 'merged') {
    review = won_by !== null && !rally.won_by && won_by === rally.model_won_by && review !== 'corrected' ? 'confirmed' : 'corrected'
  }
  return { ...segmentToIn(rally), won_by, review }
}

/** The tagging data with a rally's winner changed and the score line computed again. */
export function withWinner(data: Tagging, id: string, won_by: Side | null): Tagging {
  const segments = data.segments.map((s) => (s.id === id ? { ...s, won_by, review: winnerChange(s, won_by).review ?? s.review } : s))
  const scored = rescore(data.periods, segments, personTags(data.events), serveCodes(data.taxonomy))
  return { ...data, segments: scored.segments, periods: scored.periods }
}

export interface WinnerCall {
  rally: Segment
  won_by: Side | null
}

export function useSetWinner(videoId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ rally, won_by }: WinnerCall) => api.postChanges(videoId, { events: [], periods: [], segments: [winnerChange(rally, won_by)] }),
    onMutate: async ({ rally, won_by }: WinnerCall) => {
      await client.cancelQueries({ queryKey: keys.tagging(videoId) })
      const before = client.getQueryData<Tagging>(keys.tagging(videoId))
      if (before) client.setQueryData<Tagging>(keys.tagging(videoId), withWinner(before, rally.id, won_by))
      return { before }
    },
    // The server's score line (the model scored again around the new fact).
    onSuccess: (out) =>
      client.setQueryData<Tagging>(keys.tagging(videoId), (old) =>
        old ? { ...old, segments: out.segments.filter((s) => s.deleted_at === null), periods: out.periods.filter((p) => p.deleted_at === null) } : old,
      ),
    onError: (_error, _call, context) => {
      if (context?.before) client.setQueryData(keys.tagging(videoId), context.before)
    },
    onSettled: () => {
      void client.invalidateQueries({ queryKey: keys.tagging(videoId) })
      void client.invalidateQueries({ queryKey: keys.analysis(videoId) })
      void client.invalidateQueries({ queryKey: keys.videoStats(videoId) })
    },
  })
}
