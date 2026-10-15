import { describe, expect, it } from 'vitest'
import { elapsedIn, locate, moveTo, preloadTarget, reelStep, reelTimeline, type ReelPiece } from './reelPlayer'

const A = 'video-a'
const B = 'video-b'
const pieces: ReelPiece[] = [
  { video_id: A, start_ms: 10_000, end_ms: 15_000 },
  { video_id: A, start_ms: 14_000, end_ms: 20_000 }, // overlaps the first: goes on without a seek
  { video_id: A, start_ms: 60_000, end_ms: 65_000 },
  { video_id: B, start_ms: 5_000, end_ms: 8_000 },
  { video_id: A, start_ms: 90_000, end_ms: 92_000 },
]

describe('reelStep', () => {
  it('keeps playing inside a piece, also before its start (a seek by the viewer)', () => {
    expect(reelStep(pieces, 0, 12_000)).toEqual({ kind: 'play' })
    expect(reelStep(pieces, 0, 9_000)).toEqual({ kind: 'play' })
  })

  it('goes on into an overlapping piece of the same video without seeking', () => {
    expect(reelStep(pieces, 0, 15_000)).toEqual({ kind: 'next', index: 1 })
  })

  it('seeks to the next piece of the same video', () => {
    expect(reelStep(pieces, 1, 20_000)).toEqual({ kind: 'seek', index: 2, to_ms: 60_000 })
  })

  it('switches the video for a piece of another video, and back', () => {
    expect(reelStep(pieces, 2, 65_000)).toEqual({ kind: 'switch', index: 3, video_id: B, to_ms: 5_000 })
    expect(reelStep(pieces, 3, 8_000)).toEqual({ kind: 'switch', index: 4, video_id: A, to_ms: 90_000 })
  })

  it('ends after the last piece, a frame before its end', () => {
    expect(reelStep(pieces, 4, 91_970)).toEqual({ kind: 'end' })
    expect(reelStep(pieces, 4, 91_900)).toEqual({ kind: 'play' })
    expect(reelStep([], 0, 0)).toEqual({ kind: 'end' })
  })
})

describe('moveTo', () => {
  it('loads the first piece when nothing is loaded yet', () => {
    expect(moveTo(pieces, 0, null)).toEqual({ kind: 'switch', index: 0, video_id: A, to_ms: 10_000 })
  })

  it('jumps within the same video and switches to another one', () => {
    expect(moveTo(pieces, 2, { video_id: A, t_ms: 11_000 })).toEqual({ kind: 'seek', index: 2, to_ms: 60_000 })
    expect(moveTo(pieces, 3, { video_id: A, t_ms: 11_000 })).toEqual({ kind: 'switch', index: 3, video_id: B, to_ms: 5_000 })
    expect(moveTo(pieces, 9, { video_id: A, t_ms: 11_000 })).toEqual({ kind: 'end' })
  })
})

describe('preloadTarget', () => {
  it('names the next piece only when it is in another video', () => {
    expect(preloadTarget(pieces, 1)).toBeNull()
    expect(preloadTarget(pieces, 2)).toEqual(pieces[3])
    expect(preloadTarget(pieces, 3)).toEqual(pieces[4])
    expect(preloadTarget(pieces, 4)).toBeNull()
  })
})

describe('reel time', () => {
  it('adds up the pieces', () => {
    expect(reelTimeline(pieces)).toEqual({ starts: [0, 5_000, 11_000, 16_000, 19_000], total_ms: 21_000 })
  })

  it('tells how far into the reel a position is', () => {
    expect(elapsedIn(pieces, 0, 10_000)).toBe(0)
    expect(elapsedIn(pieces, 2, 62_500)).toBe(13_500)
    // Before the piece's start counts as its start; after its end as its end.
    expect(elapsedIn(pieces, 3, 1_000)).toBe(16_000)
    expect(elapsedIn(pieces, 3, 9_000)).toBe(19_000)
  })

  it('finds the piece and the position for a point of the reel', () => {
    expect(locate(pieces, 0)).toEqual({ index: 0, t_ms: 10_000 })
    expect(locate(pieces, 17_000)).toEqual({ index: 3, t_ms: 6_000 })
    expect(locate(pieces, 99_000)).toEqual({ index: 4, t_ms: 92_000 })
    expect(locate([], 0)).toBeNull()
  })
})
