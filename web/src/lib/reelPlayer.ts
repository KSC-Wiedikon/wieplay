// Playing a reel (docs/PLAN.md §7.7): the pieces play one after another, across videos. Nothing
// is rendered: the player seeks to a piece's start, plays to its end, then jumps to the next
// piece, switching the video source when the next piece is in another video (preloaded in a
// second, hidden player). Pure sequencing; the <video> elements are driven by ReelPlayer.

export interface ReelPiece {
  video_id: string
  start_ms: number
  end_ms: number
}

export type ReelStep =
  /** Keep playing the current piece. */
  | { kind: 'play' }
  /** The next piece goes on from here (same video, the position is already inside it). */
  | { kind: 'next'; index: number }
  /** Jump inside the same video. */
  | { kind: 'seek'; index: number; to_ms: number }
  /** Another video: switch the source, then play from `to_ms`. */
  | { kind: 'switch'; index: number; video_id: string; to_ms: number }
  /** After the last piece. */
  | { kind: 'end' }

/** A piece counts as finished this close to its end (a frame or so; the loop runs per frame). */
const END_EPSILON_MS = 40

/** Moving to piece `to`, from a position in some video (null when nothing is loaded yet). */
export function moveTo(
  pieces: readonly ReelPiece[],
  to: number,
  from: { video_id: string; t_ms: number } | null,
): ReelStep {
  const piece = pieces[to]
  if (!piece) return { kind: 'end' }
  if (from && from.video_id === piece.video_id) {
    if (piece.start_ms <= from.t_ms && from.t_ms < piece.end_ms - END_EPSILON_MS) return { kind: 'next', index: to }
    return { kind: 'seek', index: to, to_ms: piece.start_ms }
  }
  return { kind: 'switch', index: to, video_id: piece.video_id, to_ms: piece.start_ms }
}

/** What the reel does while piece `index` plays and its video is at `t_ms`. */
export function reelStep(pieces: readonly ReelPiece[], index: number, t_ms: number): ReelStep {
  const piece = pieces[index]
  if (!piece) return { kind: 'end' }
  if (t_ms < piece.end_ms - END_EPSILON_MS) return { kind: 'play' }
  return moveTo(pieces, index + 1, { video_id: piece.video_id, t_ms })
}

/** The piece to load in the hidden player: the next one, when it is in another video. */
export function preloadTarget(pieces: readonly ReelPiece[], index: number): ReelPiece | null {
  const current = pieces[index]
  const next = pieces[index + 1]
  if (!current || !next || next.video_id === current.video_id) return null
  return next
}

/** Start of each piece within the reel, and the reel's length (ms). */
export function reelTimeline(pieces: readonly ReelPiece[]): { starts: number[]; total_ms: number } {
  const starts: number[] = []
  let total = 0
  for (const piece of pieces) {
    starts.push(total)
    total += Math.max(0, piece.end_ms - piece.start_ms)
  }
  return { starts, total_ms: total }
}

/** How far into the reel a position of piece `index` is. */
export function elapsedIn(pieces: readonly ReelPiece[], index: number, t_ms: number): number {
  const piece = pieces[index]
  if (!piece) return reelTimeline(pieces).total_ms
  const { starts } = reelTimeline(pieces)
  const within = Math.min(piece.end_ms - piece.start_ms, Math.max(0, t_ms - piece.start_ms))
  return (starts[index] ?? 0) + within
}

/** The piece and the position in its video at a point of the reel (for a click on its bar). */
export function locate(pieces: readonly ReelPiece[], elapsed_ms: number): { index: number; t_ms: number } | null {
  if (pieces.length === 0) return null
  let offset = 0
  for (const [index, piece] of pieces.entries()) {
    const length = Math.max(0, piece.end_ms - piece.start_ms)
    if (elapsed_ms < offset + length || index === pieces.length - 1) {
      return { index, t_ms: piece.start_ms + Math.min(length, Math.max(0, elapsed_ms - offset)) }
    }
    offset += length
  }
  return null
}
