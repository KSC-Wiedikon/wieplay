import { describe, expect, it } from 'vitest'
import { finished } from './exportWatch'
import type { VideoExport } from './types'

function item(id: string, status: VideoExport['status'], kind: VideoExport['kind'] = 'recap'): VideoExport {
  return { id, kind, title: id, duration_ms: 1, status, bytes: null, progress: null, error: null, created_at: '', expires_at: null }
}

describe('finished', () => {
  it('names the exports that were being made and now are ready or failed', () => {
    const before = new Map([
      ['a', 'queued'],
      ['b', 'queued'],
      ['c', 'ready'],
      ['d', 'queued'],
    ])
    const now = [item('a', 'ready'), item('b', 'failed'), item('c', 'ready'), item('d', 'queued'), item('e', 'ready')]
    expect(finished(before, now).map((e) => e.id)).toEqual(['a', 'b'])
  })
})
