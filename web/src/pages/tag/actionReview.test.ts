import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Outbox } from '../../lib/outbox'
import { Playhead } from '../../lib/playhead'
import { TaggingStore } from '../../lib/taggingStore'
import type { ChangesIn, ChangesOut, Tag } from '../../lib/types'
import { bundle, rally, tag, volleyball } from '../../test/tagging'
import { ActionReviewController } from './actionReview'
import { TagController } from './controller'

const VIDEO = '00000000-0000-4000-8000-0000000000ee'
const R1 = '00000000-0000-4000-8000-0000000001a1'
const R2 = '00000000-0000-4000-8000-0000000001a2'
const action = (id: string, t_ms: number, code: string, patch: Partial<Tag> = {}) =>
  tag({ id, t_ms, code, number: null, member_id: null, source: 'model', review: 'check', confidence: 0.8, model_run_id: 'run-a', ...patch })

/** Two rallies: serve, reception, set to check in the first (the set already accepted), a serve in the second. */
function data() {
  return bundle({
    segments: [rally({ id: R1, start_ms: 10_000, end_ms: 20_000 }), rally({ id: R2, start_ms: 30_000, end_ms: 40_000 })],
    events: [
      action('s1', 11_000, 'S', { segment_id: R1 }),
      action('r1', 12_200, 'R', { segment_id: R1, side: 'them' }),
      action('e1', 13_200, 'E', { segment_id: R1, side: 'them', review: 'confirmed', version: 2 }),
      action('a1', 14_000, 'A', { segment_id: R1, side: 'them' }),
      action('s2', 31_000, 'S', { segment_id: R2, side: 'them' }),
      tag({ id: 'hand', t_ms: 32_000, code: 'R', number: 7 }),
    ],
  })
}

/** The API's /changes, for events: saved with a new version; model rows keep their source. */
function server(initial: Tag[]) {
  const rows = new Map(initial.map((e) => [e.id, e]))
  return (body: ChangesIn): ChangesOut => {
    const out: Tag[] = []
    for (const c of body.events) {
      const prev = rows.get(c.id)
      const row: Tag = {
        ...(prev ?? tag({ id: c.id, t_ms: c.t_ms })),
        code: c.code,
        t_ms: c.t_ms,
        side: c.side,
        number: c.number ?? null,
        attributes: c.attributes,
        time_precision: c.time_precision,
        review: c.review ?? 'unreviewed',
        version: (prev?.version ?? 0) + 1,
        deleted_at: c.deleted ? '2026-10-17T10:00:00Z' : null,
      }
      rows.set(c.id, row)
      out.push(row)
    }
    return { events: out, segments: data().segments, periods: [], stale: [] }
  }
}

let video: HTMLVideoElement

async function setup({ onLeave = () => {} } = {}, initial = data()) {
  const api = server(initial.events)
  const send = vi.fn(async (body: ChangesIn) => api(body))
  const store = new TaggingStore(VIDEO, initial)
  const outbox = await Outbox.open({ videoId: VIDEO, send, onSynced: (out) => store.applySynced(out), debounceMs: 60_000 })
  store.attach(outbox)
  video = document.createElement('video')
  const playhead = new Playhead()
  playhead.attach(video)
  const tagController = new TagController(store, playhead, volleyball, { duration_ms: 600_000 })
  tagController.setFps(50)
  const actions = new ActionReviewController(store, playhead, tagController, { rejectedBefore: 2, onLeave })
  actions.attach()
  actions.start()
  const press = (key: string, mods: KeyboardEventInit = {}) => actions.onKeyDown(new KeyboardEvent('keydown', { key, cancelable: true, ...mods }))
  const sent = async () => {
    await outbox.flush()
    return send.mock.calls.at(-1)?.[0].events ?? []
  }
  const event = (id: string) => store.getView().events.find((e) => e.id === id)
  return { store, actions, press, sent, event }
}

beforeEach(() => {
  vi.stubGlobal('indexedDB', undefined) // memory outbox
  window.localStorage.clear()
  vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(() => Promise.resolve())
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
})

afterEach(() => vi.useRealTimers())

