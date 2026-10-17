import 'fake-indexeddb/auto'

import { IDBFactory } from 'fake-indexeddb'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Analysis, ChangesIn, ChangesOut, Period, Scoring, Segment, Tagging, Video } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { bundle, period, rally } from '../test/tagging'
import { TagPage } from './TagPage'

// The automatic scoring on the tagging page (PLAN §10.3): who served and who won as facts or
// suggestions, the breaks Nomal labelled in the review, and the sets bar's scoring controls.

const ID = '00000000-0000-4000-8000-0000000000d1'

const video: Video = {
  id: ID,
  team_id: 92,
  team_name: 'H3',
  team_season: '2026/27',
  sport: 'volleyball',
  kind: 'match',
  game_id: 503,
  title: 'KSCW H3 – Volley Smash 05',
  recorded_on: '2026-10-12',
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
  created_at: '2026-10-12T22:00:00Z',
  created_by: 7,
  can_delete: true,
  can_upload: true,
  can_tag: true,
  can_share: true,
  can_export: true,
  files: [],
  rallies_reviewed_at: null,
  // The teams' names instead of us and them (the server's: backend nomal/core/names.py).
  names: { us: 'KSCW H3', them: 'Smash 05', us_full: 'KSC Wiedikon H3', them_full: 'Volley Smash 05', them_from: 'game', us_set: null, them_set: null },
}

const scoring = (patch: Partial<Scoring> = {}): Scoring => ({
  run_id: 'score-1',
  created_at: '2026-10-16T20:00:00Z',
  serve_finder: 'v1',
  rallies: 3,
  flagged: 1,
  sets: [],
  sets_matching_official: 0,
  sets_official: 0,
  sides_confidence: 0.97,
  gaps: { warmup: 0, set_break: 0, timeout: 1, stoppage: 1, other_dead: 0 },
  first_serve_ms: 2_000,
  vs_coach: null,
  ...patch,
})

const analysis = (patch: Partial<Analysis> = {}): Analysis => ({
  status: 'done',
  model_run: { id: 'run-1', model_name: 'rally-finder', model_version: 'v0', created_at: '2026-10-16T18:00:00Z', params: {}, metrics: {} },
  proposals: { unreviewed: 1, confirmed: 2, corrected: 0, rejected: 0 },
  metrics_vs_human: null,
  scoring: scoring(),
  ...patch,
})

const set1 = period({ no: 1, start_ms: 0, first_serve: 'us', our_court_side_at_start: 'near' })
const model = { source: 'model', model_run_id: 'run-1', version: 1 } as const

const R1 = '00000000-0000-4000-8000-0000000006a1'
const R2 = '00000000-0000-4000-8000-0000000006a2'
const S1 = '00000000-0000-4000-8000-0000000006a3'
const G1 = '00000000-0000-4000-8000-0000000006b1'
const G2 = '00000000-0000-4000-8000-0000000006b2'
const G3 = '00000000-0000-4000-8000-0000000006b3'

/** A coach's rally, an accepted one whose winner and score are Nomal's (flagged), an open suggestion and three breaks. */
const data = (): Tagging =>
  bundle({
    periods: [set1],
    segments: [
      rally({ id: R1, start_ms: 2_000, end_ms: 8_000, period_id: set1.id, serving: 'us', won_by: 'us', score_us_after: 1, score_them_after: 0, score_source: 'tags' }),
      rally({
        ...model,
        id: R2,
        review: 'confirmed',
        confidence: 0.93,
        start_ms: 12_000,
        end_ms: 20_000,
        period_id: set1.id,
        serving: 'us',
        model_serving: 'us',
        model_won_by: 'them',
        score_confidence: 0.64,
        score_us_after: 1,
        score_them_after: 1,
        score_source: 'model',
        score_conflict: true,
        score_note: 'serve',
      }),
      rally({ ...model, id: G1, kind: 'timeout', confidence: 0.8, start_ms: 25_000, end_ms: 55_000, period_id: set1.id }),
      rally({
        ...model,
        id: S1,
        confidence: 0.88,
        start_ms: 60_000,
        end_ms: 68_000,
        period_id: set1.id,
        model_serving: 'them',
        serving_court: 'far',
        serve_confidence: 0.87,
        model_won_by: 'them',
        score_confidence: 0.91,
        score_us_after: 1,
        score_them_after: 2,
        score_source: 'model',
        score_conflict: true,
        score_note: 'missing_before',
      }),
      rally({ ...model, id: G2, kind: 'stoppage', confidence: 0.7, start_ms: 70_000, end_ms: 130_000, period_id: set1.id }),
      rally({ ...model, id: G3, kind: 'other_dead', confidence: 0.55, start_ms: 140_000, end_ms: 200_000, period_id: set1.id }),
    ],
  })

