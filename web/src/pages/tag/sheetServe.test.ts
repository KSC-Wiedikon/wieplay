import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { Outbox } from '../../lib/outbox'
import { Playhead } from '../../lib/playhead'
import type { SheetCourt } from '../../lib/sheetCourt'
import { TaggingStore } from '../../lib/taggingStore'
import type { ChangesIn, ChangesOut } from '../../lib/types'
import { bundle, rally, volleyball } from '../../test/tagging'
import { TagController } from './controller'

const VIDEO = '00000000-0000-4000-8000-0000000000ee'

const six = { us: [7, 3, 11, 5, 9, 1], them: [4, 8, 12, 14, 2, 6], rotation_us: 0, rotation_them: 0, winner: 'them' as const, penalty: false }
/** Rallies the scoresheet was lined up with: we served the first (#7), they the second (#8). */
const court: SheetCourt = {
  rallies: [
    { rally: 'r1', set: 1, point: 1, serving: 'us', server: 7, ...six },
    { rally: 'r2', set: 1, point: 2, serving: 'them', server: 8, ...six },
  ],
  notes: [],
  liberos: {},
}

let video: HTMLVideoElement

async function setup() {
  const initial = bundle({
    segments: [
      rally({ id: 'r1', start_ms: 10_000, end_ms: 18_000 }),
      rally({ id: 'r2', start_ms: 40_000, end_ms: 46_000 }),
      rally({ id: 'r3', start_ms: 70_000, end_ms: 76_000 }),
    ],
  })
  const send = vi.fn(async (body: ChangesIn): Promise<ChangesOut> => ({ events: [], segments: [], periods: [], stale: [], ...{ _: body } }) as ChangesOut)
  const store = new TaggingStore(VIDEO, initial)
  const outbox = await Outbox.open({ videoId: VIDEO, send, onSynced: (out) => store.applySynced(out), debounceMs: 60_000 })
  store.attach(outbox)
  video = document.createElement('video')
  const playhead = new Playhead()
  playhead.attach(video)
  const tag = new TagController(store, playhead, volleyball, { duration_ms: 600_000 })
  tag.setSheetCourt(court)
  const type = (text: string) => {
    for (const key of text) tag.onKeyDown(new KeyboardEvent('keydown', { key, cancelable: true }))
    tag.onKeyDown(new KeyboardEvent('keydown', { key: 'Enter', cancelable: true }))
  }
  /** The tag at the playhead (the events are kept in time order). */
  const last = () => store.getView().events.find((e) => Math.abs(e.t_ms - video.currentTime * 1000) < 1_000)
  return { tag, type, last }
}

beforeEach(() => {
  vi.stubGlobal('indexedDB', undefined) // memory outbox
})

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('a serve tagged in a rally the scoresheet was lined up with', () => {
  it('gets the sheet’s server when typed without a number', async () => {
    const { type, last } = await setup()
    video.currentTime = 42
    type('aS')
    expect(last()).toMatchObject({ code: 'S', side: 'them', number: 8 })
  })

  it('keeps the number the coach types', async () => {
    const { type, last } = await setup()
    video.currentTime = 42
    type('a12S')
    expect(last()?.number).toBe(12)
    video.currentTime = 12
    type('9S')
    expect(last()?.number).toBe(9)
  })

  it('gets none when the other team served, or in a rally the sheet is not lined up with', async () => {
    const { type, last } = await setup()
    video.currentTime = 12
    type('aS')
    expect(last()).toMatchObject({ side: 'them', number: null })
    video.currentTime = 72
    type('aS')
    expect(last()?.number).toBeNull()
  })
})
