import { describe, expect, it } from 'vitest'
import { toCheckIn } from './analysis'
import { ApiError } from './api'

describe('toCheckIn', () => {
  it('reads the count from the 409 the server sends while something is still to check', () => {
    const body = { detail: { message: '3 rallies are still to check', to_check: 3 } }
    expect(toCheckIn(new ApiError(409, '3 rallies are still to check', body))).toBe(3)
    // Older servers.
    expect(toCheckIn(new ApiError(409, 'open', { detail: { open_proposals: 2 } }))).toBe(2)
    expect(toCheckIn(new ApiError(409, 'open', { detail: { open_suggestions: 4 } }))).toBe(4)
  })

  it('is null for any other error', () => {
    expect(toCheckIn(new ApiError(409, 'Conflict', { detail: 'other' }))).toBeNull()
    expect(toCheckIn(new ApiError(500, 'boom'))).toBeNull()
    expect(toCheckIn(new Error('x'))).toBeNull()
  })
})
