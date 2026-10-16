import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Analysis, Tagging, TeamDetail, Video } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { boxScore, statRow, teamLine, videoStats } from '../test/stats'
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
      [`/api/videos/${ID}/stats`]: { body: videoStats() },
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

  describe('automatic rallies', () => {
    const set1 = period({ no: 1, start_ms: 0, first_serve: 'us' })
    const r1 = rally({ start_ms: 10_000, end_ms: 20_000, won_by: 'us', period_id: set1.id, score_us_after: 1, score_them_after: 0 })
    const open = rally({ start_ms: 40_000, end_ms: 48_000, source: 'model', confidence: 0.81 })
    const accepted = rally({ start_ms: 60_000, end_ms: 70_000, source: 'model', review: 'confirmed', confidence: 0.93 })
    const counts = (patch: Partial<Analysis['proposals']> = {}) => ({ unreviewed: 0, confirmed: 0, corrected: 0, rejected: 0, ...patch })
    const analysis = (patch: Partial<Analysis> = {}): Analysis => ({ status: 'done', model_run: null, proposals: counts(), metrics_vs_human: null, ...patch })

    function showAnalysis(state: { analysis: Analysis }, { canTag = true, segments = [r1, open, accepted], reviewedAt = null as string | null } = {}) {
      const posts: { path: string; body: unknown }[] = []
      const current = { video: video({ can_tag: canTag, rallies_reviewed_at: reviewedAt }) }
      const view = show(current.video, 200, {
        [`/api/videos/${ID}`]: { body: () => current.video },
        [`/api/videos/${ID}/tagging`]: { body: bundle({ can_tag: canTag, periods: [set1], segments, events: [] }) },
        [`/api/videos/${ID}/analysis`]: { body: () => state.analysis },
        [`POST /api/videos/${ID}/analyse`]: {
          status: 202,
          body: ({ path, body }: { path: string; body: unknown }) => {
            posts.push({ path, body })
            state.analysis = { ...state.analysis, status: 'queued' }
            return {}
          },
        },
        [`POST /api/videos/${ID}/rallies-reviewed`]: {
          body: ({ path, body }: { path: string; body: unknown }) => {
            posts.push({ path, body })
            const at = (body as { reviewed: boolean }).reviewed ? '2026-10-16T19:12:00Z' : null
            current.video = { ...current.video, rallies_reviewed_at: at }
            return { rallies_reviewed_at: at }
          },
        },
      })
      return { ...view, posts }
    }

    it("keeps open suggestions out of the rallies and links coaches to their review", async () => {
      showAnalysis({ analysis: analysis({ proposals: counts({ unreviewed: 1, confirmed: 1, rejected: 1 }) }) })
      expect(await screen.findByText('Nomal suggested 3 rallies.')).toBeInTheDocument()
      expect(screen.getByText('1 still to review.')).toBeInTheDocument()
      expect(screen.getByRole('link', { name: 'Review suggestions (1)' })).toHaveAttribute('href', `/videos/${ID}/tag?mode=review`)
      // Nothing is shown as fact until it is reviewed (PLAN §10): the accepted one is a rally, the open one not.
      expect(screen.getByText('0 tags · 2 rallies')).toBeInTheDocument()
      const list = screen.getByRole('table', { name: 'Rallies' })
      expect(within(list).getAllByRole('row').slice(1)).toHaveLength(2)
      // Marking the rallies reviewed now accepts the open suggestion: asked first.
      const reviewed = screen.getByRole('switch', { name: 'Rallies reviewed' })
      expect(reviewed).toHaveAccessibleDescription(/the open suggestion is then accepted as it is/)
      fireEvent.click(reviewed)
      const dialog = screen.getByRole('alertdialog', { name: 'Mark the rallies reviewed?' })
      expect(dialog).toHaveTextContent('The suggestion still open is accepted as it is')
      fireEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }))
      expect(reviewed).toHaveAttribute('aria-checked', 'false')
    })

    it('analyses on request, and shows the analysis queued, running and failed', async () => {
      const state = { analysis: analysis({ status: 'none' }) }
      const { posts, queryClient } = showAnalysis(state, { segments: [r1] })
      expect(await screen.findByText('Nomal has not analysed this video yet.')).toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: 'Analyse' }))
      expect(await screen.findByText('Analysis queued.')).toBeInTheDocument()
      expect(posts).toEqual([{ path: `/api/videos/${ID}/analyse`, body: undefined }])
      expect(screen.getByText('This takes a few minutes. The page updates by itself.')).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Analyse' })).not.toBeInTheDocument()

      state.analysis = analysis({ status: 'running' })
      await queryClient.invalidateQueries()
      expect(await screen.findByText('Nomal is finding the rallies…')).toBeInTheDocument()

      state.analysis = analysis({ status: 'failed' })
      await queryClient.invalidateQueries()
      expect(await screen.findByText('The analysis failed.')).toBeInTheDocument()
      fireEvent.click(within(screen.getByRole('region', { name: 'Automatic rallies' })).getByRole('button', { name: 'Try again' }))
      await waitFor(() => expect(posts).toHaveLength(2))
    })

    it("tells how well the suggestions matched the coach's own rallies", async () => {
      showAnalysis(
        { analysis: analysis({ metrics_vs_human: { f1_1s: 0.94, precision: 0.95, recall: 0.93, iou: 0.88, rallies_human: 185, rallies_model: 181 } }) },
        { segments: [r1] },
      )
      expect(await screen.findByText('Nomal compared its rally finder with your rallies.')).toBeInTheDocument()
      expect(screen.getByText('Suggestions matched your rallies: F1 94% at ±1 s')).toBeInTheDocument()
      expect(screen.getByText('Precision 95%, recall 93%. Nomal: 181 rallies, you: 185.')).toBeInTheDocument()
    })

    it('asks to mark the rallies reviewed once every suggestion is answered, and can take it back', async () => {
      const { posts } = showAnalysis({ analysis: analysis({ proposals: counts({ confirmed: 1, rejected: 1 }) }) }, { segments: [r1, accepted] })
      expect(await screen.findByText('Every suggestion is reviewed.')).toBeInTheDocument()
      fireEvent.click(screen.getByRole('button', { name: 'Mark rallies reviewed' }))
      const reviewed = await screen.findByRole('switch', { name: 'Rallies reviewed' })
      expect(reviewed).toHaveAttribute('aria-checked', 'true')
      expect(await screen.findByText('Rallies marked reviewed. Nomal learns from this game.')).toBeInTheDocument()
      expect(posts).toEqual([{ path: `/api/videos/${ID}/rallies-reviewed`, body: { reviewed: true } }])
      await waitFor(() => expect(reviewed).toHaveAccessibleDescription('Marked reviewed on 16.10.2026 21:12. Nomal learns from this game.'))

      fireEvent.click(reviewed)
      await waitFor(() => expect(posts.at(-1)).toEqual({ path: `/api/videos/${ID}/rallies-reviewed`, body: { reviewed: false } }))
      expect(await screen.findByText('Every suggestion is reviewed.')).toBeInTheDocument()
    })

    it('lets a coach mark a game tagged by hand reviewed (training data without suggestions)', async () => {
      const { posts } = showAnalysis({ analysis: analysis({ status: 'none' }) }, { segments: [r1] })
      const reviewed = await screen.findByRole('switch', { name: 'Rallies reviewed' })
      expect(reviewed).toHaveAccessibleDescription(/Nomal learns from reviewed games/)
      fireEvent.click(reviewed)
      await waitFor(() => expect(posts).toEqual([{ path: `/api/videos/${ID}/rallies-reviewed`, body: { reviewed: true } }]))
    })

    it('shows players neither the analysis nor open suggestions', async () => {
      showAnalysis({ analysis: analysis({ proposals: counts({ unreviewed: 1 }) }) }, { canTag: false })
      expect(await screen.findByRole('table', { name: 'Rallies' })).toBeInTheDocument()
      expect(screen.queryByText(/Nomal suggested/)).not.toBeInTheDocument()
      expect(within(screen.getByRole('table', { name: 'Rallies' })).getAllByRole('row').slice(1)).toHaveLength(2)
    })
  })

  describe('stats', () => {
    const set1 = period({ no: 1, start_ms: 0, first_serve: 'us', final_us: 1, final_them: 1 })
    const r1 = rally({ start_ms: 10_000, end_ms: 20_000, serving: 'us', won_by: 'us', period_id: set1.id, score_us_after: 1, score_them_after: 0 })
    const r2 = rally({ start_ms: 60_000, end_ms: 70_000, serving: 'us', won_by: 'them', period_id: set1.id, score_us_after: 1, score_them_after: 1 })
    const kill = tag({ t_ms: 15_000, code: 'A', number: 7, attributes: { grade: '#' }, segment_id: r1.id })
    const error = tag({ t_ms: 65_000, code: 'A', number: 7, attributes: { grade: '=' }, segment_id: r2.id })
    const tagging = bundle({ can_tag: false, periods: [set1], segments: [r1, r2], events: [kill, error] })
    const attack = { points: 1, att_ta: 2, att_kill: 1, att_err: 1, att_blocked: 0, att_hit: 0, att_kill_pct: 0.5, att_err_pct: 0.5 }
    const box = boxScore({
      players: [statRow({ kind: 'player', number: 7, member_id: 107, name: 'Luca Brunner', filter: { side: 'us', number: 7 }, values: attack })],
      team: statRow({ kind: 'team', values: attack }),
      summary: teamLine({ rallies: 2, won: 1, lost: 1, break_point_won: 1, break_point_of: 2, break_point_pct: 0.5 }),
    })
    const stats = videoStats({ tags: 2, rallies: 2, match: box, sets: [{ ...box, no: 1, score_us: 1, score_them: 1 }] })

    function showStats(patch: { tagging?: Tagging; stats?: unknown; video?: Partial<Video> } = {}) {
      return show(video(patch.video ?? {}), 200, {
        [`/api/videos/${ID}/tagging`]: { body: patch.tagging ?? tagging },
        [`/api/videos/${ID}/stats`]: { body: patch.stats ?? stats },
      })
    }

    beforeEach(() => {
      window.localStorage.clear()
      vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve())
    })

    it('shows the box score in groups, with a dash where nothing was tried', async () => {
      showStats()
      const table = await screen.findByRole('table', { name: 'Box score' })
      expect(within(table).getByRole('columnheader', { name: 'Attack' })).toHaveAttribute('colspan', '7')
      expect(within(table).getByRole('columnheader', { name: 'Serve' })).toBeInTheDocument()
      const row = within(table).getByRole('rowheader', { name: /#7/ }).closest('tr') as HTMLTableRowElement
      const cells = within(row).getAllByRole('cell').map((c) => c.textContent)
      expect(cells.slice(0, 3)).toEqual(['1', '–', '–']) // points, then no serves
      expect(cells.slice(12, 19)).toEqual(['2', '1', '1', '0', '0.0%', '50.0%', '50.0%']) // attack
      // Only numbers with moments behind them are buttons.
      expect(within(row).queryByRole('button', { name: /Attacks blocked/ })).not.toBeInTheDocument()
      expect(within(table).getByRole('rowheader', { name: 'Team' })).toBeInTheDocument()
      expect(within(table).getByRole('rowheader', { name: 'Opponent' })).toBeInTheDocument()
      expect(screen.getByRole('link', { name: 'Download CSV' })).toHaveAttribute('href', `/api/videos/${ID}/stats.csv?lang=en`)
      expect(screen.getByText('What the numbers mean')).toBeInTheDocument()
    })

    it("plays a number's moments one after another, and leads back to the stats", async () => {
      showStats()
      const button = await screen.findByRole('button', { name: 'Play #7 Luca Brunner, Attacks: 2' })
      fireEvent.click(button)
      expect(button).toHaveAttribute('aria-pressed', 'true')
      const player = document.querySelector('video') as HTMLVideoElement
      expect(player.currentTime).toBe(12) // the attack 3 s before
      expect(screen.getByText('Playing #7 Luca Brunner · Attack · # Kill · 0:15')).toBeInTheDocument()
      expect(screen.getByText('1 of 2')).toBeInTheDocument()

      Object.defineProperty(player, 'paused', { configurable: true, get: () => false })
      player.currentTime = 17.2
      fireEvent(player, new Event('play'))
      expect(player.currentTime).toBe(62)
      expect(screen.getByText('Playing #7 Luca Brunner · Attack · = Error · 1:05')).toBeInTheDocument()

      const scroll = vi.fn()
      const section = document.getElementById('video-stats') as HTMLElement
      section.scrollIntoView = scroll
      fireEvent.click(screen.getByRole('button', { name: 'Back to the stats' }))
      expect(scroll).toHaveBeenCalled()
    })

    it('narrows the box score to a set; team numbers play their rallies', async () => {
      showStats()
      const sets = await screen.findByRole('group', { name: 'Box score of' })
      fireEvent.click(within(sets).getByRole('button', { name: 'Set 1' }))
      expect(within(sets).getByRole('button', { name: 'Set 1' })).toHaveAttribute('aria-pressed', 'true')
      expect(screen.getByRole('table', { name: 'Box score · Set 1' })).toBeInTheDocument()

      const team = screen.getByRole('table', { name: 'Points, side-out and break-point' })
      expect(within(team).getAllByRole('row').map((r) => r.firstElementChild?.textContent)).toEqual(['Set', 'Set 1', 'Match'])
      fireEvent.click(within(team).getByRole('button', { name: 'Play Set 1, Rallies lost: 1' }))
      expect((document.querySelector('video') as HTMLVideoElement).currentTime).toBe(58)
      expect(screen.getByText('Playing Rally 2 · 1:1')).toBeInTheDocument()
      // Nothing behind a zero: no side-outs to play.
      expect(within(team).queryByRole('button', { name: /Side-outs: rallies we received and won/ })).not.toBeInTheDocument()
    })

    it('counts facts only and says how many suggested actions wait', async () => {
      // Nomal suggested a third attack of #7; nobody has accepted it: not a number, not a play.
      const suggested = tag({ t_ms: 40_000, code: 'A', number: 7, source: 'model', review: 'unreviewed', confidence: 0.7 })
      showStats({ tagging: { ...tagging, events: [kill, suggested, error] }, stats: { ...stats, suggested: 1 } })
      expect(await screen.findByText('Suggested, not reviewed: 1 action. It counts once a coach accepts it.')).toBeInTheDocument()
      fireEvent.click(await screen.findByRole('button', { name: 'Play #7 Luca Brunner, Attacks: 2' }))
      expect(screen.getByText('1 of 2')).toBeInTheDocument()
    })

    it('says when there is nothing to count yet', async () => {
      showStats({ tagging: bundle({ can_tag: true }), stats: videoStats() })
      expect(await screen.findByText('No stats yet')).toBeInTheDocument()
      expect(screen.getByText(/Tag the match and the numbers fill in here/)).toBeInTheDocument()
      expect(screen.queryByRole('link', { name: 'Download CSV' })).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'See the stats' })).not.toBeInTheDocument()
    })

    it('has no stats for basketball yet', async () => {
      const { fetch } = showStats({ video: { sport: 'basketball' }, tagging: bundle({ sport: 'basketball' }) })
      await screen.findByRole('heading', { level: 1 })
      await waitFor(() => expect(fetch).toHaveBeenCalledWith(`/api/videos/${ID}/tagging`, expect.anything()))
      expect(screen.queryByRole('heading', { name: 'Stats' })).not.toBeInTheDocument()
      expect(fetch).not.toHaveBeenCalledWith(`/api/videos/${ID}/stats`, expect.anything())
    })
  })

  describe('automatic scoring', () => {
    const set1 = period({ no: 1, start_ms: 300_000, end_ms: 1_800_000, first_serve: 'us' })
    const set2 = period({ no: 2, start_ms: 2_000_000, source: 'model', review: 'unreviewed', confidence: 0.7 })
    const r1 = rally({ start_ms: 300_000, end_ms: 310_000, won_by: 'us', period_id: set1.id, score_us_after: 1, score_them_after: 0, score_source: 'tags' })
    const r2 = rally({
      start_ms: 320_000,
      end_ms: 330_000,
      source: 'model',
      review: 'confirmed',
      model_won_by: 'them',
      period_id: set1.id,
      score_us_after: 1,
      score_them_after: 1,
      score_source: 'model',
    })
    const warmup = rally({ kind: 'warmup', start_ms: 0, end_ms: 290_000, source: 'model', confidence: 0.9 })
    const timeout = rally({ kind: 'timeout', start_ms: 600_000, end_ms: 630_000 })
    const tagged = (patch: Partial<Tagging> = {}): Tagging =>
      bundle({ can_tag: false, periods: [set1, set2], segments: [warmup, r1, r2, timeout], first_serve_ms: 300_000, ...patch })

    beforeEach(() => {
      window.localStorage.clear()
      vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve())
    })

    it('starts at the first serve, just before it, and plays', async () => {
      show(video({}), 200, { [`/api/videos/${ID}/tagging`]: { body: tagged() } })
      const start = await screen.findByRole('button', { name: 'Start at first serve' })
      const player = document.querySelector('video') as HTMLVideoElement
      fireEvent.click(start)
      expect(player.currentTime).toBe(298) // the rallies' 2 s pre-roll
      expect(HTMLMediaElement.prototype.play).toHaveBeenCalled()
    })

    it('has no first serve to start at when it is not known', async () => {
      show(video({}), 200, { [`/api/videos/${ID}/tagging`]: { body: tagged({ first_serve_ms: null }) } })
      await screen.findByRole('table', { name: 'Rallies' })
      expect(screen.queryByRole('button', { name: 'Start at first serve' })).not.toBeInTheDocument()
    })

    it("marks breaks and set starts below the player, Nomal's suggestions lighter", async () => {
      show(video({}), 200, { [`/api/videos/${ID}/tagging`]: { body: tagged() } })
      const strip = await screen.findByTestId('moments-strip')
      const marks = [...strip.querySelectorAll('[data-marker]')].map((m) => [m.getAttribute('data-marker'), m.getAttribute('title'), m.hasAttribute('data-suggested')])
      expect(marks).toEqual([
        ['warmup', 'Warm-up (suggested)', true],
        ['timeout', 'Timeout', false],
        ['set-start', 'Set 1 starts', false],
        ['set-start', 'Set 2 starts (suggested)', true],
      ])
      // The breaks are never played: skipping dead time plays the rallies only.
      expect(screen.getByText('Plays only the rallies: 0:27 of 1:32:10.')).toBeInTheDocument()
    })

    it("shows Nomal's winners as suggestions in the rally list", async () => {
      show(video({}), 200, { [`/api/videos/${ID}/tagging`]: { body: tagged() } })
      const list = await screen.findByRole('table', { name: 'Rallies' })
      const rows = within(list).getAllByRole('row').slice(1)
      expect(rows.map((r) => [...r.querySelectorAll('[data-suggested]')].map((e) => e.textContent))).toEqual([[], ['1:1 (suggested)', 'Them (suggested)']])
    })

    it('sums up the automatic score in the analysis card', async () => {
      const analysis: Analysis = {
        status: 'done',
        model_run: null,
        proposals: { unreviewed: 0, confirmed: 1, corrected: 0, rejected: 0 },
        metrics_vs_human: null,
        scoring: {
          run_id: 'score-1',
          created_at: '2026-10-16T20:00:00Z',
          serve_finder: 'v1',
          rallies: 98,
          flagged: 12,
          sets: [
            { no: 1, start_ms: 300_000, end_ms: 1_800_000, source: 'coach', official: [25, 27], model: [25, 27], evidence: [25, 27], matches_official: true, rallies: 52, flagged: 4, first_serve: 'us', our_side_at_start: 'near' },
            { no: 2, start_ms: 2_000_000, end_ms: null, source: 'model', official: [25, 20], model: [25, 20], evidence: [24, 20], matches_official: false, rallies: 46, flagged: 8, first_serve: 'them', our_side_at_start: 'far' },
          ],
          sets_matching_official: 1,
          sets_official: 2,
          sides_confidence: 0.95,
          gaps: { warmup: 1, set_break: 3, timeout: 5, stoppage: 0, other_dead: 0 },
          first_serve_ms: 300_000,
          vs_coach: { serving_side_accuracy: 0.96, serving_known: 50, winner_accuracy: 0.9, winners_known: 40 },
        },
      }
      show(video({ can_tag: true }), 200, {
        [`/api/videos/${ID}/tagging`]: { body: tagged({ can_tag: true }) },
        [`/api/videos/${ID}/analysis`]: { body: analysis },
      })
      const card = await screen.findByRole('region', { name: 'Automatic score' })
      expect(within(card).getByText('Set 1: official 25:27 · Nomal 25:27')).toBeInTheDocument()
      expect(within(card).getByText('matches')).toBeInTheDocument()
      // Against the official score: where the serve evidence alone ends the set.
      expect(within(card).getByText('Set 2: official 25:20 · Nomal 24:20')).toBeInTheDocument()
      expect(within(card).getByText('differs')).toBeInTheDocument()
      expect(within(card).getByText('12 rallies to check')).toBeInTheDocument()
      expect(within(card).getByText('Breaks found: 1 warm-up · 3 set breaks · 5 timeouts')).toBeInTheDocument()
      expect(within(card).getByText('Serving side right: 96% of 50 rallies you decided')).toBeInTheDocument()
      expect(within(card).getByText('Winner right: 90% of 40 rallies you decided')).toBeInTheDocument()
    })
  })

  describe('stats per rotation', () => {
    const set1 = period({ no: 1, start_ms: 0, first_serve: 'us', final_us: 1, final_them: 1, start_rotation: 1 })
    const r1 = rally({ start_ms: 10_000, end_ms: 20_000, serving: 'us', won_by: 'us', period_id: set1.id, score_us_after: 1, score_them_after: 0 })
    const kill = tag({ t_ms: 15_000, code: 'A', number: 7, attributes: { grade: '#' }, segment_id: r1.id })
    const tagging = bundle({ can_tag: false, periods: [set1], segments: [r1], events: [kill] })
    const rotations = [1, 2, 3, 4, 5, 6].map((rotation) => ({
      rotation,
      ...teamLine(rotation === 1 ? { rallies: 3, won: 2, lost: 1, side_out_won: 1, side_out_of: 1, side_out_pct: 1, break_point_won: 1, break_point_of: 2, break_point_pct: 0.5 } : {}),
    }))
    const box = boxScore({ summary: teamLine({ rallies: 3, won: 2, lost: 1 }), rotations })

    function showStats(stats: unknown) {
      return show(video({}), 200, { [`/api/videos/${ID}/tagging`]: { body: tagging }, [`/api/videos/${ID}/stats`]: { body: stats } })
    }

    beforeEach(() => window.localStorage.clear())

    it('shows points per rotation for the match and the chosen set', async () => {
      showStats(videoStats({ tags: 1, rallies: 3, match: box, sets: [{ ...box, no: 1, score_us: 1, score_them: 1, rotations: rotations.slice(0, 6).map((r) => (r.rotation === 1 ? { ...r, rallies: 2 } : r)) }] }))
      const table = await screen.findByRole('table', { name: 'Points per rotation' })
      const rows = within(table).getAllByRole('row')
      expect(rows.map((r) => r.firstElementChild?.textContent)).toEqual(['Rotation', ...[1, 2, 3, 4, 5, 6].map((n) => `Rotation ${n}`)])
      const first = within(rows[1] as HTMLElement).getAllByRole('cell').map((c) => c.textContent)
      expect(first).toEqual(['3', '2', '1', '1', '1', '100.0%', '2', '1', '50.0%'])
      // Plain numbers: no moments behind them to play.
      expect(within(table).queryByRole('button')).not.toBeInTheDocument()

      fireEvent.click(within(screen.getByRole('group', { name: 'Box score of' })).getByRole('button', { name: 'Set 1' }))
      const set = screen.getByRole('table', { name: 'Points per rotation · Set 1' })
      expect(within(within(set).getAllByRole('row')[1] as HTMLElement).getAllByRole('cell')[0]).toHaveTextContent('2')
    })

    it('says the starting rotation per set is needed when there is none', async () => {
      showStats(videoStats({ tags: 1, rallies: 3, match: { ...box, rotations: null } }))
      expect(await screen.findByText(/need the starting rotation of each set: set it in the sets bar on the tagging page/)).toBeInTheDocument()
      expect(screen.queryByRole('table', { name: 'Points per rotation' })).not.toBeInTheDocument()
    })
  })
})
