import { describe, expect, it } from 'vitest'
import { formatDate, formatDateTime, formatTime, formatTimeRange, todayIso } from './format'

describe('formatDate', () => {
  it('formats API dates as dd.mm.yyyy', () => {
    expect(formatDate('2026-10-18')).toBe('18.10.2026')
    expect(formatDate('2027-01-05')).toBe('05.01.2027')
  })

  it('keeps the calendar day of date-only values', () => {
    // Date-only strings must not shift a day through time zone conversion.
    expect(formatDate('2026-03-29')).toBe('29.03.2026')
    expect(formatDate('2026-12-31')).toBe('31.12.2026')
  })

  it('shows timestamps on the Zurich calendar day', () => {
    expect(formatDate('2026-10-18T22:30:00Z')).toBe('19.10.2026')
    expect(formatDate(new Date('2026-10-18T12:00:00Z'))).toBe('18.10.2026')
  })

  it('returns an empty string for missing or invalid values', () => {
    expect(formatDate(null)).toBe('')
    expect(formatDate(undefined)).toBe('')
    expect(formatDate('')).toBe('')
    expect(formatDate('not a date')).toBe('')
  })
})

describe('formatTime', () => {
  it('formats times as 24 h HH:MM', () => {
    expect(formatTime('14:00')).toBe('14:00')
    expect(formatTime('20:30:00')).toBe('20:30')
    expect(formatTime('9:05')).toBe('09:05')
  })

  it('never uses a 12 h clock or 24:00 at midnight', () => {
    expect(formatTime('00:00')).toBe('00:00')
    expect(formatTime('12:15')).toBe('12:15')
    expect(formatTime('23:59')).toBe('23:59')
  })

  it('shows timestamps in Zurich time (CET and CEST)', () => {
    expect(formatTime('2026-01-15T19:00:00Z')).toBe('20:00')
    expect(formatTime(new Date('2026-07-15T19:00:00Z'))).toBe('21:00')
  })

  it('returns an empty string for missing values', () => {
    expect(formatTime(null)).toBe('')
  })
})

describe('formatDateTime', () => {
  it('joins date and time', () => {
    expect(formatDateTime('2026-10-15T08:34:00Z')).toBe('15.10.2026 10:34')
    expect(formatDateTime('2026-10-15T08:34:00+02:00')).toBe('15.10.2026 08:34')
  })

  it('returns an empty string for missing values', () => {
    expect(formatDateTime(null)).toBe('')
  })
})

describe('formatTimeRange', () => {
  it('joins start and end with an en dash', () => {
    expect(formatTimeRange('20:00', '22:00')).toBe('20:00–22:00')
  })

  it('falls back to whichever end is known', () => {
    expect(formatTimeRange('20:00', null)).toBe('20:00')
    expect(formatTimeRange(null, '22:00')).toBe('22:00')
    expect(formatTimeRange(null, null)).toBe('')
  })
})

describe('todayIso', () => {
  it('uses the Zurich calendar day', () => {
    expect(todayIso(new Date('2026-10-15T21:59:00Z'))).toBe('2026-10-15')
    expect(todayIso(new Date('2026-10-15T22:30:00Z'))).toBe('2026-10-16')
  })
})
