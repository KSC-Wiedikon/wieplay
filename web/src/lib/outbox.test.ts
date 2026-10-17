import 'fake-indexeddb/auto'

import { openDB } from 'idb'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError, ForbiddenError } from './api'
import { chunk, LEGACY_DB, Outbox, type Change, type OutboxItem, type OutboxOptions } from './outbox'
import type { ChangesIn, ChangesOut, EventIn } from './types'

const VIDEO = '00000000-0000-4000-8000-0000000000aa'
let dbCount = 0
const opened: Outbox[] = []

function event(id: string, patch: Partial<EventIn> = {}): Change {
  return { kind: 'event', payload: { id, base_version: 0, code: 'A', t_ms: 1000, time_precision: 'live', side: 'us', number: 7, attributes: { grade: '#' }, ...patch } }
}

const ok = (body: ChangesIn): ChangesOut => ({
  events: body.events.map((e) => ({
    id: e.id,
    code: e.code,
    t_ms: e.t_ms,
    frame: null,
    time_precision: e.time_precision,
    side: e.side,
    number: e.number ?? null,
    member_id: null,
    attributes: e.attributes,
    segment_id: null,
    clip_pre_ms: null,
    clip_post_ms: null,
    note: null,
    source: 'manual',
    review: 'unreviewed',
    version: e.base_version + 1,
    updated_at: '2026-10-15T18:00:00Z',
    deleted_at: e.deleted ? '2026-10-15T18:00:00Z' : null,
  })),
  segments: [],
  periods: [],
  stale: [],
})

async function open(options: Partial<OutboxOptions> & { dbName: string }) {
  const outbox = await Outbox.open({
    videoId: VIDEO,
    send: vi.fn(async (body: ChangesIn) => ok(body)),
    debounceMs: 60_000, // the tests flush by hand
    backoffMs: 5,
    ...options,
  })
  opened.push(outbox)
  return outbox
}

function newDb() {
  dbCount += 1
  return `outbox-test-${dbCount}`
}

async function stored(dbName: string): Promise<OutboxItem[]> {
  const db = await openDB(dbName, 1)
  const all = (await db.getAll('changes')) as OutboxItem[]
  db.close()
  return all
}

afterEach(() => {
  while (opened.length) opened.pop()?.close()
})

