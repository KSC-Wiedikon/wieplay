import type { TFunction } from 'i18next'
import type { ImportPlatform, SportName, Video } from './types'

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

// Imports from YouTube and Twitch links (PLAN §7.2.2). The patterns mirror the API's
// (core/imports.py), so the upload page can say at once what a pasted link is; the API
// parses it again and decides.

const YOUTUBE_ID = /^[A-Za-z0-9_-]{11}$/
const TWITCH_ID = /^[0-9]{5,15}$/
const YOUTUBE_CHANNEL_ID = /^UC[A-Za-z0-9_-]{22}$/
const YOUTUBE_HOSTS: ReadonlySet<string> = new Set(['youtube.com', 'www.youtube.com', 'm.youtube.com'])
const TWITCH_HOSTS: ReadonlySet<string> = new Set(['twitch.tv', 'www.twitch.tv', 'm.twitch.tv'])
const LINK_MAX = 500

const PLATFORM_NAMES: Record<ImportPlatform, string> = { youtube: 'YouTube', twitch: 'Twitch' }

/** "YouTube", "Twitch"; an unknown platform as it is. */
export function platformName(platform: string): string {
  return Object.hasOwn(PLATFORM_NAMES, platform) ? PLATFORM_NAMES[platform as ImportPlatform] : platform
}

export interface VideoLink {
  platform: ImportPlatform
  id: string
  /** The canonical address, the one WiePlay fetches. */
  url: string
}

/**
 * A plain web address (scheme optional) → host, path and query. Null for other schemes, user
 * info, ports and spaces. Split by hand like Python's urlsplit: no normalising of the path.
 */
function splitLink(raw: string): { host: string; path: string; query: string } | null {
  if (raw.length > LINK_MAX || /\s/.test(raw)) return null
  const parts = /^([A-Za-z][A-Za-z0-9+.-]*):\/\/([^/?#]*)([^?#]*)(?:\?([^#]*))?/.exec(
    raw.includes('://') ? raw : `https://${raw}`,
  )
  if (!parts) return null
  const [, scheme = '', authority = '', path = '', query = ''] = parts
  if (!['http', 'https'].includes(scheme.toLowerCase()) || /[@:]/.test(authority)) return null
  return { host: authority.toLowerCase(), path: path.replace(/\/+$/, ''), query }
}

function videoLink(platform: ImportPlatform, id: string): VideoLink {
  const url = platform === 'youtube' ? `https://www.youtube.com/watch?v=${id}` : `https://www.twitch.tv/videos/${id}`
  return { platform, id, url }
}

/**
 * A YouTube video (youtube.com/watch?v=…, youtu.be/…, youtube.com/live/… or /shorts/…) or a
 * Twitch video (twitch.tv/videos/…); null for anything else, channels and playlists included.
 */
export function parseVideoLink(text: string): VideoLink | null {
  const parts = splitLink(text.trim())
  if (!parts) return null
  const { host, path, query } = parts
  if (YOUTUBE_HOSTS.has(host)) {
    if (path === '/watch') {
      // Empty values don't count, as in Python's parse_qs.
      const ids = new URLSearchParams(query).getAll('v').filter((v) => v !== '')
      const [id] = ids
      if (ids.length === 1 && id !== undefined && YOUTUBE_ID.test(id)) return videoLink('youtube', id)
    }
    const id = /^\/(?:live|shorts)\/([^/]+)$/.exec(path)?.[1]
    if (id !== undefined && YOUTUBE_ID.test(id)) return videoLink('youtube', id)
  } else if (host === 'youtu.be') {
    const id = path.replace(/^\/+/, '')
    if (YOUTUBE_ID.test(id)) return videoLink('youtube', id)
  } else if (TWITCH_HOSTS.has(host)) {
    const id = /^\/videos\/([^/]+)$/.exec(path)?.[1]
    if (id !== undefined && TWITCH_ID.test(id)) return videoLink('twitch', id)
  }
  return null
}

/**
 * An allow-listed channel as people write it, with its page: "@kscwiedikon" or "UC…" on
 * YouTube, the login on Twitch. The API stores handles without the @.
 */
export function channelDisplay(channel: { platform: string; ref: string }): { text: string; url: string | null } {
  const ref = encodeURIComponent(channel.ref)
  if (channel.platform === 'youtube') {
    return YOUTUBE_CHANNEL_ID.test(channel.ref)
      ? { text: channel.ref, url: `https://www.youtube.com/channel/${ref}` }
      : { text: `@${channel.ref}`, url: `https://www.youtube.com/@${ref}` }
  }
  if (channel.platform === 'twitch') return { text: channel.ref, url: `https://www.twitch.tv/${ref}` }
  return { text: channel.ref, url: null }
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

/** Videos by month of recording, newest month first and newest video first in each. */
export function byMonth(videos: readonly Video[]): { month: string; videos: Video[] }[] {
  const groups: { month: string; videos: Video[] }[] = []
  for (const video of newestFirst(videos)) {
    const month = video.recorded_on.slice(0, 7)
    const last = groups.at(-1)
    if (last?.month === month) last.videos.push(video)
    else groups.push({ month, videos: [video] })
  }
  return groups
}

/** "Oktober 2026" / "October 2026" for "2026-10". */
export function monthLabel(month: string, language: string): string {
  const [year, mm] = month.split('-').map(Number) as [number, number]
  return new Intl.DateTimeFormat(language === 'de' ? 'de-CH' : 'en-GB', { month: 'long', year: 'numeric' }).format(
    new Date(Date.UTC(year, mm - 1, 15)),
  )
}

/** "Match"; null for a kind WiePlay no longer offers (training rows from before 16.10.2026). */
export function kindLabel(t: TFunction, kind: string): string | null {
  return kind === 'match' ? t('video.kind.match') : null
}

/** "1920×1080 · 50 fps · 1:32:10" parts that are known. */
export function videoFacts(video: Video): { resolution: string | null; fps: number | null } {
  return {
    resolution: video.width && video.height ? `${video.width}×${video.height}` : null,
    fps: video.fps ? Math.round(video.fps * 100) / 100 : null,
  }
}
