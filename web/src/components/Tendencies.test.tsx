import { fireEvent, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { BallTrackPayload } from '../lib/ballTrack'
import type { Video } from '../lib/types'
import { courtState, FLOOR_2_12, savedCalibration } from '../test/court'
import { renderRoute, stubFetch } from '../test/render'
import { bundle, period, tag } from '../test/tagging'
import { Tendencies } from './Tendencies'

const ID = '00000000-0000-4000-8000-000000000001'
const video = { id: ID, duration_ms: 600_000 } as Video

const serves = [
  tag({ id: 's1', code: 'S', number: 7, member_id: 107, t_ms: 10_000, attributes: { grade: '#', zone_from: 1, zone_to: 5 } }),
  tag({ id: 's2', code: 'S', number: 7, member_id: 107, t_ms: 40_000, attributes: { grade: '=', zone_to: 6 } }),
  tag({ id: 's3', code: 'S', number: 3, member_id: 103, t_ms: 70_000, attributes: { grade: '+', zone_to: 1 } }),
  tag({ id: 's4', code: 'S', side: 'them', number: 12, member_id: null, t_ms: 90_000, attributes: { grade: '-', zone_to: 6 } }),
  tag({ id: 'a1', code: 'A', number: 7, member_id: 107, t_ms: 20_000, attributes: { grade: '#', zone_from: 4, zone_to: 1 } }),
]

function show(courtBody = courtState({ ball_track: { ...courtState().ball_track, available: false } }), extra: Record<string, { status?: number; body?: unknown }> = {}) {
  const fetch = vi.fn(stubFetch({ [`/api/videos/${ID}/court`]: { body: courtBody }, ...extra }))
  vi.stubGlobal('fetch', fetch)
  const onPlay = vi.fn()
  const sets = [period({ id: 'p1', no: 1, start_ms: 0, end_ms: 50_000 }), period({ id: 'p2', no: 2, start_ms: 50_000 })]
  renderRoute(<Tendencies video={video} tagging={bundle({ events: serves, periods: sets })} onPlay={onPlay} />)
  return { fetch, onPlay }
}

const arrows = () => within(screen.getByTestId('court-diagram')).queryAllByRole('button')

describe('Tendencies', () => {
  it('draws our serves from their zones, coloured by what came of them', async () => {
    show()
    expect(await screen.findByRole('heading', { name: 'Tendencies' })).toBeInTheDocument()
    expect(arrows()).toHaveLength(3)
    expect(arrows().map((a) => a.getAttribute('data-outcome')).sort()).toEqual(['error', 'play', 'win'])
    expect(arrows().every((a) => a.getAttribute('data-evidence') === 'zones')).toBe(true)
    expect(screen.getByRole('status')).toHaveTextContent('3 shown: 1 points, 1 errors, 1 in play')
    expect(screen.getByLabelText(/#7 Luca Brunner, Serve Ace, Point \(ace, kill\), 0:10/)).toBeInTheDocument()
    expect(screen.getByText(/Set up the court on the video/)).toBeInTheDocument()
  })

  it('filters by player, side, set and skill like the rest of the page', async () => {
    show()
    await screen.findByTestId('court-diagram')
    fireEvent.change(screen.getByLabelText('Player'), { target: { value: 'us:7' } })
    expect(arrows()).toHaveLength(2)
    fireEvent.change(screen.getByLabelText('Set'), { target: { value: 'p2' } })
    expect(arrows()).toHaveLength(0)
    expect(screen.getByRole('status')).toHaveTextContent(/Nothing to draw yet/)
    fireEvent.change(screen.getByLabelText('Set'), { target: { value: '' } })
    fireEvent.change(screen.getByLabelText('Player'), { target: { value: 'them' } })
    expect(arrows()).toHaveLength(1)
    fireEvent.change(screen.getByLabelText('Player'), { target: { value: 'us' } })
    fireEvent.click(screen.getByRole('button', { name: 'Attacks' }))
    expect(arrows()).toHaveLength(1)
    expect(arrows()[0]).toHaveAttribute('data-outcome', 'win')
  })

  it('plays an arrow’s moment when it is selected, by click or keyboard', async () => {
    const { onPlay } = show()
    await screen.findByTestId('court-diagram')
    const ace = screen.getByLabelText(/Serve Ace/)
    fireEvent.click(ace)
    expect(onPlay).toHaveBeenCalledWith([expect.objectContaining({ key: 's1', start_ms: 8_000, end_ms: 13_000 })])
    expect(ace).toHaveAttribute('aria-pressed', 'true')
    fireEvent.keyDown(screen.getByLabelText(/Serve Error/), { key: 'Enter' })
    expect(onPlay).toHaveBeenLastCalledWith([expect.objectContaining({ key: 's2' })])
  })

  it('places serves where the ball came down once the court is set up', async () => {
    const track: BallTrackPayload = {
      schema: 1,
      fps: 15,
      scale: 1000,
      frames: 9000,
      duration_s: 600,
      tracks: [],
      // A bounce 1.2 s after the first serve, on the far half at (2, 12).
      contacts: [[11_200, Math.round(FLOOR_2_12[0] * 1000), Math.round(FLOOR_2_12[1] * 1000), 0]],
    }
    show(courtState({ calibration: savedCalibration() }), { [`/api/videos/${ID}/ball-track`]: { body: track } })
    await screen.findByTestId('court-diagram')
    const ace = await screen.findByLabelText(/Serve Ace/)
    await vi.waitFor(() => expect(ace).toHaveAttribute('data-evidence', 'ball'))
    expect(screen.queryByText(/Set up the court on the video/)).toBeNull()
    expect(screen.getByText('From the ball (1)')).toBeInTheDocument()
  })

  it('stays away when there is nothing to draw', () => {
    vi.stubGlobal('fetch', vi.fn(stubFetch({ [`/api/videos/${ID}/court`]: { body: courtState() } })))
    renderRoute(<Tendencies video={video} tagging={bundle({ events: [tag({ code: 'S', t_ms: 1000, attributes: { grade: '+' } })] })} onPlay={() => {}} />)
    expect(screen.queryByRole('heading', { name: 'Tendencies' })).toBeNull()
  })
})
