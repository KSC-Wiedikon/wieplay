// The video page's setup (src/components/VideoSetup.tsx): how far each step is (the analysis,
// the ball's path, the court, the scoreboard, the scoresheet, the players, the teams' names), and
// the video's own setup facts (PATCH /api/videos/{id}/setup: the names a coach set, "no
// scoreboard", "no scoresheet").

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { proposalsToCheck, proposalTotal } from './analysis'
import { apiRequest } from './api'
import type { CourtState } from './court'
import type { PlayersSummary } from './players'
import type { Scoresheet } from './scoresheet'
import { keys } from './queryClient'
import { scoreboardKey, type ScoreboardState } from './scoreboard'
import type { Analysis, Video, VideoSetupIn } from './types'

/**
 * How far a step is: done; running (WiePlay works on it); to do (the coach's turn); needs you
 * (something to check or a failure); waiting (nothing to do yet); optional (on demand).
 */
export type StepState = 'done' | 'busy' | 'todo' | 'attention' | 'waiting' | 'optional'
export type StepKey = 'analysis' | 'ball' | 'court' | 'scoreboard' | 'sheet' | 'players' | 'names'

export const BUSY_JOBS = ['queued', 'running']

// --- what each step's state is (pure, so the tests can name every case) -------------------------

export function analysisState(analysis: Analysis | undefined, video: Pick<Video, 'rallies_reviewed_at'>, rallies: number): StepState {
  if (!analysis) return 'waiting'
  if (BUSY_JOBS.includes(analysis.status)) return 'busy'
  if (analysis.status === 'failed') return 'attention'
  if (analysis.status === 'none') return 'todo'
  if ((analysis.to_check?.rallies ?? proposalsToCheck(analysis.proposals)) > 0) return 'attention'
  if (video.rallies_reviewed_at) return 'done'
  // Nothing left to check (or the coach's own rallies): "rallies reviewed" is left.
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

export function scoreboardState(board: ScoreboardState | undefined, noScoreboard: boolean, sheetTrusted = false): StepState {
  // The club scoreboard's log lined up and matching the official result, or the official
  // scoresheet with every set fitting it: the score comes from them, nobody needs to draw a box
  // (docs/PLAN.md §10.10, §10.3a).
  if (board?.log?.trusted || sheetTrusted) return 'done'
  if (!board?.region) return noScoreboard ? 'done' : board ? 'todo' : 'waiting'
  const job = board.job?.status
  if (job && BUSY_JOBS.includes(job)) return 'busy'
  if (job === 'failed') return 'attention'
  if (!board.reading) return 'waiting'
  return board.reading.segments.length > 0 ? 'done' : 'attention'
}

/**
 * The official scoresheet (docs/PLAN.md §10.3a): done when every set fits the official result
 * (or a coach said there is none); needs you when a set doesn't fit; to do for a league game
 * without one (every league match has a sheet), optional for a friendly.
 */
export function sheetState(sheet: Scoresheet | undefined, video: Pick<Video, 'game_id' | 'no_scoresheet'>): StepState {
  if (!sheet) return 'waiting'
  if (!sheet.exists) return video.no_scoresheet ? 'done' : video.game_id !== null ? 'todo' : 'optional'
  if (sheet.trusted) return 'done'
  return sheet.checks.some((c) => c.status === 'inconsistent') ? 'attention' : 'todo'
}

export function playersState(players: PlayersSummary): StepState {
  if (players.run_id !== null) return 'done'
  if (players.status === 'failed') return 'attention'
  return BUSY_JOBS.includes(players.status) ? 'busy' : 'optional'
}

export function namesState(video: Pick<Video, 'names'>): StepState {
  return video.names?.them ? 'done' : 'todo'
}

/** PATCH /api/videos/{id}/setup: the names, "no scoreboard", "no scoresheet". */
export function useVideoSetup(videoId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: VideoSetupIn) => apiRequest<Video>(`/api/videos/${videoId}/setup`, { method: 'PATCH', body }),
    onSuccess: (video, body) => {
      client.setQueryData(keys.video(videoId), video)
      if (body.no_scoresheet !== undefined) void client.invalidateQueries({ queryKey: ['videos', videoId, 'scoresheet'] })
      if (body.no_scoreboard !== undefined) {
        // A box drawn before is forgotten and the match scored again.
        void client.invalidateQueries({ queryKey: scoreboardKey(videoId) })
        void client.invalidateQueries({ queryKey: keys.tagging(videoId) })
        void client.invalidateQueries({ queryKey: keys.analysis(videoId) })
      }
    },
  })
}
