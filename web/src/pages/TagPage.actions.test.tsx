import 'fake-indexeddb/auto'

import { IDBFactory } from 'fake-indexeddb'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Analysis, ChangesIn, ChangesOut, Tag, Tagging, Video } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { bundle, period, rally, tag } from '../test/tagging'
import { TagPage } from './TagPage'

const ID = '00000000-0000-4000-8000-0000000000c7'
const R1 = '00000000-0000-4000-8000-0000000007a1'
const S1 = '00000000-0000-4000-8000-0000000007e1'
const R = '00000000-0000-4000-8000-0000000007e2'
const E = '00000000-0000-4000-8000-0000000007e3'
const A = '00000000-0000-4000-8000-0000000007e4'

const video: Video = {
  id: ID,
  team_id: 92,
  team_name: 'H3',
  team_season: '2026/27',
  sport: 'volleyball',
  kind: 'match',
  game_id: 502,
  title: 'VBC Züri Unterland – KSCW H3',
  recorded_on: '2026-10-04',
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
  renditions: [{ name: '1080p', width: 1920, height: 1080, fps: 50 }],
  has_thumbs: false,
  progress: null,
  error: null,
  created_at: '2026-10-04T22:00:00Z',
  created_by: 7,
  can_delete: true,
  can_upload: true,
  can_tag: true,
  can_share: true,
  can_export: true,
  files: [],
  rallies_reviewed_at: '2026-10-16T20:00:00Z',
}

const set1 = period({ no: 1, start_ms: 0, first_serve: 'us' })
const action = (id: string, t_ms: number, code: string, patch: Partial<Tag> = {}) =>
  tag({ id, t_ms, code, number: null, member_id: null, segment_id: R1, source: 'model', confidence: 0.8, model_run_id: 'run-a', ...patch })

const data = (): Tagging =>
  bundle({
    periods: [set1],
    segments: [rally({ id: R1, start_ms: 10_000, end_ms: 18_000, period_id: set1.id, won_by: 'us', score_us_after: 1, score_them_after: 0 })],
    events: [
      action(S1, 11_000, 'S'),
      action(R, 12_200, 'R', { side: 'them' }),
      action(E, 13_200, 'E', { side: 'them' }),
      action(A, 14_000, 'A', { side: 'them', attributes: { grade: '=' } }),
      tag({ t_ms: 16_000, code: 'D', number: 7, segment_id: R1 }),
    ],
  })

const analysis: Analysis = {
  status: 'done',
  model_run: null,
  proposals: { unreviewed: 0, confirmed: 0, corrected: 0, rejected: 0 },
  metrics_vs_human: null,
  actions: {
    status: 'done',
    run_id: 'run-a',
    created_at: '2026-10-17T08:00:00Z',
    finder: 'v0',
    suggestions: { unreviewed: 4, confirmed: 0, corrected: 0, rejected: 0 },
    counts: { S: 1, R: 1, E: 1, A: 1, B: 0, D: 0, F: 0 },
    proposed: 4,
    rallies: 1,
    coach_rallies: 0,
    vs_coach: null,
    reviewed_at: null,
    model: { finder: 'v0', run_id: null, trained_at: null, games: 0, summary: {} },
  },
}

/** /changes for events like the API: model rows keep their source and confidence. */
function fakeServer(initial: Tagging) {
  const events = new Map(initial.events.map((e) => [e.id, e]))
  const posts: ChangesIn[] = []
  const changes = ({ body }: { body: unknown }): ChangesOut => {
    const input = body as ChangesIn
    posts.push(input)
    const out: Tag[] = []
    for (const c of input.events) {
      const prev = events.get(c.id)
      const row: Tag = {
        ...(prev ?? tag({ id: c.id, t_ms: c.t_ms })),
        code: c.code,
        t_ms: c.t_ms,
        side: c.side,
        number: c.number ?? null,
        attributes: c.attributes,
        review: c.review ?? 'unreviewed',
        version: (prev?.version ?? 0) + 1,
        deleted_at: c.deleted ? '2026-10-17T09:00:00Z' : null,
      }
      events.set(c.id, row)
      out.push(row)
    }
    return { events: out, segments: initial.segments, periods: initial.periods, stale: [] }
  }
  const current = (): Tagging => ({ ...initial, events: [...events.values()].filter((e) => !e.deleted_at) })
  const sent = () => new Map(posts.flatMap((p) => p.events).map((e) => [e.id, e]))
  return { posts, changes, current, sent }
}

function show(url = `/videos/${ID}/tag`) {
  const server = fakeServer(data())
  const reviewed = vi.fn(() => ({ actions_reviewed_at: '2026-10-17T10:00:00Z', training_job_id: 'job-t' }))
  const fetch = vi.fn(
    stubFetch({
      [`/api/videos/${ID}`]: { body: video },
      [`/api/videos/${ID}/tagging`]: { body: () => server.current() },
      [`POST /api/videos/${ID}/changes`]: { body: server.changes },
      [`/api/videos/${ID}/analysis`]: { body: analysis },
      [`POST /api/videos/${ID}/actions-reviewed`]: { body: reviewed },
      [`/api/videos/${ID}/comments`]: { body: [] },
      '/api/reels': { body: [] },
    }),
  )
  vi.stubGlobal('fetch', fetch)
  const view = renderRoute(<TagPage />, { path: '/videos/:id/tag', url, routes: [{ path: '/videos/:id', element: <p>Video page</p> }] })
  return { ...view, fetch, server, reviewed }
}

