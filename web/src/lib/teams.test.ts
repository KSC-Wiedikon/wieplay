import { describe, expect, it } from 'vitest'
import { mayUpload, teamEntries } from './teams'
import type { Team } from './types'

const team = (id: number, name: string, patch: Partial<Team> = {}): Team => ({
  id,
  name,
  sport: 'volleyball',
  season: '2026/27',
  league: null,
  rel: [],
  lineage_id: `lineage-${name}`,
  can_tag: false,
  can_share: false,
  can_export: false,
  ...patch,
})

describe('mayUpload', () => {
  it('mirrors the API: coaches, team managers and sport admins', () => {
    expect(mayUpload(team(1, 'H3', { rel: ['coach'] }), { sport_admin: [] })).toBe(true)
    expect(mayUpload(team(1, 'H3', { rel: ['team_responsible'] }), { sport_admin: [] })).toBe(true)
    expect(mayUpload(team(1, 'H3', { rel: ['player', 'captain'] }), { sport_admin: [] })).toBe(false)
    expect(mayUpload(team(1, 'H3'), { sport_admin: ['volleyball'] })).toBe(true)
    expect(mayUpload(team(1, 'Herren 1', { sport: 'basketball' }), { sport_admin: ['volleyball'] })).toBe(false)
  })

  it('lets a team helper upload for the team they help', () => {
    expect(mayUpload(team(1, 'H3', { helper: true }), { sport_admin: [] })).toBe(true)
    expect(mayUpload(team(1, 'H3', { helper: false, rel: ['player'] }), { sport_admin: [] })).toBe(false)
  })
})

describe('teamEntries', () => {
  it('puts volleyball first and keeps earlier seasons with their team', () => {
    const entries = teamEntries([
      team(1, 'Herren 1', { sport: 'basketball' }),
      team(3, 'H3', { season: '2025/26' }),
      team(92, 'H3'),
      team(94, 'D1'),
    ])
    expect(entries.map(({ latest, earlier }) => [latest.id, earlier.map((t) => t.id)])).toEqual([
      [94, []],
      [92, [3]],
      [1, []],
    ])
  })
})
