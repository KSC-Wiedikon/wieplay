import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { AdminTeam, Me } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { AdminTeamsPage } from './AdminTeamsPage'

const me: Me = {
  member: { id: 1, uuid: '00000000-0000-4000-8000-000000000101', name: 'Luca' },
  roles: [],
  is_admin: true,
  sport_admin: [],
  teams: [],
}

const team = (id: number, name: string, season: string, enabled = false): AdminTeam => ({
  id,
  name,
  sport: 'volleyball',
  season,
  league: '3. Liga',
  enabled,
})

const teams = [team(92, 'H3', '2026/27', true), team(93, 'D1', '2026/27'), team(3, 'H3', '2025/26', true)]

function open(url = '/admin/teams') {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      stubFetch({
        '/api/admin/teams': { body: () => teams },
        '/api/admin/sync': {
          body: () => ({ last_run: null, last_ok: null, error: null, counts: { teams: 3, members: 0, games: 0 } }),
        },
      }),
    ),
  )
  return renderRoute(<AdminTeamsPage />, { path: '/admin/teams', url, me })
}

const rowNames = (table: HTMLElement) =>
  within(table)
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getByText(/^(H3|D1)$/).textContent)

describe('AdminTeamsPage', () => {
  it('shows one season at a time, the newest first', async () => {
    const { router } = open()
    const season = await screen.findByLabelText('Season')
    expect(season).toHaveValue('2026/27')
    expect(within(season).getAllByRole('option').map((o) => o.textContent)).toEqual(['2026/27', '2025/26'])
    expect(rowNames(await screen.findByRole('table', { name: 'Volleyball' }))).toEqual(['D1', 'H3'])

    fireEvent.change(season, { target: { value: '2025/26' } })
    await waitFor(() => expect(rowNames(screen.getByRole('table', { name: 'Volleyball' }))).toEqual(['H3']))
    expect(screen.getByRole('switch', { name: 'H3 2025/26 uses WiePlay' })).toBeChecked()
    expect(router.state.location.search).toBe('?season=2025%2F26')
  })

  it('opens the season given in the address', async () => {
    open('/admin/teams?season=2025%2F26')
    expect(await screen.findByLabelText('Season')).toHaveValue('2025/26')
    expect(rowNames(await screen.findByRole('table', { name: 'Volleyball' }))).toEqual(['H3'])
  })

  it('falls back to the newest season for one without teams', async () => {
    open('/admin/teams?season=2019%2F20')
    expect(await screen.findByLabelText('Season')).toHaveValue('2026/27')
  })
})
