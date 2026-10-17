import type {
  AdminTeam,
  Analysis,
  Blocker,
  ChangesIn,
  ChangesOut,
  CoachInvited,
  EventRevision,
  FilmingCheck,
  Game,
  GuestInvite,
  GuestPerson,
  GuestPersonIn,
  GuestTeam,
  GuestTeamCreate,
  GuestTeamUpdate,
  Helper,
  HelperCandidate,
  ImportChannel,
  InboxFile,
  InviteLink,
  Job,
  Me,
  PartUrl,
  Policy,
  PolicyUpdate,
  RosterCandidate,
  RosterEntry,
  RosterEntryIn,
  Season,
  SyncStatus,
  Tagging,
  Taxonomy,
  Team,
  TeamDetail,
  UpcomingFilming,
  UploadedPart,
  Video,
  VideoConfig,
  VideoCreate,
  Worker,
  WorkerCreated,
} from './types'

export class ApiError extends Error {
  readonly status: number
  readonly detail: string
  /** The parsed JSON error body, or null when it wasn't JSON. */
  readonly body: unknown

  constructor(status: number, detail: string, body: unknown = null) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
    this.body = body
  }
}

export class UnauthorizedError extends ApiError {
  constructor(detail: string, body: unknown = null) {
    super(401, detail, body)
    this.name = 'UnauthorizedError'
  }
}

export class ForbiddenError extends ApiError {
  constructor(detail: string, body: unknown = null) {
    super(403, detail, body)
    this.name = 'ForbiddenError'
  }
}

export class NotFoundError extends ApiError {
  constructor(detail: string, body: unknown = null) {
    super(404, detail, body)
    this.name = 'NotFoundError'
  }
}

function isBlocker(value: unknown): value is Blocker {
  if (!value || typeof value !== 'object') return false
  const b = value as Record<string, unknown>
  return (
    typeof b.code === 'string' &&
    (b.count === null || b.count === undefined || typeof b.count === 'number') &&
    (b.detail === null || b.detail === undefined || typeof b.detail === 'string')
  )
}

/** The reasons from a 409 "filming not allowed" answer; null for any other error. */
export function blockersOf(error: unknown): Blocker[] | null {
  if (!(error instanceof ApiError) || error.status !== 409) return null
  const body = error.body
  if (!body || typeof body !== 'object' || !('blockers' in body) || !Array.isArray(body.blockers)) return null
  return body.blockers.filter(isBlocker).map((b) => ({ code: b.code, count: b.count ?? null, detail: b.detail ?? null }))
}

/** FastAPI's detail: a string, or a list of validation errors (first message is used). */
function detailOf(body: unknown): string | null {
  if (!body || typeof body !== 'object' || !('detail' in body)) return null
  const { detail } = body
  if (typeof detail === 'string') return detail
  // {"message": …, …more fields}: e.g. 409 "suggestions still open" with their count.
  if (detail && typeof detail === 'object' && 'message' in detail && typeof detail.message === 'string') {
    return detail.message
  }
  if (Array.isArray(detail)) {
    const first: unknown = detail[0]
    if (first && typeof first === 'object' && 'msg' in first && typeof first.msg === 'string') return first.msg
  }
  return null
}

let onUnauthorized: () => void = () => {}

/** Called whenever a request other than GET /api/me comes back 401 (the session ended). */
export function setUnauthorizedHandler(handler: () => void): void {
  onUnauthorized = handler
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  signal?: AbortSignal
}

