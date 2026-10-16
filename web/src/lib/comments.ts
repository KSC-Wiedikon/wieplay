import type { Comment } from './types'

/** Comments on the whole video first, then by moment, then by when they were written (as the API sorts). */
export function sortComments(comments: readonly Comment[]): Comment[] {
  return [...comments].sort((a, b) => {
    if (a.t_ms === null || b.t_ms === null) {
      if (a.t_ms !== b.t_ms) return a.t_ms === null ? -1 : 1
    } else if (a.t_ms !== b.t_ms) {
      return a.t_ms - b.t_ms
    }
    return a.created_at.localeCompare(b.created_at)
  })
}

/** Moments with comments, for markers on a timeline. */
export function commentMoments(comments: readonly Comment[]): { id: string; t_ms: number }[] {
  return comments.flatMap((c) => (c.t_ms === null ? [] : [{ id: c.id, t_ms: c.t_ms }]))
}