function keys(...list: (string | { key: string; shiftKey?: boolean })[]) {
  for (const k of list) fireEvent.keyDown(document.body, typeof k === 'string' ? { key: k } : k)
}

const panel = () => screen.getByRole('region', { name: 'Review actions' })

beforeEach(() => {
  vi.stubGlobal('indexedDB', new IDBFactory())
  window.localStorage.clear()
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve())
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
})

describe('TagPage, suggested actions', () => {
  it('shows suggested actions apart in the tag list and on the timeline', async () => {
    const { container, server } = show()
    await screen.findByRole('region', { name: 'Tag entry' })
    const table = screen.getByRole('table', { name: 'Tags' })
    const open = within(table)
      .getAllByRole('row')
      .filter((r) => r.getAttribute('data-action-suggestion') === 'open')
    expect(open).toHaveLength(4)
    expect(within(table).getAllByText('Suggested · 80%')).toHaveLength(4)
    expect(screen.getByText(/· 4 suggested/)).toBeInTheDocument()
    expect(container.querySelectorAll('[data-action-suggestion="open"]').length).toBeGreaterThan(4) // + timeline
    // Accepted right in the list.
    fireEvent.click(within(table).getByRole('button', { name: 'Accept S' }))
    await waitFor(() => expect(server.sent().get(S1)).toMatchObject({ review: 'confirmed' }), { timeout: 3000 })
  })

  it('reviews the actions with the keys and marks them reviewed', async () => {
    const { server, router, reviewed } = show()
    await screen.findByRole('region', { name: 'Tag entry' })
    fireEvent.click(screen.getByRole('button', { name: 'Review suggestions (4)' }))
    fireEvent.click(screen.getByRole('button', { name: 'Actions (4)' }))
    expect(router.state.location.search).toBe('?mode=review&queue=actions')
    expect(within(panel()).getByRole('heading', { name: /Serve at 0:11\.0/ })).toBeInTheDocument()
    expect(within(panel()).getByText('0 of 4 suggested actions reviewed')).toBeInTheDocument()
    const player = document.querySelector('video') as HTMLVideoElement
    expect(player.currentTime).toBe(10)

    keys('7')
    expect(within(panel()).getByText('Player 7: Enter names them and accepts')).toBeInTheDocument()
    keys('Enter')
    expect(within(panel()).getByText('Accepted: Serve at 0:11.0, player 7.')).toBeInTheDocument()
    expect(within(panel()).getByRole('heading', { name: /Reception at 0:12\.2/ })).toBeInTheDocument()
    keys('#', 'Enter')
    keys('x')
    expect(within(panel()).getByRole('heading', { name: /Attack at 0:14\.0/ })).toBeInTheDocument()
    keys('d', 'Tab')
    expect(within(panel()).getByRole('button', { name: 'D · Dig' })).toHaveAttribute('aria-pressed', 'true')
    keys('Enter')
    expect(within(panel()).getByText('4 of 4 suggested actions reviewed')).toBeInTheDocument()
    expect(within(panel()).getByText('Every suggested action is reviewed')).toBeInTheDocument()

    await waitFor(() => expect(server.sent().get(A)).toBeDefined(), { timeout: 3000 })
    expect(server.sent().get(S1)).toMatchObject({ number: 7, review: 'confirmed' })
    expect(server.sent().get(R)).toMatchObject({ attributes: { grade: '#' }, review: 'corrected' })
    expect(server.sent().get(E)).toMatchObject({ review: 'rejected', deleted: true })
    expect(server.sent().get(A)).toMatchObject({ code: 'D', side: 'us', review: 'corrected' })

    fireEvent.click(within(panel()).getByRole('button', { name: 'Mark actions reviewed' }))
    await waitFor(() => expect(reviewed).toHaveBeenCalled())
    expect(await within(panel()).findByText('The actions are marked reviewed. Nomal learns from this game.')).toBeInTheDocument()
  })

  it('opens the actions queue from the address and leaves the review with Esc', async () => {
    const { router } = show(`/videos/${ID}/tag?mode=review&queue=actions`)
    await screen.findByRole('region', { name: 'Review actions' })
    keys('ArrowDown')
    expect(within(panel()).getByRole('heading', { name: /Reception at 0:12\.2/ })).toBeInTheDocument()
    keys('?')
    const dialog = screen.getByRole('dialog', { name: 'Keys' })
    expect(within(dialog).getByText('The player: type the number, then Enter')).toBeInTheDocument()
    keys('Escape') // the cheatsheet
    keys('Escape') // the review
    await waitFor(() => expect(router.state.location.search).toBe(''))
    expect(screen.getByRole('region', { name: 'Tag entry' })).toBeInTheDocument()
  })

  it('accepts the rest of a rally at once', async () => {
    const { server } = show(`/videos/${ID}/tag?mode=review&queue=actions`)
    await screen.findByRole('region', { name: 'Review actions' })
    fireEvent.click(within(panel()).getByRole('button', { name: /Accept the rest of this rally \(4\)/ }))
    expect(within(panel()).getByText('Accepted 4 actions of this rally.')).toBeInTheDocument()
    await waitFor(() => expect(server.sent().size).toBe(4), { timeout: 3000 })
    expect([...server.sent().values()].map((e) => e.review)).toEqual(['confirmed', 'confirmed', 'confirmed', 'confirmed'])
  })
})
