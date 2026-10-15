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
  /** The same team across seasons (D1 2026/27 was D2 in 2025/26). */
  lineage_id: string
  /** Coaches and team managers (any season of the team) and sport admins: tag, share, download. */
  can_tag: boolean
  can_share: boolean
  can_export: boolean
}

export interface TeamDetail extends Team {
  /** Coach or team manager of this season's team, or sport admin. */
  can_upload: boolean
}

/** A season of a team's lineage that the user can see (GET /api/teams/{id}/seasons, newest first). */
export interface Season {
  team_id: number
  name: string
  /** 'yyyy/yy' */
  season: string
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
  /** Coaches and team managers (any season of the team) and sport admins. */
  can_tag: boolean
  /** Share clips and reels with the team. */
  can_share: boolean
  /** Download watermarked MP4s. */
  can_export: boolean
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

// Tagging (M2, docs/PLAN.md §6.5–§6.7, §9).

export type Side = 'us' | 'them'
export type Review = 'unreviewed' | 'confirmed' | 'corrected' | 'rejected'
export type TimePrecision = 'live' | 'adjusted' | 'frame'
export type SegmentKind = 'rally' | 'possession' | 'timeout' | 'set_break' | 'warmup' | 'stoppage' | 'other_dead'

export interface TaxonomySkill {
  /** One letter for volleyball (S R E A B D F); one or two characters for basketball (S2 S3 FT RO … F). */
  code: string
  /** i18n key, e.g. 'skill.serve'. */
  name_key: string
  category: string
  /** The tag is placed this long before the first keystroke. */
  lead_ms: number
  pre_ms: number
  post_ms: number
  starts_rally: boolean
  /** Grade → who wins the rally: the tagged side ('self') or the other one. Empty for basketball. */
  ends_rally_on: Record<string, 'self' | 'other'>
  /** What the skill adds to the score when made (grade `+`): basketball shots 2, 3, 1; else 0. */
  points: number
  /** Whether the skill takes a grade at all (volleyball: every skill; basketball: shots only). */
  graded: boolean
}

export interface Taxonomy {
  sport: string
  version: number
  grades: string[]
  zones: number[]
  skills: TaxonomySkill[]
}

export interface RosterEntry {
  side: Side
  number: number
  member_id: number | null
  position: string | null
  is_libero: boolean
  name: string | null
}

export interface Period {
  id: string
  /** Set (volleyball) or quarter (basketball: 1–4, then overtimes from 5). */
  no: number
  start_ms: number
  end_ms: number | null
  start_us: number
  start_them: number
  first_serve: Side | null
  our_court_side_at_start: 'near' | 'far' | null
  side_switch_at_score: number | null
  /** Computed by the server: from the rallies (volleyball), the running score at its end (basketball). */
  final_us: number | null
  final_them: number | null
  version: number
  /** Null while live. Deleted sets only come back in a /changes answer that touched them. */
  deleted_at: string | null
}

export interface Segment {
  id: string
  kind: SegmentKind
  start_ms: number
  end_ms: number
  /** Computed by the server: the set this segment is in. */
  period_id: string | null
  serving: Side | null
  won_by: Side | null
  score_us_after: number | null
  score_them_after: number | null
  score_source: string | null
  source: string
  review: Review
  version: number
  /** Null while live. Deleted segments only come back in a /changes answer that touched them. */
  deleted_at: string | null
}

export interface TagAttributes {
  grade?: string
  zone_from?: number
  zone_to?: number
}

/** A tag (`event` in the API; named Tag here so it doesn't shadow the DOM's Event). */
export interface Tag {
  id: string
  code: string
  t_ms: number
  frame: number | null
  time_precision: TimePrecision
  side: Side
  number: number | null
  /** Derived by the server from the roster snapshot. */
  member_id: number | null
  attributes: TagAttributes
  /** Derived by the server: the rally the tag falls in, by time. */
  segment_id: string | null
  clip_pre_ms: number | null
  clip_post_ms: number | null
  note: string | null
  source: string
  review: Review
  version: number
  updated_at: string
  /** Null while live; set on a stale change's answer when the server has the tag deleted. */
  deleted_at: string | null
}

export interface Tagging {
  /** Coaches and team managers of the team, and sport admins, on a ready video. */
  can_tag: boolean
  sport: string
  taxonomy: Taxonomy | null
  roster: RosterEntry[]
  periods: Period[]
  segments: Segment[]
  events: Tag[]
  /** Federation set scores as [us, them]; null when unknown. */
  official_sets: [number, number][] | null
  /** The game's official final result as [us, them] (both sports); null when not played or unknown. */
  official_final: [number, number] | null
}

/**
 * Every change names the server version it was based on: 0 for a new item, else the version
 * last received. A change based on an older version than the server's is skipped (`stale`).
 */
interface ChangeBase {
  id: string
  base_version: number
  deleted?: boolean
}

export interface EventIn extends ChangeBase {
  code: string
  t_ms: number
  frame?: number | null
  time_precision: TimePrecision
  side: Side
  number?: number | null
  attributes: TagAttributes
  /** Ignored by the server (it links tags to rallies itself); always sent as null. */
  segment_id?: null
  clip_pre_ms?: number | null
  clip_post_ms?: number | null
  note?: string | null
  review?: Review
}

/** Who served is always derived by the server. */
export interface SegmentIn extends ChangeBase {
  kind: SegmentKind
  start_ms: number
  end_ms: number
  won_by?: Side | null
  review?: Review
}

export interface PeriodIn extends ChangeBase {
  no: number
  start_ms: number
  end_ms?: number | null
  start_us: number
  start_them: number
  first_serve?: Side | null
  our_court_side_at_start?: 'near' | 'far' | null
  side_switch_at_score?: number | null
}

export interface ChangesIn {
  events: EventIn[]
  segments: SegmentIn[]
  periods: PeriodIn[]
}

export interface ChangesOut {
  /** The events that were sent, with their new version (the server's state for stale ones). */
  events: Tag[]
  /** All live segments of the video, rescored, plus the deleted ones this batch touched. */
  segments: Segment[]
  /** All live periods of the video, plus the deleted ones this batch touched. */
  periods: Period[]
  /** Ids of changes skipped because the server had a newer version. */
  stale: string[]
}

export interface RosterEntryIn {
  side: Side
  number: number
  member_id?: number | null
  position?: string | null
  is_libero: boolean
}

/** Someone who can go on the video's roster: a player of the team in any season. */
export interface RosterCandidate {
  member_id: number
  name: string
  number: number | null
}

export interface EventRevision {
  version: number
  action: string
  changed_by: number | null
  changed_at: string
  after: Record<string, unknown>
}

// Clips, reels, comments, exports (M3, docs/PLAN.md §6.8, §7.7).

export type Visibility = 'private' | 'team'

/** A time range of a video. Nothing is rendered: watching it seeks in the stream. */
export interface Clip {
  id: string
  video_id: string
  video_title: string
  /** yyyy-mm-dd */
  recorded_on: string
  start_ms: number
  end_ms: number
  title: string
  note: string | null
  event_id: string | null
  owner: number
  owner_name: string | null
  mine: boolean
  visibility: Visibility
  created_at: string
}

/** PUT /api/clips/{client id}: creates or updates one's own clip (at most 10 minutes). */
export interface ClipIn {
  video_id: string
  start_ms: number
  end_ms: number
  title: string
  note?: string | null
  event_id?: string | null
  /** `team` only for coaches, team managers and sport admins (403 otherwise). */
  visibility: Visibility
}

export type ReelKind = 'smart' | 'manual'

/** A smart reel's saved query, evaluated for each viewer when the reel is opened. */
export interface ReelQuery {
  lineage_id?: string | null
  /** 'yyyy/yy' */
  season?: string | null
  video_ids?: string[]
  member_id?: number | null
  side?: Side | null
  codes?: string[]
  grades?: string[]
  /** 1..500, default 200. */
  limit?: number
}

export interface Reel {
  id: string
  title: string
  kind: ReelKind
  query: ReelQuery | null
  visibility: Visibility
  lineage_id: string | null
  owner: number
  mine: boolean
  created_at: string
  updated_at: string
}

/** GET /api/reels/{id}: the reel with the moments the viewer may watch, counted. */
export interface ReelDetail {
  reel: Reel
  item_count: number
  total_ms: number
}

export interface ReelItemIn {
  video_id: string
  start_ms: number
  end_ms: number
  event_id?: string | null
  clip_id?: string | null
  caption?: string | null
}

export interface ReelIn {
  title: string
  kind: ReelKind
  /** Smart reels only. */
  query?: ReelQuery | null
  /** Manual reels only, in order. */
  items?: ReelItemIn[]
  visibility: Visibility
  /** Required for `team`. */
  lineage_id?: string | null
}

/** One stretch of a reel, a smart reel's match or a condensed match, as the viewer may watch it. */
export interface Piece {
  video_id: string
  start_ms: number
  end_ms: number
  event_id: string | null
  clip_id: string | null
  caption: string | null
  video_title: string | null
  recorded_on: string | null
  code: string | null
  grade: string | null
  side: Side | null
  number: number | null
  member_id: number | null
  player_name: string | null
  /** The score after the rally the moment belongs to, when it is tagged. */
  score_us: number | null
  score_them: number | null
}

/** Someone of the video's team in any season: who a comment can mention or be addressed to. */
export interface VideoPerson {
  member_id: number
  name: string
  number: number | null
  /** player, coach, captain, team_responsible, … */
  roles: string[]
}

export type Audience = 'team' | 'coaches' | 'person'
export type CommentTarget = 'video' | 'event' | 'segment' | 'clip'

export interface Comment {
  id: string
  video_id: string
  target_type: CommentTarget
  target_id: string | null
  t_ms: number | null
  author: number
  author_name: string | null
  body: string
  /** Member ids of the team. */
  mentions: number[]
  audience: Audience
  recipient: number | null
  recipient_name: string | null
  created_at: string
  edited_at: string | null
  mine: boolean
  /** The author, and the team's coaches for any comment. */
  can_delete: boolean
}

export interface CommentIn {
  /** Client-made UUID. */
  id: string
  target_type: CommentTarget
  /** Required unless the target is the video. */
  target_id?: string | null
  t_ms?: number | null
  /** 1..4000 characters. */
  body: string
  mentions: number[]
  audience: Audience
  /** Required for `person`. */
  recipient?: number | null
}

export type ExportKind = 'clip' | 'reel' | 'condensed'
export type ExportStatus = 'queued' | 'ready' | 'failed' | 'expired' | 'deleted'

/** A watermarked MP4 rendered by the media worker (coaches only, PLAN §5.2). */
export interface VideoExport {
  id: string
  kind: ExportKind
  title: string
  duration_ms: number
  status: ExportStatus
  bytes: number | null
  /** 0..1 while queued, when the worker reports it. */
  progress: number | null
  error: string | null
  created_at: string
  expires_at: string | null
}

export interface ExportIn {
  kind: ExportKind
  clip_id?: string
  reel_id?: string
  /** A condensed match. */
  video_id?: string
  title?: string
}
