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
  lineage_id: number | null
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

export interface Training {
  id: number
  date: string
  start_time: string | null
  end_time: string | null
  hall_name: string | null
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
  counts: { teams: number; members: number; games: number; trainings: number }
}
