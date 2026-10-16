import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Outbox } from '../../lib/outbox'
import { Playhead } from '../../lib/playhead'
import { TaggingStore } from '../../lib/taggingStore'
import type { ChangesIn, ChangesOut, Segment } from '../../lib/types'
import { bundle, rally, volleyball } from '../../test/tagging'
import { TagController } from './controller'
import { NUDGE_MS, ReviewController } from './review'

const VIDEO = '00000000-0000-4000-8000-0000000000dd'
const suggestion = (id: string, start_ms: number, confidence: number, patch: Partial<Segment> = {}) =>
  rally({ id, start_ms, end_ms: start_ms + 8_000, source: 'model', confidence, model_run_id: 'run-1', version: 1, ...patch })

/** Four suggestions (one accepted already) and a rally marked by hand. */
function data() {
  return bundle({
    segments: [
      rally({ id: 'hand', start_ms: 1_000, end_ms: 6_000 }),
      suggestion('s1', 10_000, 0.95),
      suggestion('s2', 30_000, 0.6),
      suggestion('s3', 50_000, 0.92, { review: 'confirmed', version: 2 }),
      suggestion('s4', 70_000, 0.97),
    ],
  })
}

/** The API's /changes, for segments: saved with a new version; model rows keep their source. */
function server(initial: Segment[]) {
  const rows = new Map(initial.map((s) => [s.id, s]))
  return (body: ChangesIn): ChangesOut => {
    const touched: Segment[] = []
    for (const c of body.segments) {
      const prev = rows.get(c.id)
      const row: Segment = {
        ...(prev ?? rally({ id: c.id, start_ms: c.start_ms, end_ms: c.end_ms })),
        kind: c.kind,
        start_ms: c.start_ms,
        end_ms: c.end_ms,
        won_by: c.won_by ?? null,
        review: c.review ?? 'unreviewed',
        // Without the key the API keeps the coach's serve call.
        serving_manual: 'serving_manual' in c ? (c.serving_manual ?? null) : (prev?.serving_manual ?? null),
        version: (prev?.version ?? 0) + 1,
        deleted_at: c.deleted ? '2026-10-16T20:00:00Z' : null,
      }
      rows.set(c.id, row)
      touched.push(row)
    }
    return { events: [], segments: [...rows.values()].filter((s) => !s.deleted_at || touched.includes(s)), periods: [], stale: [] }
  }
}

let video: HTMLVideoElement

async function setup(initial = data(), { onLeave = () => {} } = {}) {
  const api = server(initial.segments)
  const send = vi.fn(async (body: ChangesIn) => api(body))
  const store = new TaggingStore(VIDEO, initial)
  const outbox = await Outbox.open({ videoId: VIDEO, send, onSynced: (out) => store.applySynced(out), debounceMs: 60_000 })
  store.attach(outbox)
  video = document.createElement('video')
  const playhead = new Playhead()
  playhead.attach(video)
  const tag = new TagController(store, playhead, volleyball, { duration_ms: 600_000 })
  tag.setFps(50)
  const review = new ReviewController(store, playhead, tag, { rejectedBefore: 1, onLeave })
  review.attach()
  review.start()
  const press = (key: string, mods: KeyboardEventInit = {}) => review.onKeyDown(new KeyboardEvent('keydown', { key, cancelable: true, ...mods }))
  /** Sends what is queued; the segments of that request. */
  const sent = async () => {
    await outbox.flush()
    return send.mock.calls.at(-1)?.[0].segments ?? []
  }
  const segment = (id: string) => store.getView().segments.find((s) => s.id === id)
  return { store, outbox, review, tag, press, sent, send, segment }
}

beforeEach(() => {
  vi.stubGlobal('indexedDB', undefined) // memory outbox
  window.localStorage.clear()
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve())
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
})

afterEach(() => vi.useRealTimers())

