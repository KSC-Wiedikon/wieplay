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
  tag({ id, t_ms, code, number: null, member_id: null, segment_id: R1, source: 'model', review: 'check', confidence: 0.8, model_run_id: 'run-a', ...patch })

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
  proposals: { auto: 0, check: 0, confirmed: 0, corrected: 0, rejected: 0 },
  metrics_vs_human: null,
  actions: {
    status: 'done',
    run_id: 'run-a',
    created_at: '2026-10-17T08:00:00Z',
    finder: 'v0',
    suggestions: { auto: 0, check: 4, confirmed: 0, corrected: 0, rejected: 0 },
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

function show(url = `/videos/${ID}/tag`, initial = data(), state: Analysis = analysis) {
  const server = fakeServer(initial)
  const reviewed = vi.fn(() => ({ actions_reviewed_at: '2026-10-17T10:00:00Z', training_job_id: 'job-t' }))
  const fetch = vi.fn(
    stubFetch({
      [`/api/videos/${ID}`]: { body: video },
      [`/api/videos/${ID}/tagging`]: { body: () => server.current() },
      [`POST /api/videos/${ID}/changes`]: { body: server.changes },
      [`/api/videos/${ID}/analysis`]: { body: state },
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

const panel = () => screen.getByRole('region', { name: 'Actions to check' })

beforeEach(() => {
  vi.stubGlobal('indexedDB', new IDBFactory())
  window.localStorage.clear()
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve())
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
})

describe("TagPage, WiePlay's actions to check", () => {
  it('marks the actions to check in the tag list and on the timeline', async () => {
    const { container, server } = show()
    await screen.findByRole('region', { name: 'Tag entry' })
    const table = screen.getByRole('table', { name: 'Tags' })
    const open = within(table)
      .getAllByRole('row')
      .filter((r) => r.getAttribute('data-action-suggestion') === 'check')
    expect(open).toHaveLength(4)
    expect(within(table).getAllByText('To check · 80%')).toHaveLength(4)
    expect(screen.getByText(/· 4 to check/)).toBeInTheDocument()
    expect(container.querySelectorAll('[data-action-suggestion="check"]').length).toBeGreaterThan(4) // + timeline
    // Accepted right in the list.
    fireEvent.click(within(table).getByRole('button', { name: 'Accept S' }))
    await waitFor(() => expect(server.sent().get(S1)).toMatchObject({ review: 'confirmed' }), { timeout: 3000 })
  })

  it('goes on into the actions when no rally is left to check, and checks them with the keys', async () => {
    const { server, router, reviewed } = show()
    await screen.findByRole('region', { name: 'Tag entry' })
    // No rally to check: "To check" opens on the actions at once.
    fireEvent.click(screen.getByRole('button', { name: 'To check (4)' }))
    await waitFor(() => expect(router.state.location.search).toBe('?mode=review&queue=actions'))
    expect(within(panel()).getByRole('heading', { name: /Serve at 0:11\.0/ })).toBeInTheDocument()
    expect(within(panel()).getByText('0 of 4 actions checked')).toBeInTheDocument()
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
    expect(within(panel()).getByText('4 of 4 actions checked')).toBeInTheDocument()
    expect(within(panel()).getByText('Every action is checked')).toBeInTheDocument()

    await waitFor(() => expect(server.sent().get(A)).toBeDefined(), { timeout: 3000 })
    expect(server.sent().get(S1)).toMatchObject({ number: 7, review: 'confirmed' })
    expect(server.sent().get(R)).toMatchObject({ attributes: { grade: '#' }, review: 'corrected' })
    expect(server.sent().get(E)).toMatchObject({ review: 'rejected', deleted: true })
    expect(server.sent().get(A)).toMatchObject({ code: 'D', side: 'us', review: 'corrected' })

    fireEvent.click(within(panel()).getByRole('button', { name: 'Mark actions reviewed' }))
    await waitFor(() => expect(reviewed).toHaveBeenCalled())
    expect(await within(panel()).findByText('The actions are marked reviewed. WiePlay learns from this game.')).toBeInTheDocument()
  })

  it('opens the actions queue from the address and leaves the review with Esc', async () => {
    const { router } = show(`/videos/${ID}/tag?mode=review&queue=actions`)
    await screen.findByRole('region', { name: 'Actions to check' })
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
    await screen.findByRole('region', { name: 'Actions to check' })
    fireEvent.click(within(panel()).getByRole('button', { name: /Accept the rest of this rally \(4\)/ }))
    expect(within(panel()).getByText('Accepted 4 actions of this rally.')).toBeInTheDocument()
    await waitFor(() => expect(server.sent().size).toBe(4), { timeout: 3000 })
    expect([...server.sent().values()].map((e) => e.review)).toEqual(['confirmed', 'confirmed', 'confirmed', 'confirmed'])
  })
})

describe('TagPage, sure actions and the ones to check', () => {
  // The serve and the set are sure; the reception and the attack are to check.
  const mixed = (): Tagging => {
    const base = data()
    return {
      ...base,
      events: base.events.map((e) =>
        e.id === S1 || e.id === E ? { ...e, review: 'auto' as const, model_sure: true } : e.id === R ? { ...e, model_sure: false, model_alternative: 'D:them' } : e,
      ),
    }
  }

  it("walks only those to check, and takes WiePlay's other answer with its button", async () => {
    const { server } = show(`/videos/${ID}/tag?mode=review&queue=actions`, mixed())
    const region = await screen.findByRole('region', { name: 'Actions to check' })
    expect(await within(region).findByText('2 actions still to check')).toBeInTheDocument()
    expect(within(region).getByRole('heading', { name: /Reception at/ })).toBeInTheDocument()
    expect(within(region).getByText('WiePlay is 80% sure of this action.')).toBeInTheDocument()
    // No button accepts many at once: the sure ones count already.
    expect(within(region).queryByRole('button', { name: /Accept all/ })).not.toBeInTheDocument()
    fireEvent.click(within(region).getByRole('button', { name: 'Or: Dig, Them' }))
    expect(within(region).getByRole('heading', { name: /Attack at/ })).toBeInTheDocument()
    await waitFor(() => expect(server.sent().get(R)).toBeDefined(), { timeout: 3000 })
    expect(server.sent().get(R)).toMatchObject({ code: 'D', side: 'them', review: 'corrected' })
    expect(server.sent().get(S1)).toBeUndefined()
  })
})

describe("TagPage, WiePlay's action tagging switched off", () => {
  const off: Analysis = {
    ...analysis,
    actions: {
      ...(analysis.actions as NonNullable<Analysis['actions']>),
      suggestions: { auto: 0, check: 0, confirmed: 0, corrected: 0, rejected: 0 },
      proposed: 0,
      gate: {
        mode: 'off', open: false, reason: 'off', finder: 'v0', games: 0, precision: null, contact_recall: null, trained_at: null,
        measured: { rallies: 61, contacts: 300, found: 0.3, precision: 0.58, sure: 40, sure_wrong: 0 },
      },
    },
  }
  // The coach tagged the rally by hand; WiePlay suggested nothing.
  const tagged = (): Tagging => ({ ...data(), events: [tag({ t_ms: 11_000, code: 'S', number: 7, segment_id: R1 }), tag({ t_ms: 12_200, code: 'R', side: 'them', number: 4, segment_id: R1 })] })

  it('says why in the Actions queue, what is measured, and keeps "Mark actions reviewed" for a tagged game', async () => {
    const { reviewed } = show(`/videos/${ID}/tag?mode=review&queue=actions`, tagged(), off)
    const region = await screen.findByRole('region', { name: 'Actions to check' })
    expect(await within(region).findByText('WiePlay’s action tagging is switched off.')).toBeInTheDocument()
    expect(within(region).getByText(/Measured on 61 hand-checked rallies: 58% of WiePlay’s actions are fully right/)).toBeInTheDocument()
    fireEvent.click(within(region).getByRole('button', { name: 'Mark actions reviewed' }))
    await waitFor(() => expect(reviewed).toHaveBeenCalled())
  })

  it('says it on the analysis card too, with the switch', async () => {
    show(`/videos/${ID}/tag`, tagged(), off)
    const card = await screen.findByRole('region', { name: 'Automatic rallies' })
    const actions = within(card).getByRole('region', { name: 'Actions' })
    expect(within(actions).getByText('WiePlay’s action tagging is switched off.')).toBeInTheDocument()
    expect(within(actions).getByText(/Of 40 sure ones, 0 were wrong\./)).toBeInTheDocument()
    expect(within(actions).getByRole('switch', { name: 'Actions reviewed' })).not.toBeChecked()
  })
})
