import { screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { Me } from '../lib/types'
import { renderRoute } from '../test/render'
import { Header } from './Header'

const me = (patch: Partial<Me>): Me => ({
  member: { id: 107, uuid: 'u', name: 'Luca Brunner' },
  roles: [],
  is_admin: false,
  sport_admin: [],
  teams: [{ id: 92, name: 'H3', sport: 'volleyball', season: '2026/27', league: '2L', rel: ['player'] }],
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

  it('adds downloads for coaches, team managers and sport admins, and admin for admins', () => {
    renderRoute(<Header me={me({ teams: [{ id: 92, name: 'H3', sport: 'volleyball', season: '2026/27', league: '2L', rel: ['team_responsible'] }] })} />)
    expect(links()).toEqual(['Clips', 'Reels', 'Downloads'])
  })

  it('shows the admin link to admins', () => {
    renderRoute(<Header me={me({ teams: [], sport_admin: ['volleyball'], is_admin: true })} />)
    expect(links()).toEqual(['Clips', 'Reels', 'Downloads', 'Admin'])
  })
})
