import { QueryClient } from '@tanstack/react-query'
import { ApiError, setUnauthorizedHandler } from './api'

export const keys = {
  me: ['me'] as const,
  teams: ['teams'] as const,
  team: (id: number) => ['teams', id] as const,
  teamGames: (id: number) => ['teams', id, 'games'] as const,
  teamVideos: (id: number) => ['teams', id, 'videos'] as const,
  teamFilming: (id: number, on: string) => ['teams', id, 'filming', on] as const,
  upcomingFilming: (id: number) => ['teams', id, 'upcoming-filming'] as const,
  videoConfig: ['video-config'] as const,
  video: (id: string) => ['videos', id] as const,
  inbox: ['inbox'] as const,
  adminTeams: ['admin', 'teams'] as const,
  sync: ['admin', 'sync'] as const,
  workers: ['admin', 'workers'] as const,
  jobs: (status: string | null) => ['admin', 'jobs', status] as const,
  policies: ['admin', 'policies'] as const,
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