describe('ActionReviewController', () => {
  it('starts at the first action to check, 1 s before its contact', async () => {
    const { actions } = await setup()
    expect(actions.getState()).toMatchObject({ active: true, currentId: 's1', number: '' })
    expect(video.currentTime).toBe(10)
    // Four to check (e1 answered), two rejected before this session.
    expect(actions.progress()).toEqual({ done: 2, total: 6 })
  })

  it('Enter accepts ("confirmed") and moves on; X rejects (deleted, "rejected")', async () => {
    const { actions, press, sent, event } = await setup()
    press('Enter')
    expect(actions.getState().currentId).toBe('r1')
    expect(video.currentTime).toBeCloseTo(11.2)
    press('x')
    expect(event('r1')).toBeUndefined()
    expect(actions.getState().currentId).toBe('a1') // e1 is answered already
    expect(actions.progress()).toEqual({ done: 4, total: 6 })
    const out = await sent()
    expect(out.find((e) => e.id === 's1')).toMatchObject({ review: 'confirmed', code: 'S', base_version: 1 })
    expect(out.find((e) => e.id === 'r1')).toMatchObject({ review: 'rejected', deleted: true })
  })

  it('the skill, grade and side keys correct the action on screen without moving on', async () => {
    const { actions, press, sent, event } = await setup()
    press('d')
    expect(event('s1')).toMatchObject({ code: 'D', review: 'corrected' })
    press('#')
    expect(event('s1')?.attributes).toEqual({ grade: '#' })
    press('#') // again: no grade
    expect(event('s1')?.attributes).toEqual({})
    press('=')
    press('Tab')
    expect(event('s1')).toMatchObject({ side: 'them', attributes: { grade: '=' } })
    press('S') // back to the serve: still a correction (the side and grade changed)
    expect(actions.getState().currentId).toBe('s1')
    press('Enter')
    expect(actions.getState().currentId).toBe('r1')
    const out = await sent()
    expect(out.find((e) => e.id === 's1')).toMatchObject({ code: 'S', side: 'them', attributes: { grade: '=' }, review: 'corrected' })
  })

  it('a number then Enter names the player and accepts', async () => {
    const { actions, press, sent } = await setup()
    press('1')
    press('7')
    press('3') // two digits at most: the last two
    expect(actions.getState().number).toBe('73')
    press('Backspace')
    expect(actions.getState().number).toBe('7')
    press('Enter')
    expect(actions.getState()).toMatchObject({ currentId: 'r1', number: '' })
    const out = await sent()
    expect(out.find((e) => e.id === 's1')).toMatchObject({ number: 7, review: 'confirmed' })
  })

  it('←/→ move the contact by one frame; the time is then frame-precise', async () => {
    const { press, event } = await setup()
    press('ArrowRight')
    press('ArrowRight')
    press('ArrowLeft')
    expect(event('s1')).toMatchObject({ t_ms: 11_020, frame: 551, time_precision: 'frame', review: 'corrected' })
  })

  it('Shift+Enter accepts the rest of the rally, U undoes it', async () => {
    const { actions, press, event } = await setup()
    press('Enter', { shiftKey: true })
    expect(['s1', 'r1', 'a1'].map((id) => event(id)?.review)).toEqual(['confirmed', 'confirmed', 'confirmed'])
    expect(event('s2')?.review).toBe('check')
    expect(actions.getState().currentId).toBe('s2')
    press('u')
    expect(['s1', 'r1', 'a1'].map((id) => event(id)?.review)).toEqual(['check', 'check', 'check'])
    expect(actions.getState().currentId).toBe('s1')
    expect(actions.getState().notice?.key).toBe('undone')
  })

  it('↑/↓ go through every action; Esc clears the number, then leaves', async () => {
    const onLeave = vi.fn()
    const { actions, press } = await setup({ onLeave })
    press('ArrowDown')
    press('ArrowDown')
    expect(actions.getState().currentId).toBe('e1')
    press('ArrowUp')
    expect(actions.getState().currentId).toBe('r1')
    press('4')
    press('Escape')
    expect(actions.getState().number).toBe('')
    expect(onLeave).not.toHaveBeenCalled()
    press('Escape')
    expect(onLeave).toHaveBeenCalledOnce()
  })

  it('ends when everything is answered, and a reject can be undone with its model source', async () => {
    const { actions, press, event } = await setup()
    press('x')
    expect(event('s1')).toBeUndefined()
    press('u')
    expect(event('s1')).toMatchObject({ source: 'model', review: 'check', confidence: 0.8 })
    for (let i = 0; i < 4; i++) press('Enter')
    expect(actions.getState().currentId).toBeNull()
    expect(actions.progress()).toEqual({ done: 6, total: 6 })
    press('Enter')
    expect(actions.getState().notice?.key).toBe('nothingSelected')
  })

  it("leaves the sure ones out of the walk and takes WiePlay's other answer with one button", async () => {
    const sure = bundle({
      segments: [rally({ id: R1, start_ms: 10_000, end_ms: 20_000 })],
      events: [
        action('s1', 11_000, 'S', { segment_id: R1, review: 'auto', model_sure: true }),
        action('r1', 12_200, 'R', { segment_id: R1, side: 'them', model_alternative: 'D:us' }),
        action('x1', 13_000, 'E', { segment_id: R1, model_alternative: 'none' }),
      ],
    })
    const { actions, event, sent } = await setup({}, sure)
    expect(actions.getState().currentId).toBe('r1')
    actions.takeAlternative()
    expect(event('r1')).toMatchObject({ code: 'D', side: 'us', review: 'corrected' })
    expect(actions.getState().currentId).toBe('x1')
    actions.takeAlternative()
    expect(event('x1')).toBeUndefined()
    expect(actions.getState().currentId).toBeNull()
    expect(event('s1')?.review).toBe('auto')
    const out = await sent()
    expect(out.find((e) => e.id === 'r1')).toMatchObject({ code: 'D', side: 'us', review: 'corrected' })
    expect(out.find((e) => e.id === 'x1')).toMatchObject({ review: 'rejected', deleted: true })
  })
})
