import { describe, expect, it } from 'vitest'
import { openProposals } from './analysis'
import { ApiError } from './api'

describe('openProposals', () => {
  it('reads the count from the 409 the server sends while suggestions are open', () => {
    const body = { detail: { message: '3 rally proposals are still open', open_proposals: 3 } }
    expect(openProposals(new ApiError(409, '3 rally proposals are still open', body))).toBe(3)
  })

  it('is null for any other error', () => {
    expect(openProposals(new ApiError(409, 'Conflict', { detail: 'other' }))).toBeNull()
    expect(openProposals(new ApiError(500, 'boom'))).toBeNull()
    expect(openProposals(new Error('x'))).toBeNull()
  })
})
