import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Team } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { HomePage } from './HomePage'

const team = (id: number, name: string, sport: Team['sport'], rel: string[] = ['player']): Team => ({
  id,
  name,
  sport,
  season: '2026/27',
  league: '2L',
  rel,
})

describe('HomePage', () => {
  it('groups teams by sport, volleyball first', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        stubFetch({
          '/api/teams': {
            body: [
              team(1, 'Herren 1', 'basketball', ['coach']),
              team(92, 'H3', 'volleyball', ['player', 'captain']),
              team(10, 'D10', 'volleyball'),
              team(2, 'D2', 'volleyball', []),
            ],
          },
        }),
      ),
    )

    renderRoute(<HomePage />)

    const volleyball = await screen.findByRole('region', { name: 'Volleyball' })
    const basketball = screen.getByRole('region', { name: 'Basketball' })
    const headings = screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    expect(headings).toEqual(['Volleyball', 'Basketball'])

    // Natural sort within a sport: D2 before D10.
    const vbTeams = within(volleyball)
      .getAllByRole('link')
      .map((link) => link.textContent)
    expect(vbTeams).toEqual(['D2', 'D10', 'H3'])
    expect(within(basketball).getAllByRole('link').map((link) => link.textContent)).toEqual(['Herren 1'])

    expect(within(volleyball).getAllByText('VB')).toHaveLength(3)
    expect(within(basketball).getByText('BB')).toBeInTheDocument()

    const h3 = within(volleyball).getByRole('row', { name: /H3/ })
    expect(h3).toHaveTextContent('Player, Captain')
    expect(within(volleyball).getByRole('row', { name: /D2/ })).toHaveTextContent('Admin access')
    expect(within(volleyball).getByRole('link', { name: 'H3' })).toHaveAttribute('href', '/teams/92')
  })

  it('leaves out a sport without teams', async () => {
    vi.stubGlobal('fetch', vi.fn(stubFetch({ '/api/teams': { body: [team(92, 'H3', 'volleyball')] } })))
    renderRoute(<HomePage />)
    expect(await screen.findByRole('region', { name: 'Volleyball' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Basketball' })).not.toBeInTheDocument()
  })

  it('asks for consent when there is no answer, or one to an older notice', async () => {
    const consent = (status: string | null, accepted_version: string | null) => ({
      notice_version: '2027-01',
      accepted_version,
      status,
      via: status ? 'nomal' : null,
      at: status ? '2026-10-12T10:00:00Z' : null,
    })
    vi.stubGlobal('fetch', vi.fn(stubFetch({ '/api/teams': { body: [] }, '/api/me/consent': { body: consent(null, null) } })))
    const first = renderRoute(<HomePage />)
    expect(await screen.findByText(/Read how, and tell us whether you agree/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Read and answer' })).toHaveAttribute('href', '/consent')
    first.unmount()

    vi.stubGlobal(
      'fetch',
      vi.fn(stubFetch({ '/api/teams': { body: [] }, '/api/me/consent': { body: consent('accepted', '2026-10-draft') } })),
    )
    const second = renderRoute(<HomePage />)
    expect(await screen.findByText('The information notice changed. Please read it and answer again.')).toBeInTheDocument()
    second.unmount()

    vi.stubGlobal(
      'fetch',
      vi.fn(stubFetch({ '/api/teams': { body: [] }, '/api/me/consent': { body: consent('accepted', '2027-01') } })),
    )
    renderRoute(<HomePage />)
    expect(await screen.findByText('None of your teams uses Nomal yet.')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Read and answer' })).not.toBeInTheDocument()
  })

  it('shows the empty state', async () => {
    vi.stubGlobal('fetch', vi.fn(stubFetch({ '/api/teams': { body: [] } })))
    renderRoute(<HomePage />)
    expect(await screen.findByText('None of your teams uses Nomal yet.')).toBeInTheDocument()
  })
})
