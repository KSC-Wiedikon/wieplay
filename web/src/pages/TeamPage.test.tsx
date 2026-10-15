import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { TeamDetail, Video } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
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
  training_id: null,
  title: 'KSCW – Volley Näfels',
  recorded_on: '2026-10-11',
  source: 'file',
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
  files: [],
  ...patch,
})

const config = { enabled: true, part_size: 67_108_864, max_file_bytes: 2e11, max_files: 20, notice_version: 'x' }

function stub(teamDetail: TeamDetail, videos: Video[], extra: Record<string, { status?: number; body?: unknown }> = {}) {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      stubFetch({
        '/api/teams/92': { body: teamDetail },
        '/api/teams/92/games': { body: [] },
        '/api/teams/92/trainings': { body: [] },
        '/api/teams/92/videos': { body: videos },
        '/api/teams/92/upcoming-filming': { body: [] },
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
          title: 'Training 12.10.2026',
          kind: 'training',
          recorded_on: '2026-10-12',
          status: 'processing',
          has_thumbs: false,
          duration_ms: null,
          progress: 0.4,
        }),
      ],
      { '/api/teams/92/consent': { body: [] } },
    )

    const section = await screen.findByRole('region', { name: 'Videos' })
    const table = await within(section).findByRole('table', { name: 'Videos' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows.map((row) => within(row).getByRole('link').textContent)).toEqual([
      'Training 12.10.2026',
      'Older match',
    ])

    const [training, match] = rows as [HTMLElement, HTMLElement]
    expect(within(training).getByRole('link')).toHaveAttribute('href', '/videos/00000000-0000-4000-8000-000000000002')
    expect(within(training).getByText('Processing')).toBeInTheDocument()
    expect(training.querySelector('img')).toBeNull()

    expect(within(match).getByText('Ready')).toBeInTheDocument()
    expect(within(match).getAllByText(/1:32:10/).length).toBeGreaterThan(0)
    expect(match.querySelector('img')).toHaveAttribute('src', '/api/media/00000000-0000-4000-8000-000000000001/poster.jpg')

    expect(within(section).getByRole('link', { name: /Upload video/ })).toHaveAttribute('href', '/teams/92/upload')
  })

  it('says so when video storage is not set up', async () => {
    stub(team({ can_upload: true }), [], {
      '/api/video/config': { body: { ...config, enabled: false } },
      '/api/teams/92/consent': { body: [] },
    })
    const section = await screen.findByRole('region', { name: 'Videos' })
    expect(await within(section).findByText('Video storage is not set up yet.')).toBeInTheDocument()
    expect(within(section).queryByRole('link', { name: /Upload video/ })).not.toBeInTheDocument()
  })

  it('marks roster answers to an older notice and offers the paper form again', async () => {
    stub(team({ can_upload: true }), [], {
      '/api/video/config': { body: { ...config, notice_version: '2027-01' } },
      '/api/teams/92/consent': {
        body: [
          { member_id: 1, name: 'Luca Beispiel', number: 4, status: 'accepted', accepted_version: '2027-01', via: 'nomal', at: null },
          { member_id: 2, name: 'Marco Alt', number: 5, status: 'accepted', accepted_version: '2026-10-draft', via: 'nomal', at: null },
        ],
      },
    })
    const section = await screen.findByRole('region', { name: 'Consent' })
    const rows = (await within(section).findAllByRole('row')).slice(1)
    expect(rows[0]).toHaveTextContent('Agreed')
    expect(within(rows[0] as HTMLElement).getByRole('button', { name: 'Record a withdrawal for Luca Beispiel' })).toBeInTheDocument()
    expect(rows[1]).toHaveTextContent('Agreed to an older notice')
    expect(within(rows[1] as HTMLElement).getByRole('button', { name: 'Record a signed paper form for Marco Alt' })).toBeInTheDocument()
  })

  it('shows upcoming games as filming allowed or blocked, with the reasons', async () => {
    stub(team(), [], {
      '/api/teams/92/upcoming-filming': {
        body: [
          { kind: 'match', id: 1, date: '2026-10-18', label: 'KSCW – Volley Näfels', allowed: true, blockers: [], adult_checked_at: null },
          {
            kind: 'training',
            id: 2,
            date: '2026-10-20',
            label: 'Sihlhölzli',
            allowed: false,
            blockers: [{ code: 'consent_missing', count: 2, detail: null }],
            adult_checked_at: null,
          },
        ],
      },
    })
    const section = await screen.findByRole('region', { name: 'Filming' })
    const rows = (await within(section).findAllByRole('row')).slice(1)
    expect(rows[0]).toHaveTextContent('18.10.2026')
    expect(rows[0]).toHaveTextContent('Filming allowed')
    expect(rows[1]).toHaveTextContent('Filming blocked')
    expect(rows[1]).toHaveTextContent('2 players have not agreed to filming yet.')
  })
})
