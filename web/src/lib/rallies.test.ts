import { describe, expect, it } from 'vitest'
import { basketball, period, rally, tag, volleyball } from '../test/tagging'
import {
  addPoint,
  periodAt,
  proposeByKey,
  proposeByTag,
  rallyStartForServe,
  rescore,
  rescoreFor,
  rescoreShots,
  scoreBefore,
  serveCodes,
  shotPoints,
  shotScoreAt,
  winnerFromTags,
} from './rallies'

const S = serveCodes(volleyball)

describe('periodAt', () => {
  const one = period({ no: 1, start_ms: 60_000, end_ms: 1_500_000 })
  const two = period({ no: 2, start_ms: 1_600_000 })

  it('finds the set a moment is in', () => {
    expect(periodAt([one, two], 100_000)?.no).toBe(1)
    expect(periodAt([one, two], 2_000_000)?.no).toBe(2)
  })

  it('has no set before the first one and in the break between sets', () => {
    expect(periodAt([one, two], 10_000)).toBeNull()
    expect(periodAt([one, two], 1_550_000)).toBeNull()
  })

  it('takes the later set when an earlier one was never ended', () => {
    const open = period({ no: 1, start_ms: 0 })
    expect(periodAt([open, two], 1_700_000)?.no).toBe(2)
  })
})

