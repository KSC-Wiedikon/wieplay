import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Me, Team } from '../lib/types'
import { renderRoute } from '../test/render'
import { Header } from './Header'

const team = (patch: Partial<Team> = {}): Team => ({
  id: 92,
  name: 'H3',
  sport: 'volleyball',
  season: '2026/27',
  league: '2L',
  rel: ['player'],
  lineage_id: '6f1c0a52-2a43-4c11-9a43-111111111111',
  can_tag: false,
  can_share: false,
  can_export: false,
  ...patch,
})

const me = (patch: Partial<Me>): Me => ({
  member: { id: 107, uuid: 'u', name: 'Luca Brunner' },
  roles: [],
  is_admin: false,
  sport_admin: [],
  teams: [team()],
  ...patch,
})

function links(): string[] {
  // The menu is there twice: in the header row from sm up, and in its own row on phones.
  const [menu] = screen.getAllByRole('navigation', { name: 'Main' })
  return within(menu as HTMLElement).getAllByRole('link').map((a) => a.textContent ?? '')
}

describe('Header', () => {
  it('shows players clips and reels', () => {
    renderRoute(<Header me={me({})} />, { url: '/clips', path: '/clips' })
    expect(links()).toEqual(['Clips', 'Reels'])
    const [menu] = screen.getAllByRole('navigation', { name: 'Main' })
    expect(within(menu as HTMLElement).getByRole('link', { name: 'Clips' })).toHaveAttribute('aria-current', 'page')
  })

  it('adds downloads where the server allows them (coaches, team managers, sport admins)', () => {
    renderRoute(<Header me={me({ teams: [team(), team({ id: 93, rel: ['team_responsible'], can_tag: true, can_share: true, can_export: true })] })} />)
    expect(links()).toEqual(['Clips', 'Reels', 'Downloads'])
  })

  it('shows the admin link to admins', () => {
    renderRoute(<Header me={me({ teams: [team({ rel: [], can_tag: true, can_share: true, can_export: true })], sport_admin: ['volleyball'], is_admin: true })} />)
    expect(links()).toEqual(['Clips', 'Reels', 'Downloads', 'Admin'])
  })

  it('shows no downloads to an admin who leads no team', () => {
    renderRoute(<Header me={me({ teams: [team({ rel: [] })], is_admin: true })} />)
    expect(links()).toEqual(['Clips', 'Reels', 'Admin'])
  })
})
