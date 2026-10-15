import 'fake-indexeddb/auto'

import { IDBFactory } from 'fake-indexeddb'
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { rescore, serveCodes } from '../lib/rallies'
import type { ChangesIn, ChangesOut, Period, RosterCandidate, Segment, Tag, Tagging, Video } from '../lib/types'
import { renderRoute, stubFetch } from '../test/render'
import { bundle, period, tag, volleyball } from '../test/tagging'
import { TagPage } from './TagPage'

const ID = '00000000-0000-4000-8000-0000000000b1'

const video: Video = {
  id: ID,
  team_id: 92,
  team_name: 'H3',
  team_season: '2026/27',
  sport: 'volleyball',
  kind: 'match',
  game_id: 501,
  training_id: null,
  title: 'KSCW H3 – Volley Näfels',
  recorded_on: '2026-10-11',
  source: 'file',
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
  created_at: '2026-10-11T22:00:00Z',
  created_by: 7,
  can_delete: true,
  can_upload: true,
  files: [],
}

/** A tiny in-memory API for /changes, scoring like the real one. Deleted rows are kept with deleted_at. */
function fakeServer(initial: Tagging) {
  const events = new Map(initial.events.map((e) => [e.id, e]))
  const segments = new Map(initial.segments.map((s) => [s.id, s]))
  const periods = new Map(initial.periods.map((p) => [p.id, p]))
  const posts: ChangesIn[] = []
  const live = <T extends { deleted_at: string | null }>(map: Map<string, T>) => [...map.values()].filter((x) => !x.deleted_at)
  const stamp = (deleted?: boolean) => (deleted ? '2026-10-15T19:00:00Z' : null)

  const changes = ({ body }: { body: unknown }): ChangesOut => {
    const input = body as ChangesIn
    posts.push(input)
    for (const p of input.periods) {
      const version = (periods.get(p.id)?.version ?? 0) + 1
      periods.set(p.id, { ...period({ no: p.no, start_ms: p.start_ms }), ...p, id: p.id, end_ms: p.end_ms ?? null, first_serve: p.first_serve ?? null, version, deleted_at: stamp(p.deleted) } as Period)
    }
    for (const s of input.segments) {
      const version = (segments.get(s.id)?.version ?? 0) + 1
      segments.set(s.id, {
        id: s.id, kind: s.kind, start_ms: s.start_ms, end_ms: s.end_ms, period_id: null, serving: null, won_by: s.won_by ?? null,
        score_us_after: null, score_them_after: null, score_source: null, source: 'manual', review: 'unreviewed', version, deleted_at: stamp(s.deleted),
      } satisfies Segment)
    }
    const saved = input.events.map((e) => {
      const version = (events.get(e.id)?.version ?? 0) + 1
      const row: Tag = tag({ ...e, id: e.id, number: e.number ?? null, segment_id: null, frame: e.frame ?? null, version, deleted_at: stamp(e.deleted) })
      events.set(e.id, row)
      return row
    })
    const scored = rescore(live(periods), live(segments), live(events), serveCodes(volleyball))
    for (const s of scored.segments) segments.set(s.id, s)
    for (const p of scored.periods) periods.set(p.id, p)
    const touched = new Set([...input.segments.map((s) => s.id), ...input.periods.map((p) => p.id)])
    return {
      events: saved,
      segments: [...segments.values()].filter((s) => !s.deleted_at || touched.has(s.id)),
      periods: [...periods.values()].filter((p) => !p.deleted_at || touched.has(p.id)),
      stale: [],
    }
  }
  const current = (): Tagging => ({
    ...initial,
    events: live(events).sort((a, b) => a.t_ms - b.t_ms),
    segments: live(segments),
    periods: live(periods),
  })
  return { posts, changes, current }
}

const candidates: RosterCandidate[] = [
  { member_id: 103, name: 'Mia Keller', number: 3, consent: 'accepted' },
  { member_id: 115, name: 'Sara Frei', number: 15, consent: 'accepted' },
  { member_id: 116, name: 'Tim Huber', number: 16, consent: 'withdrawn' },
  { member_id: 117, name: 'Lea Graf', number: null, consent: null },
]

