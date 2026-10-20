import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { TeamMemory } from '../lib/teamMemory'
import type { TeamDetail } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { teamStats } from '../test/stats'
import { TeamPage } from './TeamPage'

const team = (patch: Partial<TeamDetail> = {}): TeamDetail => ({
  id: 92,
  name: 'H3',
  sport: 'volleyball',
  season: '2026/27',
  league: '2L',
  rel: ['coach'],
  lineage_id: '6f1c0a52-2a43-4c11-9a43-111111111111',
  can_upload: true,
  can_tag: true,
  can_share: true,
  can_export: true,
  can_grant: false,
  helper: false,
  ...patch,
})

const memory = (patch: Partial<TeamMemory> = {}): TeamMemory => ({
  team_id: 92,
  available: true,
  games: 2,
  refreshed_at: '2026-10-20T18:00:00Z',
  players: [
    { number: 12, member_id: 101, name: 'Alex Muster', libero: false, games: 2, answers: 3, sets: 7, last_on: '2026-10-02', source: 'roster', ambiguous: false, fixed: false, hidden: false },
    { number: 8, member_id: null, name: null, libero: false, games: 1, answers: 0, sets: 3, last_on: '2026-10-02', source: 'roster', ambiguous: true, fixed: false, hidden: false },
    { number: 4, member_id: 103, name: 'Lia Libero', libero: true, games: 0, answers: 0, sets: 0, last_on: null, source: 'roster', ambiguous: false, fixed: false, hidden: false },
  ],
  rotations: {
    sets: 7,
    orders: [{ order: [7, 12, 2, 1, 22, 8], share: 0.6 }],
    start_zones: { '1': 0.5, '2': 0.25, '4': 0.25 },
    setters: [7, 17],
    liberos: [4, 10],
    check: { sets: 7, serves: 158, right: 108, sure: 0, sure_right: 0 },
  },
  models: {
    rallies: { trained_at: '2026-10-19T10:00:00Z', games: 3, v0: 0.71, v1: 0.84, team: { variant: 'weighted', games: 3, model: true } },
    players: { games: 2, sure_share: 0.215 },
  },
  can_fix: true,
  members: [
    { id: 101, name: 'Alex Muster', number: 12 },
    { id: 102, name: 'Sam Mittel', number: 8 },
    { id: 104, name: 'Kim Gast', number: 8 },
  ],
  ...patch,
})

const config = { enabled: true, part_size: 67_108_864, max_file_bytes: 2e11, max_files: 20 }

function stub(detail: TeamDetail, found: TeamMemory | null, extra: Parameters<typeof stubFetch>[0] = {}) {
  const fetch = vi.fn(
    stubFetch({
      '/api/teams/92': { body: detail },
      '/api/teams/92/games': { body: [] },
      '/api/teams/92/videos': { body: [] },
      '/api/teams/92/upcoming-filming': { body: [] },
      '/api/teams/92/stats': { body: teamStats() },
      '/api/video/config': { body: config },
      ...(found ? { '/api/teams/92/memory': { body: found } } : {}),
      ...extra,
    }),
  )
  vi.stubGlobal('fetch', fetch)
  renderRoute(<TeamPage />, { path: '/teams/:id', url: '/teams/92' })
  return fetch
}

afterEach(() => vi.unstubAllGlobals())

describe('Team page: what WiePlay knows about this team', () => {
  it('shows the players with their games, the usual order and the models’ scores', async () => {
    stub(team(), memory())
    const section = await screen.findByRole('region', { name: 'What WiePlay knows about this team' })
    expect(await within(section).findByText('Learnt from 2 games')).toBeInTheDocument()
    const row = within(section).getByText('Alex Muster').closest('tr') as HTMLElement
    expect(within(row).getByText('#12')).toBeInTheDocument()
    expect(within(row).getByText('2')).toBeInTheDocument()
    expect(within(section).getByText('Unclear: several players wear #8')).toBeInTheDocument()
    expect(within(section).getByText('Libero')).toBeInTheDocument()
    const rotations = within(section).getByRole('region', { name: 'Usual lineups and rotations' })
    expect(within(rotations).getAllByText(/^#\d+$/).map((c) => c.textContent)).toEqual(['#7', '#12', '#2', '#1', '#22', '#8'])
    expect(within(rotations).getByText('Zone I · 50%')).toBeInTheDocument()
    expect(within(rotations).getByText(/named our server right at 108 of 158 serves/)).toBeInTheDocument()
    const models = within(section).getByRole('region', { name: 'How WiePlay does for this team' })
    expect(within(models).getByText('Rules 71%, learned 84%')).toBeInTheDocument()
    expect(within(models).getByText('Its own model: its games count more')).toBeInTheDocument()
    expect(within(models).getByText(/No reviewed game yet/)).toBeInTheDocument() // actions
    expect(within(models).getByText('22% of our footage on sure cards (2 games)')).toBeInTheDocument()
  })

  it('a coach fixes who wears a number', async () => {
    const fixed = memory({
      players: memory()
        .players.filter((p) => p.number === 8)
        .map((p) => ({ ...p, member_id: 102, name: 'Sam Mittel', ambiguous: false, fixed: true, source: 'coach' as const })),
    })
    const fetch = stub(team(), memory(), { 'PUT /api/teams/92/memory/players/8': { body: fixed } })
    fireEvent.click(await screen.findByRole('button', { name: 'Who wears #8?' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByRole('combobox'), { target: { value: '102' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() => {
      const puts = fetch.mock.calls.filter(([url, init]) => url === '/api/teams/92/memory/players/8' && (init as RequestInit).method === 'PUT')
      expect(puts.map(([, init]) => JSON.parse((init as RequestInit).body as string) as unknown)).toEqual([{ member_id: 102, hidden: false }])
    })
    expect(await screen.findByText('Set by a coach')).toBeInTheDocument()
  })

  it('players see it without fixing', async () => {
    stub(team({ rel: ['player'] }), memory({ can_fix: false, members: [] }))
    const section = await screen.findByRole('region', { name: 'What WiePlay knows about this team' })
    await within(section).findByText('Learnt from 2 games')
    expect(within(section).queryByRole('button', { name: /Who wears/ })).not.toBeInTheDocument()
  })

  it('is hidden for a team without a memory', async () => {
    stub(team(), null)
    await screen.findByRole('region', { name: 'Videos' })
    await waitFor(() => expect(screen.queryByRole('region', { name: 'What WiePlay knows about this team' })).not.toBeInTheDocument())
  })
})
