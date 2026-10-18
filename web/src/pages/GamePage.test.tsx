import { screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { addDaysIso, formatDate, todayIso } from '../lib/format'
import type { GameVideos, LinkedGame, Video } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { GamePage } from './GamePage'

const played = addDaysIso(todayIso(), -2)

const game = (patch: Partial<LinkedGame['team']> = {}, date: string | null = played): LinkedGame => ({
  id: 501,
  date,
  time: '20:00:00',
  home_name: 'KSCW H3',
  away_name: 'Volley Näfels',
  team: { id: 92, name: 'H3', season: '2026/27', sport: 'volleyball', can_upload: false, ...patch },
})

let n = 0
const video = (patch: Partial<Video> = {}): Video => {
  n += 1
  return {
    id: `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`,
    team_id: 92,
    team_name: 'H3',
    team_season: '2026/27',
    sport: 'volleyball',
    kind: 'match',
    game_id: 501,
    title: `KSCW H3 – Volley Näfels ${n}`,
    recorded_on: played,
    source: 'file',
    imported_from: null,
    status: 'ready',
    duration_ms: 5_400_000,
    width: 1920,
    height: 1080,
    fps: 50,
    has_audio: true,
    meets_standard: true,
    standard_issues: [],
    renditions: [],
    has_thumbs: false,
    progress: null,
    error: null,
    created_at: `${played}T20:00:00Z`,
    created_by: 7,
    can_delete: false,
    can_upload: false,
    can_tag: false,
    can_share: false,
    can_export: false,
    files: [],
    ...patch,
  }
}

function open(answer: GameVideos, url = '/games/501') {
  vi.stubGlobal(
    'fetch',
    vi.fn(
      stubFetch({
        '/api/games/501/videos': { body: answer },
        '/api/video/config': { body: { enabled: true, part_size: 10, max_file_bytes: 1e12, max_files: 20 } },
      }),
    ),
  )
  return renderRoute(<GamePage />, {
    path: '/games/:gameId',
    url,
    routes: [{ path: '/videos/:id', element: <p>Video page</p> }],
  })
}

describe('GamePage', () => {
  it('opens the only video straight away', async () => {
    const only = video()
    const { router } = open({ game: game(), videos: [only] })
    expect(await screen.findByText('Video page')).toBeInTheDocument()
    expect(router.state.location.pathname).toBe(`/videos/${only.id}`)
    // Replaced, so Back leaves WiePlay instead of bouncing here again.
    expect(router.state.historyAction).toBe('REPLACE')
  })

  it('lists the videos when more than one camera filmed the game', async () => {
    const [first, second] = [video(), video()]
    open({ game: game(), videos: [first, second] })
    expect(await screen.findByRole('heading', { level: 1, name: 'KSCW H3 – Volley Näfels' })).toBeInTheDocument()
    expect(screen.getByText('More than one camera filmed this game. Pick a video.')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: first.title })).toHaveAttribute('href', `/videos/${first.id}`)
    expect(screen.getByRole('link', { name: second.title })).toHaveAttribute('href', `/videos/${second.id}`)
    expect(screen.getByRole('link', { name: 'H3 2026/27' })).toHaveAttribute('href', '/teams/92')
    const header = screen.getByRole('heading', { level: 1 }).parentElement as HTMLElement
    expect(within(header).getByText(formatDate(played))).toBeInTheDocument()
  })

  it('says there is no video yet and links to the team', async () => {
    open({ game: game(), videos: [] })
    expect(await screen.findByText('No video of this game yet')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Go to H3' })).toHaveAttribute('href', '/teams/92')
    expect(screen.queryByRole('link', { name: 'Upload the video' })).not.toBeInTheDocument()
  })

  it('offers the upload with the game picked to those who may upload', async () => {
    open({ game: game({ can_upload: true }), videos: [] })
    expect(await screen.findByRole('link', { name: 'Upload the video' })).toHaveAttribute(
      'href',
      '/teams/92/upload?game=501',
    )
  })

  it('offers no upload for a game not played yet', async () => {
    open({ game: game({ can_upload: true }, addDaysIso(todayIso(), 3)), videos: [] })
    expect(await screen.findByText('No video of this game yet')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Upload the video' })).not.toBeInTheDocument()
  })

  it('answers a game the viewer may not see like one without a video', async () => {
    open({ game: null, videos: [] })
    expect(await screen.findByText('No video of this game yet')).toBeInTheDocument()
    expect(screen.getByRole('heading', { level: 1, name: 'Game' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to your teams' })).toHaveAttribute('href', '/')
  })

  it.each(['abc', '0', '2147483648', '12345678901'])('treats /games/%s as an unknown page', async (id) => {
    const fetch = vi.fn(stubFetch({}))
    vi.stubGlobal('fetch', fetch)
    renderRoute(<GamePage />, { path: '/games/:gameId', url: `/games/${id}` })
    expect(await screen.findByRole('heading', { level: 1, name: 'Page not found' })).toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalled()
  })
})
