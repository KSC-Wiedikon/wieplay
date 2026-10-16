import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Clip, Me, Piece, ReelQuery, Team, TeamDetail } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { volleyball } from '../test/tagging'
import { ReelBuilderPage } from './ReelBuilderPage'

const LINEAGE = '6f1c0a52-2a43-4c11-9a43-111111111111'
const VIDEO = '00000000-0000-4000-8000-0000000000b1'

const coach: Me = {
  member: { id: 7, uuid: 'u7', name: 'Coach Test' },
  roles: ['coach'],
  is_admin: false,
  sport_admin: [],
  teams: [{ id: 92, name: 'H3', sport: 'volleyball', season: '2026/27', league: '2L', rel: ['coach'], lineage_id: LINEAGE, can_tag: true, can_share: true, can_export: true }],
}
const detail: TeamDetail = { ...(coach.teams[0] as Team), can_upload: true }
const playerTeam: Team = { ...(coach.teams[0] as Team), rel: ['player'], can_tag: false, can_share: false, can_export: false }

const piece = (patch: Partial<Piece>): Piece => ({
  video_id: VIDEO,
  start_ms: 15_000,
  end_ms: 20_000,
  event_id: '00000000-0000-4000-8000-00000000a001',
  clip_id: null,
  caption: null,
  video_title: 'KSCW H3 – Volley Näfels',
  recorded_on: '2026-10-11',
  code: 'A',
  grade: '#',
  side: 'us',
  number: 7,
  member_id: 107,
  player_name: 'Luca Brunner',
  score_us: null,
  score_them: null,
  ...patch,
})

function show({ me = coach, team = detail, url = '/reels/new', routes = {} }: { me?: Me; team?: TeamDetail; url?: string; routes?: Parameters<typeof stubFetch>[0] } = {}) {
  const previews: ReelQuery[] = []
  const fetch = vi.fn(
    stubFetch({
      '/api/me': { body: me },
      '/api/teams': { body: me.teams },
      '/api/teams/92': { body: team },
      '/api/teams/92/seasons': {
        body: [
          { team_id: 92, name: 'H3', season: '2026/27' },
          { team_id: 61, name: 'H3', season: '2025/26' },
        ],
      },
      // The team's people come with its newest ready video.
      '/api/teams/92/videos': { body: [{ id: VIDEO, status: 'ready', recorded_on: '2026-10-11', created_at: '2026-10-11T22:00:00Z' }] },
      [`/api/videos/${VIDEO}/people`]: {
        body: [
          { member_id: 107, name: 'Luca Brunner', number: 7, roles: ['player'] },
          { member_id: 103, name: 'Mia Keller', number: 3, roles: ['player'] },
          { member_id: 7, name: 'Coach Test', number: null, roles: ['coach'] },
        ],
      },
      '/api/taxonomies/volleyball': { body: volleyball },
      '/api/reels': { body: [] },
      [`/api/videos/${VIDEO}/tagging`]: { status: 404 },
      'POST /api/reels/preview': {
        body: ({ body }: { body: unknown }) => {
          previews.push(body as ReelQuery)
          const query = body as ReelQuery
          return query.member_id
            ? [piece({ score_us: 1, score_them: 0 }), piece({ start_ms: 95_000, end_ms: 100_000, number: 7, score_us: 14, score_them: 12 })]
            : [piece({}), piece({ number: 3, member_id: 103, player_name: 'Mia Keller' }), piece({ code: 'S' })]
        },
      },
      'POST /api/reels': { status: 201, body: { id: '00000000-0000-4000-8000-00000000e001', title: 't', kind: 'smart', query: {}, visibility: 'team', lineage_id: LINEAGE, owner: 7, mine: true, created_at: '', updated_at: '' } },
      ...routes,
    }),
  )
  vi.stubGlobal('fetch', fetch)
  const view = renderRoute(<ReelBuilderPage />, { path: '/reels/new', url, routes: [{ path: '/reels/:id', element: <p>Reel page</p> }] })
  return { ...view, fetch, previews }
}

beforeEach(() => {
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
})

