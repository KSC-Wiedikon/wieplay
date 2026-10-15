import { describe, expect, it, vi } from 'vitest'
import { jsonResponse } from '../test/render'
import {
  api,
  ApiError,
  fetchMe,
  ForbiddenError,
  loginUrl,
  NotFoundError,
  setUnauthorizedHandler,
  UnauthorizedError,
} from './api'
import { createQueryClient, keys } from './queryClient'

const me = {
  member: { id: 123, uuid: 'u-1', name: 'Anna Muster' },
  roles: ['user'],
  is_admin: false,
  sport_admin: [],
  teams: [],
}

function mockFetch(...responses: Response[]) {
  const fetch = vi.fn<typeof globalThis.fetch>()
  for (const response of responses) fetch.mockResolvedValueOnce(response)
  vi.stubGlobal('fetch', fetch)
  return fetch
}

describe('fetchMe', () => {
  it('returns the user when logged in', async () => {
    const fetch = mockFetch(jsonResponse(200, me))
    await expect(fetchMe()).resolves.toEqual(me)
    expect(fetch).toHaveBeenCalledWith('/api/me', expect.objectContaining({ credentials: 'same-origin' }))
  })

  it('returns null on 401 without reporting a logout', async () => {
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)
    mockFetch(jsonResponse(401, { detail: 'Not authenticated' }))
    await expect(fetchMe()).resolves.toBeNull()
    expect(onUnauthorized).not.toHaveBeenCalled()
  })

  it('still throws other errors', async () => {
    mockFetch(jsonResponse(500, { detail: 'Boom' }))
    await expect(fetchMe()).rejects.toMatchObject({ status: 500, detail: 'Boom' })
  })
})

describe('apiRequest', () => {
  it('throws UnauthorizedError on 401 and reports the logout', async () => {
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)
    mockFetch(jsonResponse(401, { detail: 'Session expired' }))

    const error = await api.teams().catch((e: unknown) => e)
    expect(error).toBeInstanceOf(UnauthorizedError)
    expect(error).toBeInstanceOf(ApiError)
    expect(error).toMatchObject({ status: 401, detail: 'Session expired' })
    expect(onUnauthorized).toHaveBeenCalledOnce()
  })

  it('maps 403 and 404 to typed errors without reporting a logout', async () => {
    const onUnauthorized = vi.fn()
    setUnauthorizedHandler(onUnauthorized)
    mockFetch(jsonResponse(403, { detail: 'Forbidden' }), jsonResponse(404, { detail: 'No such team' }))

    await expect(api.adminTeams()).rejects.toBeInstanceOf(ForbiddenError)
    await expect(api.team(7)).rejects.toMatchObject({ name: 'NotFoundError', detail: 'No such team' })
    expect(onUnauthorized).not.toHaveBeenCalled()
  })

  it('falls back to the status text when the error body is not JSON', async () => {
    mockFetch(new Response('<html>Bad gateway</html>', { status: 502, statusText: 'Bad Gateway' }))
    await expect(api.teams()).rejects.toMatchObject({ status: 502, detail: 'Bad Gateway' })
  })

  it('sends JSON bodies and accepts 204', async () => {
    const fetch = mockFetch(jsonResponse(200, { id: 92, enabled: true }), jsonResponse(204))
    await api.setTeamEnabled(92, true)
    expect(fetch).toHaveBeenCalledWith(
      '/api/admin/teams/92',
      expect.objectContaining({ method: 'PUT', body: '{"enabled":true}', credentials: 'same-origin' }),
    )
    await expect(api.logout()).resolves.toBeUndefined()
  })

  it('NotFoundError is an ApiError', () => {
    expect(new NotFoundError('x')).toBeInstanceOf(ApiError)
  })
})

describe('401 with the query client', () => {
  it('invalidates the me query, which then resolves to logged out', async () => {
    const client = createQueryClient()
    client.setQueryData(keys.me, me)
    mockFetch(jsonResponse(401, { detail: 'Session expired' }))

    const invalidate = vi.spyOn(client, 'invalidateQueries')
    await expect(api.teams()).rejects.toBeInstanceOf(UnauthorizedError)
    expect(invalidate).toHaveBeenCalledWith({ queryKey: keys.me })
    expect(client.getQueryState(keys.me)?.isInvalidated).toBe(true)
  })
})

describe('loginUrl', () => {
  it('URL-encodes the path to come back to', () => {
    expect(loginUrl('/teams/92?tab=games&x=1')).toBe('/api/auth/login?next=%2Fteams%2F92%3Ftab%3Dgames%26x%3D1')
  })
})
