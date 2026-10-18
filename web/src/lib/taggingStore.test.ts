import { beforeEach, describe, expect, it, vi } from 'vitest'
import { basketball, bundle, period, rally, tag, volleyball } from '../test/tagging'
import { Outbox, type OutboxItem } from './outbox'
import { eventToIn, mergeView, periodToIn, segmentToIn, TaggingStore } from './taggingStore'
import type { ChangesIn, ChangesOut, Tag } from './types'

const VIDEO = '00000000-0000-4000-8000-0000000000cc'

async function setup(data = bundle()) {
  vi.stubGlobal('indexedDB', undefined) // memory outbox
  const send = vi.fn<(body: ChangesIn) => Promise<ChangesOut>>()
  const store = new TaggingStore(VIDEO, data)
  const outbox = await Outbox.open({
    videoId: VIDEO,
    send,
    onSynced: (out) => store.applySynced(out),
    debounceMs: 60_000,
  })
  store.attach(outbox)
  return { store, outbox, send }
}

const served = (tags: Tag[], extra: Partial<ChangesOut> = {}): ChangesOut => ({ events: tags, segments: [], periods: [], stale: [], ...extra })

beforeEach(() => window.localStorage.clear())

describe('mergeView', () => {
  it('lays queued changes over the server data, last state wins, deletes remove', () => {
    const a = tag({ t_ms: 1000, version: 2 })
    const b = tag({ t_ms: 2000, version: 1 })
    const base = { events: new Map([[a.id, a], [b.id, b]]), segments: new Map(), periods: new Map() }
    const items = [
      { kind: 'event', id: a.id, key: a.id, videoId: VIDEO, seq: 1, queuedAt: 0, payload: { ...eventToIn(a), attributes: { grade: '=' } } },
      { kind: 'event', id: b.id, key: b.id, videoId: VIDEO, seq: 2, queuedAt: 0, payload: { ...eventToIn(b), deleted: true } },
    ] as OutboxItem[]
    const view = mergeView(base, items, [], volleyball)
    expect(view.events.map((e) => [e.id, e.attributes.grade, e.version])).toEqual([[a.id, '=', 2]])
    expect(view.provisional).toBe(true)
  })

  it('scores queued rallies locally until the server answers', () => {
    const set = period({ no: 1, start_ms: 0, first_serve: 'us' })
    const r = rally({ start_ms: 1000, end_ms: 5000, won_by: 'us' })
    const base = { events: new Map(), segments: new Map(), periods: new Map([[set.id, set]]) }
    const items = [{ kind: 'segment', id: r.id, key: r.id, videoId: VIDEO, seq: 1, queuedAt: 0, payload: { id: r.id, base_version: 0, kind: 'rally', start_ms: 1000, end_ms: 5000, won_by: 'us' } }] as OutboxItem[]
    const view = mergeView(base, items, [], volleyball)
    expect(view.segments[0]).toMatchObject({ score_us_after: 1, score_them_after: 0, period_id: set.id })
  })

  it('scores queued basketball shots locally, a running score across quarters', () => {
    const q1 = period({ no: 1, start_ms: 0, end_ms: 600_000, final_us: 0, final_them: 0 })
    const q2 = period({ no: 2, start_ms: 700_000, final_us: 0, final_them: 0 })
    const base = { events: new Map(), segments: new Map(), periods: new Map([[q1.id, q1], [q2.id, q2]]) }
    const made = (id: string, t_ms: number, code: string, side: 'us' | 'them') =>
      ({ kind: 'event', id, key: id, videoId: VIDEO, seq: t_ms, queuedAt: 0, payload: { id, base_version: 0, code, t_ms, time_precision: 'live', side, number: 23, attributes: { grade: '+' } } }) as OutboxItem
    const view = mergeView(base, [made('e1', 10_000, 'S3', 'us'), made('e2', 20_000, 'S2', 'them'), made('e3', 710_000, 'FT', 'us')], [], basketball)
    expect(view.provisional).toBe(true)
    expect(view.periods.map((p) => [p.no, p.final_us, p.final_them])).toEqual([
      [1, 3, 2],
      [2, 4, 2],
    ])
  })
})

