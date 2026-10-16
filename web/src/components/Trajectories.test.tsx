import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { decodeTrack } from '../lib/ballTrack'
import type { Video } from '../lib/types'
import { courtState } from '../test/court'
import { renderRoute, stubFetch } from '../test/render'
import { TrajectoryOverlay } from './Trajectories'
import { VideoPlayer } from './VideoPlayer'

const ID = '00000000-0000-4000-8000-000000000001'
// The ball at (0.5, 0.5) for samples 0-14 (1 s at 15 fps), then gone.
const track = decodeTrack({
  schema: 1,
  fps: 15,
  scale: 1000,
  frames: 30,
  duration_s: 2,
  tracks: [[0, 400, 500, ...Array.from({ length: 14 }, () => [10, 0]).flat()]],
  contacts: [],
})

const box = (left: number, top: number, width: number, height: number) => () =>
  ({ left, top, width, height, right: left + width, bottom: top + height, x: left, y: top, toJSON: () => ({}) }) as DOMRect

function fakeContext() {
  const calls: { arcs: [number, number][]; lines: [number, number][] } = { arcs: [], lines: [] }
  const ctx = {
    setTransform: vi.fn(),
    clearRect: vi.fn(() => {
      calls.arcs = []
      calls.lines = []
    }),
    beginPath: vi.fn(),
    moveTo: vi.fn(),
    lineTo: vi.fn((x: number, y: number) => calls.lines.push([x, y])),
    stroke: vi.fn(),
    fill: vi.fn(),
    arc: vi.fn((x: number, y: number) => calls.arcs.push([x, y])),
  }
  return { ctx, calls }
}

describe('TrajectoryOverlay', () => {
  let frames: FrameRequestCallback[]
  beforeEach(() => {
    frames = []
    vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => frames.push(cb))
    vi.stubGlobal('cancelAnimationFrame', vi.fn())
  })
  const tick = () => act(() => frames.splice(0).forEach((cb) => cb(0)))

  it('follows the video’s clock and finds the picture inside the letterbox', () => {
    const { ctx, calls } = fakeContext()
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx as unknown as CanvasRenderingContext2D)
    const video = document.createElement('video')
    Object.defineProperty(video, 'videoWidth', { value: 1920 })
    Object.defineProperty(video, 'videoHeight', { value: 1080 })
    // A square box: the 16:9 picture sits between bars above and below.
    video.getBoundingClientRect = box(0, 0, 1000, 1000)
    video.currentTime = 0.5
    render(<TrajectoryOverlay video={video} track={track} />)
    const canvas = screen.getByTestId('trajectory-overlay') as HTMLCanvasElement
    canvas.getBoundingClientRect = box(0, 0, 1000, 1000)
    tick()
    // Sample 7.5: x = 0.475 → 475 px; y = 0.5 of a 562.5 px picture starting at 218.75.
    const head = calls.arcs.at(-1)
    expect(head?.[0]).toBeCloseTo(475)
    expect(head?.[1]).toBeCloseTo(218.75 + 281.25)
    expect(calls.lines.length).toBeGreaterThan(5)

    // The video moves on: the next frame draws the ball further along.
    video.currentTime = 0.8
    tick()
    expect(calls.arcs.at(-1)?.[0]).toBeCloseTo(520)
    // After the track: no ball, only the fading tail; a moment later nothing.
    video.currentTime = 1.9
    tick()
    expect(calls.arcs).toHaveLength(0)
    expect(calls.lines.length).toBeGreaterThan(0)
    video.currentTime = 2.5
    tick()
    expect(calls.lines).toHaveLength(0)
  })

  it('redraws for a new size (resize, full screen) even while paused', () => {
    const { ctx, calls } = fakeContext()
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx as unknown as CanvasRenderingContext2D)
    const video = document.createElement('video')
    Object.defineProperty(video, 'videoWidth', { value: 1920 })
    Object.defineProperty(video, 'videoHeight', { value: 1080 })
    video.getBoundingClientRect = box(0, 0, 640, 360)
    video.currentTime = 0.5
    render(<TrajectoryOverlay video={video} track={track} />)
    const canvas = screen.getByTestId('trajectory-overlay') as HTMLCanvasElement
    canvas.getBoundingClientRect = box(0, 0, 640, 360)
    tick()
    expect(calls.arcs.at(-1)?.[0]).toBeCloseTo(0.475 * 640)
    tick() // same time, same size: nothing new to draw
    expect(ctx.clearRect).toHaveBeenCalledTimes(1)
    video.getBoundingClientRect = box(0, 0, 1280, 720)
    canvas.getBoundingClientRect = box(0, 0, 1280, 720)
    tick()
    expect(calls.arcs.at(-1)?.[0]).toBeCloseTo(0.475 * 1280)
    expect(canvas.width).toBe(1280)
  })
})

