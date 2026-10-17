// Stats fixtures shaped like GET /api/videos/{id}/stats and /api/teams/{id}/stats
// (backend/nomal/api/stats.py). The columns are the server's for the volleyball taxonomy.

import type { BoxScore, RotationLine, SetterLine, SetterStats, StatColumn, StatRow, StatValue, TeamLine, TeamStats, VideoStats } from '../lib/stats'

const IN_PLAY = ['+', '!', '-', '/', null]

export const statColumns: StatColumn[] = [
  { key: 'points', group: null, format: 'count', codes: ['A', 'S', 'B'], grades: ['#'] },
  { key: 'serve_ta', group: 'serve', format: 'count', codes: ['S'], grades: null },
  { key: 'serve_ace', group: 'serve', format: 'count', codes: ['S'], grades: ['#'] },
  { key: 'serve_err', group: 'serve', format: 'count', codes: ['S'], grades: ['='] },
  { key: 'serve_in', group: 'serve', format: 'count', codes: ['S'], grades: IN_PLAY },
  { key: 'serve_eff', group: 'serve', format: 'pct', codes: ['S'], grades: null },
  { key: 'serve_rtg', group: 'serve', format: 'rating', codes: ['S'], grades: null },
  { key: 'rec_ta', group: 'reception', format: 'count', codes: ['R'], grades: null },
  { key: 'rec_err', group: 'reception', format: 'count', codes: ['R'], grades: ['='] },
  { key: 'rec_perfect', group: 'reception', format: 'pct', codes: ['R'], grades: ['#'] },
  { key: 'rec_positive', group: 'reception', format: 'pct', codes: ['R'], grades: ['#', '+'] },
  { key: 'rec_rtg', group: 'reception', format: 'rating', codes: ['R'], grades: null },
  { key: 'att_ta', group: 'attack', format: 'count', codes: ['A'], grades: null },
  { key: 'att_kill', group: 'attack', format: 'count', codes: ['A'], grades: ['#'] },
  { key: 'att_err', group: 'attack', format: 'count', codes: ['A'], grades: ['='] },
  { key: 'att_blocked', group: 'attack', format: 'count', codes: ['A'], grades: ['/'] },
  { key: 'att_hit', group: 'attack', format: 'pct', codes: ['A'], grades: null },
  { key: 'att_kill_pct', group: 'attack', format: 'pct', codes: ['A'], grades: ['#'] },
  { key: 'att_err_pct', group: 'attack', format: 'pct', codes: ['A'], grades: ['=', '/'] },
  { key: 'blk_point', group: 'block', format: 'count', codes: ['B'], grades: ['#'] },
  { key: 'blk_touch', group: 'block', format: 'count', codes: ['B'], grades: ['+', '!', '-', null] },
  { key: 'blk_err', group: 'block', format: 'count', codes: ['B'], grades: ['=', '/'] },
  { key: 'dig_ta', group: 'other', format: 'count', codes: ['D'], grades: null },
  { key: 'set_ta', group: 'other', format: 'count', codes: ['E'], grades: null },
  { key: 'free_ta', group: 'other', format: 'count', codes: ['F'], grades: null },
]

/** Every number empty, then the given ones. */
export function statValues(values: Record<string, StatValue> = {}): Record<string, StatValue> {
  return { ...Object.fromEntries(statColumns.map((c) => [c.key, null])), ...values }
}

export function statRow(patch: Partial<StatRow> & Pick<StatRow, 'kind'>): StatRow {
  const side = patch.kind === 'opponent' ? 'them' : 'us'
  return {
    side,
    number: null,
    member_id: null,
    name: null,
    filter: { side },
    matches: null,
    ...patch,
    values: statValues(patch.values),
  }
}

export function teamLine(patch: Partial<TeamLine> = {}): TeamLine {
  return {
    rallies: 0,
    won: 0,
    lost: 0,
    side_out_won: 0,
    side_out_of: 0,
    side_out_pct: null,
    break_point_won: 0,
    break_point_of: 0,
    break_point_pct: null,
    ...patch,
  }
}

export function boxScore(patch: Partial<BoxScore> = {}): BoxScore {
  return {
    players: [],
    team: statRow({ kind: 'team' }),
    opponent: statRow({ kind: 'opponent' }),
    summary: teamLine(),
    ...patch,
  }
}

const TEAM_FILTERS = {
  won: { won_by: 'us' },
  lost: { won_by: 'them' },
  side_out_won: { serving: 'them', won_by: 'us' },
  side_out_of: { serving: 'them', won_by: 'any' },
  break_point_won: { serving: 'us', won_by: 'us' },
  break_point_of: { serving: 'us', won_by: 'any' },
} as const

/** A match's stats; empty (no tags) unless patched. */
export function videoStats(patch: Partial<VideoStats> = {}): VideoStats {
  return {
    video_id: '00000000-0000-4000-8000-000000000001',
    team_id: 92,
    title: 'KSCW H3 – Volley Näfels',
    recorded_on: '2026-10-11',
    tags: 0,
    rallies: 0,
    columns: statColumns,
    team_filters: TEAM_FILTERS,
    match: boxScore(),
    sets: [],
    ...patch,
  }
}

/** A season's stats; nothing tagged unless patched. */
export function teamStats(patch: Partial<TeamStats> = {}): TeamStats {
  return {
    team_id: 92,
    team_name: 'H3',
    season: '2026/27',
    lineage_id: '6f1c0a52-2a43-4c11-9a43-111111111111',
    videos: 0,
    tagged: 0,
    columns: statColumns,
    season_box: boxScore(),
    matches: [],
    ...patch,
  }
}

/** The rotation table's columns, as the server sends them (`rotation_columns`). */
export const rotationColumns: StatColumn[] = [
  'serve_ta',
  'serve_ace',
  'serve_err',
  'serve_eff',
  'rec_ta',
  'rec_positive',
  'rec_eff',
  'att_ta',
  'att_kill',
  'att_hit',
].map((key) =>
  key === 'rec_eff'
    ? { key, group: 'reception', format: 'pct', codes: ['R'], grades: null }
    : (statColumns.find((c) => c.key === key) as StatColumn),
)

/** One rotation's line; empty unless patched. */
export function rotationLine(rotation: number, patch: Partial<RotationLine> = {}): RotationLine {
  return {
    rotation,
    ...teamLine(),
    values: Object.fromEntries(rotationColumns.map((c) => [c.key, null])),
    plays: [],
    lineup: null,
    lineup_varies: false,
    ...patch,
  }
}

export function setterLine(key: string, patch: Partial<SetterLine> = {}): SetterLine {
  return {
    key,
    filter: {},
    number: null,
    member_id: null,
    name: null,
    sets: 0,
    attacks: 0,
    share: null,
    zones: {},
    kills: 0,
    errors: 0,
    blocked: 0,
    kill_pct: null,
    hit_pct: null,
    ...patch,
  }
}

export function setterStats(patch: Partial<SetterStats> = {}): SetterStats {
  return { sets: 0, attacks: 0, by_attacker: [], by_zone: [], by_rotation: [], by_reception: [], by_setter: [], plays: null, ...patch }
}
