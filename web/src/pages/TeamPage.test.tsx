import { fireEvent, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { TeamDetail, Video } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { boxScore, rotationColumns, rotationLine, setterLine, setterStats, statRow, teamLine, teamStats } from '../test/stats'
import { TeamPage } from './TeamPage'

const team = (patch: Partial<TeamDetail> = {}): TeamDetail => ({
  id: 92,
  name: 'H3',
  sport: 'volleyball',
  season: '2026/27',
  league: '2L',
  rel: ['player'],
  lineage_id: '6f1c0a52-2a43-4c11-9a43-111111111111',
  can_upload: false,
  can_tag: false,
  can_share: false,
  can_export: false,
  ...patch,
})

const video = (patch: Partial<Video>): Video => ({
  id: '00000000-0000-4000-8000-000000000001',
  team_id: 92,
  team_name: 'H3',
  team_season: '2026/27',
  sport: 'volleyball',
  kind: 'match',
  game_id: 501,
  title: 'KSCW – Volley Näfels',
  recorded_on: '2026-10-11',
  source: 'file',
  imported_from: null,
  status: 'ready',
  duration_ms: 5_530_000,
  width: 1920,
  height: 1080,
  fps: 50,
  has_audio: true,
  meets_standard: true,
  standard_issues: [],
  renditions: [],
  has_thumbs: true,
  progress: null,
  error: null,
  created_at: '2026-10-11T22:00:00Z',
  created_by: 7,
  can_delete: false,
  can_upload: false,
  can_tag: false,
  can_share: false,
  can_export: false,
  files: [],
  ...patch,
})

const config = { enabled: true, part_size: 67_108_864, max_file_bytes: 2e11, max_files: 20 }

function stub(teamDetail: TeamDetail, videos: Video[], extra: Record<string, { status?: number; body?: unknown }> = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      stubFetch({
        '/api/teams/92': { body: teamDetail },
        '/api/teams/92/games': { body: [] },
        '/api/teams/92/videos': { body: videos },
        '/api/teams/92/upcoming-filming': { body: [] },
        '/api/teams/92/stats': { body: teamStats() },
        '/api/video/config': { body: config },
        ...extra,
      }),
    ),
  )
  return renderRoute(<TeamPage />, { path: '/teams/:id', url: '/teams/92' })
}

