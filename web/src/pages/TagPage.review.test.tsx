import 'fake-indexeddb/auto'

import { IDBFactory } from 'fake-indexeddb'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { Analysis, ChangesIn, ChangesOut, Segment, Tagging, Video } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { bundle, period, rally } from '../test/tagging'
import { TagPage } from './TagPage'

const ID = '00000000-0000-4000-8000-0000000000c1'

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
  rallies_reviewed_at: null,
}

const set1 = period({ no: 1, start_ms: 0, first_serve: 'us' })
const suggestion = (id: string, start_ms: number, confidence: number, patch: Partial<Segment> = {}) =>
  rally({ id, start_ms, end_ms: start_ms + 8_000, period_id: set1.id, source: 'model', confidence, model_run_id: 'run-1', ...patch })
const S1 = '00000000-0000-4000-8000-0000000005a1'
const S2 = '00000000-0000-4000-8000-0000000005a2'
const S3 = '00000000-0000-4000-8000-0000000005a3'
const S4 = '00000000-0000-4000-8000-0000000005a4'

const data = (): Tagging =>
  bundle({
    periods: [set1],
    segments: [
      rally({ start_ms: 2_000, end_ms: 7_000, period_id: set1.id, won_by: 'us', score_us_after: 1, score_them_after: 0 }),
      suggestion(S1, 10_000, 0.95),
      suggestion(S2, 30_000, 0.62),
      suggestion(S3, 50_000, 0.91, { review: 'confirmed', version: 2 }),
      suggestion(S4, 70_000, 0.97),
    ],
  })

const analysis: Analysis = {
  status: 'done',
  model_run: { id: 'run-1', model_name: 'rally-finder', model_version: 'v0', created_at: '2026-10-16T18:00:00Z', params: {}, metrics: {} },
  proposals: { unreviewed: 3, confirmed: 1, corrected: 0, rejected: 1 },
  metrics_vs_human: null,
}

/** /changes for segments like the API: model rows keep their source and confidence. */
function fakeServer(initial: Tagging) {
  const segments = new Map(initial.segments.map((s) => [s.id, s]))
  const posts: ChangesIn[] = []
  const changes = ({ body }: { body: unknown }): ChangesOut => {
    const input = body as ChangesIn
    posts.push(input)
    const touched = new Set<string>()
    for (const c of input.segments) {
      const prev = segments.get(c.id)
      segments.set(c.id, {
        ...(prev ?? rally({ id: c.id, start_ms: c.start_ms, end_ms: c.end_ms })),
        kind: c.kind,
        start_ms: c.start_ms,
        end_ms: c.end_ms,
        won_by: c.won_by ?? null,
        review: c.review ?? 'unreviewed',
        version: (prev?.version ?? 0) + 1,
        deleted_at: c.deleted ? '2026-10-16T19:00:00Z' : null,
      })
      touched.add(c.id)
    }
    return { events: [], segments: [...segments.values()].filter((s) => !s.deleted_at || touched.has(s.id)), periods: initial.periods, stale: [] }
  }
  const current = (): Tagging => ({ ...initial, segments: [...segments.values()].filter((s) => !s.deleted_at) })
  /** The last state each segment was sent in. */
  const sent = () => new Map(posts.flatMap((p) => p.segments).map((s) => [s.id, s]))
  return { posts, changes, current, sent }
}

function show(url = `/videos/${ID}/tag`, initial = data()) {
  const server = fakeServer(initial)
  const fetch = vi.fn(
    stubFetch({
      [`/api/videos/${ID}`]: { body: video },
      [`/api/videos/${ID}/tagging`]: { body: () => server.current() },
      [`POST /api/videos/${ID}/changes`]: { body: server.changes },
      [`/api/videos/${ID}/analysis`]: { body: analysis },
      [`/api/videos/${ID}/comments`]: { body: [] },
      '/api/reels': { body: [] },
    }),
  )
  vi.stubGlobal('fetch', fetch)
  const view = renderRoute(<TagPage />, {
    path: '/videos/:id/tag',
    url,
    routes: [{ path: '/videos/:id', element: <p>Video page</p> }],
  })
  return { ...view, fetch, server }
}

function keys(...list: (string | { key: string; shiftKey?: boolean; altKey?: boolean })[]) {
  for (const k of list) fireEvent.keyDown(document.body, typeof k === 'string' ? { key: k } : k)
}

const panel = () => screen.getByRole('region', { name: 'Review suggestions' })