function show(
  data: Tagging = bundle({ periods: [period({ no: 1, start_ms: 0, first_serve: 'us' })] }),
  { down = false, postDelay = 0 } = {},
) {
  const server = fakeServer(data)
  const stub = stubFetch({
      [`/api/videos/${ID}`]: { body: video },
      [`/api/videos/${ID}/tagging`]: { body: () => server.current() },
      [`POST /api/videos/${ID}/changes`]: down ? { status: 503, body: { detail: 'Unavailable' } } : { body: server.changes },
      [`/api/videos/${ID}/roster/candidates`]: { body: candidates },
      [`PUT /api/videos/${ID}/roster`]: { body: ({ body }: { body: unknown }) => (body as { entries: unknown[] }).entries },
  })
  const fetch = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method === 'POST' && postDelay) await new Promise((resolve) => setTimeout(resolve, postDelay))
    return stub(input, init)
  })
  vi.stubGlobal('fetch', fetch)
  const view = renderRoute(<TagPage />, {
    path: '/videos/:id/tag',
    url: `/videos/${ID}/tag`,
    routes: [{ path: '/videos/:id', element: <p>Video page</p> }],
  })
  return { ...view, fetch, server }
}

async function ready() {
  await screen.findByRole('region', { name: 'Tag entry' })
  const player = document.querySelector('video')
  if (!player) throw new Error('no video element')
  return player
}

function keys(...list: (string | { key: string; shiftKey?: boolean; ctrlKey?: boolean })[]) {
  for (const k of list) fireEvent.keyDown(document.body, typeof k === 'string' ? { key: k } : k)
}

function type(text: string) {
  keys(...text.split(''))
}

beforeEach(() => {
  vi.stubGlobal('indexedDB', new IDBFactory())
  window.localStorage.clear()
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve())
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
})

