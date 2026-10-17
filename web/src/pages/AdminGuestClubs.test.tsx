import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { seasonOf } from '../lib/guests'
import type { GuestTeam } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { GuestClubsSection } from './AdminGuestClubs'

const GAST: GuestTeam = {
  id: 1000000001,
  lineage_id: '6f1c0a52-2a43-4c11-9a43-333333333333',
  club: 'VBC Gast',
  name: 'Herren 2',
  sport: 'volleyball',
  season: '2026/27',
  league: '3. Liga',
  enabled: true,
  training_ok: false,
  video_limit: 30,
  videos: 4,
  people: 9,
  accounts: 6,
  adults_declared_at: null,
}

function requests(fetch: ReturnType<typeof vi.fn>, method: string, path: string): unknown[] {
  return fetch.mock.calls
    .filter(([url, init]) => url === path && ((init as RequestInit | undefined)?.method ?? 'GET') === method)
    .map(([, init]) => JSON.parse(((init as RequestInit).body as string | undefined) ?? 'null') as unknown)
}

describe('Admin → Teams → Guest clubs', () => {
  it('lists guest teams with their counts and switches', async () => {
    const fetch = vi.fn(
      stubFetch({
        '/api/admin/guest-teams': { body: [GAST] },
        [`PUT /api/admin/guest-teams/${GAST.id}`]: { body: { ...GAST, training_ok: true } },
      }),
    )
    vi.stubGlobal('fetch', fetch)
    renderRoute(<GuestClubsSection />)
    const table = await screen.findByRole('table', { name: 'Guest clubs' })
    expect(within(table).getByText('VBC Gast · Herren 2')).toBeInTheDocument()
    expect(within(table).getByText('4 of 30')).toBeInTheDocument()
    expect(within(table).getByText('Adults not confirmed yet')).toBeInTheDocument()
    expect(within(table).getByRole('link', { name: /Open the team page of VBC Gast Herren 2/ })).toHaveAttribute(
      'href',
      `/teams/${GAST.id}`,
    )
    fireEvent.click(within(table).getByRole('switch', { name: "VBC Gast Herren 2 2026/27's games may train Nomal" }))
    await waitFor(() =>
      expect(requests(fetch, 'PUT', `/api/admin/guest-teams/${GAST.id}`)).toEqual([{ training_ok: true }]),
    )
  })

  it('creates a guest team and makes its coach’s invite link at once', async () => {
    const link = 'http://localhost:5173/join/xyz'
    const fetch = vi.fn(
      stubFetch({
        '/api/admin/guest-teams': { body: [] },
        'POST /api/admin/guest-teams': { status: 201, body: GAST },
        [`POST /api/admin/guest-teams/${GAST.id}/coaches`]: {
          status: 201,
          body: {
            link,
            expires_at: '2026-10-31T10:00:00Z',
            person: {
              member_id: 1000000002, name: 'Gina Gast', number: null, role: 'coach', account: false,
              disabled: false, last_login_at: null, invite_expires_at: '2026-10-31T10:00:00Z', me: false,
            },
          },
        },
      }),
    )
    vi.stubGlobal('fetch', fetch)
    renderRoute(<GuestClubsSection />)
    expect(await screen.findByText('No guest teams yet.')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Create a guest team' }))
    const create = await screen.findByRole('dialog', { name: 'Create a guest team' })
    expect(within(create).getByLabelText('Season')).toHaveValue(seasonOf(new Date().toISOString().slice(0, 10)))
    fireEvent.change(within(create).getByLabelText('Club'), { target: { value: 'VBC Gast' } })
    fireEvent.change(within(create).getByLabelText('Team'), { target: { value: 'Herren 2' } })
    fireEvent.change(within(create).getByLabelText('Season'), { target: { value: '2026/27' } })
    fireEvent.click(within(create).getByRole('button', { name: 'Create a guest team' }))
    await waitFor(() =>
      expect(requests(fetch, 'POST', '/api/admin/guest-teams')).toEqual([
        { club: 'VBC Gast', name: 'Herren 2', league: null, season: '2026/27', video_limit: 30, training_ok: false },
      ]),
    )

    const invite = await screen.findByRole('dialog', { name: 'Invite a coach to VBC Gast Herren 2 2026/27' })
    fireEvent.change(within(invite).getByLabelText("Coach's name"), { target: { value: 'Gina Gast' } })
    fireEvent.click(within(invite).getByRole('button', { name: 'Make the invite link' }))
    expect(await within(invite).findByText(link)).toBeInTheDocument()
    expect(requests(fetch, 'POST', `/api/admin/guest-teams/${GAST.id}/coaches`)).toEqual([
      { name: 'Gina Gast', number: null },
    ])
  })

  it('knows the season of a day', () => {
    expect(seasonOf('2026-10-17')).toBe('2026/27')
    expect(seasonOf('2027-05-31')).toBe('2026/27')
    expect(seasonOf('2027-06-01')).toBe('2027/28')
  })
})
