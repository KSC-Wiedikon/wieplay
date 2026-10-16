import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Tagging, TeamDetail, Video } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { bundle, period, rally, tag } from '../test/tagging'
import { VideoPage } from './VideoPage'

const ID = '00000000-0000-4000-8000-000000000001'

const team: TeamDetail = {
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
}

const video = (patch: Partial<Video>): Video => ({
  id: ID,
  team_id: 92,
  team_name: 'H3',
  team_season: '2026/27',
  sport: 'volleyball',
  kind: 'match',
  game_id: 501,
  title: 'KSCW H3 – Volley Näfels',
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
  renditions: [
    { name: '1080p', width: 1920, height: 1080, fps: 50 },
    { name: '540p', width: 960, height: 540, fps: 25 },
  ],
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

function show(body: Video | null, status = 200, extra: Record<string, { status?: number; body?: unknown }> = {}) {
  const fetch = vi.fn(
    stubFetch({
      [`/api/videos/${ID}`]: { status, body: body ?? { detail: 'Video not found' } },
      '/api/teams/92': { body: team },
      '/api/video/config': { body: { enabled: true, part_size: 10, max_file_bytes: 1e12, max_files: 20 } },
      ...extra,
    }),
  )
  vi.stubGlobal('fetch', fetch)
  const view = renderRoute(<VideoPage />, {
    path: '/videos/:id',
    url: `/videos/${ID}`,
    routes: [{ path: '/teams/:id', element: <p>Team page</p> }],
  })
  return { ...view, fetch }
}

describe('VideoPage', () => {
  it('shows the processing progress', async () => {
    show(video({ status: 'processing', progress: 0.42, has_thumbs: false }))
    expect(await screen.findByRole('heading', { level: 1, name: 'KSCW H3 – Volley Näfels' })).toBeInTheDocument()
    expect(screen.getByText('42 %')).toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: 'Processing' })).toHaveAttribute('aria-valuenow', '42')
    expect(screen.getByText(/being prepared for playback/)).toBeInTheDocument()
    expect(await screen.findByRole('link', { name: 'H3 2026/27' })).toHaveAttribute('href', '/teams/92')
    expect(document.querySelector('video')).toBeNull()
  })

  it('shows a waiting video without a known progress as an open-ended bar', async () => {
    show(video({ status: 'queued', progress: null }))
    const bar = await screen.findByRole('progressbar', { name: 'Waiting' })
    expect(bar).not.toHaveAttribute('aria-valuenow')
  })

  it('shows why processing failed', async () => {
    show(video({ status: 'failed', error: 'ffprobe: moov atom not found' }))
    expect(await screen.findByText('Processing failed')).toBeInTheDocument()
    expect(screen.getByText('ffprobe: moov atom not found')).toBeInTheDocument()
    expect(document.querySelector('video')).toBeNull()
  })

  it('plays a ready video, with its facts and the standard hint', async () => {
    show(video({ meets_standard: false, standard_issues: ['720p', '25fps'], width: 1280, height: 720, fps: 25 }))
    await screen.findByRole('heading', { level: 1 })
    const player = document.querySelector('video')
    expect(player).not.toBeNull()
    expect(player).toHaveAttribute('poster', `/api/media/${ID}/poster.jpg`)
    expect(screen.getByText('1:32:10 · 1280×720 · 25 fps')).toBeInTheDocument()
    expect(screen.getByText('Below the filming standard')).toBeInTheDocument()
    expect(screen.getByText('720p')).toBeInTheDocument()
    expect(screen.getByText(/automatic analysis will skip it/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Delete video' })).not.toBeInTheDocument()
  })

  it('shows a refused video like a failed one, with its own wording', async () => {
    show(video({ status: 'refused', error: 'Consent was withdrawn during the upload.', can_delete: true }))
    expect(await screen.findByText('Not allowed to be filmed')).toBeInTheDocument()
    expect(screen.getByText('Consent was withdrawn during the upload.')).toBeInTheDocument()
    expect(screen.getByText('Not allowed')).toBeInTheDocument()
    expect(document.querySelector('video')).toBeNull()
    expect(screen.queryByRole('button', { name: /Try again|Resume/ })).not.toBeInTheDocument()
  })

  it('says the same thing for a missing video and one without access', async () => {
    show(null, 404)
    expect(await screen.findByText("This video doesn't exist or you don't have access to it.")).toBeInTheDocument()
  })

  it('deletes after confirming', async () => {
    const { fetch, router } = show(video({ can_delete: true }), 200, {
      [`DELETE /api/videos/${ID}`]: { status: 204 },
    })
    fireEvent.click(await screen.findByRole('button', { name: 'Delete video' }))
    const dialog = screen.getByRole('alertdialog', { name: 'Delete this video?' })
    expect(dialog).toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('button', { name: 'Delete video' }).at(-1) as HTMLElement)
    await waitFor(() => expect(router.state.location.pathname).toBe('/teams/92'))
    expect(fetch).toHaveBeenCalledWith(`/api/videos/${ID}`, expect.objectContaining({ method: 'DELETE' }))
  })

  it('lists the files of an unfinished upload and refuses files that do not belong to it', async () => {
    show(
      video({
        status: 'uploading',
        can_upload: true,
        can_tag: true,
        can_share: true,
        can_export: true,
        files: [
          { id: 'f1', part_no: 1, name: 'GX010042.MP4', size: 25, part_size: 10, part_count: 3, uploaded: true },
          { id: 'f2', part_no: 2, name: 'GX020042.MP4', size: 15, part_size: 10, part_count: 2, uploaded: false },
        ],
      }),
    )
    expect(await screen.findByRole('heading', { name: 'Upload not finished' })).toBeInTheDocument()
    expect(screen.getByRole('row', { name: /GX010042\.MP4.*Uploaded/ })).toBeInTheDocument()
    expect(screen.getByRole('row', { name: /GX020042\.MP4.*Not uploaded yet/ })).toBeInTheDocument()

    const wrong = new File(['x'.repeat(14)], 'GX020042.MP4')
    fireEvent.change(screen.getByLabelText('Select files'), { target: { files: [wrong] } })
    expect(await screen.findByRole('alert')).toHaveTextContent("These files don't belong to this video: GX020042.MP4.")
  })

  it('shows an import in progress', async () => {
    show(video({ status: 'importing', source: 'import', progress: null, has_thumbs: false, duration_ms: null }))
    expect(await screen.findByRole('heading', { level: 2, name: 'Importing' })).toBeInTheDocument()
    expect(screen.getAllByText('Importing')).toHaveLength(2) // the badge and the heading
    expect(screen.getByRole('progressbar', { name: 'Importing' })).not.toHaveAttribute('aria-valuenow')
    expect(screen.getByText(/fetching the video from its link/)).toBeInTheDocument()
    expect(document.querySelector('video')).toBeNull()
  })

  it('links an imported video to where it came from', async () => {
    show(
      video({
        source: 'import',
        imported_from: {
          platform: 'youtube',
          url: 'https://www.youtube.com/watch?v=NXNsZ_UNM6w',
          channel: 'Luca Zbinden Volley',
          title: 'Herren Volleyball 2. Liga',
        },
      }),
    )
    const link = await screen.findByRole('link', { name: 'Imported from YouTube · Luca Zbinden Volley' })
    expect(link).toHaveAttribute('href', 'https://www.youtube.com/watch?v=NXNsZ_UNM6w')
    expect(link).toHaveAttribute('target', '_blank')
    expect(link).toHaveAttribute('rel', 'noreferrer noopener')
  })

  it('never links anything but a video address', async () => {
    show(
      video({
        source: 'import',
        imported_from: { platform: 'twitch', url: 'javascript:alert(1)', channel: null, title: null },
      }),
    )
    expect(await screen.findByText('Imported from Twitch')).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: /Imported from/ })).not.toBeInTheDocument()
  })

  it('says when an import failed or its channel was refused', async () => {
    show(
      video({
        status: 'failed',
        source: 'import',
        error: 'import: The channel X is not on the import allow-list',
        can_delete: true,
      }),
    )
    expect(await screen.findByText('Import failed')).toBeInTheDocument()
    expect(screen.getByText("The video couldn't be fetched from its link.")).toBeInTheDocument()
    expect(screen.getByText('import: The channel X is not on the import allow-list')).toBeInTheDocument()
  })

  it('words a refused import apart from the filming rules alone', async () => {
    show(video({ status: 'refused', source: 'import', error: "The video's channel is not on the import allow-list" }))
    expect(await screen.findByText('Not imported')).toBeInTheDocument()
    expect(screen.getByText(/channel or the filming rules did not allow it/)).toBeInTheDocument()
  })

  describe('rallies and skip dead time', () => {
    const set1 = period({ no: 1, start_ms: 0, first_serve: 'us' })
    const r1 = rally({ start_ms: 10_000, end_ms: 20_000, won_by: 'us', period_id: set1.id, score_us_after: 1, score_them_after: 0 })
    const r2 = rally({ start_ms: 60_000, end_ms: 70_000, won_by: 'them', period_id: set1.id, score_us_after: 1, score_them_after: 1 })
    const tagged = (patch: Partial<Tagging> = {}): Tagging =>
      bundle({
        can_tag: false,
        periods: [set1],
        segments: [r1, r2],
        events: [
          tag({ t_ms: 15_000, code: 'A', number: 7, attributes: { grade: '#' } }),
          tag({ t_ms: 65_000, code: 'A', number: 3, member_id: 103, attributes: { grade: '=' } }),
        ],
        ...patch,
      })

    function play(player: HTMLVideoElement) {
      Object.defineProperty(player, 'paused', { configurable: true, get: () => false })
      fireEvent(player, new Event('play'))
    }

    beforeEach(() => {
      window.localStorage.clear()
      vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve())
    })

    it('plays only the rallies, with 2 s before and 1.5 s after, until switched off', async () => {
      show(video({}), 200, { [`/api/videos/${ID}/tagging`]: { body: tagged() } })
      const toggle = await screen.findByRole('switch', { name: 'Skip dead time' })
      expect(toggle).toHaveAttribute('aria-checked', 'true')
      expect(screen.getByText('2 tags · 2 rallies')).toBeInTheDocument()
      expect(screen.getByText('Plays only the rallies: 0:27 of 1:32:10.')).toBeInTheDocument()

      const player = document.querySelector('video') as HTMLVideoElement
      player.currentTime = 0
      play(player)
      expect(player.currentTime).toBe(8)

      player.currentTime = 21.4
      fireEvent(player, new Event('timeupdate'))
      expect(player.currentTime).toBe(21.4)
      player.currentTime = 30
      fireEvent(player, new Event('timeupdate'))
      expect(player.currentTime).toBe(58)

      fireEvent.click(toggle)
      expect(toggle).toHaveAttribute('aria-checked', 'false')
      expect(screen.getByText('Plays the whole video.')).toBeInTheDocument()
      player.currentTime = 30
      fireEvent(player, new Event('timeupdate'))
      expect(player.currentTime).toBe(30)
      expect(window.localStorage.getItem('nomal-skip-dead-time')).toBe('off')
    })

    it('lists the rallies with set and score; a click plays from 2 s before', async () => {
      show(video({}), 200, { [`/api/videos/${ID}/tagging`]: { body: tagged() } })
      const list = await screen.findByRole('table', { name: 'Rallies' })
      const rows = within(list).getAllByRole('row').slice(1)
      expect(rows.map((r) => r.textContent)).toEqual(['110:101:0Us', '211:001:1Them'])

      const player = document.querySelector('video') as HTMLVideoElement
      fireEvent.click(within(list).getByRole('button', { name: 'Play rally 2' }))
      expect(player.currentTime).toBe(58)
      expect(HTMLMediaElement.prototype.play).toHaveBeenCalled()
    })

    it('narrows the rallies to those with matching tags, and plays only those', async () => {
      show(video({}), 200, { [`/api/videos/${ID}/tagging`]: { body: tagged() } })
      const list = await screen.findByRole('table', { name: 'Rallies' })
      fireEvent.change(screen.getByLabelText('Player'), { target: { value: 'us:3' } })
      expect(within(list).getAllByRole('row').slice(1)).toHaveLength(1)
      expect(within(list).getByRole('button', { name: 'Play rally 2' })).toBeInTheDocument()

      const player = document.querySelector('video') as HTMLVideoElement
      player.currentTime = 0
      play(player)
      expect(player.currentTime).toBe(58)

      fireEvent.change(screen.getByLabelText('Grade'), { target: { value: '#' } })
      expect(screen.getByText('No rallies match the filters.')).toBeInTheDocument()
    })

    it('offers tagging to coaches', async () => {
      show(video({}), 200, { [`/api/videos/${ID}/tagging`]: { body: tagged({ can_tag: true, segments: [], events: [] }) } })
      expect(await screen.findByRole('link', { name: 'Tag this video' })).toHaveAttribute('href', `/videos/${ID}/tag`)
      expect(screen.getByText('0 tags · 0 rallies')).toBeInTheDocument()
      // No rallies: the player plays as before.
      expect(screen.queryByRole('switch', { name: 'Skip dead time' })).not.toBeInTheDocument()
    })

    it('shows nothing extra when the tagging data is missing', async () => {
      show(video({}))
      await screen.findByRole('heading', { level: 1 })
      await waitFor(() => expect(screen.queryByText(/tags ·/)).not.toBeInTheDocument())
      expect(screen.queryByRole('link', { name: 'Tag this video' })).not.toBeInTheDocument()
    })
  })
})
