import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { addDaysIso, formatDate, todayIso } from '../lib/format'
import type { TeamDetail, Video } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { UploadPage } from './UploadPage'

const VIDEO_ID = '00000000-0000-4000-8000-0000000000aa'
const FILE_ID = '00000000-0000-4000-8000-0000000000f1'
const PART_SIZE = 10

const team: TeamDetail = {
  id: 92,
  name: 'H3',
  sport: 'volleyball',
  season: '2026/27',
  league: '2L',
  rel: ['coach'],
  lineage_id: '6f1c0a52-2a43-4c11-9a43-111111111111',
  can_upload: true,
}

const gameDate = addDaysIso(todayIso(), -3)

const created: Video = {
  id: VIDEO_ID,
  team_id: 92,
  team_name: 'H3',
  team_season: '2026/27',
  sport: 'volleyball',
  kind: 'match',
  game_id: 501,
  training_id: null,
  title: 'KSCW H3 – Volley Näfels',
  recorded_on: gameDate,
  source: 'file',
  status: 'uploading',
  duration_ms: null,
  width: null,
  height: null,
  fps: null,
  has_audio: null,
  meets_standard: null,
  standard_issues: [],
  renditions: [],
  has_thumbs: false,
  progress: null,
  error: null,
  created_at: '2026-10-15T10:00:00Z',
  created_by: 7,
  can_delete: true,
  can_upload: true,
  files: [{ id: FILE_ID, part_no: 1, name: 'GX010042.MP4', size: 25, part_size: PART_SIZE, part_count: 3, uploaded: false }],
}

/** Answers every PUT with 200 after reporting the whole body as sent. */
class FakeXhr {
  static puts: { method: string; url: string; size: number; contentType: string | undefined }[] = []
  status = 0
  upload: { onprogress: ((event: { loaded: number }) => void) | null } = { onprogress: null }
  onload: (() => void) | null = null
  onerror: (() => void) | null = null
  ontimeout: (() => void) | null = null
  onabort: (() => void) | null = null
  private method = ''
  private url = ''
  private headers: Record<string, string> = {}

  open(method: string, url: string) {
    this.method = method
    this.url = url
  }

  setRequestHeader(name: string, value: string) {
    this.headers[name.toLowerCase()] = value
  }

  send(body: Blob) {
    setTimeout(() => {
      this.upload.onprogress?.({ loaded: body.size })
      FakeXhr.puts.push({ method: this.method, url: this.url, size: body.size, contentType: this.headers['content-type'] })
      this.status = 200
      this.onload?.()
    }, 0)
  }

  abort() {
    this.onabort?.()
  }
}

const game = {
  id: 501,
  date: gameDate,
  time: '20:00:00',
  home_name: 'KSCW H3',
  away_name: 'Volley Näfels',
  our_side: 'home',
  league: '2L',
  hall_name: 'Sihlhölzli',
  home_score: 3,
  away_score: 1,
  sets: null,
}

/** The API for one upload; `complete` is the answer to the last file's completion. */
function uploadApi(complete: { status?: number; body?: unknown } = { body: { ...created, status: 'queued' } }) {
  return stubFetch({
    '/api/teams/92': { body: team },
    '/api/video/config': {
      body: { enabled: true, part_size: PART_SIZE, max_file_bytes: 1e12, max_files: 20, notice_version: 'x' },
    },
    '/api/teams/92/games': { body: [game] },
    '/api/teams/92/trainings': { body: [] },
    '/api/inbox': { status: 403, body: { detail: 'Coaches and sport admins only' } },
    [`/api/teams/92/filming?kind=match&on=${gameDate}`]: {
      body: { allowed: true, blockers: [], adult_checked_at: null },
    },
    'POST /api/teams/92/videos': { status: 201, body: created },
    [`GET /api/videos/${VIDEO_ID}/files/${FILE_ID}/parts`]: { body: [] },
    [`POST /api/videos/${VIDEO_ID}/files/${FILE_ID}/parts`]: {
      body: ({ body }: { body: unknown }) =>
        (body as { parts: number[] }).parts.map((part) => ({
          part,
          url: `https://bucket.example/incoming/${VIDEO_ID}/1?partNumber=${part}&sig=x`,
        })),
    },
    [`POST /api/videos/${VIDEO_ID}/files/${FILE_ID}/complete`]: complete,
  })
}

async function pickGameAndFile(name = 'GX010042.MP4') {
  fireEvent.change(await screen.findByLabelText('Game'), { target: { value: '501' } })
  expect(await screen.findByText(`Filming is allowed on ${formatDate(gameDate)}.`)).toBeInTheDocument()
  const file = new File(['x'.repeat(25)], name, { type: 'video/mp4' })
  fireEvent.change(screen.getByLabelText('Choose files'), { target: { files: [file] } })
}

