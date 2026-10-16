import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Solution } from '../lib/court'
import { CORNER_POINTS, courtState, savedCalibration } from '../test/court'
import { renderRoute, stubFetch } from '../test/render'
import { CourtSetupDialog } from './CourtSetupDialog'

const ID = '00000000-0000-4000-8000-000000000001'
const solution = (patch: Partial<Solution> = {}): Solution => ({
  image_to_court: savedCalibration().image_to_court,
  court_to_image: savedCalibration().court_to_image,
  camera: null,
  error_cm: null,
  max_error_cm: null,
  residuals_cm: {},
  lines: [
    [
      [0.1, 0.8],
      [0.9, 0.8],
    ],
    [
      [0.3, 0.4],
      [0.7, 0.4],
    ],
  ],
  ...patch,
})

function paused(): HTMLVideoElement {
  const video = document.createElement('video')
  Object.defineProperty(video, 'videoWidth', { value: 1920 })
  Object.defineProperty(video, 'videoHeight', { value: 1080 })
  Object.defineProperty(video, 'readyState', { value: 4 })
  video.pause = vi.fn()
  return video
}

function area(): HTMLElement {
  const svg = screen.getByRole('application')
  svg.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1000, height: 562.5, right: 1000, bottom: 562.5, x: 0, y: 0, toJSON: () => ({}) })
  return svg
}

describe('CourtSetupDialog', () => {
  let drawImage: ReturnType<typeof vi.fn>
  beforeEach(() => {
    drawImage = vi.fn()
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ drawImage } as unknown as CanvasRenderingContext2D)
  })

  it('pre-fills the court finder’s corners, checks the fit with more points and saves', async () => {
    const solves: unknown[] = []
    const fetch = vi.fn(
      stubFetch({
        [`POST /api/videos/${ID}/court/solve`]: {
          body: ({ body }: { body: unknown }) => {
            solves.push(body)
            const n = (body as { points: unknown[] }).points.length
            return n > 4 ? solution({ error_cm: 6.2, max_error_cm: 9.5 }) : solution()
          },
        },
        [`PUT /api/videos/${ID}/court`]: { body: savedCalibration({ method: 'manual' }) },
      }),
    )
    vi.stubGlobal('fetch', fetch)
    const onClose = vi.fn()
    const video = paused()
    renderRoute(
      <CourtSetupDialog
        open
        videoId={ID}
        element={video}
        onClose={onClose}
        state={courtState({ suggestions: [{ source: 'auto', points: CORNER_POINTS, confidence: 0.82, video_id: ID, video_title: null, recorded_on: null, calibration_id: 'x' }] })}
      />,
    )
    const dialog = await screen.findByRole('dialog', { name: 'Set up the court' })
    expect(video.pause).toHaveBeenCalled()
    expect(drawImage).toHaveBeenCalledWith(video, 0, 0, 1920, 1080)
    expect(within(dialog).getByText(/Found automatically/)).toBeInTheDocument()
    expect(within(dialog).getAllByText('placed')).toHaveLength(4)
    // Four corners: the server fits them; four points always fit exactly.
    expect(await within(dialog).findByText(/Four points always fit/)).toBeInTheDocument()
    expect(within(dialog).getAllByTestId('court-line')).toHaveLength(2)

    // Next to place: the centre line's left end; a click on the picture puts it there.
    fireEvent.click(within(dialog).getByRole('button', { name: /Centre line, left end/ }))
    fireEvent.pointerDown(area(), { clientX: 250, clientY: 281.25, pointerId: 1 })
    expect(within(dialog).getAllByText('placed')).toHaveLength(5)
    expect(await within(dialog).findByText(/Fit: ± 6.2 cm \(worst 9.5 cm\) · Good fit/)).toBeInTheDocument()
    const last = solves.at(-1) as { points: { landmark: string; u: number; v: number }[] }
    expect(last.points.find((p) => p.landmark === 'net_left')).toEqual({ landmark: 'net_left', u: 0.25, v: 0.5 })

    // Arrow keys nudge the selected point by a pixel.
    fireEvent.keyDown(area(), { key: 'ArrowRight' })
    await waitFor(() => expect((solves.at(-1) as typeof last).points.find((p) => p.landmark === 'net_left')?.u).toBeCloseTo(0.251))

    fireEvent.click(within(dialog).getByRole('button', { name: 'Save court' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    const put = fetch.mock.calls.find(([, init]) => init?.method === 'PUT')
    const body = JSON.parse(String(put?.[1]?.body)) as { method: string; points: unknown[]; copied_from: string | null }
    expect(body.method).toBe('manual') // moved from the finder's: the coach's own
    expect(body.points).toHaveLength(5)
    expect(await screen.findByText('Court saved')).toBeInTheDocument()
  })

  it('takes over the team’s last calibration as it was, and says where it came from', async () => {
    const fetch = vi.fn(
      stubFetch({
        [`POST /api/videos/${ID}/court/solve`]: { body: solution() },
        [`PUT /api/videos/${ID}/court`]: { body: savedCalibration({ method: 'copied' }) },
      }),
    )
    vi.stubGlobal('fetch', fetch)
    renderRoute(
      <CourtSetupDialog
        open
        videoId={ID}
        element={paused()}
        onClose={() => {}}
        state={courtState({
          suggestions: [{ source: 'team', points: CORNER_POINTS, confidence: null, video_id: 'v0', video_title: 'KSCW H3 – Volero', recorded_on: '2026-10-02', calibration_id: 'cal-1' }],
        })}
      />,
    )
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/From KSCW H3 – Volero \(02.10.2026\)/)).toBeInTheDocument()
    await within(dialog).findByText(/Four points always fit/)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save court' }))
    await waitFor(() => expect(fetch.mock.calls.some(([, init]) => init?.method === 'PUT')).toBe(true))
    const put = fetch.mock.calls.find(([, init]) => init?.method === 'PUT')
    expect(JSON.parse(String(put?.[1]?.body))).toMatchObject({ method: 'copied', copied_from: 'cal-1' })
  })

  it('shows why points do not make a court, and does not save them', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(stubFetch({ [`POST /api/videos/${ID}/court/solve`]: { status: 422, body: { detail: 'the court comes out mirrored: left and right (or near and far) swapped?' } } })),
    )
    renderRoute(<CourtSetupDialog open videoId={ID} element={paused()} onClose={() => {}} state={courtState({ suggestions: [{ source: 'auto', points: CORNER_POINTS, confidence: 0.9, video_id: ID, video_title: null, recorded_on: null, calibration_id: 'x' }] })} />)
    expect(await screen.findByText(/mirrored/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save court' })).toBeDisabled()
  })

  it('asks for a paused frame when the video shows none, and removes a saved court', async () => {
    const fetch = vi.fn(stubFetch({ [`DELETE /api/videos/${ID}/court`]: { status: 204 } }))
    vi.stubGlobal('fetch', fetch)
    const onClose = vi.fn()
    renderRoute(<CourtSetupDialog open videoId={ID} element={null} onClose={onClose} state={courtState({ calibration: savedCalibration() })} />)
    const dialog = await screen.findByRole('dialog')
    expect(within(dialog).getByText(/Pause the video where the whole court is visible/)).toBeInTheDocument()
    // The saved fit shows as it was saved, without asking the server again.
    expect(within(dialog).getByText(/Fit: ± 6.2 cm/)).toBeInTheDocument()
    await act(async () => {
      fireEvent.click(within(dialog).getByRole('button', { name: 'Remove court' }))
    })
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(true)
    expect(fetch.mock.calls.some(([, init]) => init?.method === 'POST')).toBe(false)
  })
})
