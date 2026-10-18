// Redacting a photo of the paper scoresheet before it leaves the device (docs/PLAN.md §10.3a,
// SS3; the owner's privacy OK of 18.10.2026). The Matchblatt lists every player's date of birth:
// the coach covers it (and whatever else) with black boxes, turns and crops the photo, and only
// the result is uploaded, a new JPEG whose pixels under the boxes are black. Drawing the photo on
// a canvas and encoding it again also drops the camera's EXIF and GPS; the server refuses any
// upload that still carries them.
//
// The pixel work is plain functions on RGBA buffers (tested without a browser); the canvas only
// decodes the photo and encodes the JPEG.

/** A rectangle as fractions (0-1) of the turned photo, before the crop. */
export interface Rect {
  x: number
  y: number
  w: number
  h: number
}

export type Rotation = 0 | 90 | 180 | 270

/** What the coach did: the photo turned clockwise, the part kept, the black boxes. */
export interface Edits {
  rotation: Rotation
  crop: Rect
  boxes: Rect[]
}

export interface Raster {
  width: number
  height: number
  /** RGBA, row by row, as ImageData. */
  data: Uint8ClampedArray
}

export const FULL: Rect = { x: 0, y: 0, w: 1, h: 1 }
/** The longer side of the uploaded JPEG: plenty for reading a box, and a phone copes with it. */
export const MAX_SIDE = 3200
export const JPEG_QUALITY = 0.9
export const MIN_BOX = 0.01

/**
 * The prefilled box: the team lists at the lower right of the Matchblatt (FIVB layout, a sheet
 * photographed upright in landscape), where each player's date of birth stands next to the
 * name. The coach moves and sizes it over the dates of birth; on a portrait photo it starts in
 * the middle.
 */
export function defaultBox(width: number, height: number): Rect {
  if (width >= height) return { x: 0.77, y: 0.52, w: 0.22, h: 0.3 }
  return { x: 0.3, y: 0.4, w: 0.4, h: 0.2 }
}

export function initialEdits(width: number, height: number): Edits {
  return { rotation: 0, crop: { ...FULL }, boxes: [defaultBox(width, height)] }
}

const clamp = (value: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, value))

/** Inside the photo, at least MIN_BOX wide and high. */
export function clampRect(r: Rect): Rect {
  const w = clamp(r.w, MIN_BOX, 1)
  const h = clamp(r.h, MIN_BOX, 1)
  return { x: clamp(r.x, 0, 1 - w), y: clamp(r.y, 0, 1 - h), w, h }
}

/** A rectangle from two corners, any order. */
export function fromCorners(ax: number, ay: number, bx: number, by: number): Rect {
  const x0 = clamp(Math.min(ax, bx), 0, 1)
  const y0 = clamp(Math.min(ay, by), 0, 1)
  return { x: x0, y: y0, w: clamp(Math.max(ax, bx), 0, 1) - x0, h: clamp(Math.max(ay, by), 0, 1) - y0 }
}

/** The same place on the photo after a quarter turn: clockwise (1) or back (-1). */
export function turnRect(r: Rect, dir: 1 | -1): Rect {
  // Clockwise, a point (x, y) moves to (1 - y, x); back, to (y, 1 - x).
  return dir === 1 ? { x: 1 - r.y - r.h, y: r.x, w: r.h, h: r.w } : { x: r.y, y: 1 - r.x - r.w, w: r.h, h: r.w }
}

/** Turn the photo a quarter; the boxes and the crop stay on what they cover. */
export function turn(edits: Edits, dir: 1 | -1): Edits {
  return {
    rotation: (((edits.rotation + dir * 90) % 360) + 360) % 360 as Rotation,
    crop: turnRect(edits.crop, dir),
    boxes: edits.boxes.map((b) => turnRect(b, dir)),
  }
}

/** Is any part of the photo cut away? */
export function isCropped(crop: Rect): boolean {
  return crop.x > 0.0005 || crop.y > 0.0005 || crop.w < 0.999 || crop.h < 0.999
}

/** The pixels a rectangle touches, [x0, y0, x1, y1) in a width × height image: a pixel the box
 * covers only in part is blacked out too. */
export function pixelBounds(r: Rect, width: number, height: number): [number, number, number, number] {
  const x0 = clamp(Math.floor(r.x * width), 0, width)
  const y0 = clamp(Math.floor(r.y * height), 0, height)
  const x1 = clamp(Math.ceil((r.x + r.w) * width), 0, width)
  const y1 = clamp(Math.ceil((r.y + r.h) * height), 0, height)
  return [x0, y0, x1, y1]
}