describe('the player with the overlay', () => {
  afterEach(() => {
    Object.defineProperty(document, 'fullscreenEnabled', { value: undefined, configurable: true })
  })

  it('takes its own box to full screen, overlay included', async () => {
    Object.defineProperty(document, 'fullscreenEnabled', { value: true, configurable: true })
    const request = vi.fn().mockResolvedValue(undefined)
    HTMLElement.prototype.requestFullscreen = request
    vi.stubGlobal('fetch', vi.fn(stubFetch({})))
    const video = { id: ID, title: 'Match', renditions: [], fps: 50, has_thumbs: false } as unknown as Video
    renderRoute(<VideoPlayer video={video} overlay={<div data-testid="over" />} />)
    expect(await screen.findByTestId('over')).toBeInTheDocument()
    expect(document.querySelector('video')).toHaveAttribute('controlsList', 'nofullscreen')
    fireEvent.click(screen.getByRole('button', { name: 'Full screen' }))
    expect(request).toHaveBeenCalled()
  })

  it('keeps the browser’s own full screen without an overlay', async () => {
    Object.defineProperty(document, 'fullscreenEnabled', { value: true, configurable: true })
    vi.stubGlobal('fetch', vi.fn(stubFetch({})))
    const video = { id: ID, title: 'Match', renditions: [], fps: 50, has_thumbs: false } as unknown as Video
    renderRoute(<VideoPlayer video={video} />)
    await waitFor(() => expect(document.querySelector('video')).not.toBeNull())
    expect(screen.queryByRole('button', { name: 'Full screen' })).toBeNull()
    expect(document.querySelector('video')).not.toHaveAttribute('controlsList')
  })
})

describe('the Trajectories switch on the video page', () => {
  it('is there when the video has a ball track, and draws the overlay while on', async () => {
    const { VideoPage } = await import('../pages/VideoPage')
    vi.stubGlobal('matchMedia', vi.fn().mockReturnValue({ matches: false, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(null)
    const ready = {
      id: ID, team_id: 92, team_name: 'H3', team_season: '2026/27', sport: 'volleyball', kind: 'match', game_id: 501,
      title: 'KSCW H3 – Volero', recorded_on: '2026-10-02', source: 'file', imported_from: null, status: 'ready',
      duration_ms: 7_900_000, width: 1920, height: 1080, fps: 60, has_audio: true, meets_standard: true, standard_issues: [],
      renditions: [{ name: '1080p', width: 1920, height: 1080, fps: 50 }], has_thumbs: false, progress: null, error: null,
      created_at: '2026-10-03T10:00:00Z', created_by: 7, can_delete: false, can_upload: false, can_tag: false,
      can_share: false, can_export: false, files: [],
    } // fmt: skip
    vi.stubGlobal(
      'fetch',
      vi.fn(
        stubFetch({
          [`/api/videos/${ID}`]: { body: ready },
          '/api/video/config': { body: { enabled: true, part_size: 10, max_file_bytes: 1e12, max_files: 20 } },
          [`/api/videos/${ID}/court`]: { body: courtState({ can_edit: false }) },
          [`/api/videos/${ID}/ball-track`]: { body: { schema: 1, fps: 15, scale: 1000, frames: 10, duration_s: 1, tracks: [], contacts: [] } },
        }),
      ),
    )
    renderRoute(<VideoPage />, { path: '/videos/:id', url: `/videos/${ID}` })
    const toggle = await screen.findByRole('switch', { name: 'Trajectories' })
    expect(toggle).toHaveAttribute('aria-checked', 'true')
    expect(await screen.findByTestId('trajectory-overlay')).toBeInTheDocument()
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-checked', 'false')
    expect(screen.queryByTestId('trajectory-overlay')).toBeNull()
    // A viewer who may not tag sees no court set-up.
    expect(screen.queryByRole('button', { name: 'Set up the court' })).toBeNull()
  })
})
