import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ScoreboardState } from '../lib/scoreboard'
import type { Video } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { ScoreboardTools } from './ScoreboardDialog'

const ID = '00000000-0000-4000-8000-000000000001'
const video = { id: ID, duration_ms: 600_000, status: 'ready', sport: 'volleyball' } as Video
const BOX = { x: 0, y: 0.84, w: 0.03, h: 0.05 }

function state(over: Partial<ScoreboardState> = {}): ScoreboardState {
  return { video_id: ID, can_edit: true, aspect: 16 / 9, region: null, suggestion: null, job: null, reading: null, ...over }
}

const region = { id: 'r1', rect: BOX, sets: null, kind: 'flip' as const, hall_name: 'Halle Test', at_ms: null, copied_from: null, updated_at: '2026-10-17T10:00:00Z' }

afterEach(() => vi.unstubAllGlobals())

describe('ScoreboardTools', () => {
  it('offers to mark the scoreboard, and nothing to someone who may not tag', async () => {
    vi.stubGlobal('fetch', vi.fn(stubFetch({ [`/api/videos/${ID}/scoreboard`]: { body: state() } })))
    renderRoute(<ScoreboardTools video={video} element={null} />)
    expect(await screen.findByRole('button', { name: 'Mark the scoreboard' })).toBeInTheDocument()
  })

  it('says how the reading went', async () => {
    const reading = {
      at: '2026-10-17T10:00:00Z',
      summary: {},
      segments: [
        { first_ms: 0, last_ms: 4000, left: 0, right: 0, set_no: 1, confidence: 0.9, kind: 'start' as const },
        { first_ms: 9000, last_ms: 14000, left: 1, right: 0, set_no: 1, confidence: 0.9, kind: 'step' as const },
      ],
    }
    vi.stubGlobal('fetch', vi.fn(stubFetch({ [`/api/videos/${ID}/scoreboard`]: { body: state({ region, job: { status: 'done', progress: null, error: null }, reading }) } })))
    renderRoute(<ScoreboardTools video={video} element={null} />)
    expect(await screen.findByText('Scoreboard read: 2 scores')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Scoreboard marked' })).toBeInTheDocument()
  })

  it('shows the reading’s progress and lets a failed one be read again', async () => {
    vi.stubGlobal('fetch', vi.fn(stubFetch({ [`/api/videos/${ID}/scoreboard`]: { body: state({ region, job: { status: 'running', progress: 0.42, error: null } }) } })))
    renderRoute(<ScoreboardTools video={video} element={null} />)
    expect(await screen.findByText('Reading the scoreboard… 42 %')).toBeInTheDocument()
  })

  it('draws a box, takes the hall’s last one, saves', async () => {
    const saved: unknown[] = []
    const suggestion = { source: 'hall' as const, region: { ...region, id: 'old' }, video_title: 'H3 – Testclub A', recorded_on: '2026-10-01', hall_name: 'Halle Test' }
    vi.stubGlobal(
      'fetch',
      vi.fn(
        stubFetch({
          [`/api/videos/${ID}/scoreboard`]: { body: () => (saved.length ? state({ region }) : state({ suggestion })) },
          [`PUT /api/videos/${ID}/scoreboard`]: {
            body: ({ body }: { body: unknown }) => {
              saved.push(body)
              return state({ region })
            },
          },
        }),
      ),
    )
    renderRoute(<ScoreboardTools video={video} element={null} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Mark the scoreboard' }))
    const dialog = await screen.findByRole('dialog', { name: 'Mark the scoreboard' })
    expect(within(dialog).getByText(/this hall’s last video: H3 – Testclub A/)).toBeInTheDocument()
    expect(within(dialog).getByTestId('box-score')).toBeInTheDocument()
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save and read' }))
    await waitFor(() => expect(saved).toHaveLength(1))
    expect(saved[0]).toMatchObject({ rect: BOX, kind: 'flip', copied_from: 'old' })

    // Drawing a box of its own: no longer the copy.
    fireEvent.click(await screen.findByRole('button', { name: 'Scoreboard marked' }))
    const again = await screen.findByRole('dialog', { name: 'Mark the scoreboard' })
    const area = within(again).getByRole('application')
    area.getBoundingClientRect = () => ({ left: 0, top: 0, width: 1000, height: 562, right: 1000, bottom: 562, x: 0, y: 0, toJSON: () => ({}) })
    fireEvent.pointerDown(area, { clientX: 100, clientY: 400, pointerId: 1 })
    fireEvent.pointerMove(area, { clientX: 200, clientY: 450, pointerId: 1 })
    fireEvent.pointerUp(area, { pointerId: 1 })
    fireEvent.click(within(again).getByRole('button', { name: 'Lit digits' }))
    fireEvent.click(within(again).getByRole('button', { name: 'Save and read' }))
    await waitFor(() => expect(saved).toHaveLength(2))
    const second = saved[1] as { rect: { x: number; w: number }; kind: string; copied_from: string | null }
    expect(second.kind).toBe('led')
    expect(second.copied_from).toBeNull()
    expect(second.rect.x).toBeCloseTo(0.1)
    expect(second.rect.w).toBeCloseTo(0.1)
  })
})
