import { fireEvent, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { foundCourt, type Solution } from '../lib/court'
import { CORNER_POINTS, courtState, savedCalibration } from '../test/court'
import { renderRoute, stubFetch } from '../test/render'
import { CourtCheckDialog } from './CourtCheckDialog'

const ID = '00000000-0000-4000-8000-000000000001'
const proposal = { source: 'auto' as const, points: CORNER_POINTS, confidence: 0.55, sure: false, video_id: ID, video_title: null, recorded_on: null, calibration_id: 'p1' }
const solution: Solution = {
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
  ],
}

describe('CourtCheckDialog', () => {
  beforeEach(() => {
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ drawImage: vi.fn() } as unknown as CanvasRenderingContext2D)
  })

  it('finds the court WiePlay found: its own unchecked court first, else its proposal', () => {
    expect(foundCourt(courtState())).toBeNull()
    expect(foundCourt(courtState({ suggestions: [{ ...proposal, source: 'team' }] }))).toBeNull()
    expect(foundCourt(courtState({ suggestions: [proposal] }))).toMatchObject({ kind: 'proposal', sure: false, method: 'auto' })
    const set = savedCalibration({ method: 'auto-hall', checked: false, sure: true, confidence: 0.9 })
    expect(foundCourt(courtState({ calibration: set }))).toMatchObject({ kind: 'set', sure: true, method: 'auto-hall' })
    expect(foundCourt(courtState({ calibration: savedCalibration() }))).toBeNull() // a coach's
  })

  it('draws the unsure court over the frame and confirms it', async () => {
    const fetch = vi.fn(
      stubFetch({
        [`POST /api/videos/${ID}/court/solve`]: { body: solution },
        [`POST /api/videos/${ID}/court/confirm`]: { body: savedCalibration({ method: 'auto', checked: true }) },
      }),
    )
    vi.stubGlobal('fetch', fetch)
    const onClose = vi.fn()
    const onFix = vi.fn()
    const state = courtState({ suggestions: [proposal] })
    const found = foundCourt(state)
    if (!found) throw new Error('no court')
    renderRoute(<CourtCheckDialog open videoId={ID} state={state} found={found} element={null} onClose={onClose} onFix={onFix} />)
    expect(screen.getByText('Not sure')).toBeInTheDocument()
    expect(screen.getByText(/isn’t sure of it/)).toBeInTheDocument()
    await waitFor(() => expect(screen.getAllByTestId('court-line')).toHaveLength(1))
    fireEvent.click(screen.getByRole('button', { name: 'Fix' }))
    expect(onFix).toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm' }))
    await waitFor(() => expect(onClose).toHaveBeenCalled())
    expect(fetch.mock.calls.some(([url, init]) => String(url).endsWith('/court/confirm') && (init as RequestInit).method === 'POST')).toBe(true)
  })
})
