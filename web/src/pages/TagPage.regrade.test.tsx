import 'fake-indexeddb/auto'

import { IDBFactory } from 'fake-indexeddb'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChangesIn, ChangesOut, Tag, Tagging, Video } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { bundle, period, rally, tag } from '../test/tagging'
import { TagPage } from './TagPage'

const ID = '00000000-0000-4000-8000-0000000000d1'
const R1 = '00000000-0000-4000-8000-0000000008a1'
const A1 = '00000000-0000-4000-8000-0000000008e1'
const A2 = '00000000-0000-4000-8000-0000000008e2'
const A3 = '00000000-0000-4000-8000-0000000008e3'

const video: Video = {
  id: ID,
  team_id: 92,
  team_name: 'H3',
  team_season: '2026/27',
  sport: 'volleyball',
  kind: 'match',
  game_id: 502,
  title: 'KSCW H3 – Volley Näfels',
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
  can_delete: false,
  can_upload: false,
  can_tag: true,
  can_share: false,
  can_export: false,
  files: [],
}

const set1 = period({ no: 1, start_ms: 0, first_serve: 'us' })
const data = (): Tagging =>
  bundle({
    periods: [set1],
    segments: [rally({ id: R1, start_ms: 10_000, end_ms: 40_000, period_id: set1.id, won_by: 'us', serving: 'us' })],
    events: [
      tag({ id: A1, t_ms: 14_000, code: 'A', number: 7, segment_id: R1 }),
      tag({ id: A2, t_ms: 24_000, code: 'A', number: 3, member_id: 103, segment_id: R1, attributes: { grade: '+' } }),
      tag({ id: A3, t_ms: 34_000, code: 'A', number: 7, segment_id: R1 }),
      tag({ t_ms: 11_000, code: 'S', number: 7, segment_id: R1 }),
    ],
  })

/** /changes for tags like the API: the version goes up, the rest is kept. */
function fakeServer(initial: Tagging) {
  const events = new Map(initial.events.map((e) => [e.id, e]))
  const posts: ChangesIn[] = []
  const changes = ({ body }: { body: unknown }): ChangesOut => {
    const input = body as ChangesIn
    posts.push(input)
    const out: Tag[] = []
    for (const c of input.events) {
      const prev = events.get(c.id) as Tag
      const row: Tag = { ...prev, attributes: c.attributes, version: prev.version + 1 }
      events.set(c.id, row)
      out.push(row)
    }
    return { events: out, segments: initial.segments, periods: initial.periods, stale: [] }
  }
  const current = (): Tagging => ({ ...initial, events: [...events.values()] })
  const sent = () => new Map(posts.flatMap((p) => p.events).map((e) => [e.id, e]))
  return { changes, current, sent }
}

function show(url = `/videos/${ID}/tag`) {
  const server = fakeServer(data())
  vi.stubGlobal(
    'fetch',
    vi.fn(
      stubFetch({
        [`/api/videos/${ID}`]: { body: video },
        [`/api/videos/${ID}/tagging`]: { body: () => server.current() },
        [`POST /api/videos/${ID}/changes`]: { body: server.changes },
        [`/api/videos/${ID}/analysis`]: { status: 404 },
        [`/api/videos/${ID}/comments`]: { body: [] },
        '/api/reels': { body: [] },
        '/api/filters?team_id=92': { body: [] },
      }),
    ),
  )
  const view = renderRoute(<TagPage />, { path: '/videos/:id/tag', url, routes: [{ path: '/videos/:id', element: <p>Video page</p> }] })
  return { ...view, server }
}

function keys(...list: string[]) {
  for (const key of list) fireEvent.keyDown(document.body, { key })
}

const panel = () => screen.getByRole('region', { name: 'Regrade' })

