import type { SportName, Video } from './types'

/** The API decides by extension only (415 otherwise). */
export const VIDEO_EXTENSIONS = ['.mp4', '.mov', '.m4v', '.mts', '.m2ts', '.mkv', '.avi'] as const

/** For <input accept>: the extensions, plus MP4/QuickTime so phones offer their photo library. */
export const VIDEO_ACCEPT = [...VIDEO_EXTENSIONS, 'video/mp4', 'video/quicktime'].join(',')

export function isVideoFileName(name: string): boolean {
  const lower = name.toLowerCase()
  return VIDEO_EXTENSIONS.some((ext) => lower.endsWith(ext))
}

/**
 * The team's folder in the NAS inbox share, as the API names it (pipeline.team_folder):
 * vb-H3, vb-DU23-1, bb-Herren-1.
 */
export function inboxFolder(team: { id: number; sport: SportName; name: string }): string {
  const name = team.name.replace(/[^A-Za-z0-9-]+/g, '-').replace(/^-+|-+$/g, '') || String(team.id)
  return `${team.sport === 'volleyball' ? 'vb' : 'bb'}-${name}`
}

/** Inbox paths start with the team folder; the API compares it case-insensitively. */
export function inTeamFolder(path: string, folder: string): boolean {
  const slash = path.indexOf('/')
  return slash > 0 && path.slice(0, slash).toLowerCase() === folder.toLowerCase()
}


const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

export function isUuid(value: string | undefined): value is string {
  return value !== undefined && UUID.test(value)
}

/** Latest recording first; same day: latest upload first. */
export function newestFirst(videos: readonly Video[]): Video[] {
  return [...videos].sort(
    (a, b) => b.recorded_on.localeCompare(a.recorded_on) || b.created_at.localeCompare(a.created_at),
  )
}

/** "1920×1080 · 50 fps · 1:32:10" parts that are known. */
export function videoFacts(video: Video): { resolution: string | null; fps: number | null } {
  return {
    resolution: video.width && video.height ? `${video.width}×${video.height}` : null,
    fps: video.fps ? Math.round(video.fps * 100) / 100 : null,
  }
}