describe('Outbox', () => {
  it('writes changes to IndexedDB first, then sends them in one batch', async () => {
    const dbName = newDb()
    const onSynced = vi.fn()
    const outbox = await open({ dbName, onSynced })
    await outbox.enqueue([event('e1'), event('e2'), { kind: 'segment', payload: { id: 's1', base_version: 0, kind: 'rally', start_ms: 0, end_ms: 5000, won_by: 'us' } }])
    expect(outbox.getState()).toMatchObject({ pending: 3, phase: 'waiting', persistent: true })
    expect((await stored(dbName)).map((i) => i.key).sort()).toEqual([`${VIDEO}:event:e1`, `${VIDEO}:event:e2`, `${VIDEO}:segment:s1`])

    await outbox.flush()
    const send = vi.mocked(outbox['options'].send)
    expect(send).toHaveBeenCalledTimes(1)
    const body = send.mock.calls[0]?.[0] as ChangesIn
    expect(body.events.map((e) => e.id)).toEqual(['e1', 'e2'])
    expect(body.segments.map((s) => s.id)).toEqual(['s1'])
    expect(onSynced).toHaveBeenCalledTimes(1)
    expect(outbox.getState()).toMatchObject({ pending: 0, phase: 'idle', online: true })
    expect(outbox.items()).toEqual([])
    expect(await stored(dbName)).toEqual([])
  })

  it('keeps only the last state of an id', async () => {
    const dbName = newDb()
    const outbox = await open({ dbName })
    await outbox.enqueue([event('e1', { attributes: { grade: '+' } })])
    await outbox.enqueue([event('e1', { attributes: { grade: '#' } })])
    await outbox.enqueue([event('e1', { attributes: { grade: '#' }, deleted: true })])
    expect(outbox.items()).toHaveLength(1)
    expect(await stored(dbName)).toHaveLength(1)

    await outbox.flush()
    const body = vi.mocked(outbox['options'].send).mock.calls[0]?.[0] as ChangesIn
    expect(body.events).toEqual([expect.objectContaining({ id: 'e1', deleted: true, attributes: { grade: '#' } })])
  })

  it('picks up what an earlier page left and sends it', async () => {
    const dbName = newDb()
    const first = await open({ dbName })
    await first.enqueue([event('e1'), event('e2', { t_ms: 2000 })])
    first.close()

    const send = vi.fn(async (body: ChangesIn) => ok(body))
    const second = await open({ dbName, send, debounceMs: 0 })
    expect(second.items().map((i) => i.id)).toEqual(['e1', 'e2'])
    expect(second.getState().pending).toBe(2)
    // open() schedules the resend by itself.
    await vi.waitFor(() => expect(send).toHaveBeenCalledTimes(1))
    await vi.waitFor(() => expect(second.getState().phase).toBe('idle'))
    expect(await stored(dbName)).toEqual([])
  })

  it('takes over what the old app (Nomal) left queued, every video, and removes its database', async () => {
    const legacy = await openDB(LEGACY_DB, 1, {
      upgrade(db) {
        db.createObjectStore('changes', { keyPath: 'key' }).createIndex('videoId', 'videoId')
      },
    })
    const queued = (video: string, id: string, seq: number) =>
      ({ ...event(id), key: `${video}:event:${id}`, videoId: video, id, seq, queuedAt: 1 }) as OutboxItem
    await legacy.put('changes', queued(VIDEO, 'e1', 1))
    await legacy.put('changes', queued('other', 'e2', 2))
    legacy.close()

    // A send that never answers: the taken-over change stays queued for the checks below.
    const outbox = await Outbox.open({ videoId: VIDEO, send: vi.fn(() => new Promise<ChangesOut>(() => {})), debounceMs: 60_000 })
    opened.push(outbox)
    expect(outbox.items().map((i) => i.id)).toEqual(['e1'])
    expect((await indexedDB.databases()).some((d) => d.name === LEGACY_DB)).toBe(false)
    expect((await stored('wieplay-outbox')).map((i) => i.id).sort()).toEqual(['e1', 'e2'])
  })

  it('keeps other videos out', async () => {
    const dbName = newDb()
    const other = await Outbox.open({ videoId: 'other', send: vi.fn(), dbName, debounceMs: 60_000 })
    opened.push(other)
    await other.enqueue([event('x1')])
    const outbox = await open({ dbName })
    expect(outbox.items()).toEqual([])
  })

  it('retries with back-off when the network is down, and keeps the items meanwhile', async () => {
    const dbName = newDb()
    let down = true
    const send = vi.fn(async (body: ChangesIn) => {
      if (down) throw new TypeError('Failed to fetch')
      return ok(body)
    })
    const outbox = await open({ dbName, send })
    await outbox.enqueue([event('e1')])
    await outbox.flush()
    expect(outbox.getState()).toMatchObject({ phase: 'retrying', online: false, pending: 1 })
    expect(await stored(dbName)).toHaveLength(1)

    await vi.waitFor(() => expect(send.mock.calls.length).toBeGreaterThanOrEqual(2))
    down = false
    await vi.waitFor(() => expect(outbox.getState()).toMatchObject({ phase: 'idle', online: true, pending: 0 }), { timeout: 2000 })
    expect(await stored(dbName)).toEqual([])
  })

  it('retries after a server error too', async () => {
    const dbName = newDb()
    const send = vi
      .fn<(body: ChangesIn) => Promise<ChangesOut>>()
      .mockRejectedValueOnce(new ApiError(502, 'Bad gateway'))
      .mockImplementation(async (body) => ok(body))
    const outbox = await open({ dbName, send })
    await outbox.enqueue([event('e1')])
    await outbox.flush()
    expect(outbox.getState()).toMatchObject({ phase: 'retrying', online: true })
    await vi.waitFor(() => expect(outbox.getState().phase).toBe('idle'))
    expect(send).toHaveBeenCalledTimes(2)
  })

  it('sends at once when the browser comes back online', async () => {
    const dbName = newDb()
    const send = vi
      .fn<(body: ChangesIn) => Promise<ChangesOut>>()
      .mockRejectedValueOnce(new TypeError('offline'))
      .mockImplementation(async (body) => ok(body))
    const outbox = await open({ dbName, send, backoffMs: 60_000 })
    await outbox.enqueue([event('e1')])
    await outbox.flush()
    expect(outbox.getState().online).toBe(false)
    window.dispatchEvent(new Event('online'))
    await vi.waitFor(() => expect(outbox.getState()).toMatchObject({ phase: 'idle', online: true }))
  })

  it('drops an item the API refuses (422), reports it, and saves the rest', async () => {
    const dbName = newDb()
    const send = vi.fn(async (body: ChangesIn) => {
      if (body.events.some((e) => e.id === 'bad')) throw new ApiError(422, "unknown skill 'X'")
      return ok(body)
    })
    const onSynced = vi.fn()
    const outbox = await open({ dbName, send, onSynced })
    await outbox.enqueue([event('e1'), event('e2'), event('bad', { code: 'X' }), event('e3'), event('e4')])
    await outbox.flush()

    const state = outbox.getState()
    expect(state.pending).toBe(0)
    expect(state.phase).toBe('idle')
    expect(state.rejected).toEqual([{ item: expect.objectContaining({ id: 'bad' }), detail: "unknown skill 'X'" }])
    const saved = onSynced.mock.calls.flatMap(([out]) => (out as ChangesOut).events.map((e) => e.id)).sort()
    expect(saved).toEqual(['e1', 'e2', 'e3', 'e4'])
    expect(await stored(dbName)).toEqual([])

    outbox.dismissNotes()
    expect(outbox.getState().rejected).toEqual([])
  })

  it('stops when not allowed, keeps the items, and sends them on retry', async () => {
    const dbName = newDb()
    const send = vi
      .fn<(body: ChangesIn) => Promise<ChangesOut>>()
      .mockRejectedValueOnce(new ForbiddenError('Only the coaches tag videos'))
      .mockImplementation(async (body) => ok(body))
    const outbox = await open({ dbName, send, debounceMs: 0 })
    await outbox.enqueue([event('e1')])
    await vi.waitFor(() => expect(outbox.getState().phase).toBe('blocked'))
    expect(outbox.getState().blocked).toEqual({ status: 403, detail: 'Only the coaches tag videos' })

    // More changes queue up but are not sent while blocked.
    await outbox.enqueue([event('e2')])
    await new Promise((resolve) => setTimeout(resolve, 20))
    expect(send).toHaveBeenCalledTimes(1)
    expect(outbox.getState().pending).toBe(2)

    await outbox.retry()
    expect(send).toHaveBeenCalledTimes(2)
    expect(outbox.getState()).toMatchObject({ phase: 'idle', pending: 0, blocked: null })
  })

  it('keeps a change made while its older state was being sent', async () => {
    const dbName = newDb()
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => (release = resolve))
    const send = vi.fn(async (body: ChangesIn) => {
      await gate
      return ok(body)
    })
    const outbox = await open({ dbName, send })
    await outbox.enqueue([event('e1', { attributes: { grade: '+' } })])
    const sending = outbox.flush()
    await outbox.enqueue([event('e1', { attributes: { grade: '=' } })])
    release()
    await sending

    expect(outbox.items()).toEqual([expect.objectContaining({ id: 'e1', payload: expect.objectContaining({ attributes: { grade: '=' } }) })])
    const kept = await stored(dbName)
    expect(kept).toHaveLength(1)
    expect((kept[0] as OutboxItem & { kind: 'event' }).payload.attributes).toEqual({ grade: '=' })

    await outbox.flush()
    expect(outbox.items()).toEqual([])
  })

  it('drops a change someone else overtook (stale), with what was queued after it, and reports it', async () => {
    const dbName = newDb()
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => (release = resolve))
    const send = vi.fn(async (body: ChangesIn): Promise<ChangesOut> => {
      await gate
      const out = ok(body)
      // The server has version 5 of e1 from another coach: skipped, its state returned.
      return {
        ...out,
        events: out.events.map((e) => (e.id === 'e1' ? { ...e, attributes: { grade: '-' }, version: 5 } : e)),
        stale: ['e1'],
      }
    })
    const onSynced = vi.fn()
    const outbox = await open({ dbName, send, onSynced })
    await outbox.enqueue([event('e1', { base_version: 3 }), event('e2')])
    const sending = outbox.flush()
    await outbox.enqueue([event('e1', { base_version: 3, attributes: { grade: '=' } })])
    release()
    await sending

    expect(onSynced).toHaveBeenCalledWith(expect.objectContaining({ stale: ['e1'] }), expect.any(Array))
    expect(outbox.items()).toEqual([])
    expect(outbox.getState().stale).toEqual([expect.objectContaining({ id: 'e1' })])
    expect(await stored(dbName)).toEqual([])
    outbox.dismissNotes()
    expect(outbox.getState().stale).toEqual([])
  })

  it('bases a change made during the send on the version the answer brings', async () => {
    const dbName = newDb()
    let release: () => void = () => {}
    const gate = new Promise<void>((resolve) => (release = resolve))
    const send = vi.fn(async (body: ChangesIn) => {
      await gate
      return ok(body)
    })
    const outbox = await open({ dbName, send })
    await outbox.enqueue([event('e1', { base_version: 2, attributes: { grade: '+' } })])
    const sending = outbox.flush()
    await outbox.enqueue([event('e1', { base_version: 2, attributes: { grade: '#' } })])
    release()
    await sending

    // The first change made version 3, so the queued one is now based on 3.
    const [item] = outbox.items()
    expect(item?.payload).toMatchObject({ base_version: 3, attributes: { grade: '#' } })
    await vi.waitFor(async () => expect((await stored(dbName))[0]?.payload.base_version).toBe(3))
    await outbox.flush()
    const second = send.mock.calls[1]?.[0] as ChangesIn
    expect(second.events[0]).toMatchObject({ id: 'e1', base_version: 3 })
  })

  it('works in memory without IndexedDB', async () => {
    vi.stubGlobal('indexedDB', undefined)
    const outbox = await open({ dbName: newDb() })
    await outbox.enqueue([event('e1')])
    expect(outbox.getState()).toMatchObject({ persistent: false, pending: 1 })
    await outbox.flush()
    expect(outbox.getState().pending).toBe(0)
  })

  it('tells listeners about every change of state', async () => {
    const outbox = await open({ dbName: newDb() })
    const phases: string[] = []
    const unsubscribe = outbox.subscribe(() => phases.push(outbox.getState().phase))
    await outbox.enqueue([event('e1')])
    await outbox.flush()
    unsubscribe()
    await outbox.enqueue([event('e2')])
    expect(phases).toEqual(['waiting', 'sending', 'sending', 'idle'])
  })
})

describe('chunk', () => {
  const item = (kind: 'event' | 'segment' | 'period', n: number): OutboxItem =>
    ({ kind, id: `${kind}${n}`, key: `${kind}${n}`, videoId: VIDEO, seq: n, queuedAt: 0, payload: { id: `${kind}${n}` } }) as unknown as OutboxItem

  it('stays within the API limits and sends sets and segments first', () => {
    const items = [
      ...Array.from({ length: 501 }, (_, n) => item('event', n)),
      item('segment', 1000),
      ...Array.from({ length: 21 }, (_, n) => item('period', 2000 + n)),
    ]
    const chunks = chunk(items)
    expect(chunks).toHaveLength(2)
    expect(chunks[0]?.filter((i) => i.kind === 'event')).toHaveLength(500)
    expect(chunks[0]?.filter((i) => i.kind === 'period')).toHaveLength(20)
    expect(chunks[0]?.slice(0, 21).map((i) => i.kind)).toEqual([...Array(20).fill('period'), 'segment'])
    expect(chunks[1]?.map((i) => i.kind)).toEqual(['period', 'event'])
  })
})