/** /changes like the API: rows keep what the request doesn't name (source, the model's fields, the serve call). */
function fakeServer(initial: Tagging) {
  const segments = new Map(initial.segments.map((s) => [s.id, s]))
  const periods = new Map(initial.periods.map((p) => [p.id, p]))
  const posts: ChangesIn[] = []
  const stamp = (deleted?: boolean) => (deleted ? '2026-10-16T21:00:00Z' : null)
  const changes = ({ body }: { body: unknown }): ChangesOut => {
    const input = body as ChangesIn
    posts.push(input)
    for (const c of input.segments) {
      const prev = segments.get(c.id) ?? rally({ id: c.id, start_ms: c.start_ms, end_ms: c.end_ms })
      const { deleted, ...rest } = c
      segments.set(c.id, { ...prev, ...rest, won_by: c.won_by ?? null, version: prev.version + 1, deleted_at: stamp(deleted) } as Segment)
    }
    for (const p of input.periods) {
      const prev = periods.get(p.id) ?? period({ id: p.id, no: p.no, start_ms: p.start_ms })
      const { deleted, ...rest } = p
      periods.set(p.id, { ...prev, ...rest, version: prev.version + 1, deleted_at: stamp(deleted) } as Period)
    }
    const touched = new Set([...input.segments.map((s) => s.id), ...input.periods.map((p) => p.id)])
    return {
      events: [],
      segments: [...segments.values()].filter((s) => !s.deleted_at || touched.has(s.id)),
      periods: [...periods.values()].filter((p) => !p.deleted_at || touched.has(p.id)),
      stale: [],
    }
  }
  const current = (): Tagging => ({
    ...initial,
    segments: [...segments.values()].filter((s) => !s.deleted_at),
    periods: [...periods.values()].filter((p) => !p.deleted_at),
  })
  const sentSegments = () => new Map(posts.flatMap((p) => p.segments).map((s) => [s.id, s]))
  const sentPeriods = () => posts.flatMap((p) => p.periods)
  return { posts, changes, current, sentSegments, sentPeriods }
}

function show(initial = data(), { url = `/videos/${ID}/tag`, state = { analysis: analysis() } } = {}) {
  const server = fakeServer(initial)
  const scored: unknown[] = []
  const fetch = vi.fn(
    stubFetch({
      [`/api/videos/${ID}`]: { body: video },
      [`/api/videos/${ID}/tagging`]: { body: () => server.current() },
      [`POST /api/videos/${ID}/changes`]: { body: server.changes },
      [`/api/videos/${ID}/analysis`]: { body: () => state.analysis },
      [`POST /api/videos/${ID}/scoring`]: {
        body: () => {
          scored.push(server.posts.length)
          state.analysis = analysis({ scoring: scoring({ flagged: 0, run_id: 'score-2' }) })
          return state.analysis
        },
      },
      [`/api/videos/${ID}/comments`]: { body: [] },
      '/api/reels': { body: [] },
    }),
  )
  vi.stubGlobal('fetch', fetch)
  const view = renderRoute(<TagPage />, { path: '/videos/:id/tag', url, routes: [{ path: '/videos/:id', element: <p>Video page</p> }] })
  return { ...view, fetch, server, scored }
}

function keys(...list: string[]) {
  for (const key of list) fireEvent.keyDown(document.body, { key })
}

const panel = () => screen.getByRole('region', { name: 'Review suggestions' })

beforeEach(() => {
  vi.stubGlobal('indexedDB', new IDBFactory())
  window.localStorage.clear()
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve())
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
})