describe('ReviewController', () => {
  it('starts at the first open suggestion, 1 s before it', async () => {
    const { review } = await setup()
    expect(review.getState()).toMatchObject({ active: true, currentId: 's1' })
    expect(video.currentTime).toBe(9)
    expect(review.progress()).toEqual({ done: 2, total: 5 }) // s3 answered, one rejected before
  })

  it('Enter accepts: review "confirmed" through the outbox, and the list moves on', async () => {
    const { review, press, sent } = await setup()
    press('Enter')
    expect(review.getState().currentId).toBe('s2')
    expect(video.currentTime).toBe(29)
    expect(review.progress()).toEqual({ done: 3, total: 5 })
    expect(await sent()).toEqual([
      { id: 's1', base_version: 1, kind: 'rally', start_ms: 10_000, end_ms: 18_000, won_by: null, review: 'confirmed' },
    ])
  })

  it('X rejects: deleted with review "rejected" (a negative for the server), then the next one', async () => {
    const { review, press, sent, segment } = await setup()
    press('x')
    expect(segment('s1')).toBeUndefined()
    expect(review.getState().currentId).toBe('s2')
    expect(review.progress()).toEqual({ done: 3, total: 5 })
    expect(await sent()).toEqual([expect.objectContaining({ id: 's1', review: 'rejected', deleted: true, base_version: 1 })])
  })

  it('←/→ move the start, Shift the end, by 0.25 s; with Alt by one frame; the suggestion is then corrected', async () => {
    const { review, press, sent, segment } = await setup()
    press('ArrowRight')
    press('ArrowRight')
    expect(segment('s1')).toMatchObject({ start_ms: 10_000 + 2 * NUDGE_MS, review: 'corrected' })
    press('ArrowLeft', { shiftKey: true })
    press('ArrowRight', { altKey: true }) // one frame at 50 fps
    press('ArrowRight', { altKey: true, shiftKey: true })
    expect(segment('s1')).toMatchObject({ start_ms: 10_520, end_ms: 17_770 })
    // Paused, the player shows the boundary it moved.
    expect(video.currentTime).toBeCloseTo(17.77)
    // Still on screen: a correction is not an answer yet.
    expect(review.getState().currentId).toBe('s1')
    expect(await sent()).toEqual([
      { id: 's1', base_version: 1, kind: 'rally', start_ms: 10_520, end_ms: 17_770, won_by: null, review: 'corrected' },
    ])
    // Enter keeps the correction and goes on.
    press('Enter')
    expect(segment('s1')?.review).toBe('corrected')
    expect(review.getState().currentId).toBe('s2')
  })

  it("won't make a rally shorter than the minimum", async () => {
    const { review, press, segment } = await setup()
    for (let i = 0; i < 40; i += 1) press('ArrowLeft', { shiftKey: true })
    const s1 = segment('s1')
    expect(s1 && s1.end_ms - s1.start_ms).toBeGreaterThanOrEqual(200)
    expect(review.getState().notice?.key).toBe('tooShort')
  })

  it('M merges with the next suggestion: this one ends where that one ended, that one is merged into it', async () => {
    const { review, press, sent, segment } = await setup()
    press('m')
    expect(segment('s1')).toMatchObject({ start_ms: 10_000, end_ms: 38_000, review: 'corrected' })
    expect(segment('s2')).toBeUndefined()
    expect(review.getState().currentId).toBe('s1')
    const body = await sent()
    expect(body).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: 's1', end_ms: 38_000, review: 'corrected' }),
        expect.objectContaining({ id: 's2', review: 'merged', merged_into: 's1', deleted: true }),
      ]),
    )
    expect(body).toHaveLength(2)
    expect(body.find((s) => s.id === 's1')?.deleted).toBeUndefined()
  })

  it('S splits at the playhead; the second part (a new rally) comes next in the list', async () => {
    const { review, press, sent, segment, store } = await setup()
    video.currentTime = 14
    press('s')
    expect(segment('s1')).toMatchObject({ end_ms: 14_000, review: 'corrected' })
    const part = store.getView().segments.find((s) => s.start_ms === 14_000)
    if (!part) throw new Error('no second part')
    expect(part).toMatchObject({ kind: 'rally', end_ms: 18_000, source: 'manual' })
    const body = await sent()
    expect(body).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: 's1', end_ms: 14_000, review: 'corrected' }),
        { id: part.id, base_version: 0, kind: 'rally', start_ms: 14_000, end_ms: 18_000, won_by: null },
      ]),
    )
    press('Enter')
    expect(review.getState().currentId).toBe(part.id)
    press('Enter')
    expect(review.getState().currentId).toBe('s2')
  })

  it('S outside the rally explains instead of splitting', async () => {
    const { review, press, store } = await setup()
    video.currentTime = 40
    press('s')
    expect(review.getState().notice).toMatchObject({ key: 'cannotSplit', tone: 'error' })
    expect(store.getView().segments).toHaveLength(5)
  })

  it('U takes back the last answer and goes back to its suggestion', async () => {
    const { review, press, sent, segment } = await setup()
    press('Enter')
    press('x')
    expect(review.getState().currentId).toBe('s4')
    press('u')
    expect(segment('s2')).toMatchObject({ review: 'unreviewed' })
    expect(review.getState().currentId).toBe('s2')
    press('u')
    expect(segment('s1')).toMatchObject({ review: 'unreviewed' })
    expect(review.getState().currentId).toBe('s1')
    expect(review.progress()).toEqual({ done: 2, total: 5 })
    // The queue holds each suggestion's state from before: nothing changes on the server.
    expect(await sent()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: 's1', review: 'unreviewed' }),
        expect.objectContaining({ id: 's2', review: 'unreviewed' }),
      ]),
    )
    press('u')
    expect(review.getState().notice?.key).toBe('nothingToUndo')
  })

  it('undoes a rejection that was saved already: the suggestion comes back as one', async () => {
    const { review, press, sent, segment } = await setup()
    press('x')
    await sent()
    expect(segment('s1')).toBeUndefined()
    press('u')
    expect(segment('s1')).toMatchObject({ source: 'model', confidence: 0.95, review: 'unreviewed' })
    expect(review.getState().currentId).toBe('s1')
    expect(await sent()).toEqual([expect.objectContaining({ id: 's1', review: 'unreviewed', base_version: 2 })])
  })

  it('accepts all remaining with confidence ≥ 90% after a confirmation, in one undo step', async () => {
    const { review, sent, segment, press } = await setup()
    review.askAcceptAll()
    expect(review.getState().confirmAll).toBe(true)
    // Keys wait while the dialog asks.
    press('x')
    expect(segment('s1')).toBeDefined()
    review.confirmAcceptAll()
    expect(segment('s1')?.review).toBe('confirmed')
    expect(segment('s2')?.review).toBe('unreviewed') // 60%
    expect(segment('s4')?.review).toBe('confirmed')
    expect(review.getState().currentId).toBe('s2')
    const body = await sent()
    expect(body.map((s) => [s.id, s.review])).toEqual([
      ['s1', 'confirmed'],
      ['s4', 'confirmed'],
    ])
    press('u')
    expect(segment('s4')?.review).toBe('unreviewed')
  })

  it('ends with nothing on screen once every suggestion is answered', async () => {
    const { review, press } = await setup()
    press('Enter')
    press('Enter')
    press('Enter')
    expect(review.getState().currentId).toBeNull()
    expect(review.progress()).toEqual({ done: 5, total: 5 })
  })

  it('↑/↓ move through the list without answering; Esc leaves the review', async () => {
    const { review, press } = await setup()
    press('ArrowDown')
    press('ArrowDown')
    expect(review.getState().currentId).toBe('s3')
    press('ArrowUp')
    expect(review.getState().currentId).toBe('s2')
    press('Escape')
    expect(review.getState().active).toBe(false)
  })

  it('leaves Enter on a button focused from the keyboard to the button', async () => {
    const { review, segment } = await setup()
    const button = document.createElement('button')
    document.body.append(button)
    const event = new KeyboardEvent('keydown', { key: 'Enter', bubbles: true, cancelable: true })
    button.dispatchEvent(event)
    review.onKeyDown(event)
    expect(event.defaultPrevented).toBe(false)
    expect(segment('s1')?.review).toBe('unreviewed')
    button.remove()
  })

  it('leaves keys typed into a form field alone', async () => {
    const { review, segment } = await setup()
    const input = document.createElement('input')
    document.body.append(input)
    const event = new KeyboardEvent('keydown', { key: 'x', bubbles: true, cancelable: true })
    input.dispatchEvent(event)
    review.onKeyDown(event)
    expect(segment('s1')).toBeDefined()
    input.remove()
  })
})