describe('ReelBuilderPage', () => {
  it('previews the query as it is built, then saves it as a smart reel for the team', async () => {
    const { previews, fetch, router } = show()
    // The team and its current season to begin with.
    await waitFor(() => expect(previews).toContainEqual({ lineage_id: LINEAGE, season: '2026/27', limit: 200 }))
    expect(await screen.findByText('3 moments · 0:15')).toBeInTheDocument()

    fireEvent.change(screen.getByLabelText('Player'), { target: { value: '107' } })
    // A player is on our side: the side choice goes away.
    expect(screen.queryByLabelText('Side')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Attack' }))
    expect(screen.getByRole('button', { name: 'Attack' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: '# Kill' }))
    // Matches only (16.10.2026): no choice between matches and trainings.
    expect(screen.queryByLabelText('Videos')).not.toBeInTheDocument()

    await waitFor(() =>
      expect(previews.at(-1)).toEqual({ lineage_id: LINEAGE, season: '2026/27', member_id: 107, codes: ['A'], grades: ['#'], limit: 200 }),
    )
    expect(await screen.findByText('2 moments · 0:10')).toBeInTheDocument()
    const moments = screen.getByRole('table', { name: 'Moments' })
    expect(within(moments).getAllByRole('button', { name: /Play moment/ })).toHaveLength(2)
    expect(within(moments).getAllByText('#7 Luca Brunner · Attack · # Kill')).toHaveLength(2)
    // The score after each moment's rally.
    expect(within(moments).getByText('1:0')).toBeInTheDocument()
    expect(within(moments).getByText('14:12')).toBeInTheDocument()

    expect(screen.getByLabelText('Title')).toHaveAttribute('placeholder', 'Luca Brunner · Attack · # Kill · 2026/27')
    fireEvent.click(screen.getByLabelText('The team (H3 2026/27)'))
    fireEvent.click(screen.getByRole('button', { name: 'Save reel' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/reels/00000000-0000-4000-8000-00000000e001'))
    const post = fetch.mock.calls.find(([path, init]) => path === '/api/reels' && init?.method === 'POST')
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      title: 'Luca Brunner · Attack · # Kill · 2026/27',
      kind: 'smart',
      query: { lineage_id: LINEAGE, season: '2026/27', member_id: 107, codes: ['A'], grades: ['#'], limit: 200 },
      visibility: 'team',
      lineage_id: LINEAGE,
    })
  })

  it('looks at another season, all seasons and one side when asked', async () => {
    const { previews } = show()
    await screen.findByText('3 moments · 0:15')
    // The seasons of the team I can see, newest first.
    await waitFor(() =>
      expect(within(screen.getByLabelText('Season')).getAllByRole('option').map((o) => o.textContent)).toEqual(['2026/27', '2025/26', 'All seasons']),
    )
    fireEvent.change(screen.getByLabelText('Season'), { target: { value: '2025/26' } })
    await waitFor(() => expect(previews.at(-1)).toEqual({ lineage_id: LINEAGE, season: '2025/26', limit: 200 }))
    fireEvent.change(screen.getByLabelText('Season'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('Side'), { target: { value: 'them' } })
    await waitFor(() => expect(previews.at(-1)).toEqual({ lineage_id: LINEAGE, side: 'them', limit: 200 }))
  })

  it('starts from the link of "My highlights" and keeps players to private reels', async () => {
    const player: Me = { ...coach, member: { id: 107, uuid: 'u', name: 'Luca Brunner' }, teams: [playerTeam] }
    const { previews } = show({
      me: player,
      team: { ...playerTeam, can_upload: false },
      url: '/reels/new?team=92&member=107&season=2026%2F27',
      routes: { '/api/teams/92/videos': { body: [] } },
    })
    await waitFor(() => expect(previews).toContainEqual({ lineage_id: LINEAGE, season: '2026/27', member_id: 107, limit: 200 }))
    // Without a video to read the team's people from, a player can still pick themselves.
    expect(screen.getByLabelText('Player')).toHaveValue('107')
    expect(within(screen.getByLabelText('Player')).getByRole('option', { name: 'Luca Brunner' })).toBeInTheDocument()
    expect(screen.queryByLabelText(/The team/)).not.toBeInTheDocument()
  })

  it('shares a reel from clips with a team the server allows, by the team list’s lineage', async () => {
    const clip: Clip = {
      id: '00000000-0000-4000-8000-00000000c101',
      video_id: VIDEO,
      video_title: 'KSCW H3 – Volley Näfels',
      recorded_on: '2026-10-11',
      start_ms: 15_000,
      end_ms: 22_000,
      title: 'My attack',
      note: null,
      event_id: null,
      owner: 7,
      owner_name: 'Coach Test',
      mine: true,
      visibility: 'private',
      created_at: '2026-10-12T19:00:00Z',
    }
    const other: Team = { ...playerTeam, id: 93, name: 'D1', lineage_id: '6f1c0a52-2a43-4c11-9a43-222222222222' }
    const { fetch, router } = show({ me: { ...coach, teams: [...coach.teams, other] }, url: '/reels/new?kind=manual', routes: { '/api/clips/mine': { body: [clip] } } })
    fireEvent.click(await screen.findByRole('button', { name: 'Add My attack' }))
    // Only H3 may get it: D1 is not a team I lead, so there is no team to choose.
    fireEvent.click(await screen.findByLabelText('The team (H3 2026/27)'))
    fireEvent.change(screen.getByLabelText('Title'), { target: { value: 'Attacks' } })
    fireEvent.click(screen.getByRole('button', { name: 'Save reel' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/reels/00000000-0000-4000-8000-00000000e001'))
    const post = fetch.mock.calls.find(([path, init]) => path === '/api/reels' && init?.method === 'POST')
    expect(JSON.parse(String(post?.[1]?.body))).toEqual({
      title: 'Attacks',
      kind: 'manual',
      items: [{ video_id: VIDEO, start_ms: 15_000, end_ms: 22_000, event_id: null, clip_id: clip.id, caption: 'My attack' }],
      visibility: 'team',
      lineage_id: LINEAGE,
    })
    // The lineage comes from the team list: no team detail is fetched for it.
    expect(fetch.mock.calls.some(([path]) => String(path).startsWith('/api/teams/'))).toBe(false)
  })
})
