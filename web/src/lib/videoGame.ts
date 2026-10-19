import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiRequest } from './api'
import { scoreOf, setsOf } from './games'
import { keys } from './queryClient'
import type { GameChoices, Video, VideoGame } from './types'

// A video's Wiedisync game, set or changed after the upload: the team's own game of the day, or
// another KSCW team's (a cup team shared by several club teams plays under one of them).

export const gameChoicesKey = (videoId: string) => ['videos', videoId, 'game-choices'] as const
export const teamGameChoicesKey = (teamId: number, on: string) => ['teams', teamId, 'game-choices', on] as const

/** The games of the video's day it can be linked to (coaches, sport admins, admins). */
export function useGameChoices(videoId: string, enabled = true) {
  return useQuery({
    queryKey: gameChoicesKey(videoId),
    queryFn: ({ signal }) => apiRequest<GameChoices>(`/api/videos/${videoId}/game-choices`, { signal }),
    enabled,
  })
}

/** The games of a day for a new video of the team (the upload page's hint). Never retried: it's only a hint. */
export function useTeamGameChoices(teamId: number, on: string | null, enabled = true) {
  return useQuery({
    queryKey: teamGameChoicesKey(teamId, on ?? ''),
    queryFn: ({ signal }) => apiRequest<GameChoices>(`/api/teams/${teamId}/game-choices?on=${encodeURIComponent(on ?? '')}`, { signal }),
    enabled: enabled && on !== null,
    retry: false,
    staleTime: 5 * 60_000,
  })
}

/** Link the video to a game, or to none. The official result changes, so the score is computed again. */
export function useSetVideoGame(video: Pick<Video, 'id' | 'team_id'>) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (gameId: number | null) => apiRequest<Video>(`/api/videos/${video.id}/game`, { method: 'PUT', body: { game_id: gameId } }),
    onSuccess: (updated) => {
      client.setQueryData(keys.video(video.id), updated)
      for (const queryKey of [
        gameChoicesKey(video.id),
        keys.tagging(video.id),
        keys.analysis(video.id),
        keys.videoStats(video.id),
        keys.teamVideos(video.team_id),
        ['videos', video.id, 'scoresheet'],
      ]) {
        void client.invalidateQueries({ queryKey })
      }
    },
  })
}

/** "Volley S9 – KSC Wiedikon H1" */
export function gameTeams(game: Pick<VideoGame, 'home_name' | 'away_name'>): string {
  return `${game.home_name ?? '?'} – ${game.away_name ?? '?'}`
}

/** Where Wiedisync has the game: "H1 · Mobiliar Volley Cup" for another team's, the league for the team's own. */
export function gamePlace(game: VideoGame): string {
  const parts = game.other_team ? [game.team_name, game.league] : [game.league]
  return parts.filter(Boolean).join(' · ')
}

/** The official result from our side: "3:0 (25:13, 30:28, 25:21)"; '' before there is one. */
export function gameResult(game: VideoGame): string {
  const final = scoreOf(game)
  if (!final) return ''
  const sets = setsOf(game)
  return sets ? `${final} (${sets})` : final
}