describe('ReviewController, serve calls and breaks (automatic scoring)', () => {
  const gap = (id: string, kind: Segment['kind'], start_ms: number, end_ms: number, patch: Partial<Segment> = {}) =>
    rally({ id, kind, start_ms, end_ms, source: 'model', confidence: 0.8, model_run_id: 'run-1', version: 1, ...patch })

  /** Nomal's warm-up before the first rally and a timeout between two rallies. */
  function scored() {
    return bundle({
      segments: [
        gap('w', 'warmup', 0, 9_000),
        suggestion('s1', 10_000, 0.95, { model_serving: 'us', serving_court: 'near', serve_confidence: 0.87, model_won_by: 'them' }),
        gap('g', 'timeout', 20_000, 50_000),
        suggestion('s2', 60_000, 0.9, { serving_manual: 'them' }),
      ],
    })
  }

  it('lists the breaks Nomal labelled with the rallies, in time order, and counts them in the progress', async () => {
    const { review } = await setup(scored())
    expect(review.items().map((s) => s.id)).toEqual(['w', 's1', 'g', 's2'])
    expect(review.getState().currentId).toBe('w')
    expect(review.progress()).toEqual({ done: 1, total: 5 }) // one rally rejected before
  })

  it('1 and 2 call the serving team on the rally; the same key again takes the call back; U undoes', async () => {
    const { review, press, sent, segment } = await setup(scored())
    press('ArrowDown')
    expect(review.getState().currentId).toBe('s1')
    press('1')
    expect(segment('s1')).toMatchObject({ serving_manual: 'us', review: 'unreviewed' })
    expect(review.getState().notice).toMatchObject({ key: 'serve', params: { side: 'us' } })
    // Still on screen: a serve call is not an answer.
    expect(review.getState().currentId).toBe('s1')
    press('2')
    expect(segment('s1')?.serving_manual).toBe('them')
    press('2')
    expect(segment('s1')?.serving_manual).toBeNull()
    expect(review.getState().notice?.key).toBe('serveCleared')
    press('u')
    expect(segment('s1')?.serving_manual).toBe('them')
    expect(await sent()).toEqual([
      { id: 's1', base_version: 1, kind: 'rally', start_ms: 10_000, end_ms: 18_000, won_by: null, review: 'unreviewed', serving_manual: 'them' },
    ])
    // The answer after it doesn't name the call: the server keeps it.
    press('Enter')
    const [answer] = await sent()
    expect(answer).toMatchObject({ id: 's1', review: 'confirmed' })
    expect(answer).not.toHaveProperty('serving_manual')
    expect(segment('s1')?.serving_manual).toBe('them')
  })

  it('takes a serve call back that was saved already', async () => {
    const { review, press, sent } = await setup(scored())
    review.select('s2')
    press('2')
    expect(await sent()).toEqual([expect.objectContaining({ id: 's2', serving_manual: null })])
  })

  it('accepts a break with Enter and rejects one with X', async () => {
    const { review, press, sent, segment } = await setup(scored())
    press('Enter')
    expect(segment('w')?.review).toBe('confirmed')
    expect(review.getState().notice).toMatchObject({ key: 'gapAccepted', params: { kind: 'warmup' } })
    expect(review.getState().currentId).toBe('s1')
    review.select('g')
    press('x')
    expect(segment('g')).toBeUndefined()
    expect(review.getState().notice).toMatchObject({ key: 'gapRejected', params: { kind: 'timeout' } })
    expect(review.getState().currentId).toBe('s2')
    expect(await sent()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: 'w', kind: 'warmup', review: 'confirmed' }),
        expect.objectContaining({ id: 'g', kind: 'timeout', review: 'rejected', deleted: true }),
      ]),
    )
  })

  it('relabels a break with W B T D O (corrected) and goes on; its own label accepts it', async () => {
    const { review, press, sent, segment } = await setup(scored())
    review.select('g')
    press('b')
    expect(segment('g')).toMatchObject({ kind: 'set_break', review: 'corrected' })
    expect(review.getState().notice).toMatchObject({ key: 'relabelled', params: { kind: 'set_break' } })
    expect(review.getState().currentId).toBe('s2')
    review.select('w')
    press('W')
    expect(segment('w')).toMatchObject({ kind: 'warmup', review: 'confirmed' })
    expect(await sent()).toEqual(
      expect.arrayContaining([
        { id: 'g', base_version: 1, kind: 'set_break', start_ms: 20_000, end_ms: 50_000, won_by: null, review: 'corrected' },
        expect.objectContaining({ id: 'w', review: 'confirmed' }),
      ]),
    )
    press('u')
    press('u')
    expect(segment('g')).toMatchObject({ kind: 'timeout', review: 'unreviewed' })
    expect(review.getState().currentId).toBe('g')
  })

  it('moves the boundaries of a break, but merges and splits rallies only', async () => {
    const { review, press, segment, store } = await setup(scored())
    review.select('g')
    press('ArrowRight')
    expect(segment('g')).toMatchObject({ start_ms: 20_250, review: 'corrected' })
    press('m')
    expect(review.getState().notice).toMatchObject({ key: 'ralliesOnly', tone: 'error' })
    video.currentTime = 30
    press('s')
    expect(review.getState().notice).toMatchObject({ key: 'ralliesOnly', tone: 'error' })
    press('1')
    expect(review.getState().notice).toMatchObject({ key: 'ralliesOnly', tone: 'error' })
    expect(store.getView().segments).toHaveLength(4)
    // On a rally, the break labels say what they are for.
    review.select('s1')
    press('t')
    expect(review.getState().notice).toMatchObject({ key: 'gapsOnly', tone: 'error' })
    expect(segment('s1')?.kind).toBe('rally')
  })

  it('merges a rally with the next rally, past a break between them', async () => {
    const { press, review, segment } = await setup(scored())
    review.select('s1')
    press('m')
    expect(segment('s1')).toMatchObject({ end_ms: 68_000 })
    expect(segment('s2')).toBeUndefined()
    expect(segment('g')).toBeDefined()
  })

  it('asks the server to score again on leaving after a change, not after just looking', async () => {
    const onLeave = vi.fn()
    const { review, press } = await setup(scored(), { onLeave })
    press('ArrowDown')
    press('Escape')
    expect(onLeave).not.toHaveBeenCalled()
    review.start()
    press('Enter')
    press('Escape')
    expect(onLeave).toHaveBeenCalledTimes(1)
    review.start()
    review.stop()
    expect(onLeave).toHaveBeenCalledTimes(1)
  })
})
