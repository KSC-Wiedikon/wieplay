// Shapes returned by the Nomal API (backend/nomal/api).

export type SportName = 'volleyball' | 'basketball'

export interface Team {
  id: number
  name: string
  sport: SportName
  season: string
  league: string | null
  /** The user's relations to the team (player, coach, captain, team_responsible). Empty for admins. */
  rel: string[]
}

export interface TeamDetail extends Team {
  /** Same team across seasons (UUID). */
  lineage_id: string
  /** Coach or team manager of this season's team, or sport admin. */
  can_upload: boolean
}

export interface Me {
  member: { id: number; uuid: string; name: string }
  roles: string[]
  is_admin: boolean
  sport_admin: SportName[]
  teams: Team[]
}

export interface Game {
  id: number
  date: string
  time: string | null
  home_name: string
  away_name: string
  our_side: 'home' | 'away' | null
  league: string | null
  hall_name: string | null
  home_score: number | null
  away_score: number | null
  /** Per-set points as [home, away]. */
  sets: [number, number][] | null
}

export interface AdminTeam {
  id: number
  name: string
  sport: SportName
  season: string
  league: string | null
  enabled: boolean
}

export interface SyncStatus {
  last_run: string | null
  last_ok: string | null
  error: string | null
  counts: { teams: number; members: number; games: number }
}

// Video (M1).

export interface VideoConfig {
  /** False while object storage is not configured: no uploads, no playback. */
  enabled: boolean
  part_size: number
  max_file_bytes: number
  max_files: number
}

/** Nomal handles matches only (decided 16.10.2026). */
export type VideoKind = 'match'
/**
 * `importing`: a worker fetches the video from its YouTube or Twitch link, before the ingest.
 * `refused`: the filming rules (or, for an import, the channel allow-list) turned the video
 * down after the upload; it can't be retried.
 */
export type VideoStatus = 'uploading' | 'importing' | 'queued' | 'processing' | 'ready' | 'failed' | 'refused'

/** Where imported videos come from (PLAN §7.2.2). */
export type ImportPlatform = 'youtube' | 'twitch'

/** An imported video's origin; set once the import is done. */
export interface ImportedFrom {
  /** An ImportPlatform. */
  platform: string
  /** The canonical address Nomal fetched. */
  url: string
  /** The channel's name as the platform shows it. */
  channel: string | null
  /** The video's title on the platform. */
  title: string | null
}

export interface Rendition {
  name: string
  width: number
  height: number
  fps: number
}

export interface VideoFile {
  id: string
  part_no: number
  name: string
  size: number
  part_size: number
  part_count: number
  uploaded: boolean
}

export interface Video {
  id: string
  team_id: number
  team_name: string
  team_season: string
  sport: SportName
  /** A VideoKind; rows from before 16.10.2026 may still say 'training'. */
  kind: string
  /** null: a match that is not in Wiedisync (friendly, tournament, cup). */
  game_id: number | null
  title: string
  /** yyyy-mm-dd */
  recorded_on: string
  /** file | inbox | import */
  source: string
  imported_from: ImportedFrom | null
  status: VideoStatus
  duration_ms: number | null
  width: number | null
  height: number | null
  fps: number | null
  has_audio: boolean | null
  meets_standard: boolean | null
  /** Why the video is below the filming standard, e.g. ['720p', '25fps']. */
  standard_issues: string[]
  renditions: Rendition[]
  has_thumbs: boolean
  /** 0..1 while queued or processing, else null. */
  progress: number | null
  error: string | null
  created_at: string
  created_by: number
  can_delete: boolean
  can_upload: boolean
  files: VideoFile[]
}

/** A match: a Wiedisync game, or a date and a title for one that is not in Wiedisync. */
export interface VideoCreate {
  game_id?: number
  recorded_on?: string
  title?: string
  /** Exactly one source: files to upload, inbox files or a YouTube/Twitch link. */
  files?: { name: string; size: number; type: string }[]
  inbox_file_ids?: string[]
  import_url?: string
}

export interface PartUrl {
  part: number
  url: string
}

export interface UploadedPart {
  part: number
  size: number
}

export type BlockerCode =
  | 'policy_matches'
  | 'under_18'
  | 'no_birthdate'
  | 'no_roster'
  | 'adult_check_unavailable'
  | 'roster_unavailable'

export interface Blocker {
  /** One of BlockerCode; unknown codes are shown generically. */
  code: string
  count: number | null
  /** The admin's reason, for the policy codes. */
  detail: string | null
}

export interface FilmingCheck {
  allowed: boolean
  blockers: Blocker[]
  adult_checked_at: string | null
}

export interface UpcomingFilming extends FilmingCheck {
  kind: VideoKind
  id: number
  date: string
  label: string
}

export interface InboxFile {
  id: string
  path: string
  size: number
  mtime: string
  recorded_at_guess: string | null
  duration_ms: number | null
  width: number | null
  height: number | null
  fps: number | null
}

export interface Worker {
  id: string
  name: string
  caps: string[]
  paused: boolean
  version: string | null
  last_seen_at: string | null
  created_at: string
  revoked: boolean
}

export interface WorkerCreated {
  worker: Worker
  /** Shown once; the API keeps only a hash. */
  token: string
}

export type JobStatus = 'queued' | 'running' | 'succeeded' | 'failed' | 'cancelled'

export interface Job {
  id: string
  kind: string
  status: string
  video_id: string | null
  video_title: string | null
  attempts: number
  max_attempts: number
  progress: number | null
  message: string | null
  error: string | null
  worker: string | null
  created_at: string
  started_at: string | null
  finished_at: string | null
}

export interface Policy {
  lineage_id: string
  sport: SportName
  label: string
  matches_allowed: boolean
  reason: string | null
  set_at: string | null
}

export interface PolicyUpdate {
  matches_allowed: boolean
  reason: string | null
}

/** A channel on the import allow-list. */
export interface ImportChannel {
  id: string
  /** An ImportPlatform. */
  platform: string
  /** YouTube channel id (UC…), YouTube handle without the @ (lower case), or Twitch login. */
  ref: string
  label: string
  added_at: string
}
