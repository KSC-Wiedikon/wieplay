import { describe, expect, it } from 'vitest'
import { AIR_2_12, calibration, FLOOR_2_12, FLOOR_6_4 } from '../test/court'
import { bundle, tag } from '../test/tagging'
import type { BallTrack } from './ballTrack'
import type { CalibrationSegment, Mat3 } from './court'
import {
  applyH,
  calibrationAt,
  cameraMoves,
  diagramArrows,
  listOf,
  nextLandmark,
  otherZone,
  outcomeOf,
  ownZone,
  pointsOf,
  serveStart,
  toCourt,
  withoutPoint,
} from './court'

const taxonomy = bundle().taxonomy

describe('court geometry', () => {
  it('maps the floor through the homography and a ball in the air along its ray', () => {
    const [x, y] = applyH(calibration.image_to_court, ...FLOOR_2_12) ?? [0, 0]
    expect(x).toBeCloseTo(2, 3)
    expect(y).toBeCloseTo(12, 3)
    expect(toCourt(calibration, ...FLOOR_2_12, 0)).toEqual([expect.closeTo(2, 3), expect.closeTo(12, 3)])
    expect(toCourt(calibration, ...AIR_2_12, 0.7)).toEqual([expect.closeTo(2, 3), expect.closeTo(12, 3)])
    // The same point taken as on the floor lands far beyond: why receptions use the camera.
    const floor = toCourt(calibration, ...AIR_2_12, 0) ?? [0, 0]
    expect(floor[1]).toBeGreaterThan(13)
    // Without the camera, only the floor maps.
    expect(toCourt({ ...calibration, camera: null }, ...AIR_2_12, 0.7)).toBeNull()
  })

  it('knows DataVolley zones on both halves', () => {
    expect(ownZone(4)).toEqual([1.5, 7.5]) // front left at the net
    expect(ownZone(1)).toEqual([7.5, 1.5]) // back right
    expect(otherZone(4)).toEqual([7.5, 10.5]) // their front left is our right
    expect(otherZone(1)).toEqual([1.5, 16.5])
    expect(otherZone(10)).toBeNull()
    expect(serveStart(1)).toEqual([7.5, -1.5])
    expect(serveStart(undefined)).toEqual([4.5, -1.5])
  })

  it('handles the set-up dialog’s points', () => {
    const points = pointsOf([
      { landmark: 'far_left', u: 0.3, v: 0.4 },
      { landmark: 'near_left', u: 0.1, v: 0.9 },
    ])
    expect(listOf(points).map((p) => p.landmark)).toEqual(['near_left', 'far_left'])
    expect(nextLandmark(points)).toBe('near_right')
    expect(nextLandmark(points, 'near_right')).toBe('far_right')
    expect(nextLandmark({ ...points, near_right: [0.9, 0.9], far_right: [0.7, 0.4] }, 'far_left')).toBe('net_left')
    expect(withoutPoint(points, 'near_left')).toEqual({ far_left: [0.3, 0.4] })
  })
})

