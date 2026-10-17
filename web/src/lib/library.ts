// Clips, reels, comments and exports (M3, docs/PLAN.md §6.8, §7.7): the API calls and the
// query hooks. Kept out of api.ts and queries.ts so the pages that use them (all loaded on
// demand) carry them, not the main bundle.

import '../i18n/pages'
import { keepPreviousData, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, apiRequest } from './api'
import type { Person } from './clips'
import { useTeams } from './queries'
import { keys } from './queryClient'
import { compareTeams } from './teams'
import { newestFirst } from './video'
import type {
  Clip,
  ClipIn,
  Comment,
  CommentIn,
  ExportIn,
  Piece,
  Reel,
  ReelDetail,
  ReelIn,
  ReelItemIn,
  ReelQuery,
  Taxonomy,
  Team,
  TeamDetail,
  VideoExport,
  VideoPerson,
} from './types'

export const libraryApi = {
  videoClips: (videoId: string, signal?: AbortSignal) => apiRequest<Clip[]>(`/api/videos/${videoId}/clips`, { signal }),
  myClips: (signal?: AbortSignal) => apiRequest<Clip[]>('/api/clips/mine', { signal }),
  putClip: (id: string, body: ClipIn) => apiRequest<Clip>(`/api/clips/${id}`, { method: 'PUT', body }),
  deleteClip: (id: string) => apiRequest<undefined>(`/api/clips/${id}`, { method: 'DELETE' }),

  reels: (signal?: AbortSignal) => apiRequest<Reel[]>('/api/reels', { signal }),
  reel: (id: string, signal?: AbortSignal) => apiRequest<ReelDetail>(`/api/reels/${id}`, { signal }),
  createReel: (body: ReelIn) => apiRequest<Reel>('/api/reels', { method: 'POST', body }),
  updateReel: (id: string, body: ReelIn) => apiRequest<Reel>(`/api/reels/${id}`, { method: 'PUT', body }),
  deleteReel: (id: string) => apiRequest<undefined>(`/api/reels/${id}`, { method: 'DELETE' }),
  reelItems: (id: string, signal?: AbortSignal) => apiRequest<Piece[]>(`/api/reels/${id}/items`, { signal }),
  /** 1..100 items at the end of my manual reel. 409: a smart reel; 413: over 500 items. */
  appendReelItems: (id: string, items: ReelItemIn[]) =>
    apiRequest<ReelDetail>(`/api/reels/${id}/items`, { method: 'POST', body: { items } }),
  previewReel: (query: ReelQuery, signal?: AbortSignal) =>
    apiRequest<Piece[]>('/api/reels/preview', { method: 'POST', body: query, signal }),
  condensed: (videoId: string, signal?: AbortSignal) => apiRequest<Piece[]>(`/api/videos/${videoId}/condensed`, { signal }),

  comments: (videoId: string, signal?: AbortSignal) => apiRequest<Comment[]>(`/api/videos/${videoId}/comments`, { signal }),
  videoPeople: (videoId: string, signal?: AbortSignal) => apiRequest<VideoPerson[]>(`/api/videos/${videoId}/people`, { signal }),
  addComment: (videoId: string, body: CommentIn) =>
    apiRequest<Comment>(`/api/videos/${videoId}/comments`, { method: 'POST', body }),
  editComment: (id: string, body: string) => apiRequest<Comment>(`/api/comments/${id}`, { method: 'PATCH', body: { body } }),
  deleteComment: (id: string) => apiRequest<undefined>(`/api/comments/${id}`, { method: 'DELETE' }),

  exports: (signal?: AbortSignal) => apiRequest<VideoExport[]>('/api/exports', { signal }),
  createExport: (body: ExportIn) => apiRequest<VideoExport>('/api/exports', { method: 'POST', body }),
  deleteExport: (id: string) => apiRequest<undefined>(`/api/exports/${id}`, { method: 'DELETE' }),

  taxonomy: (sport: string, signal?: AbortSignal) =>
    apiRequest<Taxonomy>(`/api/taxonomies/${encodeURIComponent(sport)}`, { signal }),
}

/** Same-origin link that answers with a 302 to a 5-minute signed download (never fetched). */
export function downloadUrl(exportId: string): string {
  return `/api/exports/${exportId}/download`
}