beforeEach(() => {
  vi.stubGlobal('indexedDB', new IDBFactory())
  window.localStorage.clear()
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve())
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
})

describe('TagPage, suggestions', () => {
  it('marks suggestions on the timeline and in the rally list, with a filter for those to review', async () => {
    const { container } = show()
    await screen.findByRole('region', { name: 'Tag entry' })
    expect(screen.getByText('Nomal suggested 5 rallies.')).toBeInTheDocument()
    expect(screen.getByText('3 still to review.')).toBeInTheDocument()
    // Open suggestions are outlined on the timeline; an accepted one keeps a marker.
    expect(container.querySelectorAll('[data-suggestion="open"]').length).toBeGreaterThanOrEqual(2)
    expect(container.querySelector('[data-suggestion="confirmed"]')).not.toBeNull()

    fireEvent.click(screen.getByRole('button', { name: 'Rallies (5)' }))
    const table = screen.getByRole('table', { name: 'Rallies' })
    const rows = () => within(table).getAllByRole('row').slice(1)
    expect(rows()).toHaveLength(5)
    expect(within(table).getByText('Suggested · 95%')).toBeInTheDocument()
    expect(within(table).getByText('Suggested, confirmed')).toBeInTheDocument()

    fireEvent.click(screen.getByRole('button', { name: 'Suggestions (3)' }))
    expect(rows()).toHaveLength(3)
    expect(rows().map((r) => within(r).getAllByRole('button')[0]?.textContent)).toEqual(['2', '3', '5'])
    // Accepted right in the list: it leaves the filter.
    fireEvent.click(screen.getByRole('button', { name: 'Accept rally 2' }))
    expect(rows()).toHaveLength(2)
    fireEvent.click(screen.getByRole('button', { name: 'Reject rally 3' }))
    fireEvent.click(screen.getByRole('button', { name: 'Reject rally 4' }))
    expect(screen.getByText('No suggestions to review.')).toBeInTheDocument()
  })

  it('accepts and rejects from the rally list through the outbox', async () => {
    const { server } = show()
    await screen.findByRole('region', { name: 'Tag entry' })
    fireEvent.click(screen.getByRole('button', { name: 'Rallies (5)' }))
    fireEvent.click(screen.getByRole('button', { name: 'Accept rally 2' }))
    fireEvent.click(screen.getByRole('button', { name: 'Reject rally 5' }))
    await waitFor(() => expect(server.posts).toHaveLength(1), { timeout: 3000 })
    expect(server.sent().get(S1)).toMatchObject({ review: 'confirmed', base_version: 1 })
    expect(server.sent().get(S4)).toMatchObject({ review: 'rejected', deleted: true })
  })

  it('reviews with the keys, sends each answer through the outbox and shows the progress', async () => {
    const { server, router } = show()
    await screen.findByRole('region', { name: 'Tag entry' })
    fireEvent.click(screen.getByRole('button', { name: 'Review suggestions (3)' }))
    expect(router.state.location.search).toBe('?mode=review')
    const player = document.querySelector('video') as HTMLVideoElement
    // The first open one, from 1 s before it.
    expect(within(panel()).getByRole('heading', { name: /Rally at 0:10\.0/ })).toBeInTheDocument()
    expect(player.currentTime).toBe(9)
    const progress = within(panel()).getByText('2 of 5 suggestions reviewed')
    expect(progress.closest('[aria-live]')).toHaveAttribute('aria-live', 'polite')

    keys('ArrowRight', { key: 'ArrowRight', shiftKey: true })
    expect(within(panel()).getByText('End moved to 0:18.2.')).toBeInTheDocument()
    keys('Enter')
    expect(within(panel()).getByText('Accepted the rally at 0:10.2.')).toBeInTheDocument()
    expect(within(panel()).getByText('3 of 5 suggestions reviewed')).toBeInTheDocument()
    expect(within(panel()).getByRole('heading', { name: /Rally at 0:30\.0/ })).toBeInTheDocument()
    keys('x')
    expect(within(panel()).getByRole('heading', { name: /Rally at 1:10\.0/ })).toBeInTheDocument()
    keys('Enter')
    expect(within(panel()).getByText('5 of 5 suggestions reviewed')).toBeInTheDocument()
    // Every suggestion answered: on to "rallies reviewed".
    expect(within(panel()).getByText('Every suggestion is reviewed')).toBeInTheDocument()
    expect(within(panel()).getByRole('button', { name: 'Mark rallies reviewed' })).toBeInTheDocument()

    await waitFor(() => expect(server.posts.length).toBeGreaterThan(0), { timeout: 3000 })
    await waitFor(() => expect(server.sent().get(S4)).toBeDefined(), { timeout: 3000 })
    expect(server.sent().get(S1)).toMatchObject({ start_ms: 10_250, end_ms: 18_250, review: 'corrected' })
    expect(server.sent().get(S2)).toMatchObject({ review: 'rejected', deleted: true })
    expect(server.sent().get(S4)).toMatchObject({ review: 'confirmed' })
    // Tagging keys are off while reviewing: nothing was tagged.
    expect(server.posts.every((p) => p.events.length === 0)).toBe(true)

    keys('Escape')
    expect(router.state.location.search).toBe('')
    expect(screen.getByRole('region', { name: 'Tag entry' })).toBeInTheDocument()
  })

  it('accepts every suggestion that is at least 90% sure after a confirmation', async () => {
    const { server } = show(`/videos/${ID}/tag?mode=review`)
    await screen.findByRole('region', { name: 'Review suggestions' })
    fireEvent.click(within(panel()).getByRole('button', { name: 'Accept all remaining with confidence ≥ 90% (2)' }))
    const dialog = screen.getByRole('alertdialog', { name: 'Accept 2 suggestions?' })
    // Keys belong to the dialog while it asks.
    fireEvent.keyDown(within(dialog).getByRole('button', { name: 'Cancel' }), { key: 'x' })
    expect(server.current().segments).toHaveLength(5)
    fireEvent.click(within(dialog).getByRole('button', { name: 'Accept 2' }))
    expect(within(panel()).getByText('Accepted 2 suggestions.')).toBeInTheDocument()
    // The one on screen was among them: on to the one left (62%).
    expect(within(panel()).getByRole('heading', { name: /Rally at 0:30\.0/ })).toBeInTheDocument()
    await waitFor(() => expect(server.sent().get(S4)).toBeDefined(), { timeout: 3000 })
    expect(server.sent().get(S1)).toMatchObject({ review: 'confirmed' })
    expect(server.sent().get(S4)).toMatchObject({ review: 'confirmed' })
    expect(server.sent().get(S2)).toBeUndefined()
  })

  it('reviews by touch on a narrow screen: big buttons, no tagging desk', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} }))
    const { server } = show()
    await screen.findByRole('region', { name: 'Review suggestions' })
    expect(screen.queryByRole('region', { name: 'Tag entry' })).not.toBeInTheDocument()
    expect(screen.getByText(/reviewing works here too/)).toBeInTheDocument()
    fireEvent.click(within(panel()).getByRole('button', { name: 'Start 0.25 s later' }))
    fireEvent.click(within(panel()).getByRole('button', { name: 'Reject' }))
    expect(within(panel()).getByRole('heading', { name: /Rally at 0:30\.0/ })).toBeInTheDocument()
    fireEvent.click(within(panel()).getByRole('button', { name: 'Undo' }))
    expect(within(panel()).getByRole('heading', { name: /Rally at 0:10\.2/ })).toBeInTheDocument()
    fireEvent.click(within(panel()).getByRole('button', { name: /Merge with next/ }))
    expect(within(panel()).getByText('Merged with the next suggestion.')).toBeInTheDocument()
    await waitFor(() => expect(server.sent().get(S2)).toBeDefined(), { timeout: 3000 })
    expect(server.sent().get(S1)).toMatchObject({ start_ms: 10_250, end_ms: 38_000, review: 'corrected' })
    expect(server.sent().get(S2)).toMatchObject({ review: 'merged', merged_into: S1, deleted: true })
  })

  it('still asks for a bigger screen on a phone when there is nothing to review', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} }))
    show(`/videos/${ID}/tag`, bundle({ periods: [set1], segments: [rally({ start_ms: 2_000, end_ms: 7_000 })] }))
    expect(await screen.findByText(/Tagging needs a larger screen/)).toBeInTheDocument()
  })

  it('lists the review keys with ?', async () => {
    show(`/videos/${ID}/tag?mode=review`)
    await screen.findByRole('region', { name: 'Review suggestions' })
    keys('?')
    const dialog = screen.getByRole('dialog', { name: 'Keys' })
    expect(within(dialog).getByText('Merge with the next suggestion')).toBeInTheDocument()
    expect(within(dialog).getByText('With the arrows: one frame instead of 0.25 s')).toBeInTheDocument()
  })
})
