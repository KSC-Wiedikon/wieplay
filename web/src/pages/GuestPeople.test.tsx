import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { GuestPerson, TeamDetail } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { teamStats } from '../test/stats'
import { TeamPage } from './TeamPage'

const ID = 1000000001

const team = (patch: Partial<TeamDetail> = {}): TeamDetail => ({
  id: ID,
  name: 'Herren 2',
  sport: 'volleyball',
  season: '2026/27',
  league: '3. Liga',
  rel: ['coach'],
  lineage_id: '6f1c0a52-2a43-4c11-9a43-222222222222',
  can_upload: true,
  can_tag: true,
  can_share: true,
  can_export: true,
  can_grant: false,
  helper: false,
  club: 'VBC Gast',
  guest: true,
  can_manage_people: true,
  video_count: 3,
  video_limit: 30,
  adults_declared_at: '2026-10-17T08:00:00Z',
  ...patch,
})

const person = (patch: Partial<GuestPerson>): GuestPerson => ({
  member_id: 1000000002,
  name: 'Gina Gast',
  number: null,
  role: 'coach',
  account: true,
  disabled: false,
  last_login_at: '2026-10-17T09:00:00Z',
  invite_expires_at: null,
  me: true,
  ...patch,
})

const GINA = person({})
const PAUL = person({
  member_id: 1000000003,
  name: 'Paul Gast',
  number: 7,
  role: 'player',
  account: false,
  last_login_at: null,
  me: false,
})

const config = { enabled: true, part_size: 67_108_864, max_file_bytes: 2e11, max_files: 20 }
const PEOPLE = `/api/teams/${ID}/people`

function stub(detail: TeamDetail, people: GuestPerson[], extra: Parameters<typeof stubFetch>[0] = {}) {
  const fetch = vi.fn(
    stubFetch({
      [`/api/teams/${ID}`]: { body: detail },
      [`/api/teams/${ID}/games`]: { body: [] },
      [`/api/teams/${ID}/videos`]: { body: [] },
      [`/api/teams/${ID}/stats`]: { body: teamStats() },
      [PEOPLE]: { body: people },
      '/api/video/config': { body: config },
      ...extra,
    }),
  )
  vi.stubGlobal('fetch', fetch)
  renderRoute(<TeamPage />, { path: '/teams/:id', url: `/teams/${ID}` })
  return fetch
}

function requests(fetch: ReturnType<typeof vi.fn>, method: string, path: string): unknown[] {
  return fetch.mock.calls
    .filter(([url, init]) => url === path && ((init as RequestInit | undefined)?.method ?? 'GET') === method)
    .map(([, init]) => JSON.parse(((init as RequestInit).body as string | undefined) ?? 'null') as unknown)
}

