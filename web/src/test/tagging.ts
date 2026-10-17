// Tagging fixtures: the volleyball and basketball taxonomies as the API serves them
// (backend/wieplay/taxonomies/volleyball.yaml, basketball.yaml).

import type { Period, RosterEntry, Segment, Tag, Tagging, Taxonomy } from '../lib/types'

export const volleyball: Taxonomy = {
  sport: 'volleyball',
  version: 1,
  grades: ['#', '+', '!', '-', '/', '='],
  zones: [1, 2, 3, 4, 5, 6, 7, 8, 9],
  skills: [
    { code: 'S', name_key: 'skill.serve', category: 'serve', lead_ms: 400, pre_ms: 2000, post_ms: 3000, starts_rally: true, ends_rally_on: { '#': 'self', '=': 'other' }, points: 0, graded: true },
    { code: 'R', name_key: 'skill.reception', category: 'reception', lead_ms: 200, pre_ms: 2000, post_ms: 3000, starts_rally: false, ends_rally_on: { '=': 'other' }, points: 0, graded: true },
    { code: 'E', name_key: 'skill.set', category: 'set', lead_ms: 200, pre_ms: 2000, post_ms: 2000, starts_rally: false, ends_rally_on: { '=': 'other' }, points: 0, graded: true },
    { code: 'A', name_key: 'skill.attack', category: 'attack', lead_ms: 300, pre_ms: 3000, post_ms: 2000, starts_rally: false, ends_rally_on: { '#': 'self', '=': 'other', '/': 'other' }, points: 0, graded: true },
    { code: 'B', name_key: 'skill.block', category: 'block', lead_ms: 200, pre_ms: 3000, post_ms: 2000, starts_rally: false, ends_rally_on: { '#': 'self', '=': 'other' }, points: 0, graded: true },
    { code: 'D', name_key: 'skill.dig', category: 'dig', lead_ms: 200, pre_ms: 3000, post_ms: 2000, starts_rally: false, ends_rally_on: { '=': 'other' }, points: 0, graded: true },
    { code: 'F', name_key: 'skill.freeball', category: 'freeball', lead_ms: 200, pre_ms: 2000, post_ms: 2000, starts_rally: false, ends_rally_on: { '=': 'other' }, points: 0, graded: true },
  ],
}

const shot = { category: 'shot', lead_ms: 300, pre_ms: 5000, post_ms: 2000, starts_rally: false, ends_rally_on: {}, graded: true }
const play = { lead_ms: 200, pre_ms: 4000, post_ms: 2000, starts_rally: false, ends_rally_on: {}, points: 0, graded: false }

export const basketball: Taxonomy = {
  sport: 'basketball',
  version: 1,
  grades: ['+', '-'],
  zones: [],
  skills: [
    { ...shot, code: 'S2', name_key: 'skill.two_pointer', points: 2 },
    { ...shot, code: 'S3', name_key: 'skill.three_pointer', points: 3 },
    { ...shot, code: 'FT', name_key: 'skill.free_throw', lead_ms: 200, pre_ms: 3000, post_ms: 1500, points: 1 },
    { ...play, code: 'RO', name_key: 'skill.offensive_rebound', category: 'rebound' },
    { ...play, code: 'RD', name_key: 'skill.defensive_rebound', category: 'rebound' },
    { ...play, code: 'AS', name_key: 'skill.assist', category: 'pass', post_ms: 3000 },
    { ...play, code: 'TO', name_key: 'skill.turnover', category: 'turnover' },
    { ...play, code: 'ST', name_key: 'skill.steal', category: 'defence', pre_ms: 3000, post_ms: 3000 },
    { ...play, code: 'BL', name_key: 'skill.block', category: 'defence', pre_ms: 3000 },
    { ...play, code: 'F', name_key: 'skill.foul', category: 'foul', pre_ms: 3000 },
  ],
}

export const basketballRoster: RosterEntry[] = [
  { side: 'us', number: 5, member_id: 205, position: 'guard', is_libero: false, name: 'Marco Rossi' },
  { side: 'us', number: 11, member_id: 211, position: 'forward', is_libero: false, name: 'Jan Weber' },
  { side: 'us', number: 23, member_id: 223, position: 'center', is_libero: false, name: 'David Frei' },
  { side: 'them', number: 4, member_id: null, position: null, is_libero: false, name: null },
]

export const roster: RosterEntry[] = [
  { side: 'us', number: 3, member_id: 103, position: 'setter', is_libero: false, name: 'Mia Keller' },
  { side: 'us', number: 7, member_id: 107, position: 'outside', is_libero: false, name: 'Luca Brunner' },
  { side: 'us', number: 11, member_id: 111, position: 'libero', is_libero: true, name: 'Noah Meier' },
  { side: 'them', number: 12, member_id: null, position: null, is_libero: false, name: null },
]

let counter = 0
/** A deterministic UUID for fixtures. */
export function uuid(n = ++counter): string {
  return `00000000-0000-4000-8000-${String(n).padStart(12, '0')}`
}

export function tag(patch: Partial<Tag> & Pick<Tag, 't_ms'>): Tag {
  return {
    id: uuid(),
    code: 'A',
    frame: null,
    time_precision: 'live',
    side: 'us',
    number: 7,
    member_id: 107,
    attributes: {},
    segment_id: null,
    clip_pre_ms: null,
    clip_post_ms: null,
    note: null,
    source: 'manual',
    review: 'unreviewed',
    version: 1,
    updated_at: '2026-10-15T18:00:00Z',
    deleted_at: null,
    ...patch,
  }
}

export function rally(patch: Partial<Segment> & Pick<Segment, 'start_ms' | 'end_ms'>): Segment {
  return {
    id: uuid(),
    kind: 'rally',
    period_id: null,
    serving: null,
    won_by: null,
    score_us_after: null,
    score_them_after: null,
    score_source: null,
    source: 'manual',
    review: 'unreviewed',
    version: 1,
    deleted_at: null,
    ...patch,
  }
}

export function period(patch: Partial<Period> & Pick<Period, 'no' | 'start_ms'>): Period {
  return {
    id: uuid(),
    end_ms: null,
    start_us: 0,
    start_them: 0,
    first_serve: null,
    our_court_side_at_start: null,
    side_switch_at_score: null,
    final_us: null,
    final_them: null,
    version: 1,
    deleted_at: null,
    ...patch,
  }
}

export function bundle(patch: Partial<Tagging> = {}): Tagging {
  return {
    can_tag: true,
    sport: 'volleyball',
    taxonomy: volleyball,
    roster,
    periods: [],
    segments: [],
    events: [],
    official_sets: null,
    official_final: null,
    ...patch,
  }
}

/** A basketball game's bundle (Herren 1): no sets, quarters; no zones, no rallies. */
export function basketballBundle(patch: Partial<Tagging> = {}): Tagging {
  return bundle({ sport: 'basketball', taxonomy: basketball, roster: basketballRoster, ...patch })
}