/** A reel item made from a piece of a reel (to save a manual reel again). */
export function pieceToItem(piece: Piece): ReelItemIn {
  return {
    video_id: piece.video_id,
    start_ms: piece.start_ms,
    end_ms: piece.end_ms,
    event_id: piece.event_id,
    clip_id: piece.clip_id,
    caption: piece.caption,
  }
}

// Clips

export function useVideoClips(videoId: string) {
  return useQuery({ queryKey: keys.videoClips(videoId), queryFn: ({ signal }) => libraryApi.videoClips(videoId, signal) })
}

export function useMyClips() {
  return useQuery({ queryKey: keys.myClips, queryFn: ({ signal }) => libraryApi.myClips(signal) })
}

export function useSaveClip() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: ClipIn }) => libraryApi.putClip(id, body),
    onSuccess: async (clip) => {
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.videoClips(clip.video_id) }),
        client.invalidateQueries({ queryKey: keys.myClips }),
      ])
    },
  })
}

export function useDeleteClip() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (clip: Clip) => libraryApi.deleteClip(clip.id),
    onSuccess: async (_data, clip) => {
      client.setQueryData<Clip[]>(keys.videoClips(clip.video_id), (list) => list?.filter((c) => c.id !== clip.id))
      client.setQueryData<Clip[]>(keys.myClips, (list) => list?.filter((c) => c.id !== clip.id))
      await Promise.all([
        client.invalidateQueries({ queryKey: keys.videoClips(clip.video_id) }),
        client.invalidateQueries({ queryKey: keys.myClips }),
      ])
    },
  })
}

// Reels

export function useReels() {
  return useQuery({ queryKey: keys.reels, queryFn: ({ signal }) => libraryApi.reels(signal) })
}

/** A reel, with how many moments the viewer may watch and how long they last. */
export function useReel(id: string, enabled = true) {
  return useQuery({
    queryKey: keys.reel(id),
    queryFn: ({ signal }) => libraryApi.reel(id, signal),
    enabled,
    // A smart reel grows as tagging continues: counted fresh when opened.
    staleTime: 0,
  })
}

export function useReelItems(id: string, enabled = true) {
  return useQuery({
    queryKey: keys.reelItems(id),
    queryFn: ({ signal }) => libraryApi.reelItems(id, signal),
    enabled,
    // A smart reel grows as tagging continues: evaluated fresh when opened.
    staleTime: 0,
  })
}

/** A query evaluated without saving it; the previous answer stays while the next one loads. */
export function useReelPreview(query: ReelQuery | null) {
  return useQuery({
    queryKey: keys.reelPreview(query),
    queryFn: ({ signal }) => libraryApi.previewReel(query ?? {}, signal),
    enabled: query !== null,
    placeholderData: keepPreviousData,
  })
}

export function useSaveReel() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, body }: { id?: string; body: ReelIn }) =>
      id ? libraryApi.updateReel(id, body) : libraryApi.createReel(body),
    onSuccess: async () => {
      await client.invalidateQueries({ queryKey: keys.reels })
    },
  })
}

export function useDeleteReel() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (reel: Reel) => libraryApi.deleteReel(reel.id),
    onSuccess: async (_data, reel) => {
      client.setQueryData<Reel[]>(keys.reels, (list) => list?.filter((r) => r.id !== reel.id))
      await client.invalidateQueries({ queryKey: keys.reels })
    },
  })
}

/**
 * Adds a moment at the end of one of my manual reels, or starts a new private reel with it.
 * The server appends under a lock and checks only the new item, so two adds never lose one
 * and an old item whose video went away doesn't block it.
 */
export function useAddToReel() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async ({ reel, title, item }: { reel: Reel | null; title: string; item: ReelItemIn }): Promise<Reel> => {
      if (!reel) return libraryApi.createReel({ title, kind: 'manual', items: [item], visibility: 'private' })
      return (await libraryApi.appendReelItems(reel.id, [item])).reel
    },
    // After a refusal too: the reel may be gone, or no longer take items.
    onSettled: async () => {
      await client.invalidateQueries({ queryKey: keys.reels })
    },
  })
}

// Comments

export function useComments(videoId: string) {
  return useQuery({ queryKey: keys.comments(videoId), queryFn: ({ signal }) => libraryApi.comments(videoId, signal) })
}