describe('rescore (same rules as the server)', () => {
  it('counts the score per set from its starting score', () => {
    const set = period({ no: 1, start_ms: 0, first_serve: 'us', start_us: 2, start_them: 1 })
    const r1 = rally({ start_ms: 10_000, end_ms: 20_000, won_by: 'us' })
    const r2 = rally({ start_ms: 30_000, end_ms: 40_000, won_by: 'them' })
    const r3 = rally({ start_ms: 50_000, end_ms: 60_000, won_by: null })
    const out = rescore([set], [r3, r1, r2], [], S)
    const [a, b, c] = out.segments
    expect(a).toMatchObject({ period_id: set.id, serving: 'us', score_us_after: 3, score_them_after: 1, score_source: 'tags' })
    expect(b).toMatchObject({ serving: 'us', score_us_after: 3, score_them_after: 2 })
    // The winner of the previous rally serves; no winner, no score.
    expect(c).toMatchObject({ serving: 'them', score_us_after: null, score_them_after: null, score_source: null })
    expect(out.periods[0]).toMatchObject({ final_us: 3, final_them: 2 })
  })

  it('takes the server from a serve tag in the rally first', () => {
    const set = period({ no: 1, start_ms: 0, first_serve: 'us' })
    const r1 = rally({ start_ms: 10_000, end_ms: 20_000, won_by: 'us' })
    const serve = tag({ code: 'S', side: 'them', t_ms: 9_000 }) // up to 2 s before the rally
    const out = rescore([set], [r1], [serve], S)
    expect(out.segments[0]?.serving).toBe('them')
  })

  it('leaves rallies outside any set without a score', () => {
    const set = period({ no: 1, start_ms: 100_000 })
    const before = rally({ start_ms: 10_000, end_ms: 20_000, won_by: 'us', score_us_after: 5, score_them_after: 5 })
    const out = rescore([set], [before], [], S)
    expect(out.segments[0]).toMatchObject({ period_id: null, score_us_after: null, score_them_after: null })
    expect(out.periods[0]).toMatchObject({ final_us: null, final_them: null })
  })

  it("takes the coach's serve call before the previous winner, and never the model's", () => {
    const set = period({ no: 1, start_ms: 0, first_serve: 'us' })
    const r1 = rally({ start_ms: 10_000, end_ms: 20_000, won_by: 'us' })
    const r2 = rally({ start_ms: 30_000, end_ms: 40_000, serving_manual: 'them', model_serving: 'us' })
    const r3 = rally({ start_ms: 50_000, end_ms: 60_000, model_serving: 'them' })
    const out = rescore([set], [r1, r2, r3], [], S)
    // After a rally without a coach's winner, who serves is not known from facts (Nomal suggests it).
    expect(out.segments.map((r) => r.serving)).toEqual(['us', 'them', null])
    // A serve tag still wins over the call.
    const serve = tag({ code: 'S', side: 'us', t_ms: 31_000 })
    expect(rescore([set], [r1, r2], [serve], S).segments[1]?.serving).toBe('us')
  })

  it("fills the score line with the model's winners where no coach decided, marked as the model's from there on", () => {
    const set = period({ no: 1, start_ms: 0, first_serve: 'us' })
    const r1 = rally({ start_ms: 10_000, end_ms: 20_000, won_by: 'us', model_won_by: 'them' })
    const r2 = rally({ start_ms: 30_000, end_ms: 40_000, model_won_by: 'them' })
    const r3 = rally({ start_ms: 50_000, end_ms: 60_000, won_by: 'them' })
    const r4 = rally({ start_ms: 70_000, end_ms: 80_000 })
    const out = rescore([set], [r1, r2, r3, r4], [], S)
    expect(out.segments.map((r) => [r.score_us_after, r.score_them_after, r.score_source])).toEqual([
      [1, 0, 'tags'],
      [1, 1, 'model'],
      [1, 2, 'model'],
      [null, null, null],
    ])
    // The model's winner is no fact: after it, who serves is not known from facts.
    expect(out.segments[2]?.serving).toBeNull()
    expect(out.segments[3]?.serving).toBe('them')
    expect(out.periods[0]).toMatchObject({ final_us: 1, final_them: 2 })
  })

  it('counts the points the model thinks were played without a rally', () => {
    const set = period({ no: 1, start_ms: 0, first_serve: 'us', model_missed_us: 0, model_missed_them: 1 })
    const r1 = rally({ start_ms: 10_000, end_ms: 20_000, won_by: 'us' })
    const r2 = rally({ start_ms: 50_000, end_ms: 60_000, model_won_by: 'us', model_missed_us: 1, model_missed_them: 0 })
    const out = rescore([set], [r1, r2], [], S)
    expect(out.segments.map((r) => [r.score_us_after, r.score_them_after, r.score_source])).toEqual([
      [1, 0, 'tags'],
      [3, 0, 'model'],
    ])
    expect(out.periods[0]).toMatchObject({ final_us: 3, final_them: 1 })
  })

  it("takes a rally's winner from the next rally's serve before the model's", () => {
    const set = period({ no: 1, start_ms: 0, first_serve: 'us' })
    const r1 = rally({ start_ms: 10_000, end_ms: 20_000, model_won_by: 'us' })
    const r2 = rally({ start_ms: 30_000, end_ms: 40_000, serving_manual: 'them' })
    const out = rescore([set], [r1, r2], [], S)
    expect(out.segments[0]).toMatchObject({ serving: 'us', score_us_after: 0, score_them_after: 1, score_source: 'tags' })
    expect(out.segments[1]).toMatchObject({ serving: 'them', score_us_after: null })
    expect(scoreBefore(45_000, out.periods, out.segments)).toEqual({ period: out.periods[0], us: 0, them: 1, serving: 'them' })
  })

  it("doesn't touch timeouts and doesn't change its inputs", () => {
    const set = period({ no: 1, start_ms: 0 })
    const timeout = rally({ kind: 'timeout', start_ms: 5_000, end_ms: 35_000 })
    const r1 = rally({ start_ms: 40_000, end_ms: 50_000, won_by: 'us' })
    const out = rescore([set], [timeout, r1], [], S)
    expect(out.segments.find((s) => s.id === timeout.id)).toEqual(timeout)
    expect(r1.score_us_after).toBeNull()
  })
})