describe('UploadPage', () => {
  it('creates the video, uploads its parts, completes it and opens the video', async () => {
    FakeXhr.puts = []
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    const fetch = vi.fn(uploadApi())
    vi.stubGlobal('fetch', fetch)

    const { router } = renderRoute(<UploadPage />, {
      path: '/teams/:id/upload',
      url: '/teams/92/upload',
      routes: [{ path: '/videos/:id', element: <p>Video page</p> }],
    })

    // 1. The game, then the filming check for its day.
    const game = await screen.findByLabelText('Game')
    expect(within(game).getByRole('option', { name: `${formatDate(gameDate)} · Volley Näfels` })).toBeInTheDocument()
    fireEvent.change(game, { target: { value: '501' } })
    expect(await screen.findByText(`Filming is allowed on ${formatDate(gameDate)}.`)).toBeInTheDocument()

    // 2. Files from this device (the NAS inbox is not offered: 403).
    expect(screen.queryByText('From the NAS inbox')).not.toBeInTheDocument()
    const start = screen.getByRole('button', { name: 'Start upload' })
    expect(start).toBeDisabled()
    const file = new File(['x'.repeat(25)], 'GX010042.MP4', { type: 'video/mp4' })
    fireEvent.change(screen.getByLabelText('Choose files'), { target: { files: [file] } })
    expect(await screen.findByText('1 file, 25 B')).toBeInTheDocument()

    // 3. Start: create → sign parts → PUT → complete → video page.
    fireEvent.click(start)
    await waitFor(() => expect(router.state.location.pathname).toBe(`/videos/${VIDEO_ID}`))
    expect(screen.getByText('Video page')).toBeInTheDocument()

    const calls = fetch.mock.calls.map(([path, init]) => `${init?.method ?? 'GET'} ${String(path)}`)
    const createCall = fetch.mock.calls.find(([path, init]) => path === '/api/teams/92/videos' && init?.method === 'POST')
    expect(JSON.parse(String(createCall?.[1]?.body))).toEqual({
      kind: 'match',
      game_id: 501,
      files: [{ name: 'GX010042.MP4', size: 25, type: 'video/mp4' }],
    })
    expect(calls).toContain(`GET /api/videos/${VIDEO_ID}/files/${FILE_ID}/parts`)
    const signCall = fetch.mock.calls.find(
      ([path, init]) => path === `/api/videos/${VIDEO_ID}/files/${FILE_ID}/parts` && init?.method === 'POST',
    )
    expect(JSON.parse(String(signCall?.[1]?.body))).toEqual({ parts: [1, 2, 3] })
    expect(calls).toContain(`POST /api/videos/${VIDEO_ID}/files/${FILE_ID}/complete`)

    expect(FakeXhr.puts.map((put) => [put.method, put.size, put.contentType])).toEqual(
      expect.arrayContaining([
        ['PUT', 10, undefined],
        ['PUT', 10, undefined],
        ['PUT', 5, undefined],
      ]),
    )
    expect(FakeXhr.puts).toHaveLength(3)
  })

  it('shows the reasons when the last check refuses the finished upload', async () => {
    FakeXhr.puts = []
    vi.stubGlobal('XMLHttpRequest', FakeXhr)
    vi.stubGlobal(
      'fetch',
      vi.fn(
        uploadApi({
          status: 409,
          body: {
            detail: 'Filming is not allowed for this team on that day',
            blockers: [{ code: 'consent_missing', count: 1, detail: null }],
          },
        }),
      ),
    )
    const { router } = renderRoute(<UploadPage />, { path: '/teams/:id/upload', url: '/teams/92/upload' })
    await pickGameAndFile()
    fireEvent.click(await screen.findByRole('button', { name: 'Start upload' }))

    expect(await screen.findByText('1 player has not agreed to filming yet.')).toBeInTheDocument()
    expect(screen.getByText(/filming is not allowed for this team on that day/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Open the video' })).toHaveAttribute('href', `/videos/${VIDEO_ID}`)
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/teams/92/upload')
  })

  it("offers the files of this team's inbox folder and says where to copy them", async () => {
    const inboxFile = (id: string, path: string) => ({
      id,
      path,
      size: 4e9,
      mtime: '2026-10-11T22:00:00Z',
      recorded_at_guess: null,
      duration_ms: 1_800_000,
      width: 1920,
      height: 1080,
      fps: 50,
    })
    const fetch = vi.fn(
      stubFetch({
        '/api/teams/92': { body: team },
        '/api/video/config': { body: { enabled: true, part_size: 10, max_file_bytes: 1e12, max_files: 20, notice_version: 'x' } },
        '/api/teams/92/games': { body: [game] },
        '/api/teams/92/trainings': { body: [] },
        '/api/inbox': {
          body: [inboxFile('i2', 'vb-H3/GX020042.MP4'), inboxFile('i1', 'vb-H3/GX010042.MP4'), inboxFile('i9', 'vb-D1/other.MP4')],
        },
        [`/api/teams/92/filming?kind=match&on=${gameDate}`]: { body: { allowed: true, blockers: [], adult_checked_at: null } },
        'POST /api/teams/92/videos': { status: 201, body: { ...created, source: 'inbox', status: 'queued' } },
      }),
    )
    vi.stubGlobal('fetch', fetch)
    const { router } = renderRoute(<UploadPage />, {
      path: '/teams/:id/upload',
      url: '/teams/92/upload',
      routes: [{ path: '/videos/:id', element: <p>Video page</p> }],
    })
    fireEvent.change(await screen.findByLabelText('Game'), { target: { value: '501' } })
    fireEvent.click(await screen.findByLabelText('From the NAS inbox'))

    expect(screen.getByText('vb-H3').tagName).toBe('CODE')
    expect(screen.getByText(/Copy the camera files into the folder/)).toBeInTheDocument()
    expect(screen.queryByText('other.MP4')).not.toBeInTheDocument()
    fireEvent.click(screen.getByLabelText('Use GX020042.MP4'))
    fireEvent.click(screen.getByLabelText('Use GX010042.MP4'))
    fireEvent.click(screen.getByRole('button', { name: 'Add video' }))

    await waitFor(() => expect(router.state.location.pathname).toBe(`/videos/${VIDEO_ID}`))
    const createCall = fetch.mock.calls.find(([path, init]) => path === '/api/teams/92/videos' && init?.method === 'POST')
    expect(JSON.parse(String(createCall?.[1]?.body))).toEqual({ kind: 'match', game_id: 501, inbox_file_ids: ['i1', 'i2'] })
  })

  it('shows the folder hint when the inbox folder is empty', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        stubFetch({
          '/api/teams/92': { body: team },
          '/api/video/config': { body: { enabled: true, part_size: 10, max_file_bytes: 1e12, max_files: 20, notice_version: 'x' } },
          '/api/teams/92/games': { body: [game] },
          '/api/teams/92/trainings': { body: [] },
          '/api/inbox': { body: [] },
          [`/api/teams/92/filming?kind=match&on=${gameDate}`]: { body: { allowed: true, blockers: [], adult_checked_at: null } },
        }),
      ),
    )
    renderRoute(<UploadPage />, { path: '/teams/:id/upload', url: '/teams/92/upload' })
    fireEvent.change(await screen.findByLabelText('Game'), { target: { value: '501' } })
    fireEvent.click(await screen.findByLabelText('From the NAS inbox'))
    expect(screen.getByText('vb-H3')).toBeInTheDocument()
    expect(screen.getByText(/No files there yet/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Add video' })).toBeDisabled()
  })

  it('takes only the video file types the API accepts', async () => {
    vi.stubGlobal('fetch', vi.fn(uploadApi()))
    renderRoute(<UploadPage />, { path: '/teams/:id/upload', url: '/teams/92/upload' })
    await pickGameAndFile('clip.webm')
    expect(await screen.findByText(/clip\.webm is not a video file Nomal takes/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start upload' })).toBeDisabled()
    expect(screen.getByLabelText('Choose files')).toHaveAttribute('accept', expect.stringContaining('.m2ts'))
  })

  it('shows the reasons when filming is blocked and offers no upload', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(
        stubFetch({
          '/api/teams/92': { body: team },
          '/api/video/config': { body: { enabled: true, part_size: 10, max_file_bytes: 1e12, max_files: 20, notice_version: 'x' } },
          '/api/teams/92/games': { body: [] },
          '/api/teams/92/trainings': { body: [] },
          '/api/inbox': { body: [] },
          [`/api/teams/92/filming?kind=training&on=${todayIso()}`]: {
            body: { allowed: false, blockers: [{ code: 'no_birthdate', count: 2, detail: null }], adult_checked_at: null },
          },
        }),
      ),
    )
    renderRoute(<UploadPage />, { path: '/teams/:id/upload', url: '/teams/92/upload' })

    fireEvent.click(await screen.findByLabelText('Training'))
    fireEvent.change(await screen.findByLabelText('Training', { selector: 'select' }), { target: { value: 'other' } })
    expect(await screen.findByText('2 people on the roster have no birthdate in Wiedisync.')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start upload' })).not.toBeInTheDocument()
  })
})