/** Who a comment on this video can mention or be addressed to (the team's people, any season). */
export function useVideoPeople(videoId: string) {
  return useQuery({ queryKey: keys.videoPeople(videoId), queryFn: ({ signal }) => libraryApi.videoPeople(videoId, signal) })
}

export function useAddComment(videoId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: CommentIn) => libraryApi.addComment(videoId, body),
    onSuccess: async (comment) => {
      client.setQueryData<Comment[]>(keys.comments(videoId), (list) => (list ? [...list, comment] : [comment]))
      await client.invalidateQueries({ queryKey: keys.comments(videoId) })
    },
  })
}

export function useEditComment(videoId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ comment, body }: { comment: Comment; body: string }) => libraryApi.editComment(comment.id, body),
    onSuccess: (updated) => {
      client.setQueryData<Comment[]>(keys.comments(videoId), (list) => list?.map((c) => (c.id === updated.id ? updated : c)))
    },
  })
}

export function useDeleteComment(videoId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (comment: Comment) => libraryApi.deleteComment(comment.id),
    onSuccess: (_data, comment) => {
      client.setQueryData<Comment[]>(keys.comments(videoId), (list) => list?.filter((c) => c.id !== comment.id))
    },
  })
}

// Exports

/** My exports; refreshes every 3 s while one is being rendered. */
export function useExports() {
  return useQuery({
    queryKey: keys.exports,
    queryFn: ({ signal }) => libraryApi.exports(signal),
    refetchInterval: (query) => (query.state.data?.some((e) => e.status === 'queued') ? 3_000 : false),
  })
}

/** Fired when an export was queued: the app then watches it and says when it is ready. */
export const EXPORT_QUEUED = 'wieplay:export-queued'

export function useCreateExport() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: ExportIn) => libraryApi.createExport(body),
    onSuccess: async () => {
      window.dispatchEvent(new Event(EXPORT_QUEUED))
      await client.invalidateQueries({ queryKey: keys.exports })
    },
  })
}

export function useDeleteExport() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (item: VideoExport) => libraryApi.deleteExport(item.id),
    onSuccess: async (_data, item) => {
      client.setQueryData<VideoExport[]>(keys.exports, (list) => list?.filter((e) => e.id !== item.id))
      await client.invalidateQueries({ queryKey: keys.exports })
    },
  })
}

export function useTaxonomy(sport: string | null) {
  return useQuery({
    queryKey: keys.taxonomy(sport ?? ''),
    queryFn: ({ signal }) => libraryApi.taxonomy(sport ?? '', signal),
    enabled: sport !== null,
    staleTime: Infinity,
  })
}

// People and teams for the reel builder

/**
 * The players of a team, for pickers: the team's people (every season) as the server lists
 * them for the team's newest ready video. Coaches and players read the same list.
 */
export function useTeamPeople(team: TeamDetail | undefined): { people: Person[]; pending: boolean } {
  const videos = useQuery({
    queryKey: keys.teamVideos(team?.id ?? 0),
    queryFn: ({ signal }) => api.teamVideos(team?.id ?? 0, signal),
    enabled: team !== undefined,
  })
  const newest = newestFirst((videos.data ?? []).filter((v) => v.status === 'ready'))[0]
  const people = useQuery({
    queryKey: keys.videoPeople(newest?.id ?? ''),
    queryFn: ({ signal }) => libraryApi.videoPeople(newest?.id ?? '', signal),
    enabled: newest !== undefined,
  })
  const players = (people.data ?? [])
    .filter((p) => p.roles.includes('player'))
    .map((p) => ({ member_id: p.member_id, name: p.name, number: p.number }))
    .sort((a, b) => a.name.localeCompare(b.name, 'de-CH'))
  return { people: players, pending: videos.isPending || (newest !== undefined && people.isPending) }
}

/** The team row (newest season) of a lineage, among the teams I see; for editing a saved reel. */
export function useLineageTeam(lineageId: string | null): { team: Team | undefined; pending: boolean } {
  const teams = useTeams()
  const team = (teams.data ?? []).filter((t) => t.lineage_id === lineageId).sort(compareTeams)[0]
  return { team, pending: lineageId !== null && teams.isPending }
}
