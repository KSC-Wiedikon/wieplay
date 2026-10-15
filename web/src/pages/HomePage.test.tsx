import { fireEvent, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Team } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { HomePage } from './HomePage'

const team = (
  id: number,
  name: string,
  sport: Team['sport'],
  rel: string[] = ['player'],
  more: Partial<Team> = {},
): Team => ({
  id,
  name,
  sport,
  season: '2026/27',
  league: '2L',
  rel,
  lineage_id: `lineage-${name}`,
  can_tag: false,
  can_share: false,
  can_export: false,
  ...more,
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

  it('shows each team once, with its earlier seasons behind a chevron', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        stubFetch({
          '/api/teams': {
            body: [
              team(3, 'H3', 'volleyball', ['coach'], { season: '2025/26', league: '3L' }),
              team(92, 'H3', 'volleyball', ['team_responsible']),
              // Renamed between seasons: the lineage, not the name, makes it the same team.
              team(1393, 'D2', 'volleyball', ['coach'], { season: '2025/26', lineage_id: 'lineage-D1' }),
              team(94, 'D1', 'volleyball', ['coach']),
              team(10, 'D10', 'volleyball'),
            ],
          },
        }),
      ),
    )
    renderRoute(<HomePage />)
    const volleyball = await screen.findByRole('region', { name: 'Volleyball' })
    const names = () => within(volleyball).getAllByRole('link').map((link) => link.getAttribute('href'))
    expect(names()).toEqual(['/teams/94', '/teams/10', '/teams/92'])
    expect(within(volleyball).queryByRole('button', { name: /D10/ })).not.toBeInTheDocument()

    const chevron = within(volleyball).getByRole('button', { name: 'Show earlier seasons of H3' })
    expect(chevron).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(chevron)
    expect(names()).toEqual(['/teams/94', '/teams/10', '/teams/92', '/teams/3'])
    expect(within(volleyball).getByRole('row', { name: /H3.*2025\/26/ })).toHaveTextContent('Coach')
    const close = within(volleyball).getByRole('button', { name: 'Hide earlier seasons of H3' })
    expect(close).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(close)
    expect(names()).toEqual(['/teams/94', '/teams/10', '/teams/92'])

    fireEvent.click(within(volleyball).getByRole('button', { name: 'Show earlier seasons of D1' }))
    expect(within(volleyball).getByRole('link', { name: 'D2' })).toHaveAttribute('href', '/teams/1393')
  })

  it('leaves out a sport without teams', async () => {
    vi.stubGlobal('fetch', vi.fn(stubFetch({ '/api/teams': { body: [team(92, 'H3', 'volleyball')] } })))
    renderRoute(<HomePage />)
    expect(await screen.findByRole('region', { name: 'Volleyball' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Basketball' })).not.toBeInTheDocument()
  })


  it('shows the empty state', async () => {
    vi.stubGlobal('fetch', vi.fn(stubFetch({ '/api/teams': { body: [] } })))
    renderRoute(<HomePage />)
    expect(await screen.findByText('None of your teams uses Nomal yet.')).toBeInTheDocument()
  })
})
