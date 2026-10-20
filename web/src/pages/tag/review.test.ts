import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Outbox } from '../../lib/outbox'
import { Playhead } from '../../lib/playhead'
import { TaggingStore } from '../../lib/taggingStore'
import type { ChangesIn, ChangesOut, Segment } from '../../lib/types'
import { bundle, rally, volleyball } from '../../test/tagging'
import { TagController } from './controller'
import { BREAK_BUTTONS, GAP_KEYS, NUDGE_MS, ReviewController } from './review'

const VIDEO = '00000000-0000-4000-8000-0000000000dd'
/** WiePlay's rally it is unsure of (to check), unless the patch says otherwise. */
const suggestion = (id: string, start_ms: number, confidence: number, patch: Partial<Segment> = {}) =>
  rally({ id, start_ms, end_ms: start_ms + 8_000, source: 'model', review: 'check', score_sure: false, confidence, model_run_id: 'run-1', version: 1, ...patch })

/** Four rallies of WiePlay's to check (one accepted already) and a rally marked by hand. */
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
  it('starts at the first rally to check, 1 s before it', async () => {
    const { review } = await setup()
    expect(review.getState()).toMatchObject({ active: true, currentId: 's1' })
    expect(video.currentTime).toBe(9)
    expect(review.progress()).toEqual({ done: 1, total: 4 }) // s1, s2, s4 to check; one rejected before
  })

  it('Enter accepts: review "confirmed" through the outbox, and the list moves on', async () => {
    const { review, press, sent } = await setup()
    press('Enter')
    expect(review.getState().currentId).toBe('s2')
    expect(video.currentTime).toBe(29)
    expect(review.progress()).toEqual({ done: 2, total: 4 })
    expect(await sent()).toEqual([
      { id: 's1', base_version: 1, kind: 'rally', start_ms: 10_000, end_ms: 18_000, won_by: null, review: 'confirmed' },
    ])
  })

  it('X rejects: deleted with review "rejected" (a negative for the server), then the next one', async () => {
    const { review, press, sent, segment } = await setup()
    press('x')
    expect(segment('s1')).toBeUndefined()
    expect(review.getState().currentId).toBe('s2')
    expect(review.progress()).toEqual({ done: 2, total: 4 })
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
    expect(segment('s2')).toMatchObject({ review: 'check' })
    expect(review.getState().currentId).toBe('s2')
    press('u')
    expect(segment('s1')).toMatchObject({ review: 'check' })
    expect(review.getState().currentId).toBe('s1')
    expect(review.progress()).toEqual({ done: 1, total: 4 })
    // The queue holds each rally's state from before: nothing changes on the server.
    expect(await sent()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: 's1', review: 'check' }),
        expect.objectContaining({ id: 's2', review: 'check' }),
      ]),
    )
    press('u')
    expect(review.getState().notice?.key).toBe('nothingToUndo')
  })

  it('undoes a rejection that was saved already: the rally comes back, to check', async () => {
    const { review, press, sent, segment } = await setup()
    press('x')
    await sent()
    expect(segment('s1')).toBeUndefined()
    press('u')
    expect(segment('s1')).toMatchObject({ source: 'model', confidence: 0.95, review: 'check' })
    expect(review.getState().currentId).toBe('s1')
    expect(await sent()).toEqual([expect.objectContaining({ id: 's1', review: 'check', base_version: 2 })])
  })

  it("Enter takes WiePlay's answer: the rally confirmed with WiePlay's winner, a fact; U takes it back", async () => {
    const reasons = (code: string) => ({ score_sure: false, score_conflict: true, score_note: code, score_reasons: [{ code }] }) as Partial<Segment>
    const scored = bundle({
      segments: [
        // Sure: WiePlay's, counted, not in the walk.
        suggestion('s1', 10_000, 0.6, { review: 'auto', score_sure: true, model_won_by: 'us' }),
        suggestion('s2', 30_000, 0.95, { model_won_by: 'them', ...reasons('video') }),
        // No point, WiePlay thinks: confirmed as it is, no winner.
        suggestion('s3', 50_000, 0.9, { model_won_by: null, ...reasons('log_none') }),
        // A person's rally whose winner the scoring is unsure of: to check too.
        rally({ id: 'h', start_ms: 70_000, end_ms: 78_000, model_won_by: 'us', score_sure: false }),
      ],
    })
    const { review, sent, segment, press } = await setup(scored)
    expect(review.getState().currentId).toBe('s2')
    expect(review.toCheck().map((s) => s.id)).toEqual(['s2', 's3', 'h'])
    press('Enter')
    expect(segment('s2')).toMatchObject({ review: 'confirmed', won_by: 'them' })
    expect(review.getState().currentId).toBe('s3')
    press('Enter')
    expect(segment('s3')).toMatchObject({ review: 'confirmed', won_by: null })
    expect(review.getState().currentId).toBe('h')
    press('Enter')
    expect(segment('h')).toMatchObject({ review: 'confirmed', won_by: 'us', source: 'manual' })
    expect(review.toCheck()).toEqual([])
    expect(review.getState().currentId).toBeNull()
    // Taking WiePlay's own winner is no correction (the server reads it the same way).
    const body = await sent()
    expect(body).toEqual([
      { id: 's2', base_version: 1, kind: 'rally', start_ms: 30_000, end_ms: 38_000, won_by: 'them', review: 'confirmed' },
      { id: 's3', base_version: 1, kind: 'rally', start_ms: 50_000, end_ms: 58_000, won_by: null, review: 'confirmed' },
      { id: 'h', base_version: 1, kind: 'rally', start_ms: 70_000, end_ms: 78_000, won_by: 'us', review: 'confirmed' },
    ])
    press('u')
    expect(segment('h')).toMatchObject({ review: 'unreviewed', won_by: null })
    // The sure one is reachable with ↑, and stays as it is.
    press('ArrowUp')
    press('ArrowUp')
    press('ArrowUp')
    expect(review.getState().currentId).toBe('s1')
    expect(segment('s1')?.review).toBe('auto')
  })

  it('a decision on a rally to check is a fact: it is no longer to check', async () => {
    const scored = bundle({
      segments: [
        suggestion('s1', 10_000, 0.6, { model_won_by: 'us', score_sure: false, score_reasons: [{ code: 'unsure' }] }),
        suggestion('s2', 30_000, 0.95, { review: 'auto', score_sure: true }),
      ],
    })
    const { review, press, segment } = await setup(scored)
    expect(review.toCheck().map((s) => s.id)).toEqual(['s1'])
    press('4') // they won: the coach's call
    expect(segment('s1')).toMatchObject({ won_by: 'them', review: 'corrected' })
    expect(review.toCheck()).toEqual([])
  })

  it("takes WiePlay's other answer: the other winner, or no rally", async () => {
    const scored = bundle({
      segments: [
        suggestion('s1', 10_000, 0.6, { model_won_by: 'us', model_alternative: 'them' }),
        suggestion('s2', 30_000, 0.6, { model_won_by: 'us', model_alternative: 'none' }),
      ],
    })
    const { review, segment, sent } = await setup(scored)
    review.takeAlternative()
    expect(segment('s1')).toMatchObject({ won_by: 'them', review: 'corrected' })
    expect(review.getState().currentId).toBe('s2')
    review.takeAlternative()
    expect(segment('s2')).toBeUndefined()
    expect(await sent()).toEqual([
      expect.objectContaining({ id: 's1', won_by: 'them', review: 'corrected' }),
      expect.objectContaining({ id: 's2', review: 'rejected', deleted: true }),
    ])
  })

  it('goes on into the actions once nothing is left to check here', async () => {
    const onEnd = vi.fn()
    const scored = bundle({ segments: [suggestion('s1', 10_000, 0.6)] })
    const { review, press } = await setup(scored)
    review.setOnEnd(onEnd)
    press('Enter')
    expect(onEnd).toHaveBeenCalledTimes(1)
    // Started with nothing to check: on at once.
    review.stop()
    review.start()
    expect(onEnd).toHaveBeenCalledTimes(2)
  })

  it('ends with nothing on screen once everything is checked', async () => {
    const { review, press } = await setup()
    press('Enter')
    press('Enter')
    press('Enter')
    expect(review.getState().currentId).toBeNull()
    expect(review.progress()).toEqual({ done: 4, total: 4 })
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
    expect(segment('s1')?.review).toBe('check')
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
    rally({ id, kind, start_ms, end_ms, source: 'model', review: 'check', confidence: 0.8, model_run_id: 'run-1', version: 1, ...patch })

  /** WiePlay's warm-up before the first rally and a timeout between two rallies. */
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

  it('lists the breaks WiePlay labelled with the rallies, in time order, and counts them in the progress', async () => {
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
    expect(segment('s1')).toMatchObject({ serving_manual: 'us', review: 'check' })
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
      { id: 's1', base_version: 1, kind: 'rally', start_ms: 10_000, end_ms: 18_000, won_by: null, review: 'check', serving_manual: 'them' },
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
    expect(segment('g')).toMatchObject({ kind: 'timeout', review: 'check' })
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
  })

  it('a break key on a rally: no rally but that break; a break already there takes the label', async () => {
    const { review, press, segment, sent } = await setup(scored())
    review.select('s1')
    press('t')
    expect(segment('s1')).toBeUndefined() // rejected: the rally finder learns from it
    expect(segment('g')).toMatchObject({ kind: 'timeout', review: 'confirmed' })
    // The notice names the rally that went (its time), not the break's start.
    expect(review.getState().notice).toMatchObject({ key: 'toBreak', params: { kind: 'timeout', time: 10_000 } })
    expect(await sent()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: 's1', review: 'rejected', deleted: true }),
        expect.objectContaining({ id: 'g', kind: 'timeout', review: 'confirmed' }),
      ]),
    )
    press('u')
    expect(segment('s1')).toMatchObject({ kind: 'rally', review: 'check' })
    expect(segment('g')).toMatchObject({ review: 'check' })
  })

  it('a break key on a rally with no break around it makes one from the rally before to the rally after', async () => {
    const { review, press, store, sent } = await setup()
    review.select('s2')
    press('b')
    const made = store.getView().segments.find((s) => s.kind === 'set_break')
    expect(made).toMatchObject({ start_ms: 18_000, end_ms: 50_000 }) // s1's end to s3's start
    expect(store.getView().segments.find((s) => s.id === 's2')).toBeUndefined()
    expect(await sent()).toEqual(expect.arrayContaining([expect.objectContaining({ id: 's2', review: 'rejected', deleted: true })]))
    expect(review.getState().currentId).toBe('s4') // the next open suggestion
  })

  it('offers every break key as a button, timeout first', () => {
    expect(BREAK_BUTTONS).toEqual(['timeout', 'substitution', 'set_break', 'warmup', 'stoppage', 'other_dead'])
    // Every key has its button; "Sub" is a button only.
    expect(BREAK_BUTTONS.filter((k) => k !== 'substitution').sort()).toEqual(Object.values(GAP_KEYS).sort())
  })

  it('the break buttons answer like their keys: a substitution on a rally, a timeout on a break', async () => {
    // The buttons call relabel, as the keys do. s4 is the last rally: its break runs from s3's end to its own end.
    const rallyCase = await setup()
    rallyCase.review.select('s4')
    rallyCase.review.relabel('substitution')
    expect(rallyCase.segment('s4')).toBeUndefined()
    expect(rallyCase.store.getView().segments.find((s) => s.kind === 'substitution')).toMatchObject({ start_ms: 58_000, end_ms: 78_000 })
    expect(rallyCase.review.getState().notice).toMatchObject({ key: 'toBreak', params: { kind: 'substitution', time: 70_000 } })
    expect(await rallyCase.sent()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: 's4', review: 'rejected', deleted: true }),
        expect.objectContaining({ kind: 'substitution', start_ms: 58_000, end_ms: 78_000 }),
      ]),
    )
    rallyCase.outbox.close()

    const { review, segment, sent } = await setup(scored())
    review.select('w')
    review.relabel('timeout')
    expect(segment('w')).toMatchObject({ kind: 'timeout', review: 'corrected' })
    expect(review.getState().notice).toMatchObject({ key: 'relabelled', params: { kind: 'timeout' } })
    expect(await sent()).toEqual([expect.objectContaining({ id: 'w', kind: 'timeout', review: 'corrected' })])
  })

  it('keeps its place when the scoring replaces the break on screen with a new one (no "every suggestion is reviewed")', async () => {
    const { review, store } = await setup(scored())
    review.select('g')
    const before = review.progress()
    // The server's scoring labelled the gaps again: the same timeout under a new id.
    const replaced = store.getView().segments.map((s) => (s.id === 'g' ? { ...s, id: 'g2' } : s))
    store.applySynced({ events: [], segments: replaced, periods: [], stale: [] })
    expect(review.getState().currentId).toBe('g2')
    expect(review.current()?.kind).toBe('timeout')
    // The old id was never answered: it no longer counts, the new one does, open as before.
    expect(review.progress()).toEqual(before)
    // Gone without a successor: the next open item from where it was, not the end of the list.
    store.applySynced({ events: [], segments: replaced.filter((s) => s.id !== 'g2'), periods: [], stale: [] })
    expect(review.getState().currentId).toBe('s2')
    expect(review.progress()).toEqual({ done: before.done, total: before.total - 1 })
  })

  it('counts a break it rejected as done once it leaves the view', async () => {
    const { review, press, store } = await setup(scored())
    review.select('g')
    const before = review.progress()
    press('x')
    store.applySynced({ events: [], segments: store.getView().segments, periods: [], stale: [] })
    expect(review.progress()).toEqual({ done: before.done + 1, total: before.total })
  })

  it('R on a break: play after all, a rally made and on screen in one undo step', async () => {
    const { review, press, store, segment, sent } = await setup(scored())
    // A long break (30 s): 8 s from 2 s before the playhead.
    review.select('g')
    video.currentTime = 31
    press('r')
    expect(segment('g')).toBeUndefined()
    const made = review.current()
    expect(made).toMatchObject({ kind: 'rally', start_ms: 29_000, end_ms: 37_000 })
    expect(review.getState().notice).toMatchObject({ key: 'toRally', params: { start: 29_000, end: 37_000 } })
    expect(review.progress().done).toBeLessThan(review.progress().total) // it waits for an answer
    expect(await sent()).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ id: 'g', review: 'rejected', deleted: true }),
        expect.objectContaining({ id: made?.id, kind: 'rally', start_ms: 29_000, end_ms: 37_000 }),
      ]),
    )
    // Answered like a split-off part.
    press('Enter')
    expect(review.getState().currentId).toBe('s2')
    // One undo takes the rally back and brings the break back on screen (accepting a part of
    // this session's own is no change of its own).
    press('u')
    expect(review.getState().notice?.key).toBe('undone')
    expect(segment('g')).toMatchObject({ kind: 'timeout', review: 'check' })
    expect(store.getView().segments.some((s) => s.id === made?.id)).toBe(false)
    expect(review.getState().currentId).toBe('g')
    // A short break (the 9 s warm-up) becomes a rally over all of it; a rally is one already.
    review.select('w')
    press('R')
    expect(review.current()).toMatchObject({ kind: 'rally', start_ms: 0, end_ms: 9_000 })
    review.select('s1')
    press('r')
    expect(review.getState().notice).toMatchObject({ key: 'rallyAlready', tone: 'error' })
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
