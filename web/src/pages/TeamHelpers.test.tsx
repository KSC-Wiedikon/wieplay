import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { Helper, HelperCandidate, Me, Team, TeamDetail } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { teamStats } from '../test/stats'
import { HomePage } from './HomePage'
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
  can_grant: true,
  helper: false,
  ...patch,
})

const pia: Helper = {
  id: '0193f000-0000-7000-8000-000000000001',
  member_id: 111,
  name: 'Pia Helfer',
  teams: ['D1'],
  note: 'Reviews the home games',
  granted_by_name: 'Jo Trainer',
  granted_at: '2026-10-17T08:30:00Z',
}

const config = { enabled: true, part_size: 67_108_864, max_file_bytes: 2e11, max_files: 20 }

function stub(detail: TeamDetail, extra: Parameters<typeof stubFetch>[0] = {}) {
  const fetch = vi.fn(
    stubFetch({
      '/api/teams/92': { body: detail },
      '/api/teams/92/games': { body: [] },
      '/api/teams/92/videos': { body: [] },
      '/api/teams/92/upcoming-filming': { body: [] },
      '/api/teams/92/stats': { body: teamStats() },
      '/api/video/config': { body: config },
      ...extra,
    }),
  )
  vi.stubGlobal('fetch', fetch)
  renderRoute(<TeamPage />, { path: '/teams/:id', url: '/teams/92' })
  return fetch
}

function calls(fetch: ReturnType<typeof vi.fn>, method: string, path: string): unknown[] {
  return fetch.mock.calls
    .filter(([url, init]) => url === path && ((init as RequestInit | undefined)?.method ?? 'GET') === method)
    .map(([, init]) => JSON.parse(((init as RequestInit).body as string | undefined) ?? 'null') as unknown)
}