describe('TaggingStore', () => {
  it('bases each change on the version last received from the server', async () => {
    const t = tag({ t_ms: 1000, version: 4 })
    const { store, outbox } = await setup(bundle({ events: [t] }))
    store.commit([{ kind: 'event', payload: { ...eventToIn(t), attributes: { grade: '#' } } }])
    store.commit([{ kind: 'event', payload: { id: 'new-1', base_version: 99, code: 'S', t_ms: 0, time_precision: 'live', side: 'us', attributes: {} } }])
    const payloads = outbox.items().map((i) => [i.id, i.payload.base_version])
    expect(payloads).toEqual([
      [t.id, 4],
      ['new-1', 0],
    ])
  })

  it('undoes and redoes its own steps, including the open rally', async () => {
    const { store } = await setup()
    store.commit([{ kind: 'event', payload: { id: 'e1', base_version: 0, code: 'S', t_ms: 5000, time_precision: 'live', side: 'them', number: 12, attributes: {} } }], {
      id: 'r1',
      start_ms: 3000,
    })
    expect(store.getView().events).toHaveLength(1)
    expect(store.getView().openRally).toEqual({ id: 'r1', start_ms: 3000 })
    expect(JSON.parse(window.localStorage.getItem(`wieplay-open-rally:${VIDEO}`) ?? 'null')).toEqual({ id: 'r1', start_ms: 3000 })

    expect(store.undo()).toBe(true)
    expect(store.getView().events).toHaveLength(0)
    expect(store.getView().openRally).toBeNull()
    expect(store.getView().canRedo).toBe(true)

    expect(store.redo()).toBe(true)
    expect(store.getView().events).toHaveLength(1)
    expect(store.getView().openRally).toEqual({ id: 'r1', start_ms: 3000 })
    expect(store.undo() && store.undo()).toBe(false)
  })

  it('restores a deleted tag after the delete was saved, based on the version of the delete', async () => {
    const t = tag({ t_ms: 1000, version: 2 })
    const { store, outbox, send } = await setup(bundle({ events: [t] }))
    store.commit([{ kind: 'event', payload: { ...eventToIn(t), deleted: true } }])
    send.mockResolvedValueOnce(served([{ ...t, version: 3, deleted_at: '2026-10-15T19:00:00Z' }]))
    await outbox.flush()
    expect(store.getView().events).toEqual([])

    store.undo()
    expect(store.getView().events.map((e) => e.id)).toEqual([t.id])
    expect(outbox.items()[0]?.payload).toMatchObject({ id: t.id, base_version: 3 })
    expect(outbox.items()[0]?.payload.deleted).toBeFalsy()
  })

  it('restores a deleted rally with the version the server reports for the delete', async () => {
    const r = rally({ start_ms: 1000, end_ms: 5000, won_by: 'us', version: 4 })
    const { store, outbox, send } = await setup(bundle({ segments: [r] }))
    store.commit([{ kind: 'segment', payload: { id: r.id, base_version: 0, kind: 'rally', start_ms: 1000, end_ms: 5000, won_by: 'us', deleted: true } }])
    expect(outbox.items()[0]?.payload.base_version).toBe(4)
    // The answer lists the deleted rally because the batch touched it (another change bumped it twice).
    send.mockResolvedValueOnce(served([], { segments: [{ ...r, version: 6, deleted_at: '2026-10-15T19:00:00Z' }] }))
    await outbox.flush()
    expect(store.getView().segments).toEqual([])

    store.undo()
    expect(store.getView().segments.map((x) => x.id)).toEqual([r.id])
    expect(outbox.items()[0]?.payload).toMatchObject({ id: r.id, base_version: 6 })
  })

  it('hides a tag the server has deleted when a change to it was stale', async () => {
    const t = tag({ t_ms: 1000, version: 2 })
    const { store, outbox, send } = await setup(bundle({ events: [t] }))
    store.commit([{ kind: 'event', payload: { ...eventToIn(t), attributes: { grade: '=' } } }])
    send.mockResolvedValueOnce(served([{ ...t, version: 5, deleted_at: '2026-10-15T19:00:00Z' }], { stale: [t.id] }))
    await outbox.flush()
    expect(store.getView().events).toEqual([])
  })

  it('sends no rally link and shows the one the server made', async () => {
    const r = rally({ start_ms: 1000, end_ms: 5000 })
    const t = tag({ t_ms: 2000, segment_id: r.id })
    const { store, outbox } = await setup(bundle({ segments: [r], events: [t] }))
    store.commit([{ kind: 'event', payload: { ...eventToIn(t), attributes: { grade: '#' } } }])
    expect(outbox.items()[0]?.payload).toMatchObject({ segment_id: null })
    expect(store.getView().events[0]?.segment_id).toBe(r.id)
  })

  it("takes over the server's state of a change it reports as stale", async () => {
    const t = tag({ t_ms: 1000, version: 2, attributes: { grade: '+' } })
    const { store, outbox, send } = await setup(bundle({ events: [t] }))
    store.commit([{ kind: 'event', payload: { ...eventToIn(t), attributes: { grade: '#' } } }])
    expect(store.getView().events[0]?.attributes).toEqual({ grade: '#' })
    send.mockResolvedValueOnce(served([{ ...t, version: 7, attributes: { grade: '-' } }], { stale: [t.id] }))
    await outbox.flush()
    expect(store.getView().events[0]).toMatchObject({ version: 7, attributes: { grade: '-' } })
    expect(outbox.getState().stale).toHaveLength(1)
  })

  it('uses the server scores once nothing is queued', async () => {
    const set = period({ no: 1, start_ms: 0 })
    const r = rally({ start_ms: 1000, end_ms: 5000, won_by: 'us', period_id: set.id, score_us_after: 9, score_them_after: 9 })
    const { store } = await setup(bundle({ periods: [set], segments: [r] }))
    // Not recomputed: whatever the server says is shown.
    expect(store.getView().segments[0]).toMatchObject({ score_us_after: 9, score_them_after: 9 })
    expect(store.getView().provisional).toBe(false)
  })

  it("keeps the coach's serve call out of a change unless it sets or clears it", async () => {
    const r = rally({ start_ms: 1000, end_ms: 5000, source: 'model', serving_manual: 'us', model_won_by: 'them', version: 2 })
    expect(segmentToIn(r)).not.toHaveProperty('serving_manual')
    const { store, outbox } = await setup(bundle({ segments: [r] }))
    store.commit([{ kind: 'segment', payload: { ...segmentToIn(r), serving_manual: 'them' } }])
    expect(store.getView().segments[0]).toMatchObject({ serving_manual: 'them', model_won_by: 'them', source: 'model' })
    // A later change that doesn't name the call keeps the queued one (the queue keeps the last state only).
    store.commit([{ kind: 'segment', payload: { ...segmentToIn(r), review: 'confirmed' } }])
    expect(outbox.items()[0]?.payload).toMatchObject({ review: 'confirmed', serving_manual: 'them' })
    store.undo()
    expect(outbox.items()[0]?.payload).toMatchObject({ review: 'unreviewed', serving_manual: 'them' })
    // Undoing the call sends the call from before.
    store.undo()
    expect(outbox.items()[0]?.payload).toMatchObject({ serving_manual: 'us' })
    expect(store.getView().segments[0]?.serving_manual).toBe('us')
  })

  it("keeps what the scoreboard log and the model say about a rally through a local change and its undo", async () => {
    const log = { log_won_by: 'us', log_score_us: 12, log_score_them: 10, log_at_ms: 9_500, model_missed_us: 1, model_missed_them: 0 } as const
    const r = rally({ start_ms: 1000, end_ms: 5000, source: 'model', version: 2, ...log })
    const { store } = await setup(bundle({ segments: [r] }))
    store.commit([{ kind: 'segment', payload: { ...segmentToIn(r), review: 'confirmed' } }])
    expect(store.getView().segments[0]).toMatchObject({ review: 'confirmed', ...log })
    store.undo()
    expect(store.getView().segments[0]).toMatchObject({ review: 'unreviewed', ...log })
  })

  it('sends a set with its review and starting rotation as they are', async () => {
    const set = period({ no: 1, start_ms: 0, source: 'model', review: 'unreviewed', start_rotation: 4, model_first_serve: 'them' })
    expect(periodToIn(set)).toMatchObject({ review: 'unreviewed', start_rotation: 4 })
    const { store } = await setup(bundle({ periods: [set] }))
    store.commit([{ kind: 'period', payload: { ...periodToIn(set), start_rotation: null, first_serve: 'us' } }])
    expect(store.getView().periods[0]).toMatchObject({ start_rotation: null, first_serve: 'us', source: 'model', model_first_serve: 'them' })
  })
})
