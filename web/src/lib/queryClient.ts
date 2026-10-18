import { QueryClient } from '@tanstack/react-query'
import { ApiError, setUnauthorizedHandler } from './api'

export const keys = {
  me: ['me'] as const,
  teams: ['teams'] as const,
  team: (id: number) => ['teams', id] as const,
  teamSeasons: (id: number) => ['teams', id, 'seasons'] as const,
  teamGames: (id: number) => ['teams', id, 'games'] as const,
  teamVideos: (id: number) => ['teams', id, 'videos'] as const,
  teamFilming: (id: number, on: string) => ['teams', id, 'filming', on] as const,
  upcomingFilming: (id: number) => ['teams', id, 'upcoming-filming'] as const,
  teamHelpers: (id: number) => ['teams', id, 'helpers'] as const,
  teamPeople: (id: number) => ['teams', id, 'people'] as const,
  earlierPeople: (id: number) => ['teams', id, 'people', 'earlier'] as const,
  guestTeams: ['admin', 'guest-teams'] as const,
  invite: (token: string) => ['invite', token] as const,
  helperCandidates: (id: number, q: string) => ['teams', id, 'helpers', 'candidates', q] as const,
  videoConfig: ['video-config'] as const,
  video: (id: string) => ['videos', id] as const,
  tagging: (id: string) => ['videos', id, 'tagging'] as const,
  analysis: (id: string) => ['videos', id, 'analysis'] as const,
  rosterCandidates: (id: string) => ['videos', id, 'roster-candidates'] as const,
  inbox: ['inbox'] as const,
  adminTeams: ['admin', 'teams'] as const,
  sync: ['admin', 'sync'] as const,
  workers: ['admin', 'workers'] as const,
  jobs: (status: string | null) => ['admin', 'jobs', status] as const,
  policies: ['admin', 'policies'] as const,
  importChannels: ['admin', 'import-channels'] as const,
  // M3: clips, reels, comments, exports
  videoClips: (id: string) => ['videos', id, 'clips'] as const,
  myClips: ['clips', 'mine'] as const,
  comments: (id: string) => ['videos', id, 'comments'] as const,
  videoPeople: (id: string) => ['videos', id, 'people'] as const,
  reels: ['reels'] as const,
  reel: (id: string) => ['reels', id] as const,
  reelItems: (id: string) => ['reels', id, 'items'] as const,
  reelPreview: (query: unknown) => ['reels', 'preview', query] as const,
  exports: ['exports'] as const,
  taxonomy: (sport: string) => ['taxonomy', sport] as const,
  // M3b: stats
  videoStats: (id: string) => ['videos', id, 'stats'] as const,
  teamStats: (id: number) => ['teams', id, 'stats'] as const,
}

function shouldRetry(failureCount: number, error: Error): boolean {
  // 4xx answers won't change on retry.
  if (error instanceof ApiError && error.status < 500) return false
  return failureCount < 2
}

export function createQueryClient(): QueryClient {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: shouldRetry, staleTime: 30_000 },
      mutations: { retry: false },
    },
  })
  // A 401 anywhere means the session ended: refetch /api/me, which then
  // resolves to null and the app shows the login screen.
  setUnauthorizedHandler(() => {
    void client.invalidateQueries({ queryKey: keys.me })
  })
  return client
}