describe('score and proposals', () => {
  const set = period({ no: 1, start_ms: 0, first_serve: 'them' })
  const r1 = rally({ start_ms: 10_000, end_ms: 20_000, won_by: 'us', period_id: set.id, score_us_after: 1, score_them_after: 0 })
  const r2 = rally({ start_ms: 30_000, end_ms: 40_000, won_by: 'us', period_id: set.id, score_us_after: 2, score_them_after: 0 })

  it('knows the score and the server before a moment', () => {
    expect(scoreBefore(5_000, [set], [r1, r2])).toMatchObject({ us: 0, them: 0, serving: 'them' })
    expect(scoreBefore(35_000, [set], [r1, r2])).toMatchObject({ us: 1, them: 0, serving: 'us' })
    expect(scoreBefore(50_000, [set], [r1, r2])).toMatchObject({ us: 2, them: 0, serving: 'us' })
    expect(scoreBefore(50_000, [set], [r1, r2])).not.toHaveProperty('suggested')
    expect(scoreBefore(50_000, [set], [r1, r2], r2.id)).toMatchObject({ us: 1, them: 0 })
  })

  it('counts a won rally that has no server score yet', () => {
    const local = rally({ start_ms: 45_000, end_ms: 48_000, won_by: 'them' })
    expect(scoreBefore(50_000, [set], [r1, r2, local])).toMatchObject({ us: 2, them: 1, serving: 'them' })
  })

  it('adds the point', () => {
    expect(addPoint({ us: 14, them: 12 }, 'us')).toEqual({ us: 15, them: 12 })
    expect(addPoint({ us: 14, them: 12 }, 'them')).toEqual({ us: 14, them: 13 })
    expect(addPoint({ us: 14, them: 12 }, null)).toEqual({ us: 14, them: 12 })
  })

  it('opens a rally 2 s before the serve, not before the video', () => {
    expect(rallyStartForServe(12_400)).toBe(10_400)
    expect(rallyStartForServe(1_000)).toBe(0)
  })

  it('proposes the end and winner from a rally-ending tag', () => {
    const open = { id: 'r', start_ms: 60_000 }
    const kill = { t_ms: 66_000, code: 'A', side: 'us' as const, attributes: { grade: '#' } }
    expect(proposeByTag(open, kill, volleyball)).toEqual({ rally: open, end_ms: 67_500, winner: 'us', reason: 'tag' })
    const error = { t_ms: 61_000, code: 'S', side: 'them' as const, attributes: { grade: '=' } }
    expect(proposeByTag(open, error, volleyball)?.winner).toBe('us')
  })

  it("doesn't propose without an open rally or for a tag that doesn't end it", () => {
    const kill = { t_ms: 66_000, code: 'A', side: 'us' as const, attributes: { grade: '#' } }
    expect(proposeByTag(null, kill, volleyball)).toBeNull()
    expect(proposeByTag({ id: 'r', start_ms: 0 }, { ...kill, attributes: { grade: '+' } }, volleyball)).toBeNull()
  })

  it('presets the winner of R from the last rally-ending tag in the rally', () => {
    const open = { id: 'r', start_ms: 60_000 }
    const tags = [
      tag({ t_ms: 50_000, code: 'A', side: 'them', attributes: { grade: '#' } }), // previous rally
      tag({ t_ms: 62_000, code: 'R', side: 'us', attributes: { grade: '+' } }),
      tag({ t_ms: 65_000, code: 'A', side: 'us', attributes: { grade: '/' } }), // blocked: them
    ]
    expect(proposeByKey(open, 68_000, tags, volleyball)).toEqual({ rally: open, end_ms: 68_000, winner: 'them', reason: 'key' })
    expect(winnerFromTags(tags.slice(0, 2), volleyball, 60_000, 68_000)).toBeNull()
  })

  it('never ends a rally before it starts', () => {
    expect(proposeByKey({ id: 'r', start_ms: 60_000 }, 59_000, [], volleyball).end_ms).toBe(60_001)
  })
})

