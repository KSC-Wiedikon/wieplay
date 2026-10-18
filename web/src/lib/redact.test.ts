import { describe, expect, it } from 'vitest'
import {
  FULL,
  clampRect,
  defaultBox,
  initialEdits,
  pixelBounds,
  redactRaster,
  rotateRaster,
  turn,
  turnRect,
  type Edits,
  type Raster,
  type Rect,
} from './redact'

/** A synthetic photo: every pixel a different, never black colour (x in red, y in green). */
function photo(width: number, height: number): Raster {
  const data = new Uint8ClampedArray(width * height * 4)
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const i = (y * width + x) * 4
      data[i] = 20 + x
      data[i + 1] = 20 + y
      data[i + 2] = 200
      data[i + 3] = 255
    }
  }
  return { width, height, data }
}

const pixel = (r: Raster, x: number, y: number) => Array.from(r.data.subarray((y * r.width + x) * 4, (y * r.width + x) * 4 + 4))
const BLACK = [0, 0, 0, 255]

/** Which pixels of the output are black. */
function blackOf(r: Raster): Set<string> {
  const out = new Set<string>()
  for (let y = 0; y < r.height; y++) for (let x = 0; x < r.width; x++) if (pixel(r, x, y).join() === BLACK.join()) out.add(`${x},${y}`)
  return out
}

function rectPixels(x0: number, y0: number, x1: number, y1: number): Set<string> {
  const out = new Set<string>()
  for (let y = y0; y < y1; y++) for (let x = x0; x < x1; x++) out.add(`${x},${y}`)
  return out
}

describe('redaction', () => {
  it('blacks out every pixel under a box and nothing else', () => {
    const src = photo(40, 30)
    const box: Rect = { x: 0.25, y: 0.5, w: 0.25, h: 0.2 } // pixels 10-19 × 15-20
    const out = redactRaster(src, { rotation: 0, crop: FULL, boxes: [box] })
    expect([out.width, out.height]).toEqual([40, 30])
    expect(blackOf(out)).toEqual(rectPixels(10, 15, 20, 21))
    // Everything else is the photo as it was.
    expect(pixel(out, 9, 15)).toEqual(pixel(src, 9, 15))
    expect(pixel(out, 20, 20)).toEqual(pixel(src, 20, 20))
    // The source is left alone (the editor keeps showing it).
    expect(blackOf(src).size).toBe(0)
  })

  it('blacks out a pixel the box covers only in part', () => {
    expect(pixelBounds({ x: 0.101, y: 0.199, w: 0.1, h: 0.0015 }, 100, 100)).toEqual([10, 19, 21, 21])
    const out = redactRaster(photo(100, 100), { rotation: 0, crop: FULL, boxes: [{ x: 0.101, y: 0.199, w: 0.1, h: 0.0015 }] })
    expect(blackOf(out)).toEqual(rectPixels(10, 19, 21, 21))
  })

  it('keeps the boxes on what they cover when the photo is turned', () => {
    const src = photo(40, 30)
    const box: Rect = { x: 0.75, y: 0.5, w: 0.25, h: 0.5 } // pixels 30-39 × 15-29 of the photo
    for (const dir of [1, -1] as const) {
      for (const times of [1, 2, 3]) {
        let edits: Edits = { rotation: 0, crop: FULL, boxes: [box] }
        for (let k = 0; k < times; k++) edits = turn(edits, dir)
        const out = redactRaster(src, edits)
        // Turn the result back: exactly the photo's pixels under the box are black.
        const back = rotateRaster(out, ((360 - edits.rotation) % 360) as Edits['rotation'])
        expect([back.width, back.height]).toEqual([40, 30])
        expect(blackOf(back)).toEqual(rectPixels(30, 15, 40, 30))
        expect(pixel(back, 5, 5)).toEqual(pixel(src, 5, 5))
      }
    }
  })

  it('turns four times back to the start', () => {
    let edits = initialEdits(4000, 3000)
    edits.crop = { x: 0.1, y: 0.2, w: 0.5, h: 0.6 }
    const start = structuredClone(edits)
    for (let k = 0; k < 4; k++) edits = turn(edits, 1)
    expect(edits.rotation).toBe(0)
    expect(edits.crop.x).toBeCloseTo(start.crop.x)
    expect(edits.boxes[0]?.y).toBeCloseTo(start.boxes[0]?.y ?? NaN)
    const back = turnRect(turnRect({ x: 0.1, y: 0.2, w: 0.3, h: 0.4 }, 1), -1)
    expect([back.x, back.y, back.w, back.h].map((v) => v.toFixed(9))).toEqual(['0.100000000', '0.200000000', '0.300000000', '0.400000000'])
  })

  it('crops after burning: the kept part keeps its black, the rest is gone', () => {
    const src = photo(40, 30)
    const out = redactRaster(src, {
      rotation: 0,
      crop: { x: 0.5, y: 0, w: 0.5, h: 1 }, // the right half: x 20-39
      boxes: [{ x: 0.25, y: 0, w: 0.5, h: 0.1 }], // x 10-29 × y 0-2: only 20-29 is kept
    })
    expect([out.width, out.height]).toEqual([20, 30])
    expect(blackOf(out)).toEqual(rectPixels(0, 0, 10, 3))
    expect(pixel(out, 10, 0)).toEqual(pixel(src, 30, 0))
  })

  it('starts with one box over the team lists of a landscape sheet', () => {
    const [box] = initialEdits(4000, 2800).boxes
    expect(box).toEqual(defaultBox(4000, 2800))
    expect(box && box.x + box.w).toBeLessThanOrEqual(1)
    expect(box && box.x).toBeGreaterThan(0.5) // the right-hand side, where the lists are
    expect(clampRect({ x: 0.95, y: -0.2, w: 0.2, h: 0 })).toEqual({ x: 0.8, y: 0, w: 0.2, h: 0.01 })
  })
})
