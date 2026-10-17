import { describe, expect, it } from 'vitest'
import { bundle, period, rally } from '../test/tagging'
import { winnerChange, withWinner } from './winner'

describe('who won a rally, set outside the workspace', () => {
  const set1 = period({ no: 1, start_ms: 0, first_serve: 'us' })
  const first = rally({ start_ms: 1_000, end_ms: 5_000, won_by: 'us', period_id: set1.id, score_us_after: 1, score_them_after: 0 })
  const second = rally({ start_ms: 9_000, end_ms: 14_000, period_id: set1.id, model_won_by: 'them', score_us_after: 1, score_them_after: 1, score_source: 'model' })
  const third = rally({ start_ms: 20_000, end_ms: 25_000, period_id: set1.id, won_by: 'them' })

  it('shows the score line at once, the coach outranking WiePlay', () => {
    const data = withWinner(bundle({ periods: [set1], segments: [first, second, third] }), second.id, 'us')
    const after = data.segments.map((s) => [s.won_by, s.score_us_after, s.score_them_after, s.score_source])
    expect(after).toEqual([
      ['us', 1, 0, 'tags'],
      ['us', 2, 0, 'tags'],
      ['them', 2, 1, 'tags'],
    ])
    // Taken back: WiePlay's winner again, the score line marked as WiePlay's from there.
    const back = withWinner(data, second.id, null)
    expect(back.segments.map((s) => [s.score_us_after, s.score_them_after, s.score_source])).toEqual([
      [1, 0, 'tags'],
      [1, 1, 'model'],
      [1, 2, 'model'],
    ])
  })

  it("sends the rally as it is with the winner; a model's rally with another winner is corrected", () => {
    expect(winnerChange(second, 'us')).toMatchObject({ id: second.id, kind: 'rally', start_ms: 9_000, end_ms: 14_000, won_by: 'us', review: 'unreviewed', base_version: second.version })
    const accepted = { ...second, source: 'model', review: 'confirmed' as const }
    expect(winnerChange(accepted, 'us').review).toBe('corrected')
    expect(winnerChange({ ...accepted, won_by: 'us' }, 'us').review).toBe('confirmed') // nothing changed
    expect(winnerChange({ ...accepted, review: 'unreviewed' }, 'them').review).toBe('corrected')
  })
})
