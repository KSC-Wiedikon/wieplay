import { screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { TrajectoryState } from '../lib/trajectories'
import type { Video } from '../lib/types'
import { courtState, savedCalibration } from '../test/court'
import { renderRoute, stubFetch } from '../test/render'
import { CourtTools } from './CourtTools'

const ID = '00000000-0000-4000-8000-000000000001'
const video = { id: ID, duration_ms: 600_000 } as Video
const trajectories: TrajectoryState = { available: true, pending: false, on: false, setOn: () => {}, track: null }

function show(body: unknown) {
  vi.stubGlobal('fetch', vi.fn(stubFetch({ [`/api/videos/${ID}/court`]: { body } })))
  renderRoute(<CourtTools video={video} element={null} trajectories={trajectories} />)
}

describe('CourtTools', () => {
  it('asks to check a court the court job set, and shows a coach’s as set up', async () => {
    show(courtState({ calibration: savedCalibration({ method: 'auto-hall', checked: false, confidence: 0.92 }) }))
    expect(await screen.findByRole('button', { name: 'Court set automatically: check' })).toBeInTheDocument()
  })

  it('shows a coach’s court as set up', async () => {
    show(courtState({ calibration: savedCalibration() }))
    expect(await screen.findByRole('button', { name: 'Court set up' })).toBeInTheDocument()
  })

  it('offers to set the court up when there is none', async () => {
    show(courtState())
    expect(await screen.findByRole('button', { name: 'Set up the court' })).toBeInTheDocument()
  })
})