beforeEach(() => {
  vi.stubGlobal('indexedDB', new IDBFactory())
  window.localStorage.clear()
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve())
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('TagPage, the regrade queue', () => {
  it('is a mode next to Tag; the pass grades with number keys and moves on by itself', async () => {
    const { router, server } = show()
    await screen.findByRole('region', { name: 'Tag entry' })
    fireEvent.click(within(screen.getByRole('group', { name: 'Mode' })).getByRole('button', { name: 'Regrade' }))
    expect(router.state.location.search).toBe('?mode=regrade')
    // Attacks by default, the ungraded ones: #7's two.
    expect(within(panel()).getByText('2 tags in this pass')).toBeInTheDocument()
    fireEvent.click(within(panel()).getByRole('button', { name: 'All of them' }))
    expect(within(panel()).getByText('3 tags in this pass')).toBeInTheDocument()
    fireEvent.change(within(panel()).getByRole('combobox', { name: 'Player' }), { target: { value: 'us:3' } })
    expect(within(panel()).getByText('1 tag in this pass')).toBeInTheDocument()
    fireEvent.change(within(panel()).getByRole('combobox', { name: 'Player' }), { target: { value: '' } })
    fireEvent.click(within(panel()).getByRole('button', { name: 'Ungraded only' }))

    keys('Enter') // start
    expect(within(panel()).getByRole('heading', { name: /#7 Luca Brunner · Attack at 0:14\.0 · Set 1/ })).toBeInTheDocument()
    expect(within(panel()).getByText('No grade yet')).toBeInTheDocument()
    // The grade buttons say what each grade means for the skill, with its key.
    const grades = within(panel()).getByRole('group', { name: 'Grade' })
    expect(within(grades).getAllByRole('button').map((b) => b.getAttribute('aria-label'))).toEqual([
      '# Kill (key 1)',
      '+ Good (key 2)',
      '! Covered (key 3)',
      '- Poor (key 4)',
      '/ Blocked (key 5)',
      '= Error (key 6)',
    ])
    const player = document.querySelector('video') as HTMLVideoElement
    expect(player.currentTime).toBe(11) // the attack's window: 3 s before it

    keys('1')
    expect(within(panel()).getByText('Attack at 0:14.0: # Kill.')).toBeInTheDocument()
    expect(within(panel()).getByRole('heading', { name: /Attack at 0:34\.0/ })).toBeInTheDocument()
    expect(player.currentTime).toBe(31)
    keys('u') // back to the first, its grade taken back
    expect(within(panel()).getByRole('heading', { name: /Attack at 0:14\.0/ })).toBeInTheDocument()
    expect(within(panel()).getByText('No grade yet')).toBeInTheDocument()
    keys('=')
    keys('ArrowLeft')
    expect(within(panel()).getByText('Now: = Error')).toBeInTheDocument()
    keys('s') // skip: it keeps its grade
    fireEvent.click(within(panel()).getByRole('button', { name: '/ Blocked (key 5)' }))
    expect(within(panel()).getByText('Pass done')).toBeInTheDocument()
    expect(within(panel()).getByText('You went through 2 tags; 2 graded.')).toBeInTheDocument()

    await waitFor(() => expect(server.sent().get(A3)).toBeDefined(), { timeout: 3000 })
    expect(server.sent().get(A1)).toMatchObject({ attributes: { grade: '=' } })
    expect(server.sent().get(A3)).toMatchObject({ attributes: { grade: '/' } })
    expect(server.sent().has(A2)).toBe(false)

    // Esc: back to the setup; again: back to tagging.
    keys('Escape')
    expect(within(panel()).getByRole('button', { name: /^Start/ })).toBeInTheDocument()
    keys('?')
    expect(within(screen.getByRole('dialog', { name: 'Keys' })).getByText('Grade, in the order of the grades table (# + ! - / =)')).toBeInTheDocument()
    keys('Escape', 'Escape')
    await waitFor(() => expect(router.state.location.search).toBe(''))
    expect(screen.getByRole('region', { name: 'Tag entry' })).toBeInTheDocument()
  })

  it('works by touch on a phone, opened from the address', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} }))
    show(`/videos/${ID}/tag?mode=regrade`)
    const region = await screen.findByRole('region', { name: 'Regrade' })
    expect(within(region).queryByText('Grade, in the order of the grades table (# + ! - / =)')).not.toBeInTheDocument() // no key legend
    fireEvent.click(within(region).getByRole('button', { name: 'Start · Attack' }))
    expect(within(region).getByText('Tap a grade: the next tag plays by itself.')).toBeInTheDocument()
    fireEvent.click(within(region).getByRole('button', { name: '+ Good (key 2)' }))
    expect(within(region).getByRole('heading', { name: /Attack at 0:34\.0/ })).toBeInTheDocument()
    fireEvent.click(within(region).getByRole('button', { name: 'No grade (key 0)' }))
    expect(within(region).getByText('Pass done')).toBeInTheDocument()
  })
})
