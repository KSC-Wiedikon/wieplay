import { QueryClient } from '@tanstack/react-query'
import { ApiError, setUnauthorizedHandler } from './api'

export const keys = {
  me: ['me'] as const,
  teams: ['teams'] as const,
  team: (id: number) => ['teams', id] as const,
  teamGames: (id: number) => ['teams', id, 'games'] as const,
  teamTrainings: (id: number) => ['teams', id, 'trainings'] as const,
  adminTeams: ['admin', 'teams'] as const,
  sync: ['admin', 'sync'] as const,
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