describe('a guest team page', () => {
  it('shows the club, the quota and the people, without Wiedisync games or helpers', async () => {
    stub(team(), [GINA, PAUL])
    expect(await screen.findByRole('heading', { level: 1, name: 'Herren 2' })).toBeInTheDocument()
    expect(screen.getByText('VBC Gast · 3. Liga · 2026/27')).toBeInTheDocument()
    expect(screen.getByText('3 of 30 videos this season')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Upcoming games' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Filming' })).not.toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Helpers' })).not.toBeInTheDocument()

    const people = await screen.findByRole('table', { name: 'People' })
    const [gina, paul] = within(people).getAllByRole('row').slice(1) as [HTMLElement, HTMLElement]
    expect(gina).toHaveTextContent('Gina Gast')
    expect(gina).toHaveTextContent('(you)')
    expect(gina).toHaveTextContent('Set up · Last')
    expect(paul).toHaveTextContent('7Paul Gast')
    expect(paul).toHaveTextContent('Not yet')
    // Nobody removes themselves.
    expect(within(gina).queryByRole('button', { name: 'Remove Gina Gast from the team' })).not.toBeInTheDocument()
  })

  it('makes an invite link and shows it once with a copy button', async () => {
    const link = { link: 'http://localhost:5173/join/abc', expires_at: '2026-10-31T10:00:00Z' }
    const fetch = stub(team(), [GINA, PAUL], { [`POST ${PEOPLE}/${PAUL.member_id}/invite`]: { body: link } })
    const writeText = vi.fn(() => Promise.resolve())
    vi.stubGlobal('navigator', { ...navigator, clipboard: { writeText } })

    fireEvent.click(await screen.findByRole('button', { name: 'Make an invite link for Paul Gast' }))
    const dialog = await screen.findByRole('dialog', { name: 'Invite link for Paul Gast' })
    expect(within(dialog).getByText(link.link)).toBeInTheDocument()
    expect(within(dialog).getByText(/shown only now/)).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Copy link' }))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(link.link))
    expect(requests(fetch, 'POST', `${PEOPLE}/${PAUL.member_id}/invite`)).toHaveLength(1)
    // Someone with a login gets a new password link instead.
    expect(screen.getByRole('button', { name: 'Make a new password link for Gina Gast' })).toBeInTheDocument()
  })

  it('adds, edits and removes people', async () => {
    const pia = person({ member_id: 1000000004, name: 'Pia Gast', number: 9, role: 'player', account: false, me: false })
    const fetch = stub(team(), [GINA, PAUL], {
      [`POST ${PEOPLE}`]: { status: 201, body: pia },
      [`PATCH ${PEOPLE}/${PAUL.member_id}`]: { body: { ...PAUL, number: 8 } },
      [`DELETE ${PEOPLE}/${PAUL.member_id}`]: { status: 204 },
    })
    fireEvent.click(await screen.findByRole('button', { name: 'Add a person' }))
    let dialog = await screen.findByRole('dialog', { name: 'Add a person to Herren 2' })
    fireEvent.change(within(dialog).getByLabelText('Name'), { target: { value: ' Pia Gast ' } })
    fireEvent.change(within(dialog).getByLabelText('Number'), { target: { value: '9' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Add a person' }))
    await waitFor(() => expect(requests(fetch, 'POST', PEOPLE)).toEqual([{ name: 'Pia Gast', number: 9, role: 'player' }]))

    fireEvent.click(screen.getByRole('button', { name: 'Edit Paul Gast' }))
    dialog = await screen.findByRole('dialog', { name: 'Edit Paul Gast' })
    expect(within(dialog).queryByLabelText('Role')).not.toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText('Number'), { target: { value: '8' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(requests(fetch, 'PATCH', `${PEOPLE}/${PAUL.member_id}`)).toEqual([{ name: 'Paul Gast', number: 8 }]),
    )

    fireEvent.click(screen.getByRole('button', { name: 'Remove Paul Gast from the team' }))
    const confirm = await screen.findByRole('alertdialog')
    expect(confirm).toHaveTextContent('Paul Gast loses access to Herren 2 at once.')
    fireEvent.click(within(confirm).getByRole('button', { name: 'Remove' }))
    await waitFor(() => expect(requests(fetch, 'DELETE', `${PEOPLE}/${PAUL.member_id}`)).toHaveLength(1))
  })

  it('shows players the roster without logins or buttons', async () => {
    stub(team({ rel: ['player'], can_upload: false, can_manage_people: false, can_tag: false }), [
      { ...GINA, account: null, disabled: null, last_login_at: null, me: false },
      { ...PAUL, account: null, disabled: null, me: true },
    ])
    const people = await screen.findByRole('table', { name: 'People' })
    expect(within(people).queryByRole('columnheader', { name: 'Login' })).not.toBeInTheDocument()
    expect(within(people).queryByRole('button')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add a person' })).not.toBeInTheDocument()
    expect(screen.queryByText(/Set up|Not yet/)).not.toBeInTheDocument()
  })

  it('asks a coach to confirm that everyone filmed is an adult before the first upload', async () => {
    const fetch = stub(team({ adults_declared_at: null }), [GINA], {
      [`POST /api/teams/${ID}/adults`]: { body: { adults_declared_at: '2026-10-17T10:00:00Z' } },
    })
    const banner = await screen.findByRole('region', { name: 'Before the first upload of 2026/27' })
    expect(banner).toHaveTextContent('Everyone we film — our players, coaches and our opponents — is 18 or older')
    fireEvent.click(within(banner).getByRole('button', { name: 'I confirm' }))
    await waitFor(() =>
      expect(screen.queryByRole('region', { name: 'Before the first upload of 2026/27' })).not.toBeInTheDocument(),
    )
    expect(requests(fetch, 'POST', `/api/teams/${ID}/adults`)).toHaveLength(1)
  })
})

describe('a guest team\'s seasons', () => {
  it('starts the next season with the people the coach picks', async () => {
    const NEXT = 1000000009
    const started = { team_id: NEXT, name: 'Herren 1', season: '2027/28', carried: 1, left: 1, disabled: 1 }
    const fetch = vi.fn(
      stubFetch({
        [`/api/teams/${ID}`]: { body: team({ next_season: '2027/28', newest: true }) },
        [`/api/teams/${ID}/games`]: { body: [] },
        [`/api/teams/${ID}/videos`]: { body: [] },
        [`/api/teams/${ID}/stats`]: { body: teamStats() },
        [PEOPLE]: { body: [GINA, PAUL] },
        '/api/video/config': { body: config },
        [`POST /api/teams/${ID}/next-season`]: { status: 201, body: started },
      }),
    )
    vi.stubGlobal('fetch', fetch)
    const { router } = renderRoute(<TeamPage />, { path: '/teams/:id', url: `/teams/${ID}` })

    const section = await screen.findByRole('region', { name: 'Next season' })
    fireEvent.click(within(section).getByRole('button', { name: 'Start 2027/28' }))
    const dialog = await screen.findByRole('dialog', { name: 'Start 2027/28 for Herren 2' })
    expect(within(dialog).getByLabelText('Team')).toHaveValue('Herren 2')
    expect(within(dialog).getByLabelText('League (optional)')).toHaveValue('3. Liga')
    expect(within(dialog).getByLabelText('Season')).toHaveValue('2027/28')
    // Everyone comes along by default; the coach starting it always.
    const everyone = within(dialog).getByRole('checkbox', { name: 'Everyone' })
    const paul = await within(dialog).findByRole('checkbox', { name: /7 · Paul Gast/ })
    const gina = within(dialog).getByRole('checkbox', { name: /Gina Gast/ })
    expect([everyone, paul, gina].map((box) => (box as HTMLInputElement).checked)).toEqual([true, true, true])
    expect(gina).toBeDisabled()

    fireEvent.click(paul)
    expect(everyone).not.toBeChecked()
    expect(dialog).toHaveTextContent('1 person not ticked stays on the 2026/27 roster but loses access to WiePlay.')
    fireEvent.change(within(dialog).getByLabelText('Team'), { target: { value: 'Herren 1' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Start 2027/28' }))
    await waitFor(() =>
      expect(requests(fetch, 'POST', `/api/teams/${ID}/next-season`)).toEqual([
        { name: 'Herren 1', league: '3. Liga', season: '2027/28', member_ids: [GINA.member_id] },
      ]),
    )
    await waitFor(() => expect(router.state.location.pathname).toBe(`/teams/${NEXT}`))
  })

  it('keeps an earlier season\'s roster: no adding, no links, no next season', async () => {
    stub(team({ newest: false, next_season: null }), [GINA, PAUL])
    const people = await screen.findByRole('table', { name: 'People' })
    expect(screen.getByText(/This is an earlier season: its roster stays as it was/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Add a person' })).not.toBeInTheDocument()
    expect(within(people).queryByRole('button', { name: 'Make an invite link for Paul Gast' })).not.toBeInTheDocument()
    // Taking someone off an earlier season (a request to be removed) still works.
    expect(within(people).getByRole('button', { name: 'Remove Paul Gast from the team' })).toBeInTheDocument()
    expect(screen.queryByRole('region', { name: 'Next season' })).not.toBeInTheDocument()
  })

  it('takes people of earlier seasons along', async () => {
    const pia = person({ member_id: 1000000004, name: 'Pia Gast', number: 9, role: 'player', account: true, me: false })
    const fetch = stub(team({ newest: true }), [GINA, PAUL], {
      [`${PEOPLE}/earlier`]: {
        body: [{ member_id: pia.member_id, name: 'Pia Gast', number: 9, role: 'player', season: '2025/26' }],
      },
      [`POST ${PEOPLE}/earlier`]: { status: 201, body: [pia] },
    })
    fireEvent.click(await screen.findByRole('button', { name: 'From earlier seasons' }))
    const dialog = await screen.findByRole('dialog', { name: 'Take people along into 2026/27' })
    const take = within(dialog).getByRole('button', { name: 'Take along' })
    expect(take).toBeDisabled()
    const box = await within(dialog).findByRole('checkbox', { name: /9 · Pia Gast/ })
    expect(dialog).toHaveTextContent('Player · Last in 2025/26')
    fireEvent.click(box)
    fireEvent.click(take)
    await waitFor(() => expect(requests(fetch, 'POST', `${PEOPLE}/earlier`)).toEqual([{ member_ids: [pia.member_id] }]))
    expect(await screen.findByText('1 person is on the team again.')).toBeInTheDocument()
  })
})