describe('basketball score (same rules as the server)', () => {
  const shot = (t_ms: number, code: string, grade: string | null, side: 'us' | 'them' = 'us') =>
    tag({ t_ms, code, side, number: side === 'us' ? 23 : 4, member_id: null, attributes: grade ? { grade } : {} })

  it('counts made shots only, by their points', () => {
    expect(shotPoints(basketball, 'S3', '+')).toBe(3)
    expect(shotPoints(basketball, 'S2', '+')).toBe(2)
    expect(shotPoints(basketball, 'FT', '+')).toBe(1)
    expect(shotPoints(basketball, 'S3', '-')).toBe(0)
    expect(shotPoints(basketball, 'S3', null)).toBe(0)
    expect(shotPoints(basketball, 'RO', '+')).toBe(0)
    expect(shotPoints(basketball, 'XX', '+')).toBe(0)
  })

  it('keeps a running score across quarters, seeded by the first one', () => {
    const q1 = period({ no: 1, start_ms: 0, end_ms: 600_000, start_us: 10, start_them: 12 })
    const q2 = period({ no: 2, start_ms: 720_000, start_us: 0, start_them: 0 }) // its own start score is not used
    const tags = [
      shot(-1, 'S2', '+'), // never happens, but before any quarter
      shot(10_000, 'S3', '+'),
      shot(20_000, 'S2', '-'),
      shot(30_000, 'S2', '+', 'them'),
      shot(40_000, 'RO', null),
      shot(650_000, 'S2', '+'), // in the break: not counted
      shot(730_000, 'FT', '+'),
      shot(740_000, 'FT', '-', 'them'),
    ]
    const [one, two] = rescoreShots([q2, q1], tags, basketball)
    expect(one).toMatchObject({ id: q1.id, final_us: 13, final_them: 14 })
    expect(two).toMatchObject({ id: q2.id, final_us: 14, final_them: 14 })
    expect(q1.final_us).toBeNull() // inputs unchanged
  })

  it('ends a quarter left open where the next one starts', () => {
    const q1 = period({ no: 1, start_ms: 0 })
    const q2 = period({ no: 2, start_ms: 100_000 })
    const [one, two] = rescoreShots([q1, q2], [shot(50_000, 'S2', '+'), shot(150_000, 'S3', '+', 'them')], basketball)
    expect(one).toMatchObject({ final_us: 2, final_them: 0 })
    expect(two).toMatchObject({ final_us: 2, final_them: 3 })
  })

  it('gives every quarter a final, also without shots, and leaves segments alone', () => {
    const q1 = period({ no: 1, start_ms: 0, start_us: 4, start_them: 2 })
    const timeout = rally({ kind: 'timeout', start_ms: 5_000, end_ms: 35_000 })
    const out = rescoreFor(basketball, [q1], [timeout], [])
    expect(out.periods[0]).toMatchObject({ final_us: 4, final_them: 2 })
    expect(out.segments).toEqual([timeout])
    // Volleyball still scores by rallies.
    expect(rescoreFor(volleyball, [q1], [], []).periods[0]).toMatchObject({ final_us: null, final_them: null })
  })

  it('knows the score at a moment: the last quarter final plus the shots since', () => {
    const q1 = period({ no: 1, start_ms: 0, end_ms: 600_000, start_us: 2, start_them: 0, final_us: 20, final_them: 18 })
    const q2 = period({ no: 2, start_ms: 720_000, final_us: 25, final_them: 18 })
    const tags = [shot(10_000, 'S3', '+'), shot(730_000, 'S2', '+'), shot(800_000, 'S3', '+'), shot(810_000, 'S2', '+', 'them')]
    expect(shotScoreAt(-1, [q1, q2], tags, basketball)).toMatchObject({ period: null, us: 0, them: 0 })
    expect(shotScoreAt(5_000, [q1, q2], tags, basketball)).toMatchObject({ period: q1, us: 2, them: 0 })
    expect(shotScoreAt(10_000, [q1, q2], tags, basketball)).toMatchObject({ us: 5, them: 0 })
    // Between quarters: the score of the one before (the server's final).
    expect(shotScoreAt(700_000, [q1, q2], tags, basketball)).toMatchObject({ period: q1, us: 5, them: 0 })
    expect(shotScoreAt(735_000, [q1, q2], tags, basketball)).toMatchObject({ period: q2, us: 22, them: 18 })
    expect(shotScoreAt(900_000, [q1, q2], tags, basketball)).toMatchObject({ us: 25, them: 20, serving: null })
  })

  it('counts the quarter before itself when it has no final yet', () => {
    const q1 = period({ no: 1, start_ms: 0, end_ms: 600_000, start_us: 2, start_them: 0 })
    const q2 = period({ no: 2, start_ms: 720_000 })
    const tags = [shot(10_000, 'S3', '+'), shot(730_000, 'S2', '+', 'them')]
    expect(shotScoreAt(740_000, [q1, q2], tags, basketball)).toMatchObject({ period: q2, us: 5, them: 2 })
  })
})

describe('score with suggested winners', () => {
  it("counts the model's winner where no coach decided, and says so", () => {
    const set = period({ no: 1, start_ms: 0, first_serve: 'us' })
    const r1 = rally({ start_ms: 10_000, end_ms: 20_000, won_by: 'us', period_id: set.id, score_us_after: 1, score_them_after: 0, score_source: 'tags' })
    const r2 = rally({ start_ms: 30_000, end_ms: 40_000, model_won_by: 'them', period_id: set.id, score_us_after: 1, score_them_after: 1, score_source: 'model' })
    expect(scoreBefore(25_000, [set], [r1, r2])).toEqual({ period: set, us: 1, them: 0, serving: 'us' })
    expect(scoreBefore(45_000, [set], [r1, r2])).toEqual({ period: set, us: 1, them: 1, serving: 'them', suggested: true })
  })
})
