import type { AdminTeam, Game, Me, SyncStatus, Team, TeamDetail, Training } from './types'

export class ApiError extends Error {
  readonly status: number
  readonly detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export class UnauthorizedError extends ApiError {
  constructor(detail: string) {
    super(401, detail)
    this.name = 'UnauthorizedError'
  }
}

export class ForbiddenError extends ApiError {
  constructor(detail: string) {
    super(403, detail)
    this.name = 'ForbiddenError'
  }
}

export class NotFoundError extends ApiError {
  constructor(detail: string) {
    super(404, detail)
    this.name = 'NotFoundError'
  }
}

let onUnauthorized: () => void = () => {}

/** Called whenever a request other than GET /api/me comes back 401 (the session ended). */
export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'DELETE'
  body?: unknown
  signal?: AbortSignal
}

async function errorFrom(res: Response): Promise<ApiError> {
  let detail = res.statusText || `HTTP ${res.status}`
  try {
    const data: unknown = await res.json()
    if (data && typeof data === 'object' && 'detail' in data && typeof data.detail === 'string') {
      detail = data.detail
    }
  } catch {
    // Body is not JSON; keep the status text.
  }
  switch (res.status) {
    case 401:
      return new UnauthorizedError(detail)
    case 403:
      return new ForbiddenError(detail)
    case 404:
      return new NotFoundError(detail)
    default:
      return new ApiError(res.status, detail)
  }
}

async function send<T>(path: string, { method = 'GET', body, signal }: RequestOptions): Promise<T> {
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  const res = await fetch(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    credentials: 'same-origin',
    signal,
  })
  if (!res.ok) throw await errorFrom(res)
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
}

/** JSON request against the API. Throws ApiError subclasses; a 401 also reports the logout. */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  try {
    return await send<T>(path, options)
  } catch (error) {
    if (error instanceof UnauthorizedError) onUnauthorized()
    throw error
  }
}

/** The logged-in user, or null when logged out (401 is the normal logged-out answer here). */
export async function fetchMe(signal?: AbortSignal): Promise<Me | null> {
  try {
    return await send<Me>('/api/me', { signal })
  } catch (error) {
    if (error instanceof UnauthorizedError) return null
    throw error
  }
}

/** Login is a browser navigation through Wiedisync, never a fetch. */
export function loginUrl(next: string): string {
  return `/api/auth/login?next=${encodeURIComponent(next)}`
}

export const api = {
  me: fetchMe,
  logout: () => apiRequest<undefined>('/api/auth/logout', { method: 'POST' }),
  teams: (signal?: AbortSignal) => apiRequest<Team[]>('/api/teams', { signal }),
  team: (id: number, signal?: AbortSignal) => apiRequest<TeamDetail>(`/api/teams/${id}`, { signal }),
  teamGames: (id: number, signal?: AbortSignal) =>
    apiRequest<Game[]>(`/api/teams/${id}/games`, { signal }),
  teamTrainings: (id: number, signal?: AbortSignal) =>
    apiRequest<Training[]>(`/api/teams/${id}/trainings`, { signal }),
  adminTeams: (signal?: AbortSignal) => apiRequest<AdminTeam[]>('/api/admin/teams', { signal }),
  setTeamEnabled: (id: number, enabled: boolean) =>
    apiRequest<AdminTeam>(`/api/admin/teams/${id}`, { method: 'PUT', body: { enabled } }),
  syncStatus: (signal?: AbortSignal) => apiRequest<SyncStatus>('/api/admin/sync', { signal }),
  runSync: () => apiRequest<SyncStatus>('/api/admin/sync', { method: 'POST' }),
}
