// The video page's setup (src/components/VideoSetup.tsx): how far each step is (the analysis,
// the ball's path, the court, the scoreboard, the players, the teams' names), and the video's
// own setup facts (PATCH /api/videos/{id}/setup: the names a coach set, "no scoreboard").

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { proposalTotal } from './analysis'
import { apiRequest } from './api'
import type { CourtState } from './court'
import type { PlayersSummary } from './players'
import { keys } from './queryClient'
import { scoreboardKey, type ScoreboardState } from './scoreboard'
import type { Analysis, Video, VideoSetupIn } from './types'

/**
 * How far a step is: done; running (WiePlay works on it); to do (the coach's turn); needs you
 * (something to check or a failure); waiting (nothing to do yet); optional (on demand).
 */
export type StepState = 'done' | 'busy' | 'todo' | 'attention' | 'waiting' | 'optional'
export type StepKey = 'analysis' | 'ball' | 'court' | 'scoreboard' | 'players' | 'names'

export const BUSY_JOBS = ['queued', 'running']

// --- what each step's state is (pure, so the tests can name every case) -------------------------

export function analysisState(analysis: Analysis | undefined, video: Pick<Video, 'rallies_reviewed_at'>, rallies: number): StepState {
  if (!analysis) return 'waiting'
  if (BUSY_JOBS.includes(analysis.status)) return 'busy'
  if (analysis.status === 'failed') return 'attention'
  if (analysis.status === 'none') return 'todo'
  if (analysis.proposals.unreviewed > 0) return 'attention'
  if (video.rallies_reviewed_at) return 'done'
  // Every suggestion answered (or the coach's own rallies): "rallies reviewed" is left.
  return proposalTotal(analysis.proposals) > 0 || rallies > 0 ? 'todo' : 'done'
}

export function ballState(court: CourtState | undefined): StepState {
  const track = court?.ball_track
  if (!track) return 'waiting'
  if (track.available) return 'done'
  if (BUSY_JOBS.includes(track.job)) return 'busy'
  return track.job === 'failed' ? 'attention' : 'waiting'
}

export function courtState(court: CourtState | undefined): StepState {
  if (!court) return 'waiting'
  const calibration = court.calibration
  if (calibration) return calibration.checked === false ? 'attention' : 'done'
  return BUSY_JOBS.includes(court.court_job ?? '') ? 'busy' : 'todo'
}

export function scoreboardState(board: ScoreboardState | undefined, noScoreboard: boolean): StepState {
  // The club scoreboard's log lined up and matching the official result: the score comes from
  // it, nobody needs to draw a box (docs/PLAN.md §10.10).
  if (board?.log?.trusted) return 'done'
  if (!board?.region) return noScoreboard ? 'done' : board ? 'todo' : 'waiting'
  const job = board.job?.status
  if (job && BUSY_JOBS.includes(job)) return 'busy'
  if (job === 'failed') return 'attention'
  if (!board.reading) return 'waiting'
  return board.reading.segments.length > 0 ? 'done' : 'attention'
}

export function playersState(players: PlayersSummary): StepState {
  if (players.run_id !== null) return 'done'
  if (players.status === 'failed') return 'attention'
  return BUSY_JOBS.includes(players.status) ? 'busy' : 'optional'
}

export function namesState(video: Pick<Video, 'names'>): StepState {
  return video.names?.them ? 'done' : 'todo'
}

/** PATCH /api/videos/{id}/setup: the names, "no scoreboard". */
export function useVideoSetup(videoId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: VideoSetupIn) => apiRequest<Video>(`/api/videos/${videoId}/setup`, { method: 'PATCH', body }),
    onSuccess: (video, body) => {
      client.setQueryData(keys.video(videoId), video)
      if (body.no_scoreboard !== undefined) {
        // A box drawn before is forgotten and the match scored again.
        void client.invalidateQueries({ queryKey: scoreboardKey(videoId) })
        void client.invalidateQueries({ queryKey: keys.tagging(videoId) })
        void client.invalidateQueries({ queryKey: keys.analysis(videoId) })
      }
    },
  })
}