/** The crop keeps whole pixels inside the photo, at least one. */
function cropBounds(r: Rect, width: number, height: number): [number, number, number, number] {
  const x0 = clamp(Math.round(r.x * width), 0, width - 1)
  const y0 = clamp(Math.round(r.y * height), 0, height - 1)
  const x1 = clamp(Math.round((r.x + r.w) * width), x0 + 1, width)
  const y1 = clamp(Math.round((r.y + r.h) * height), y0 + 1, height)
  return [x0, y0, x1, y1]
}

/** The raster turned clockwise by `rotation`. */
export function rotateRaster(src: Raster, rotation: Rotation): Raster {
  if (rotation === 0) return { width: src.width, height: src.height, data: new Uint8ClampedArray(src.data) }
  const { width: w, height: h } = src
  const quarter = rotation !== 180
  const out: Raster = { width: quarter ? h : w, height: quarter ? w : h, data: new Uint8ClampedArray(w * h * 4) }
  const s = new Uint32Array(src.data.buffer, src.data.byteOffset, w * h)
  const d = new Uint32Array(out.data.buffer)
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      let tx: number
      let ty: number
      if (rotation === 90) {
        tx = h - 1 - y
        ty = x
      } else if (rotation === 180) {
        tx = w - 1 - x
        ty = h - 1 - y
      } else {
        tx = y
        ty = w - 1 - x
      }
      d[ty * out.width + tx] = s[y * w + x] ?? 0
    }
  }
  return out
}

/** Black, opaque, over every pixel the box touches. */
export function burnBox(raster: Raster, box: Rect): void {
  const [x0, y0, x1, y1] = pixelBounds(box, raster.width, raster.height)
  for (let y = y0; y < y1; y++) {
    for (let x = x0; x < x1; x++) {
      const i = (y * raster.width + x) * 4
      raster.data[i] = 0
      raster.data[i + 1] = 0
      raster.data[i + 2] = 0
      raster.data[i + 3] = 255
    }
  }
}

/** The part of the raster inside the crop. */
export function cropRaster(src: Raster, crop: Rect): Raster {
  const [x0, y0, x1, y1] = cropBounds(crop, src.width, src.height)
  const width = x1 - x0
  const height = y1 - y0
  const data = new Uint8ClampedArray(width * height * 4)
  for (let y = 0; y < height; y++) {
    const from = ((y0 + y) * src.width + x0) * 4
    data.set(src.data.subarray(from, from + width * 4), y * width * 4)
  }
  return { width, height, data }
}

/** What is uploaded: the photo turned, the boxes burned in, then cropped. */
export function redactRaster(src: Raster, edits: Edits): Raster {
  const turned = rotateRaster(src, edits.rotation)
  for (const box of edits.boxes) burnBox(turned, box)
  return cropRaster(turned, edits.crop)
}

/** The photo's size, turned as edited. */
export function turnedSize(width: number, height: number, rotation: Rotation): [number, number] {
  return rotation % 180 === 0 ? [width, height] : [height, width]
}

// --- in the browser ----------------------------------------------------------------------------

/** Decode a photo the way the editor shows it (an <img>: the camera's orientation applied). */
export function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.decoding = 'async'
    image.onload = () => resolve(image)
    image.onerror = () => reject(new Error('unreadable'))
    image.src = url
  })
}

/**
 * The redacted JPEG: the photo drawn on a canvas (at most MAX_SIDE), the edits applied to its
 * pixels, encoded again. Nothing of the original file but its pixels reaches the result.
 */
export async function redactToJpeg(image: HTMLImageElement, edits: Edits): Promise<Blob> {
  const scale = Math.min(1, MAX_SIDE / Math.max(image.naturalWidth, image.naturalHeight))
  const width = Math.max(1, Math.round(image.naturalWidth * scale))
  const height = Math.max(1, Math.round(image.naturalHeight * scale))
  const source = document.createElement('canvas')
  source.width = width
  source.height = height
  const sctx = source.getContext('2d', { willReadFrequently: true })
  if (!sctx) throw new Error('no canvas')
  sctx.drawImage(image, 0, 0, width, height)
  const pixels = sctx.getImageData(0, 0, width, height)
  const out = redactRaster({ width, height, data: pixels.data }, edits)
  source.width = 0 // let the phone free the first canvas now
  const target = document.createElement('canvas')
  target.width = out.width
  target.height = out.height
  const tctx = target.getContext('2d')
  if (!tctx) throw new Error('no canvas')
  tctx.putImageData(new ImageData(new Uint8ClampedArray(out.data), out.width, out.height), 0, 0)
  const blob = await new Promise<Blob | null>((resolve) => target.toBlob(resolve, 'image/jpeg', JPEG_QUALITY))
  target.width = 0
  if (!blob) throw new Error('no jpeg')
  return blob
}
