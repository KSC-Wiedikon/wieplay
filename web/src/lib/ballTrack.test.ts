import { afterEach, describe, expect, it, vi } from 'vitest'
import { ballAt, contentRect, decodeTrack, rememberTrajectories, toBox, trailAt, trajectoriesDefault, type BallTrackPayload } from './ballTrack'

const payload = (patch: Partial<BallTrackPayload> = {}): BallTrackPayload => ({
  schema: 1,
  fps: 15,
  scale: 1000,
  frames: 60,
  duration_s: 4,
  // Samples 10-14: x 100, 110, 120, 130, 140 (thousandths), y 500 → 460; samples 20-22 another track.
  tracks: [
    [10, 100, 500, 10, -10, 10, -10, 10, -10, 10, -10],
    [20, 800, 300, 5, 5, 5, 5],
  ],
  contacts: [[800, 125, 470, 0]],
  ...patch,
})

describe('decodeTrack', () => {
  it('turns the deltas back into positions, with gaps between tracks', () => {
    const track = decodeTrack(payload())
    expect(track.fps).toBe(15)
    expect(track.x).toHaveLength(60)
    expect(Number.isNaN(track.x[9])).toBe(true)
    expect(track.x[10]).toBeCloseTo(0.1)
    expect(track.y[14]).toBeCloseTo(0.46)
    expect(Number.isNaN(track.x[15])).toBe(true)
    expect(track.x[22]).toBeCloseTo(0.81)
    expect(track.contacts).toEqual([{ t: 0.8, x: 0.125, y: 0.47, kind: 'bounce' }])
    expect(ballAt(track, 12 / 15)).toEqual({ x: expect.closeTo(0.12, 5), y: expect.closeTo(0.48, 5) })
    expect(ballAt(track, 1.0)).toBeNull()
  })
})

describe('trailAt', () => {
  const track = decodeTrack(payload({ contacts: [] }))

  it('draws the last stretch of the path, fading with age, ending at the ball now', () => {
    // t = 13.5 samples: a window of 0.2 s = 3 samples → samples 11, 12, 13 and the head at 13.5.
    const lines = trailAt(track, 13.5 / 15, 0.2)
    expect(lines).toHaveLength(1)
    const [line] = lines
    expect(line?.map((p) => Math.round(p.x * 1000))).toEqual([110, 120, 130, 135])
    expect(line?.at(-1)?.age).toBe(0)
    const ages = line?.map((p) => p.age) ?? []
    expect([...ages].sort((a, b) => b - a)).toEqual(ages) // oldest first
    expect(ages[0]).toBeCloseTo(2.5 / 3)
  })

  it('breaks at gaps and shows nothing where the ball was not seen', () => {
    const lines = trailAt(track, 21 / 15, 1.0)
    expect(lines).toHaveLength(2)
    expect(lines[0]?.[0]?.x).toBeCloseTo(0.1)
    expect(lines[1]?.at(-1)?.x).toBeCloseTo(0.805)
    expect(trailAt(track, 30 / 15, 0.5)).toEqual([])
    expect(trailAt(track, 0, 1)).toEqual([])
  })

  it('puts a bounce in at its own time, so the corner stays sharp', () => {
    const withBounce = decodeTrack(payload())
    const [line] = trailAt(withBounce, 14 / 15, 0.4)
    const bounce = line?.find((p) => p.contact === 'bounce')
    expect(bounce).toMatchObject({ x: 0.125, y: 0.47 })
    // Between sample 12 (0.8 s) and 13: in order of time.
    const index = bounce ? (line?.indexOf(bounce) ?? -1) : -1
    expect(line?.[index - 1]?.x).toBeCloseTo(0.12)
    expect(line?.[index + 1]?.x).toBeCloseTo(0.13)
  })
})

describe('contentRect', () => {
  it('finds the picture inside a wider box (bars left and right)', () => {
    expect(contentRect(1000, 400, 1920, 1080)).toEqual({ x: (1000 - 400 * (16 / 9)) / 2, y: 0, width: 400 * (16 / 9), height: 400 })
  })

  it('finds the picture inside a taller box (bars above and below)', () => {
    const r = contentRect(390, 600, 1920, 1080)
    expect(r?.width).toBe(390)
    expect(r?.height).toBeCloseTo(390 * (9 / 16))
    expect(r?.y).toBeCloseTo((600 - 390 * (9 / 16)) / 2)
  })

  it('is unknown before the video has a size', () => {
    expect(contentRect(640, 360, 0, 0)).toBeNull()
  })

  it('places normalised points in the box', () => {
    const r = { x: 10, y: 20, width: 200, height: 100 }
    expect(toBox(r, { x: 0.5, y: 1 })).toEqual({ x: 110, y: 120 })
  })
})

describe('the Trajectories default', () => {
  afterEach(() => localStorage.clear())

  it('is on on wide screens, off on phones, and remembers a choice', () => {
    const media = (phone: boolean) => vi.fn().mockReturnValue({ matches: phone })
    vi.stubGlobal('matchMedia', media(false))
    expect(trajectoriesDefault()).toBe(true)
    vi.stubGlobal('matchMedia', media(true))
    expect(trajectoriesDefault()).toBe(false)
    rememberTrajectories(true)
    expect(trajectoriesDefault()).toBe(true)
    rememberTrajectories(false)
    vi.stubGlobal('matchMedia', media(false))
    expect(trajectoriesDefault()).toBe(false)
  })
})