describe('Team page: helpers', () => {
  it('lists the helpers for coaches, says what helpers can and cannot do, and links the guide', async () => {
    stub(team(), { '/api/teams/92/helpers': { body: [pia] } })
    const section = await screen.findByRole('region', { name: 'Helpers' })
    const row = (await within(section).findByText('Pia Helfer')).closest('tr') as HTMLElement
    expect(within(row).getByText('D1')).toBeInTheDocument()
    expect(within(row).getAllByText('17.10.2026 by Jo Trainer')[0]).toBeInTheDocument()
    expect(within(row).getAllByText('Reviews the home games')[0]).toBeInTheDocument()
    expect(within(section).getByText('download videos')).toBeInTheDocument()
    expect(within(section).getByText('review Nomal’s suggestions, tag, correct scores')).toBeInTheDocument()
    expect(within(section).getByRole('link', { name: /How to help/ })).toHaveAttribute('href', '/how-to-help')
  })

  it('is not there for players, and a helper sees the guide instead', async () => {
    stub(team({ rel: ['player'], can_grant: false, can_tag: false, can_upload: false }))
    await screen.findByRole('region', { name: 'Videos' })
    expect(screen.queryByRole('region', { name: 'Helpers' })).not.toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'You help this team' })).not.toBeInTheDocument()
  })

  it('shows a helper what they are here for', async () => {
    stub(team({ rel: [], helper: true, can_grant: false, can_export: false }))
    const helping = await screen.findByRole('region', { name: 'You help this team' })
    expect(within(helping).getByRole('link', { name: /How to help/ })).toHaveAttribute('href', '/how-to-help')
    expect(screen.queryByRole('region', { name: 'Helpers' })).not.toBeInTheDocument()
  })

  it('says when there are no helpers yet', async () => {
    stub(team(), { '/api/teams/92/helpers': { body: [] } })
    const section = await screen.findByRole('region', { name: 'Helpers' })
    expect(await within(section).findByText(/No helpers yet/)).toBeInTheDocument()
  })

  it('searches club members as you type, picks one with the keyboard and adds them with a note', async () => {
    const found: HelperCandidate[] = [
      { id: 111, name: 'Pia Helfer', teams: ['D1'] },
      { id: 112, name: 'Pius Muster', teams: ['H1', 'D2'] },
    ]
    let helpers: Helper[] = []
    const fetch = stub(team(), {
      '/api/teams/92/helpers': { body: () => helpers },
      '/api/teams/92/helpers/candidates?q=pi': { body: found },
      'POST /api/teams/92/helpers': {
        status: 201,
        body: ({ body }: { body: unknown }) => {
          const { note } = body as { note: string | null }
          helpers = [{ ...pia, member_id: 112, name: 'Pius Muster', teams: ['H1', 'D2'], note }]
          return helpers[0]
        },
      },
    })
    const section = await screen.findByRole('region', { name: 'Helpers' })
    await within(section).findByText(/No helpers yet/)
    fireEvent.click(within(section).getByRole('button', { name: 'Add a helper' }))

    const dialog = await screen.findByRole('dialog', { name: 'Add a helper to H3' })
    const search = within(dialog).getByRole('combobox', { name: 'Club member' })
    fireEvent.change(search, { target: { value: 'p' } })
    expect(within(dialog).getByText('Type at least 2 letters of a name.')).toBeInTheDocument()
    fireEvent.change(search, { target: { value: 'pi' } })

    const list = await within(dialog).findByRole('listbox', { name: 'Matching members' })
    const options = within(list).getAllByRole('option')
    expect(options.map((o) => o.textContent)).toEqual(['Pia HelferD1', 'Pius MusterH1, D2'])
    expect(options[0]).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(search, { key: 'ArrowDown' })
    expect(within(list).getAllByRole('option')[1]).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(search, { key: 'Enter' })

    expect(within(dialog).getByText('Pius Muster')).toBeInTheDocument()
    expect(within(dialog).getByText(/will see every season of H3/)).toBeInTheDocument()
    fireEvent.change(within(dialog).getByRole('textbox', { name: 'Note (optional)' }), {
      target: { value: ' Away games ' },
    })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add helper' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(calls(fetch, 'POST', '/api/teams/92/helpers')).toEqual([{ member_id: 112, note: 'Away games' }])
    expect(await screen.findByText('Pius Muster now helps H3.')).toBeInTheDocument()
    expect(await within(section).findByText('Pius Muster')).toBeInTheDocument()
  })

  it('picks with a click and shows why adding failed', async () => {
    stub(team(), {
      '/api/teams/92/helpers': { body: [] },
      '/api/teams/92/helpers/candidates?q=pia': { body: [{ id: 111, name: 'Pia Helfer', teams: ['D1'] }] },
      'POST /api/teams/92/helpers': { status: 409, body: { detail: 'Already a helper of this team' } },
    })
    fireEvent.click(await screen.findByRole('button', { name: 'Add a helper' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByRole('combobox'), { target: { value: 'pia' } })
    fireEvent.click(await within(dialog).findByRole('option', { name: /Pia Helfer/ }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add helper' }))
    expect(await within(dialog).findByRole('alert')).toHaveTextContent("Couldn’t add the helper: Already a helper of this team")
    // Another choice starts over.
    fireEvent.click(within(dialog).getByRole('button', { name: 'Choose someone else' }))
    expect(within(dialog).getByRole('combobox')).toHaveValue('pia')
    expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument()
  })

  it('says when nobody matches', async () => {
    stub(team(), {
      '/api/teams/92/helpers': { body: [] },
      '/api/teams/92/helpers/candidates?q=zz': { body: [] },
    })
    fireEvent.click(await screen.findByRole('button', { name: 'Add a helper' }))
    const dialog = await screen.findByRole('dialog')
    fireEvent.change(within(dialog).getByRole('combobox'), { target: { value: 'zz' } })
    expect(await within(dialog).findByText('Nobody matches “zz”.')).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Add helper' })).toBeDisabled()
  })

  it('removes a helper after asking', async () => {
    let helpers = [pia]
    const fetch = stub(team(), {
      '/api/teams/92/helpers': { body: () => helpers },
      [`DELETE /api/teams/92/helpers/${pia.id}`]: {
        status: 204,
        body: () => {
          helpers = []
        },
      },
    })
    const section = await screen.findByRole('region', { name: 'Helpers' })
    fireEvent.click(await within(section).findByRole('button', { name: 'Remove Pia Helfer as a helper' }))
    const confirm = await screen.findByRole('alertdialog', { name: 'Remove Pia Helfer as a helper?' })
    expect(confirm).toHaveTextContent('Pia Helfer loses access to H3 at once. Their tags and reviews stay.')
    await act(async () => {
      fireEvent.click(within(confirm).getByRole('button', { name: 'Remove' }))
    })
    expect(await screen.findByText('Pia Helfer is no longer a helper.')).toBeInTheDocument()
    expect(await within(section).findByText(/No helpers yet/)).toBeInTheDocument()
    expect(calls(fetch, 'DELETE', `/api/teams/92/helpers/${pia.id}`)).toHaveLength(1)
  })
})

describe('Start page: helper teams', () => {
  const me: Me = { member: { id: 111, uuid: 'u', name: 'Pia Helfer' }, roles: [], is_admin: false, sport_admin: [], teams: [] }
  const row = (patch: Partial<Team>): Team => ({
    id: 92,
    name: 'H3',
    sport: 'volleyball',
    season: '2026/27',
    league: '2L',
    rel: [],
    lineage_id: 'lineage-H3',
    can_tag: true,
    can_share: true,
    can_export: false,
    helper: true,
    ...patch,
  })

  it('shows a Helper chip on the teams they help, and offers the upload there', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        stubFetch({
          '/api/teams': {
            body: [
              row({}),
              row({ id: 12, season: '2025/26' }),
              row({ id: 80, name: 'D1', lineage_id: 'lineage-D1', rel: ['player'], helper: false, can_tag: false, can_share: false }),
            ],
          },
          '/api/video/config': { body: config },
          '/api/teams/92/videos': { body: [] },
          '/api/teams/12/videos': { body: [] },
          '/api/teams/80/videos': { body: [] },
        }),
      ),
    )
    renderRoute(<HomePage />, { me })
    const section = await screen.findByRole('region', { name: 'Your teams' })
    const h3 = within(section).getByRole('link', { name: 'H3' }).closest('li') as HTMLElement
    const roles = within(h3).getByRole('list', { name: 'Your role' })
    expect(within(roles).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['Helper'])
    expect(within(h3).getByRole('button', { name: 'Show earlier seasons of H3' })).toBeInTheDocument()
    const d1 = within(section).getByRole('link', { name: 'D1' }).closest('li') as HTMLElement
    expect(within(within(d1).getByRole('list', { name: 'Your role' })).getAllByRole('listitem').map((li) => li.textContent)).toEqual([
      'Player',
    ])
    const [upload] = await screen.findAllByRole('link', { name: /Upload a video/ })
    expect(upload).toHaveAttribute('href', '/teams/92/upload')
  })
})