describe('TeamPage videos', () => {
  it('shows the empty state, without an upload button for players', async () => {
    stub(team(), [])
    const section = await screen.findByRole('region', { name: 'Videos' })
    expect(await within(section).findByText('No videos yet.')).toBeInTheDocument()
    expect(within(section).queryByRole('link', { name: /Upload video/ })).not.toBeInTheDocument()
  })

  it('shows videos as cards newest first with poster, length and status, and offers the upload to coaches', async () => {
    stub(
      team({ can_upload: true, rel: ['coach'] }),
      [
        video({ id: '00000000-0000-4000-8000-000000000001', title: 'Older match', recorded_on: '2026-10-04' }),
        video({
          id: '00000000-0000-4000-8000-000000000002',
          title: 'Friendly vs. VBC Zürich',
          game_id: null,
          recorded_on: '2026-10-12',
          status: 'processing',
          has_thumbs: false,
          duration_ms: null,
          progress: 0.4,
        }),
      ],
    )

    const section = await screen.findByRole('region', { name: 'Videos' })
    const october = await within(section).findByRole('list', { name: 'October 2026' })
    const cards = within(october).getAllByRole('listitem')
    expect(cards.map((card) => within(card).getByRole('link').textContent)).toEqual([
      'Friendly vs. VBC Zürich',
      'Older match',
    ])

    const [friendly, match] = cards as [HTMLElement, HTMLElement]
    expect(within(friendly).getByRole('link')).toHaveAttribute('href', '/videos/00000000-0000-4000-8000-000000000002')
    expect(within(friendly).getByText('Processing')).toBeInTheDocument()
    expect(friendly.querySelector('img')).toBeNull()

    // A ready video says nothing about being ready: it plays.
    expect(within(match).queryByText('Ready')).not.toBeInTheDocument()
    expect(within(match).getByText('1:32:10')).toBeInTheDocument()
    expect(match.querySelector('img')).toHaveAttribute('src', '/api/media/00000000-0000-4000-8000-000000000001/poster.jpg')

    expect(within(section).getByRole('link', { name: /Upload video/ })).toHaveAttribute('href', '/teams/92/upload')
  })

  it('shows a video being imported from a link as importing', async () => {
    stub(team({ can_upload: true, rel: ['coach'] }), [
      video({ title: 'Cup vs. VBC Zürich', source: 'import', status: 'importing', has_thumbs: false, duration_ms: null }),
    ])
    const section = await screen.findByRole('region', { name: 'Videos' })
    const [card] = (await within(section).findAllByRole('listitem')) as [HTMLElement]
    expect(within(card).getByText('Importing')).toBeInTheDocument()
  })

  it('groups the videos by month and says where the analysis stands', async () => {
    stub(team({ can_tag: true }), [
      video({ id: '00000000-0000-4000-8000-000000000011', title: 'Legends', recorded_on: '2026-10-02', analysis: { status: 'queued', open: 0 } }),
      video({ id: '00000000-0000-4000-8000-000000000012', title: 'VBC Swiss', recorded_on: '2026-09-28', analysis: { status: 'done', open: 12 } }),
      video({ id: '00000000-0000-4000-8000-000000000013', title: 'H1', recorded_on: '2026-09-23', analysis: { status: 'running', open: 0 } }),
      video({ id: '00000000-0000-4000-8000-000000000014', title: 'Stäfa', recorded_on: '2026-01-23', analysis: { status: 'done', open: 0 }, rallies_reviewed_at: '2026-10-17T08:00:00Z' }),
    ])
    const section = await screen.findByRole('region', { name: 'Videos' })
    const months = await within(section).findAllByRole('list')
    expect(months.map((list) => list.getAttribute('aria-label'))).toEqual(['October 2026', 'September 2026', 'January 2026'])
    const card = (title: string) => screen.getByRole('link', { name: title }).closest('li') as HTMLElement
    expect(within(card('Legends')).getByText('Analysis queued')).toBeInTheDocument()
    expect(within(card('VBC Swiss')).getByText('12 suggestions to review')).toBeInTheDocument()
    expect(within(card('H1')).getByText('Analysing…')).toBeInTheDocument()
    expect(within(card('Stäfa')).getByText('Rallies reviewed')).toBeInTheDocument()
  })

  it('still lists a training video from before WiePlay handled matches only', async () => {
    stub(team(), [video({ title: 'Training 12.10.2026', kind: 'training', game_id: null })])
    const section = await screen.findByRole('region', { name: 'Videos' })
    const [card] = (await within(section).findAllByRole('listitem')) as [HTMLElement]
    expect(within(card).getByRole('link', { name: 'Training 12.10.2026' })).toBeInTheDocument()
    expect(card).not.toHaveTextContent(/video\.kind|Match/)
  })

  it('says so when video storage is not set up', async () => {
    stub(team({ can_upload: true }), [], {
      '/api/video/config': { body: { ...config, enabled: false } },
    })
    const section = await screen.findByRole('region', { name: 'Videos' })
    expect(await within(section).findByText('Video storage is not set up yet.')).toBeInTheDocument()
    expect(within(section).queryByRole('link', { name: /Upload video/ })).not.toBeInTheDocument()
  })


  it('shows upcoming games as filming allowed or blocked, with the reasons', async () => {
    stub(team(), [], {
      '/api/teams/92/upcoming-filming': {
        body: [
          { kind: 'match', id: 1, date: '2026-10-18', label: 'KSCW – Volley Näfels', allowed: true, blockers: [], adult_checked_at: null },
          {
            kind: 'match',
            id: 2,
            date: '2026-10-25',
            label: 'VBC Zürich – KSCW',
            allowed: false,
            blockers: [{ code: 'under_18', count: 2, detail: null }],
            adult_checked_at: null,
          },
        ],
      },
    })
    const section = await screen.findByRole('region', { name: 'Filming' })
    const rows = (await within(section).findAllByRole('row')).slice(1)
    expect(rows[0]).toHaveTextContent('18.10.2026')
    expect(rows[0]).toHaveTextContent('KSCW – Volley Näfels')
    expect(rows[0]).toHaveTextContent('Filming allowed')
    expect(rows[1]).toHaveTextContent('Filming blocked')
    expect(rows[1]).toHaveTextContent('2 people on the roster are under 18 on that day.')
  })
})