async function errorFrom(res: Response): Promise<ApiError> {
  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    // Body is not JSON; keep the status text.
  }
  const detail = detailOf(body) ?? (res.statusText || `HTTP ${res.status}`)
  switch (res.status) {
    case 401:
      return new UnauthorizedError(detail, body)
    case 403:
      return new ForbiddenError(detail, body)
    case 404:
      return new NotFoundError(detail, body)
    default:
      return new ApiError(res.status, detail, body)
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

/** Guest clubs log in with email and password (PLAN §4.5). A wrong one is a 401 like any other. */
export function guestLogin(email: string, password: string): Promise<undefined> {
  return send<undefined>('/api/auth/guest/login', { method: 'POST', body: { email, password } })
}

/** What an invite link is for; 404 unknown, 410 used or expired. Needs no session. */
export function inviteInfo(token: string, signal?: AbortSignal): Promise<GuestInvite> {
  return send<GuestInvite>(`/api/auth/guest/invite/${encodeURIComponent(token)}`, { signal })
}

/** Sets up the login (or a new password) from an invite link and logs in; answers where to go. */
export function acceptInvite(
  token: string,
  body: { email: string | null; password: string; accept_privacy: true },
): Promise<{ next: string }> {
  return send<{ next: string }>(`/api/auth/guest/invite/${encodeURIComponent(token)}`, { method: 'POST', body })
}

export const api = {
  me: fetchMe,
  logout: () => apiRequest<undefined>('/api/auth/logout', { method: 'POST' }),
  teams: (signal?: AbortSignal) => apiRequest<Team[]>('/api/teams', { signal }),
  team: (id: number, signal?: AbortSignal) => apiRequest<TeamDetail>(`/api/teams/${id}`, { signal }),
  teamSeasons: (id: number, signal?: AbortSignal) => apiRequest<Season[]>(`/api/teams/${id}/seasons`, { signal }),
  teamGames: (id: number, signal?: AbortSignal) =>
    apiRequest<Game[]>(`/api/teams/${id}/games`, { signal }),
  // Team helpers (PLAN §5.1 "analyst"): coaches, team managers, sport admins and admins pick them
  teamHelpers: (id: number, signal?: AbortSignal) => apiRequest<Helper[]>(`/api/teams/${id}/helpers`, { signal }),
  helperCandidates: (id: number, q: string, signal?: AbortSignal) =>
    apiRequest<HelperCandidate[]>(`/api/teams/${id}/helpers/candidates?q=${encodeURIComponent(q)}`, { signal }),
  addHelper: (id: number, memberId: number, note: string | null) =>
    apiRequest<Helper>(`/api/teams/${id}/helpers`, { method: 'POST', body: { member_id: memberId, note } }),
  removeHelper: (id: number, grantId: string) =>
    apiRequest<undefined>(`/api/teams/${id}/helpers/${grantId}`, { method: 'DELETE' }),
  // A guest club's people (PLAN §4.5): its coaches and admins keep the roster and make invite links
  teamPeople: (id: number, signal?: AbortSignal) => apiRequest<GuestPerson[]>(`/api/teams/${id}/people`, { signal }),
  addPerson: (id: number, body: GuestPersonIn) =>
    apiRequest<GuestPerson>(`/api/teams/${id}/people`, { method: 'POST', body }),
  editPerson: (id: number, memberId: number, body: Partial<GuestPersonIn>) =>
    apiRequest<GuestPerson>(`/api/teams/${id}/people/${memberId}`, { method: 'PATCH', body }),
  removePerson: (id: number, memberId: number) =>
    apiRequest<undefined>(`/api/teams/${id}/people/${memberId}`, { method: 'DELETE' }),
  invitePerson: (id: number, memberId: number) =>
    apiRequest<InviteLink>(`/api/teams/${id}/people/${memberId}/invite`, { method: 'POST' }),
  declareAdults: (id: number) =>
    apiRequest<{ adults_declared_at: string }>(`/api/teams/${id}/adults`, { method: 'POST' }),
  guestTeams: (signal?: AbortSignal) => apiRequest<GuestTeam[]>('/api/admin/guest-teams', { signal }),
  createGuestTeam: (body: GuestTeamCreate) =>
    apiRequest<GuestTeam>('/api/admin/guest-teams', { method: 'POST', body }),
  updateGuestTeam: (id: number, body: GuestTeamUpdate) =>
    apiRequest<GuestTeam>(`/api/admin/guest-teams/${id}`, { method: 'PUT', body }),
  inviteCoach: (id: number, body: { name: string; number: number | null }) =>
    apiRequest<CoachInvited>(`/api/admin/guest-teams/${id}/coaches`, { method: 'POST', body }),
  adminTeams: (signal?: AbortSignal) => apiRequest<AdminTeam[]>('/api/admin/teams', { signal }),
  setTeamEnabled: (id: number, enabled: boolean) =>
    apiRequest<AdminTeam>(`/api/admin/teams/${id}`, { method: 'PUT', body: { enabled } }),
  syncStatus: (signal?: AbortSignal) => apiRequest<SyncStatus>('/api/admin/sync', { signal }),
  runSync: () => apiRequest<SyncStatus>('/api/admin/sync', { method: 'POST' }),

  // Videos
  videoConfig: (signal?: AbortSignal) => apiRequest<VideoConfig>('/api/video/config', { signal }),
  teamVideos: (teamId: number, signal?: AbortSignal) =>
    apiRequest<Video[]>(`/api/teams/${teamId}/videos`, { signal }),
  video: (id: string, signal?: AbortSignal) => apiRequest<Video>(`/api/videos/${id}`, { signal }),
  createVideo: (teamId: number, body: VideoCreate) =>
    apiRequest<Video>(`/api/teams/${teamId}/videos`, { method: 'POST', body }),
  deleteVideo: (id: string) => apiRequest<undefined>(`/api/videos/${id}`, { method: 'DELETE' }),
  signParts: (videoId: string, fileId: string, parts: number[], signal?: AbortSignal) =>
    apiRequest<PartUrl[]>(`/api/videos/${videoId}/files/${fileId}/parts`, { method: 'POST', body: { parts }, signal }),
  uploadedParts: (videoId: string, fileId: string, signal?: AbortSignal) =>
    apiRequest<UploadedPart[]>(`/api/videos/${videoId}/files/${fileId}/parts`, { signal }),
  completeFile: (videoId: string, fileId: string, signal?: AbortSignal) =>
    apiRequest<Video>(`/api/videos/${videoId}/files/${fileId}/complete`, { method: 'POST', signal }),
  inbox: (signal?: AbortSignal) => apiRequest<InboxFile[]>('/api/inbox', { signal }),

  // Tagging (M2)
  tagging: (videoId: string, signal?: AbortSignal) =>
    apiRequest<Tagging>(`/api/videos/${videoId}/tagging`, { signal }),
  postChanges: (videoId: string, body: ChangesIn, signal?: AbortSignal) =>
    apiRequest<ChangesOut>(`/api/videos/${videoId}/changes`, { method: 'POST', body, signal }),
  putRoster: (videoId: string, entries: RosterEntryIn[]) =>
    apiRequest<RosterEntry[]>(`/api/videos/${videoId}/roster`, { method: 'PUT', body: { entries } }),
  rosterCandidates: (videoId: string, signal?: AbortSignal) =>
    apiRequest<RosterCandidate[]>(`/api/videos/${videoId}/roster/candidates`, { signal }),
  eventRevisions: (eventId: string, signal?: AbortSignal) =>
    apiRequest<EventRevision[]>(`/api/events/${eventId}/revisions`, { signal }),
  taxonomy: (sport: string, signal?: AbortSignal) =>
    apiRequest<Taxonomy>(`/api/taxonomies/${encodeURIComponent(sport)}`, { signal }),

  // Automatic analysis (PLAN §10.1): rally proposals, and "rallies reviewed" (WiePlay retrains)
  analysis: (videoId: string, signal?: AbortSignal) =>
    apiRequest<Analysis>(`/api/videos/${videoId}/analysis`, { signal }),
  analyse: (videoId: string) => apiRequest<unknown>(`/api/videos/${videoId}/analyse`, { method: 'POST' }),
  /** Scores the video again now (server, winner, score, sets, gaps; PLAN §10.3); 409 without scoring. */
  scoring: (videoId: string) => apiRequest<Analysis>(`/api/videos/${videoId}/scoring`, { method: 'POST' }),
  setRalliesReviewed: (videoId: string, reviewed: boolean) =>
    apiRequest<{ rallies_reviewed_at?: string | null } | undefined>(`/api/videos/${videoId}/rallies-reviewed`, {
      method: 'POST',
      body: { reviewed },
    }),
  // Suggested actions (PLAN §10.4a): suggest again, and "actions reviewed" (WiePlay retrains)
  suggestActions: (videoId: string) => apiRequest<Analysis>(`/api/videos/${videoId}/actions`, { method: 'POST' }),
  setActionsReviewed: (videoId: string, reviewed: boolean) =>
    apiRequest<{ actions_reviewed_at?: string | null } | undefined>(`/api/videos/${videoId}/actions-reviewed`, {
      method: 'POST',
      body: { reviewed },
    }),

  // Filming rules
  filming: (teamId: number, on: string, signal?: AbortSignal) =>
    apiRequest<FilmingCheck>(`/api/teams/${teamId}/filming?on=${encodeURIComponent(on)}`, { signal }),
  upcomingFilming: (teamId: number, signal?: AbortSignal) =>
    apiRequest<UpcomingFilming[]>(`/api/teams/${teamId}/upcoming-filming`, { signal }),

  // Admin: workers, jobs, filming policies
  workers: (signal?: AbortSignal) => apiRequest<Worker[]>('/api/admin/workers', { signal }),
  createWorker: (name: string, caps: string[]) =>
    apiRequest<WorkerCreated>('/api/admin/workers', { method: 'POST', body: { name, caps } }),
  setWorkerPaused: (id: string, paused: boolean) =>
    apiRequest<Worker>(`/api/admin/workers/${id}`, { method: 'PUT', body: { paused } }),
  revokeWorker: (id: string) => apiRequest<undefined>(`/api/admin/workers/${id}`, { method: 'DELETE' }),
  retryJob: (id: string) => apiRequest<Job>(`/api/admin/jobs/${id}/retry`, { method: 'POST' }),
  jobs: (status: string | null, signal?: AbortSignal) =>
    apiRequest<Job[]>(`/api/admin/jobs?limit=100${status ? `&status=${encodeURIComponent(status)}` : ''}`, { signal }),
  policies: (signal?: AbortSignal) => apiRequest<Policy[]>('/api/admin/policies', { signal }),
  setPolicy: (lineageId: string, body: PolicyUpdate) =>
    apiRequest<Policy>(`/api/admin/policies/${lineageId}`, { method: 'PUT', body }),

  // Admin: channels whose videos may be imported from a link
  importChannels: (signal?: AbortSignal) => apiRequest<ImportChannel[]>('/api/admin/import-channels', { signal }),
  addImportChannel: (channel: string, label: string) =>
    apiRequest<ImportChannel>('/api/admin/import-channels', { method: 'POST', body: { channel, label } }),
  removeImportChannel: (id: string) =>
    apiRequest<undefined>(`/api/admin/import-channels/${id}`, { method: 'DELETE' }),
}
