import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { TeamDetail, Video } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { boxScore, statRow, teamLine, teamStats } from '../test/stats'
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

  it('lists videos newest first with poster, length and status, and offers the upload to coaches', async () => {
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
    const table = await within(section).findByRole('table', { name: 'Videos' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((row) => within(row).getByRole('link').textContent)).toEqual([
      'Friendly vs. VBC Zürich',
      'Older match',
    ])

    const [friendly, match] = rows as [HTMLElement, HTMLElement]
    expect(within(friendly).getByRole('link')).toHaveAttribute('href', '/videos/00000000-0000-4000-8000-000000000002')
    expect(within(friendly).getByText('Processing')).toBeInTheDocument()
    expect(friendly.querySelector('img')).toBeNull()

    expect(within(match).getByText('Ready')).toBeInTheDocument()
    expect(within(match).getAllByText(/1:32:10/).length).toBeGreaterThan(0)
    expect(match.querySelector('img')).toHaveAttribute('src', '/api/media/00000000-0000-4000-8000-000000000001/poster.jpg')

    expect(within(section).getByRole('link', { name: /Upload video/ })).toHaveAttribute('href', '/teams/92/upload')
  })

  it('shows a video being imported from a link as importing', async () => {
    stub(team({ can_upload: true, rel: ['coach'] }), [
      video({ title: 'Cup vs. VBC Zürich', source: 'import', status: 'importing', has_thumbs: false, duration_ms: null }),
    ])
    const table = await screen.findByRole('table', { name: 'Videos' })
    const [row] = within(table).getAllByRole('row').slice(1) as [HTMLElement]
    expect(within(row).getByText('Importing')).toBeInTheDocument()
  })

  it('still lists a training video from before Nomal handled matches only', async () => {
    stub(team(), [video({ title: 'Training 12.10.2026', kind: 'training', game_id: null })])
    const table = await screen.findByRole('table', { name: 'Videos' })
    const [row] = within(table).getAllByRole('row').slice(1) as [HTMLElement]
    expect(within(row).getByRole('link', { name: 'Training 12.10.2026' })).toBeInTheDocument()
    expect(row).not.toHaveTextContent(/video\.kind|Match/)
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