describe('TeamPage highlights', () => {
  it('offers players their highlights of the season', async () => {
    stub(team(), [])
    const section = await screen.findByRole('region', { name: 'My highlights' })
    expect(within(section).getByRole('link', { name: 'Watch my highlights' })).toHaveAttribute('href', '/teams/92/highlights')
  })

  it('has no highlights section for coaches', async () => {
    stub(team({ rel: ['coach'], can_upload: true }), [])
    await screen.findByRole('region', { name: 'Videos' })
    expect(screen.queryByRole('region', { name: 'My highlights' })).not.toBeInTheDocument()
  })
})

describe('TeamPage season stats', () => {
  const MATCH = '00000000-0000-4000-8000-000000000001'
  const season = teamStats({
    videos: 2,
    tagged: 1,
    season_box: boxScore({
      players: [
        statRow({ kind: 'player', number: 7, member_id: 107, name: 'Luca Brunner', filter: { side: 'us', member_id: 107 }, matches: 1, values: { points: 3, att_ta: 4, att_kill: 3 } }),
        statRow({ kind: 'other', filter: { side: 'us', member_id: null }, matches: 1, values: { att_ta: 1, att_kill: 1 } }),
      ],
      team: statRow({ kind: 'team', matches: 1, values: { att_ta: 5, att_kill: 4 } }),
      summary: teamLine({ rallies: 40, won: 25, lost: 15, side_out_won: 10, side_out_of: 15, side_out_pct: 10 / 15 }),
    }),
    matches: [
      { video_id: MATCH, title: 'KSCW – Volley Näfels', recorded_on: '2026-10-11', result_us: 3, result_them: 1, tagged: true, summary: teamLine({ won: 25, lost: 15 }) },
      { video_id: 'x', title: 'Not tagged', recorded_on: '2026-10-14', result_us: null, result_them: null, tagged: false, summary: teamLine() },
    ],
  })

  it('adds up the season; a number opens its moments as a smart reel', async () => {
    stub(team(), [], { '/api/teams/92/stats': { body: season } })
    const section = await screen.findByRole('region', { name: 'Season stats' })
    const table = await within(section).findByRole('table', { name: 'Season stats · H3 2026/27' })
    expect(within(section).getByText(/1 of 2 matches tagged/)).toBeInTheDocument()
    const kills = within(table).getByRole('link', { name: 'Open #7 Luca Brunner, Kills (#) as a reel: 3' })
    expect(kills).toHaveAttribute('href', '/reels/new?team=92&season=2026%2F27&member=107&codes=A&grades=%23')
    expect(within(table).getByRole('link', { name: 'Open Team, Kills (#) as a reel: 4' })).toHaveAttribute(
      'href',
      '/reels/new?team=92&season=2026%2F27&side=us&codes=A&grades=%23',
    )
    // Numbers not on a roster can't be a reel: plain numbers.
    const other = within(table).getByRole('rowheader', { name: 'Not on the roster' }).closest('tr') as HTMLTableRowElement
    expect(within(other).queryByRole('link')).not.toBeInTheDocument()

    const matches = within(section).getByRole('table', { name: 'Matches' })
    const rows = within(matches).getAllByRole('row')
    expect(rows).toHaveLength(3) // header, the tagged match, the total
    expect(within(rows[1] as HTMLElement).getByRole('link', { name: 'KSCW – Volley Näfels' })).toHaveAttribute('href', `/videos/${MATCH}`)
    expect(rows[2]).toHaveTextContent('Total1:02515')
    expect(within(section).getByRole('link', { name: 'Download CSV' })).toHaveAttribute('href', '/api/teams/92/stats.csv?lang=en')
  })

  it('adds up the rotations and the setter over the season, as plain numbers', async () => {
    const rotations = [1, 2, 3, 4, 5, 6].map((r) =>
      rotationLine(r, r === 4 ? { rallies: 30, won: 18, lost: 12, side_out_won: 9, side_out_of: 14, side_out_pct: 9 / 14, values: { att_ta: 12, att_kill: 5, att_hit: 0.25 }, lineup: [11, 9, 7, 3, 1, 14] } : {}),
    )
    const setter = setterStats({
      sets: 40,
      attacks: 36,
      by_attacker: [setterLine('m107', { filter: { attacker_member: 107 }, number: 7, member_id: 107, name: 'Luca Brunner', sets: 20, attacks: 20, share: 20 / 36, zones: { left: 14, back: 6 }, kills: 9, kill_pct: 0.45, hit_pct: 0.3 })],
      by_zone: [setterLine('left', { filter: { attacked: true, zone: 'left' }, sets: 14, attacks: 14, share: 14 / 36, kills: 6, kill_pct: 6 / 14 })],
      by_reception: [setterLine('good', { filter: { quality: 'good' }, sets: 40, attacks: 36, zones: { left: 14, back: 6, unknown: 16 } })],
    })
    stub(team(), [], { '/api/teams/92/stats': { body: { ...season, rotation_columns: rotationColumns, season_box: { ...season.season_box, rotations, setter } } } })
    const section = await screen.findByRole('region', { name: 'Season stats' })
    fireEvent.click(await within(section).findByRole('tab', { name: 'Rotations' }))
    expect(within(section).getByRole('link', { name: 'Download CSV' })).toHaveAttribute('href', '/api/teams/92/stats.csv?lang=en&part=rotations')
    const table = within(section).getByRole('table', { name: 'Points per rotation' })
    const four = within(table).getByRole('rowheader', { name: 'Rotation 4' }).closest('tr') as HTMLTableRowElement
    expect(within(four).getAllByRole('cell').slice(0, 6).map((c) => c.textContent)).toEqual(['30', '18', '12', '14', '9', '64.3%'])
    // Rotations of several matches are not played: no buttons; our skills open a reel of the rotation.
    expect(within(section).queryByRole('button', { name: /^Play/ })).not.toBeInTheDocument()
    expect(within(four).getByRole('link', { name: 'Open Rotation 4, Attacks as a reel: 12' })).toHaveAttribute(
      'href',
      '/reels/new?team=92&season=2026%2F27&side=us&codes=A&q=Rotation+4',
    )
    expect(within(section).getByText('Setter #3')).toBeInTheDocument()

    fireEvent.click(within(section).getByRole('tab', { name: 'Setter' }))
    expect(within(section).getByRole('status')).toHaveTextContent('40 sets tagged · 36 followed by our attack')
    const attackers = within(section).getByRole('table', { name: 'By attacker' })
    expect(within(attackers).getByRole('rowheader', { name: /#7/ }).closest('tr')).toHaveTextContent('202055.6%')
    expect(within(section).getByRole('img', { name: /^Left: 38.9% of the attacks \(14\)/ })).toBeInTheDocument()
    expect(within(section).getByText('Zone not tagged', { exact: false })).toBeInTheDocument()
  })

  it('narrows the season with the search bar; numbers and zones open reels of the same search', async () => {
    const searched = `?search=${encodeURIComponent('{"sets":[3]}')}`
    const zones = {
      codes: { serve: 'S', attack: 'A' },
      scopes: [
        {
          set_no: null,
          rows: [
            {
              kind: 'player',
              side: 'us',
              number: 7,
              member_id: 107,
              name: 'Luca Brunner',
              filter: { side: 'us', member_id: 107 },
              zones: { serve_to: [{ zone: 5, ta: 6, points: 2, errors: 1, blocked: 0, in_play: 3, point_pct: 2 / 6, eff: 1 / 6 }], serve_from: [], attack_from: [], attack_to: [] },
            },
          ],
        },
      ],
    }
    const { router } = stub(team(), [], {
      '/api/teams/92/stats': { body: season },
      [`/api/teams/92/stats${searched}`]: { body: { ...season, selected: 4 } },
      [`/api/teams/92/stats/zones${searched}`]: { body: zones },
    })
    const section = await screen.findByRole('region', { name: 'Season stats' })
    await within(section).findByRole('table', { name: 'Season stats · H3 2026/27' })
    fireEvent.change(within(section).getByRole('combobox', { name: 'Search' }), { target: { value: 'set 3' } })
    expect(await within(section).findByText(/4 tags match/)).toBeInTheDocument()
    expect(within(section).getByRole('link', { name: 'Download CSV' })).toHaveAttribute('href', `/api/teams/92/stats.csv?lang=en${searched.replace('?', '&')}`)
    const kills = within(section).getByRole('link', { name: 'Open #7 Luca Brunner, Kills (#) as a reel: 3' })
    expect(kills).toHaveAttribute('href', '/reels/new?team=92&season=2026%2F27&member=107&codes=A&grades=%23&q=Set+3')

    fireEvent.click(within(section).getByRole('tab', { name: 'Zones' }))
    fireEvent.change(await within(section).findByRole('combobox', { name: 'Who' }), { target: { value: 'player:107' } })
    const court = within(section).getByTestId('zone-court')
    const five = within(court).getByRole('link', { name: 'Open Zone 5: 6 serves, 2 aces, 1 errors, efficiency +16.7% as a reel: 6' })
    fireEvent.click(five)
    expect(router.state.location.pathname).toBe('/reels/new')
    expect(new URLSearchParams(router.state.location.search).get('q')).toBe('Set 3 To 5')
  })

  it('says when nothing of the season is tagged yet', async () => {
    stub(team(), [], { '/api/teams/92/stats': { body: teamStats({ videos: 3 }) } })
    const section = await screen.findByRole('region', { name: 'Season stats' })
    expect(await within(section).findByText('No season stats yet')).toBeInTheDocument()
    expect(within(section).getByText('0 of 3 matches tagged')).toBeInTheDocument()
    expect(within(section).queryByRole('link', { name: 'Download CSV' })).not.toBeInTheDocument()
  })

  it('has no season stats for basketball yet', async () => {
    stub(team({ sport: 'basketball', name: 'Herren 1' }), [])
    await screen.findByRole('region', { name: 'Videos' })
    expect(screen.queryByRole('region', { name: 'Season stats' })).not.toBeInTheDocument()
  })
})

describe('TeamPage seasons', () => {
  it('switches to another season of the team the viewer can see', async () => {
    const { router } = stub(team(), [], {
      '/api/teams/92/seasons': {
        body: [
          { team_id: 92, name: 'H3', season: '2026/27' },
          { team_id: 77, name: 'H4', season: '2025/26' },
        ],
      },
    })
    const pick = await screen.findByRole('combobox', { name: 'Season of H3' })
    expect(within(pick).getAllByRole('option').map((o) => o.textContent)).toEqual(['2026/27', '2025/26 · H4'])
    fireEvent.change(pick, { target: { value: '77' } })
    expect(router.state.location.pathname).toBe('/teams/77')
  })

  it('shows no switch for a team with one season', async () => {
    stub(team(), [], { '/api/teams/92/seasons': { body: [{ team_id: 92, name: 'H3', season: '2026/27' }] } })
    expect(await screen.findByRole('heading', { level: 1, name: 'H3' })).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Season of H3' })).not.toBeInTheDocument()
  })
})