describe('diagramArrows', () => {
  const serve = (patch: Parameters<typeof tag>[0]) => tag({ code: 'S', side: 'us', number: 7, ...patch })

  it('draws serves and attacks from their zones when the court is not set up', () => {
    const arrows = diagramArrows({
      tags: [
        serve({ id: 'a', t_ms: 1000, attributes: { grade: '#', zone_from: 1, zone_to: 5 } }),
        serve({ id: 'b', t_ms: 2000, attributes: { grade: '=', zone_to: 6 } }),
        tag({ id: 'c', code: 'A', t_ms: 3000, number: 4, attributes: { grade: '+', zone_from: 4, zone_to: 1 } }),
        serve({ id: 'd', t_ms: 4000, attributes: { grade: '+' } }), // no zone: nothing to draw
        tag({ id: 'e', code: 'R', t_ms: 5000, attributes: { zone_to: 3 } }), // not a serve or attack
      ],
      taxonomy,
      calibration: null,
      track: null,
    })
    expect(arrows.map((a) => [a.tag.id, a.outcome, a.evidence])).toEqual([
      ['a', 'win', 'zones'],
      ['b', 'error', 'zones'],
      ['c', 'play', 'zones'],
    ])
    expect(arrows[0]?.from).toEqual([7.5, -1.5])
    expect(arrows[0]?.to).toEqual(otherZone(5))
    expect(arrows[2]?.from).toEqual(ownZone(4))
  })

  it('places a serve where its ball bounced once the court is set up, turning the far team round', () => {
    const contacts = (x: number, y: number, kind: 'bounce' | 'low') => [{ t: 2.0, x, y, kind }]
    const track = (x: number, y: number, kind: 'bounce' | 'low'): BallTrack => ({
      fps: 15,
      x: new Float32Array(60).fill(Number.NaN),
      y: new Float32Array(60).fill(Number.NaN),
      contacts: contacts(x, y, kind),
    })
    const s = serve({ id: 's', t_ms: 1000, attributes: { grade: '#', zone_to: 6 } })
    const far = diagramArrows({ tags: [s], taxonomy, calibration, track: track(...FLOOR_2_12, 'bounce') })
    expect(far).toHaveLength(1)
    expect(far[0]?.evidence).toBe('ball')
    expect(far[0]?.landing).toBe('bounce')
    expect(far[0]?.to[0]).toBeCloseTo(2, 2)
    expect(far[0]?.to[1]).toBeCloseTo(12, 2)
    // Landing on the near half: the other team served; drawn turned round, at the top.
    const near = diagramArrows({ tags: [s], taxonomy, calibration, track: track(...FLOOR_6_4, 'bounce') })
    expect(near[0]?.to[0]).toBeCloseTo(3, 2)
    expect(near[0]?.to[1]).toBeCloseTo(14, 2)
    // A reception (low contact) is mapped at its height, not on the floor.
    const received = diagramArrows({ tags: [s], taxonomy, calibration, track: track(...AIR_2_12, 'low') })
    expect(received[0]?.landing).toBe('low')
    expect(received[0]?.to[1]).toBeCloseTo(12, 2)
    // A contact outside the serve's window falls back to the zones.
    const late = diagramArrows({ tags: [serve({ ...s, t_ms: -5000 })], taxonomy, calibration, track: track(...FLOOR_2_12, 'bounce') })
    expect(late[0]?.evidence).toBe('zones')
  })

  it('reads the outcome from the taxonomy', () => {
    expect(outcomeOf({ code: 'S', attributes: { grade: '#' } }, taxonomy)).toBe('win')
    expect(outcomeOf({ code: 'A', attributes: { grade: '/' } }, taxonomy)).toBe('error')
    expect(outcomeOf({ code: 'A', attributes: { grade: '+' } }, taxonomy)).toBe('play')
    expect(outcomeOf({ code: 'A', attributes: {} }, null)).toBe('play')
  })

  it('maps each moment through the camera setup of its time', () => {
    // After the bump the picture moved 3 % to the right: the same floor point shows there.
    const dx = -0.03
    const shifted = calibration.image_to_court.map((row) => [row[0] ?? 0, row[1] ?? 0, (row[2] ?? 0) + (row[0] ?? 0) * dx]) as Mat3
    const segment = (patch: Partial<CalibrationSegment>): CalibrationSegment => ({
      index: 0,
      from_ms: 0,
      to_ms: 4_350_000,
      start: 'start',
      shift_px: 0,
      view: 0,
      points: [],
      method: 'auto-hall',
      checked: false,
      main: true,
      image_to_court: calibration.image_to_court,
      court_to_image: null,
      camera: null,
      error_cm: null,
      max_error_cm: null,
      lines: [],
      ...patch,
    })
    const cal = {
      ...calibration,
      segments: [
        segment({}),
        segment({ index: 1, from_ms: 4_350_000, to_ms: 6_000_000, start: 'bump', main: false, image_to_court: shifted }),
        segment({ index: 2, from_ms: 6_000_000, to_ms: null, start: 'moved', main: false, image_to_court: null, method: 'none' }),
      ],
    }
    expect(calibrationAt(cal, 1_000)?.image_to_court).toBe(calibration.image_to_court)
    expect(calibrationAt(cal, 4_350_000)?.image_to_court).toBe(shifted)
    expect(calibrationAt(cal, 7_000_000)).toBeNull() // the camera showed no court then
    expect(calibrationAt(calibration, 7_000_000)).toBe(calibration) // one setup: the calibration
    expect(cameraMoves(cal.segments)).toEqual([4_350_000, 6_000_000])
    // A bounce after the bump: through the moved picture it lands where it did before.
    const later: [number, number] = [FLOOR_2_12[0] + 0.03, FLOOR_2_12[1]]
    const bounce = (t: number, x: number, y: number): BallTrack => ({
      fps: 15,
      x: new Float32Array(10).fill(Number.NaN),
      y: new Float32Array(10).fill(Number.NaN),
      contacts: [{ t, x, y, kind: 'bounce' }],
    })
    const s = serve({ id: 's', t_ms: 4_400_000, attributes: { grade: '#' } })
    const [arrow] = diagramArrows({ tags: [s], taxonomy, calibration: cal, track: bounce(4_401, ...later) })
    expect(arrow?.evidence).toBe('ball')
    expect(arrow?.to[0]).toBeCloseTo(2, 2)
    expect(arrow?.to[1]).toBeCloseTo(12, 2)
  })
})