describe('TagPage', () => {
  it('saves 7A# at the first keystroke, minus the skill lead time', async () => {
    const { server } = show()
    const player = await ready()

    player.currentTime = 12.5
    keys('7')
    player.currentTime = 13.4 // later keystrokes don't move the tag
    type('A#')
    expect(screen.getByText(/#7 Luca Brunner · Attack · # Kill/)).toBeInTheDocument()
    keys('Enter')

    expect(screen.getByText('Saved 7A#.')).toBeInTheDocument()
    const table = screen.getByRole('table', { name: 'Tags' })
    expect(within(table).getByText('7A#')).toBeInTheDocument()
    expect(screen.getByText('1 unsynced')).toBeInTheDocument()

    await waitFor(() => expect(server.posts).toHaveLength(1), { timeout: 3000 })
    expect(server.posts[0]?.events).toEqual([
      expect.objectContaining({
        code: 'A',
        t_ms: 12_200,
        side: 'us',
        number: 7,
        attributes: { grade: '#' },
        time_precision: 'live',
        base_version: 0,
        segment_id: null,
      }),
    ])
    expect(await screen.findByText('All changes saved')).toBeInTheDocument()
  })

  it('starts and ends a rally with R, with the winner from u/t, and shows the server score', async () => {
    const { server } = show()
    const player = await ready()

    player.currentTime = 20
    keys('r')
    expect(screen.getByText('Rally started at 0:20.0.')).toBeInTheDocument()
    expect(screen.getByText('Rally running since 0:20.0')).toBeInTheDocument()

    player.currentTime = 31
    keys('R')
    const proposal = screen.getByRole('group', { name: 'End the rally?' })
    expect(within(proposal).getByText(/No point yet/)).toBeInTheDocument()
    keys('t', 'u')
    expect(within(proposal).getByText(/Point for Us · then 1:0/)).toBeInTheDocument()
    keys('Enter')
    expect(screen.queryByRole('group', { name: 'End the rally?' })).not.toBeInTheDocument()
    expect(screen.getByText('No rally running')).toBeInTheDocument()

    await waitFor(() => expect(server.posts).toHaveLength(1), { timeout: 3000 })
    expect(server.posts[0]?.segments).toEqual([
      expect.objectContaining({ kind: 'rally', start_ms: 20_000, end_ms: 31_000, won_by: 'us', base_version: 0 }),
    ])
    expect(server.posts[0]?.segments[0]).not.toHaveProperty('serving')
    // After the sync the score is the server's.
    player.currentTime = 40
    fireEvent(player, new Event('timeupdate'))
    await screen.findByText('All changes saved')
    expect(screen.getByRole('region', { name: 'Match state' })).toHaveTextContent(/Set 1\s*1:0\s*Serve: Us/)
  })

  it('opens a rally with a serve and proposes its end after a kill', async () => {
    const { server } = show()
    const player = await ready()

    player.currentTime = 40
    type('a12S')
    keys('Enter')
    expect(screen.getByText('Rally running since 0:37.6')).toBeInTheDocument() // 40 s − 0.4 s lead − 2 s
    player.currentTime = 45
    type('7a#')
    keys('Enter')
    const proposal = screen.getByRole('group', { name: 'End the rally?' })
    expect(within(proposal).getByText(/Point for Us · then 1:0 · ends at 0:46.2/)).toBeInTheDocument()

    // Esc keeps the rally running; R proposes again, Enter ends it.
    keys('Escape')
    expect(screen.getByText('The rally keeps running.')).toBeInTheDocument()
    player.currentTime = 47
    keys('r', 'Enter')

    await waitFor(() => expect(server.posts.length).toBeGreaterThan(0), { timeout: 3000 })
    const sent = server.posts.flatMap((p) => p.segments)
    expect(sent).toEqual([expect.objectContaining({ kind: 'rally', start_ms: 37_600, end_ms: 47_000, won_by: 'us' })])
    const tags = server.posts.flatMap((p) => p.events)
    expect(tags.map((e) => [e.code, e.side, e.number, e.t_ms])).toEqual([
      ['S', 'them', 12, 39_600],
      ['A', 'us', 7, 44_700],
    ])
  })

  it('switches side with Tab, cancels with Esc, and flags numbers not on the roster', async () => {
    show()
    await ready()
    type('12')
    keys('Tab')
    expect(screen.getByText('a12')).toBeInTheDocument()
    expect(screen.getByText(/Them #12/)).toBeInTheDocument()
    keys('Escape')
    expect(screen.queryByText('a12')).not.toBeInTheDocument()

    type('99')
    expect(screen.getByText('Not on the roster')).toBeInTheDocument()
    keys('Enter') // no skill yet
    expect(screen.getByText("Can't save 99: The skill letter is missing.")).toBeInTheDocument()
    type('X')
    expect(screen.getByText('X is not a skill')).toBeInTheDocument()
  })

  it('undoes and redoes its own changes', async () => {
    const { server } = show()
    const player = await ready()
    player.currentTime = 10
    type('5R+')
    keys('Enter')
    const table = () => screen.queryByRole('table', { name: 'Tags' })
    expect(within(table() as HTMLElement).getByText('5R+')).toBeInTheDocument()

    keys({ key: 'z', ctrlKey: true })
    expect(screen.getByText('Undone.')).toBeInTheDocument()
    expect(table()).not.toBeInTheDocument()
    keys({ key: 'Z', ctrlKey: true, shiftKey: true })
    expect(within(table() as HTMLElement).getByText('5R+')).toBeInTheDocument()

    await waitFor(() => expect(server.posts).toHaveLength(1), { timeout: 3000 })
    // Coalesced in the outbox: one change with the last state.
    expect(server.posts[0]?.events).toEqual([expect.objectContaining({ code: 'R', attributes: { grade: '+' } })])
    expect(server.posts[0]?.events[0]?.deleted).toBeFalsy()
  })

  it('adds a timeout with T and opens it for editing', async () => {
    const { server } = show()
    const player = await ready()
    player.currentTime = 600
    keys('t')
    expect(screen.getByText('Timeout added. Adjust it below the timeline.')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Timeout' })).toBeInTheDocument()
    await waitFor(() => expect(server.posts).toHaveLength(1), { timeout: 3000 })
    expect(server.posts[0]?.segments).toEqual([expect.objectContaining({ kind: 'timeout', start_ms: 600_000, end_ms: 630_000 })])
  })

  it('selects a tag, snaps it to the frame (F) and deletes it with undo', async () => {
    const tagged = tag({ id: '00000000-0000-4000-8000-00000000c001', t_ms: 15_000, code: 'A', attributes: { grade: '+' }, version: 3 })
    const { server } = show(bundle({ events: [tagged] }))
    const player = await ready()
    fireEvent.click(screen.getByRole('button', { name: 'Go to 7A+ at 0:15.0' }))
    expect(player.currentTime).toBe(15)

    player.currentTime = 15.24
    keys('f')
    expect(screen.getByText('7A+ moved to this frame.')).toBeInTheDocument()
    await waitFor(() => expect(server.posts).toHaveLength(1), { timeout: 3000 })
    expect(server.posts[0]?.events[0]).toMatchObject({ t_ms: 15_240, frame: 762, time_precision: 'frame', base_version: 3 })

    fireEvent.click(screen.getByRole('button', { name: 'Delete 7A+' }))
    expect(screen.getByText('Deleted 7A+.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Undo' }))
    expect(screen.getByRole('button', { name: 'Edit 7A+' })).toBeInTheDocument()
  })

  it('selects with the arrows, sets a clip window with [ ] C, and deletes with Delete only', async () => {
    const first = tag({ id: '00000000-0000-4000-8000-00000000c101', t_ms: 10_000, code: 'R', number: 11, member_id: 111, attributes: { grade: '+' } })
    const second = tag({ id: '00000000-0000-4000-8000-00000000c102', t_ms: 20_000, code: 'A', number: 7, attributes: { grade: '#' } })
    const { server } = show(bundle({ events: [first, second] }))
    const player = await ready()
    player.currentTime = 15
    keys('ArrowDown') // the first tag after the playhead
    expect(screen.getByRole('row', { selected: true })).toHaveTextContent('7A#')
    keys('ArrowUp')
    expect(screen.getByRole('row', { selected: true })).toHaveTextContent('11R+')

    player.currentTime = 7
    keys('[')
    player.currentTime = 13.5
    keys(']', 'c')
    expect(screen.getByText('Clip window set for 11R+.')).toBeInTheDocument()

    keys('Backspace')
    expect(screen.getByRole('row', { selected: true })).toHaveTextContent('11R+')
    keys('Delete')
    expect(screen.getByText('Deleted 11R+.')).toBeInTheDocument()
    expect(screen.queryByText('11R+')).not.toBeInTheDocument()

    await waitFor(() => expect(server.posts).toHaveLength(1), { timeout: 3000 })
    expect(server.posts[0]?.events).toEqual([
      expect.objectContaining({ id: first.id, clip_pre_ms: 3000, clip_post_ms: 3500, deleted: true, base_version: 1 }),
    ])
  })

  it('filters the tag table by player, skill, grade and set', async () => {
    const one = period({ no: 1, start_ms: 0, end_ms: 100_000 })
    const two = period({ no: 2, start_ms: 100_000 })
    show(
      bundle({
        periods: [one, two],
        events: [
          tag({ t_ms: 10_000, code: 'S', side: 'them', number: 12, member_id: null, attributes: { grade: '-' } }),
          tag({ t_ms: 12_000, code: 'R', number: 11, member_id: 111, attributes: { grade: '#' } }),
          tag({ t_ms: 15_000, code: 'A', number: 7, attributes: { grade: '#' } }),
          tag({ t_ms: 115_000, code: 'A', number: 7, attributes: { grade: '=' } }),
          tag({ t_ms: 118_000, code: 'B', number: 3, member_id: 103, attributes: { grade: '#' } }),
        ],
      }),
    )
    await ready()
    const rows = () => within(screen.getByRole('table', { name: 'Tags' })).getAllByRole('row').slice(1)
    expect(rows()).toHaveLength(5)

    const tags = screen.getByRole('region', { name: 'Tags' })
    fireEvent.change(within(tags).getByLabelText('Player'), { target: { value: 'us:7' } })
    expect(rows().map((r) => within(r).getAllByRole('cell')[2]?.textContent)).toEqual(['7A#', '7A='])
    expect(within(tags).getByText('2 of 5')).toBeInTheDocument()

    fireEvent.change(within(tags).getByLabelText('Grade'), { target: { value: '#' } })
    expect(rows()).toHaveLength(1)
    fireEvent.change(within(tags).getByLabelText('Player'), { target: { value: '' } })
    fireEvent.change(within(tags).getByLabelText('Set'), { target: { value: two.id } })
    expect(rows().map((r) => within(r).getAllByRole('cell')[2]?.textContent)).toEqual(['3B#'])
    fireEvent.change(within(tags).getByLabelText('Skill'), { target: { value: 'R' } })
    expect(within(tags).getByText('No tags match the filters.')).toBeInTheDocument()

    fireEvent.click(within(tags).getByRole('button', { name: 'Clear filters' }))
    expect(rows()).toHaveLength(5)
  })

  it('shows a tag without a name once its player is unlinked', async () => {
    show(
      bundle({
        roster: [{ side: 'us', number: 7, member_id: null, position: null, is_libero: false, name: null }],
        events: [tag({ t_ms: 5_000, number: 7, member_id: null, attributes: { grade: '#' } })],
      }),
    )
    await ready()
    const row = within(screen.getByRole('table', { name: 'Tags' })).getAllByRole('row')[1] as HTMLElement
    expect(within(row).getAllByRole('cell')[3]).toHaveTextContent(/^#7$/)
  })

  it('compares set scores with the official ones', async () => {
    show(
      bundle({
        periods: [
          period({ no: 1, start_ms: 0, end_ms: 1_500_000, final_us: 25, final_them: 21 }),
          period({ no: 2, start_ms: 1_600_000, end_ms: 3_000_000, final_us: 23, final_them: 25 }),
          period({ no: 3, start_ms: 3_100_000, final_us: 3, final_them: 1 }), // running: not compared
        ],
        official_sets: [
          [25, 21],
          [25, 23],
          [25, 20],
        ],
      }),
    )
    await ready()
    expect(screen.getByText('Official score 25:23')).toBeInTheDocument()
    expect(screen.getAllByText(/Official score/)).toHaveLength(1)
  })

  it("edits the roster: only the team's players who agreed to filming can be added", async () => {
    const { fetch } = show()
    await ready()
    fireEvent.click(within(screen.getByRole('region', { name: 'Roster' })).getByRole('button', { name: 'Edit' }))
    const dialog = screen.getByRole('dialog', { name: 'Roster of this video' })
    const add = within(dialog).getByLabelText('Add')
    await within(dialog).findByRole('option', { name: '#15 Sara Frei' })
    // Mia is on the list already; the others are shown with the reason, not selectable.
    expect(within(dialog).queryByRole('option', { name: '#3 Mia Keller' })).not.toBeInTheDocument()
    expect(within(dialog).getByRole('option', { name: '#16 Tim Huber: consent withdrawn' })).toBeDisabled()
    expect(within(dialog).getByRole('option', { name: 'Lea Graf: has not agreed to filming' })).toBeDisabled()

    fireEvent.change(add, { target: { value: '115' } })
    expect(within(dialog).getByText('Sara Frei')).toBeInTheDocument()
    fireEvent.change(within(dialog).getByLabelText('Opponent numbers'), { target: { value: '4, 12 x' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(within(dialog).getByRole('alert')).toHaveTextContent('Opponent numbers are 0 to 99')

    fireEvent.change(within(dialog).getByLabelText('Opponent numbers'), { target: { value: '4, 12' } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Save' }))
    expect(await screen.findByText('Roster saved.')).toBeInTheDocument()
    const put = fetch.mock.calls.find(([, init]) => init?.method === 'PUT')
    expect(JSON.parse(String(put?.[1]?.body)).entries).toEqual([
      { side: 'us', number: 3, member_id: 103, position: 'setter', is_libero: false },
      { side: 'us', number: 7, member_id: 107, position: 'outside', is_libero: false },
      { side: 'us', number: 11, member_id: 111, position: 'libero', is_libero: true },
      { side: 'us', number: 15, member_id: 115, position: null, is_libero: false },
      { side: 'them', number: 4, is_libero: false },
      { side: 'them', number: 12, is_libero: false },
    ])
  })

  it('opens the key list with ?', async () => {
    show()
    await ready()
    keys('?')
    const dialog = screen.getByRole('dialog', { name: 'Keys' })
    expect(within(dialog).getByText('Kill')).toBeInTheDocument()
    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })

  it('asks for a bigger screen on phones', async () => {
    vi.stubGlobal('matchMedia', (query: string) => ({ matches: false, media: query, addEventListener() {}, removeEventListener() {} }))
    show()
    expect(await screen.findByText(/Tagging needs a larger screen/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to the video' })).toHaveAttribute('href', `/videos/${ID}`)
  })

  it('says who may tag', async () => {
    show(bundle({ can_tag: false }))
    expect(await screen.findByText("Only the team's coaches and sport admins tag videos.")).toBeInTheDocument()
  })

  it('shows the saved tags when coming back, not a cached copy from before', async () => {
    const { router, server } = show()
    const player = await ready()
    player.currentTime = 50
    type('9B#')
    keys('Enter')
    fireEvent.click(screen.getByRole('link', { name: 'Back to the video' }))
    expect(await screen.findByText('Video page')).toBeInTheDocument()
    await waitFor(() => expect(server.posts).toHaveLength(1)) // leaving sends at once

    await router.navigate(`/videos/${ID}/tag`)
    await ready()
    expect(within(screen.getByRole('table', { name: 'Tags' })).getByText('9B#')).toBeInTheDocument()
  })

  it('keeps the session when the window is narrow for a moment (a full-page screenshot does that)', async () => {
    // A media query that tests can flip, like Chromium reporting a 1×1 viewport while it captures.
    let wide = true
    const listeners = new Set<() => void>()
    vi.stubGlobal('matchMedia', (query: string) => ({
      get matches() {
        return wide
      },
      media: query,
      addEventListener: (_: string, listener: () => void) => listeners.add(listener),
      removeEventListener: (_: string, listener: () => void) => listeners.delete(listener),
    }))
    const { server } = show()
    const player = await ready()
    player.currentTime = 12
    type('7A#')
    keys('Enter')
    await waitFor(() => expect(server.posts).toHaveLength(1), { timeout: 3000 })
    await screen.findByText('All changes saved')

    for (const value of [false, true]) {
      wide = value
      act(() => listeners.forEach((listener) => listener()))
    }
    expect(screen.queryByText(/Tagging needs a larger screen/)).not.toBeInTheDocument()
    expect(document.querySelector('video')).toBe(player)
    expect(within(screen.getByRole('table', { name: 'Tags' })).getByText('7A#')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Match state' })).toHaveTextContent('Set 1')
  })

  it('waits for a just-closed session to finish sending before loading again', async () => {
    const { router, server, fetch } = show(undefined, { postDelay: 300 })
    const player = await ready()
    player.currentTime = 50
    type('9B#')
    keys('Enter')
    // Leave and come straight back, before the 1 s debounce: the closing session sends at once.
    fireEvent.click(screen.getByRole('link', { name: 'Back to the video' }))
    await screen.findByText('Video page')
    await router.navigate(`/videos/${ID}/tag`)
    await ready()

    expect(within(screen.getByRole('table', { name: 'Tags' })).getByText('9B#')).toBeInTheDocument()
    const calls = fetch.mock.calls.map(([path, init]) => `${init?.method ?? 'GET'} ${String(path)}`)
    expect(calls.filter((c) => c.startsWith('POST'))).toHaveLength(1)
    // The data was loaded again only once the closing session's batch had been answered.
    expect(server.posts).toHaveLength(1)
    expect(calls.at(-1)).toBe(`GET /api/videos/${ID}/tagging`)
    await new Promise((resolve) => setTimeout(resolve, 1200))
    expect(server.posts).toHaveLength(1) // nothing sent twice
  })

  it('keeps unsent changes over a reload and sends them on the next visit', async () => {
    const first = show(undefined, { down: true })
    const player = await ready()
    player.currentTime = 30
    type('3E+')
    keys('Enter')
    await waitFor(() => expect(screen.getByText(/Couldn't save, trying again/)).toBeInTheDocument(), { timeout: 3000 })
    first.unmount()

    const second = show()
    await ready()
    // The queued tag shows over the server's data at once and is sent.
    expect(within(screen.getByRole('table', { name: 'Tags' })).getByText('3E+')).toBeInTheDocument()
    await waitFor(() => expect(second.server.posts).toHaveLength(1), { timeout: 3000 })
    expect(second.server.posts[0]?.events).toEqual([expect.objectContaining({ code: 'E', number: 3, t_ms: 29_800 })])
    expect(await screen.findByText('All changes saved')).toBeInTheDocument()
  })
})