describe('TagPage, automatic scoring', () => {
  it("shows who served and who won, Nomal's values as suggestions, and flags rallies to check", async () => {
    const rendered = show()
    await screen.findByRole('region', { name: 'Tag entry' })
    fireEvent.click(screen.getByRole('button', { name: 'Rallies (3)' }))
    const table = screen.getByRole('table', { name: 'Rallies' })
    const rows = within(table).getAllByRole('row').slice(1)
    expect(rows).toHaveLength(3)
    const [first, second, third] = rows as [HTMLElement, HTMLElement, HTMLElement]
    const suggested = (row: HTMLElement) => [...row.querySelectorAll('[data-suggested]')].map((e) => e.textContent)
    // A coach's rally: facts, nothing suggested.
    expect(suggested(first)).toEqual([])
    expect(first).toHaveTextContent('1:0')
    // Nomal's score and winner; the server is a fact (the previous winner). The winner is a
    // choice of the teams' names: none yet, so Nomal's shows in the empty choice.
    expect(suggested(second)).toEqual(['1:1 (suggested)'])
    expect(second.querySelector('[data-suggested]')).toHaveAttribute('title', 'Suggested by Nomal')
    expect(within(second).getAllByText('KSCW H3')[0]?.closest('td')?.querySelector('select')).toBeNull() // served: a fact
    const point = within(second).getByRole('combobox', { name: 'Point of rally 2' })
    expect(point).toHaveValue('')
    expect(within(point).getAllByRole('option').map((o) => o.textContent)).toEqual(['Smash 05 (Nomal)', 'KSCW H3', 'Smash 05'])
    expect(within(second).getByTitle("The serve doesn't fit the score.")).toHaveTextContent('Check')
    expect(second).toHaveAttribute('data-check', 'true')
    // An open suggestion: its server is Nomal's too.
    expect(suggested(third)).toEqual(['Smash 05 (suggested)', '1:2 (suggested)'])
    // The score's order is in the column's head.
    expect(within(table).getByRole('columnheader', { name: 'Score: KSCW H3 – Smash 05' })).toBeInTheDocument()
    // The coach saw KSCW H3 win rally 2: a fact, the score follows; the accepted proposal is corrected.
    fireEvent.change(point, { target: { value: 'us' } })
    expect(within(table).getByRole('combobox', { name: 'Point of rally 2' })).toHaveValue('us')
    expect(rows[1]).toHaveTextContent('2:0')
    const { server } = rendered
    await waitFor(() => expect(server.sentSegments().get(R2)).toMatchObject({ won_by: 'us', review: 'corrected' }), { timeout: 3000 })
    expect(within(third).getByTitle('A point seems to be missing before this rally.')).toBeInTheDocument()
  })

  it('draws the breaks as labelled bands, suggestions dashed, and marks where the sets start', async () => {
    const { container } = show()
    await screen.findByRole('region', { name: 'Tag entry' })
    const timeout = container.querySelector(`[data-gap="timeout"][data-suggestion="open"]`)
    expect(timeout).not.toBeNull()
    expect(timeout?.className).toContain('border-dashed')
    expect(container.querySelector('[data-set-start="1"]')).not.toBeNull()
    expect(screen.getByText('Suggested break')).toBeInTheDocument()
    expect(screen.getByText('Set start')).toBeInTheDocument()
  })

  it('reviews the breaks with the rallies: accept, relabel with T, reject; 1 calls the server', async () => {
    const { server, fetch, scored } = show(data(), { url: `/videos/${ID}/tag?mode=review` })
    await screen.findByRole('region', { name: 'Review suggestions' })
    // Rally proposals and break labels together: the accepted rally is answered.
    expect(within(panel()).getByText('1 of 5 suggestions reviewed')).toBeInTheDocument()

    // First the timeout (in time order): a break, labelled, without merge or split.
    expect(within(panel()).getByRole('heading', { name: /Timeout at 0:25\.0/ })).toBeInTheDocument()
    expect(within(panel()).getByRole('button', { name: 'Label as Timeout' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(panel()).queryByRole('button', { name: /Merge with next/ })).not.toBeInTheDocument()
    keys('Enter')
    expect(within(panel()).getByText('Accepted: Timeout at 0:25.0.')).toBeInTheDocument()

    // Then the rally: Nomal's server and winner as suggestions, and why to check it.
    expect(within(panel()).getByRole('heading', { name: /Rally at 1:00\.0/ })).toBeInTheDocument()
    expect(within(panel()).getByText('Smash 05 (suggested, 87%, far court)')).toBeInTheDocument()
    expect(within(panel()).getByText('Smash 05 (suggested, 91%)')).toBeInTheDocument()
    expect(within(panel()).getByText('KSCW H3 1:2 Smash 05 (suggested)')).toBeInTheDocument()
    expect(within(panel()).getByText('Check: A point seems to be missing before this rally.')).toBeInTheDocument()
    keys('1')
    expect(within(panel()).getByText('Serve: KSCW H3, your call.')).toBeInTheDocument()
    expect(within(panel()).getByRole('button', { name: /KSCW H3 serves/ })).toHaveAttribute('aria-pressed', 'true')
    expect(within(panel()).getByText('KSCW H3 (your call)')).toBeInTheDocument()
    keys('Enter')

    // The stoppage is a timeout: T relabels it (corrected) and goes on.
    expect(within(panel()).getByRole('heading', { name: /Stoppage at 1:10\.0/ })).toBeInTheDocument()
    keys('t')
    expect(within(panel()).getByText('Now a Timeout: 1:10.0.')).toBeInTheDocument()
    // The last break is no break at all: X.
    expect(within(panel()).getByRole('heading', { name: /Other break at 2:20\.0/ })).toBeInTheDocument()
    keys('x')
    expect(within(panel()).getByText('5 of 5 suggestions reviewed')).toBeInTheDocument()

    await waitFor(() => expect(server.sentSegments().get(G3)).toBeDefined(), { timeout: 3000 })
    const sent = server.sentSegments()
    expect(sent.get(G1)).toMatchObject({ kind: 'timeout', review: 'confirmed' })
    expect(sent.get(S1)).toMatchObject({ review: 'confirmed', serving_manual: 'us' })
    expect(sent.get(G2)).toMatchObject({ kind: 'timeout', review: 'corrected' })
    expect(sent.get(G3)).toMatchObject({ review: 'rejected', deleted: true })
    expect(scored).toHaveLength(0)

    // Leaving the review after answers: Nomal scores the match again, then the page reloads its data.
    keys('Escape')
    await waitFor(() => expect(scored).toHaveLength(1), { timeout: 3000 })
    expect(scored[0]).toBeGreaterThan(0) // after the answers were sent
    await waitFor(() => expect(fetch.mock.calls.filter(([path]) => path === `/api/videos/${ID}/tagging`).length).toBeGreaterThan(1))
    expect(await screen.findByText('No rally to check.')).toBeInTheDocument()
  })

  it('sets who won with 3 and 4 or the buttons: a fact, the score at once, U takes it back', async () => {
    const { server } = show(data(), { url: `/videos/${ID}/tag?mode=review` })
    await screen.findByRole('region', { name: 'Review suggestions' })
    keys('Enter') // the timeout first
    expect(within(panel()).getByRole('heading', { name: /Rally at 1:00\.0/ })).toBeInTheDocument()
    const won = within(panel()).getByRole('group', { name: 'Who won the rally' })
    // The teams' names on the buttons; their accessible names say what for.
    expect(within(won).getAllByRole('button').map((b) => b.textContent)).toEqual(['KSCW H3' + '3', 'Smash 05' + '4'])
    expect(within(won).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual(['Point KSCW H3', 'Point Smash 05'])

    // Nomal says Smash 05 won it (1:2); the coach saw KSCW H3 win: 3.
    keys('3')
    expect(within(panel()).getByText('Point: KSCW H3, your call.')).toBeInTheDocument()
    expect(within(won).getByRole('button', { name: /Point KSCW H3/ })).toHaveAttribute('aria-pressed', 'true')
    expect(within(panel()).getByText('KSCW H3 (your call)')).toBeInTheDocument()
    // The score line follows at once (Nomal's winner of the rally before still counts: suggested).
    expect(within(panel()).getByText('KSCW H3 2:1 Smash 05 (suggested)')).toBeInTheDocument()
    await waitFor(() => expect(server.sentSegments().get(S1)).toMatchObject({ won_by: 'us', review: 'corrected' }), { timeout: 3000 })

    // The button does the same: Smash 05 after all.
    fireEvent.click(within(won).getByRole('button', { name: /Point Smash 05/ }))
    expect(within(panel()).getByText('Point: Smash 05, your call.')).toBeInTheDocument()
    expect(within(panel()).getByText('KSCW H3 1:2 Smash 05 (suggested)')).toBeInTheDocument()
    await waitFor(() => expect(server.sentSegments().get(S1)).toMatchObject({ won_by: 'them', review: 'corrected' }), { timeout: 3000 })

    // U: back to KSCW H3; U again: no call, the suggestion open again.
    keys('u')
    expect(within(won).getByRole('button', { name: /Point KSCW H3/ })).toHaveAttribute('aria-pressed', 'true')
    await waitFor(() => expect(server.sentSegments().get(S1)).toMatchObject({ won_by: 'us' }), { timeout: 3000 })
    keys('u')
    expect(within(won).getByRole('button', { name: /Point KSCW H3/ })).toHaveAttribute('aria-pressed', 'false')
    expect(within(panel()).getByText('Smash 05 (suggested, 91%)')).toBeInTheDocument()
    await waitFor(() => expect(server.sentSegments().get(S1)).toMatchObject({ won_by: null, review: 'unreviewed' }), { timeout: 3000 })
    // The same key twice takes the call back too.
    keys('4', '4')
    expect(within(panel()).getByText('Point call taken back.')).toBeInTheDocument()
    expect(within(won).getByRole('button', { name: /Point Smash 05/ })).toHaveAttribute('aria-pressed', 'false')
  }, 20_000) // four syncs of the outbox, a second apart

  it("doesn't score again when the review is left without a change", async () => {
    const { scored } = show(data(), { url: `/videos/${ID}/tag?mode=review` })
    await screen.findByRole('region', { name: 'Review suggestions' })
    keys('ArrowDown', 'Escape')
    await screen.findByRole('region', { name: 'Tag entry' })
    expect(scored).toHaveLength(0)
  })

  it('lists the new review keys with ?', async () => {
    show(data(), { url: `/videos/${ID}/tag?mode=review` })
    await screen.findByRole('region', { name: 'Review suggestions' })
    keys('?')
    const dialog = screen.getByRole('dialog', { name: 'Keys' })
    expect(within(dialog).getByText('KSCW H3 serves, Smash 05 serves; the same key again takes the call back')).toBeInTheDocument()
    expect(within(dialog).getByText('KSCW H3 won the rally, Smash 05 won it; the same key again takes it back')).toBeInTheDocument()
    expect(within(dialog).getByText('A break is a warm-up, set break, timeout, stoppage or other break')).toBeInTheDocument()
  })
})

describe('TagPage, sets bar and the automatic score', () => {
  const one = period({ id: '00000000-0000-4000-8000-0000000007a1', no: 1, start_ms: 0, end_ms: 1_500_000, final_us: 25, final_them: 21, ...{ source: 'model', review: 'unreviewed', confidence: 0.82, model_first_serve: 'them', model_court_side: 'near' } })
  const two = period({ id: '00000000-0000-4000-8000-0000000007a2', no: 2, start_ms: 1_600_000, end_ms: 3_000_000, final_us: 23, final_them: 25, source: 'model', review: 'unreviewed', confidence: 0.6 })
  const sets = () =>
    bundle({
      periods: [one, two],
      official_sets: [
        [25, 21],
        [25, 23],
      ],
    })

  it("shows Nomal's sets as suggestions to accept or reject, with the official score against Nomal's", async () => {
    const { server } = show(sets())
    await screen.findByRole('region', { name: 'Tag entry' })
    const bar = screen.getByRole('region', { name: 'Sets' })
    expect(within(bar).getByRole('button', { name: 'Edit set 1' })).toHaveTextContent('Suggested · 82%')
    const details = within(bar).getByRole('table', { name: 'Serve, side and rotation per set' })
    expect(within(details).getByText('Official 25:21 · Nomal 25:21')).toBeInTheDocument()
    expect(within(details).getAllByText('Same as the official score')).toHaveLength(1)
    expect(within(details).getByText('Official 25:23 · Nomal 23:25')).toBeInTheDocument()

    fireEvent.click(within(bar).getByRole('button', { name: 'Accept set 1' }))
    expect(within(bar).getByRole('button', { name: 'Edit set 1' })).not.toHaveTextContent('Suggested')
    expect(within(bar).queryByRole('button', { name: 'Accept set 1' })).not.toBeInTheDocument()
    fireEvent.click(within(bar).getByRole('button', { name: 'Reject set 2' }))
    expect(within(bar).queryByRole('button', { name: 'Edit set 2' })).not.toBeInTheDocument()

    await waitFor(() => expect(server.sentPeriods()).toHaveLength(2), { timeout: 3000 })
    expect(server.sentPeriods()).toEqual([
      expect.objectContaining({ id: one.id, review: 'confirmed', base_version: 1 }),
      expect.objectContaining({ id: two.id, review: 'rejected', deleted: true }),
    ])
  })

  it("sets first serve, our side and the starting rotation per set; Nomal's guess shows until a coach picks", async () => {
    const { server } = show(sets())
    await screen.findByRole('region', { name: 'Tag entry' })
    const serve = screen.getByRole('group', { name: 'First serve in set 1' })
    const guess = within(serve).getByRole('button', { name: 'Smash 05 (suggested)' })
    expect(guess).toHaveAttribute('aria-pressed', 'false')
    expect(guess).toHaveAttribute('title', 'Nomal guesses Smash 05. Click to confirm.')
    fireEvent.click(guess)
    expect(within(serve).getByRole('button', { name: 'Smash 05' })).toHaveAttribute('aria-pressed', 'true')

    const side = screen.getByRole('group', { name: 'Where KSCW H3 starts set 1' })
    expect(within(side).getByRole('button', { name: 'Near (suggested)' })).toBeInTheDocument()
    fireEvent.click(within(side).getByRole('button', { name: 'Far' }))
    expect(within(side).getByRole('button', { name: 'Far' })).toHaveAttribute('aria-pressed', 'true')
    expect(within(side).queryByRole('button', { name: /suggested/ })).not.toBeInTheDocument()

    fireEvent.change(screen.getByRole('combobox', { name: 'Starting rotation of set 2' }), { target: { value: '4' } })

    await waitFor(() => expect(server.sentPeriods().some((p) => p.start_rotation === 4)).toBe(true), { timeout: 3000 })
    const sent = new Map(server.sentPeriods().map((p) => [p.id, p]))
    expect(sent.get(one.id)).toMatchObject({ first_serve: 'them', our_court_side_at_start: 'far', review: 'unreviewed', start_rotation: null })
    expect(sent.get(two.id)).toMatchObject({ start_rotation: 4, first_serve: null })
  })

  it('takes a starting lineup per set: six shirt numbers on a court, the setter marks the rotation', async () => {
    const { server } = show(sets())
    await screen.findByRole('region', { name: 'Tag entry' })
    fireEvent.click(screen.getByRole('button', { name: 'Lineup of set 2' }))
    const dialog = await screen.findByRole('dialog', { name: 'Lineup of set 2' })
    // Laid out like the court from behind our end line: front row IV III II, back row V VI I.
    expect([...dialog.querySelectorAll('[data-zone]')].map((z) => z.getAttribute('data-zone'))).toEqual(['4', '3', '2', '5', '6', '1'])
    const numbers = { I: '3', II: '7', III: '11', IV: '9', V: '1', VI: '7' }
    for (const [zone, number] of Object.entries(numbers)) {
      fireEvent.change(within(dialog).getByRole('combobox', { name: `Shirt number in zone ${zone}` }), { target: { value: number } })
    }
    fireEvent.click(within(dialog).getByRole('radio', { name: 'Setter in zone I' }))
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save lineup' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('#7 stands in two zones.')
    fireEvent.change(within(dialog).getByRole('combobox', { name: 'Shirt number in zone VI' }), { target: { value: '' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save lineup' }))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Lineup of set 2' })).toHaveTextContent('3 · 7 · 11 · 9 · 1 · –')

    await waitFor(() => expect(server.sentPeriods().some((p) => p.start_lineup)).toBe(true), { timeout: 3000 })
    const sent = server.sentPeriods().find((p) => p.id === two.id)
    expect(sent).toMatchObject({ start_lineup: [3, 7, 11, 9, 1, null], start_rotation: 1 })
  })

  it("asks for set 1's first serve and side when Nomal is unsure about them", async () => {
    show(sets(), { state: { analysis: analysis({ scoring: scoring({ sides_confidence: 0.72 }) }) } })
    await screen.findByRole('region', { name: 'Tag entry' })
    const note = await screen.findByRole('note', { name: 'Set 1: who served first, and where did we start?' })
    expect(note).toHaveTextContent('Nomal is only 72% sure.')
    expect(note).toHaveTextContent('one click fixes the score of the whole match')
    fireEvent.click(within(screen.getByRole('group', { name: 'First serve in set 1' })).getByRole('button', { name: /^KSCW H3/ }))
    expect(screen.getByRole('note')).toBeInTheDocument()
    fireEvent.click(within(screen.getByRole('group', { name: 'Where KSCW H3 starts set 1' })).getByRole('button', { name: /^Near/ }))
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })

  it('stays quiet when Nomal is sure about the sides', async () => {
    show(sets())
    await screen.findByRole('region', { name: 'Tag entry' })
    await screen.findByText('Official 25:21 · Nomal 25:21')
    expect(screen.queryByRole('note')).not.toBeInTheDocument()
  })
})
