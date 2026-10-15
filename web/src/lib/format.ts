// Swiss formats everywhere: dd.mm.yyyy and 24 h HH:MM (locale de-CH).
// Date-only and time-only strings from the API are wall-clock values and are
// formatted as such; full timestamps are shown in Zurich time, so the output
// doesn't depend on the device's time zone.

const LOCALE = 'de-CH'
const ZURICH = 'Europe/Zurich'

const dateOptions: Intl.DateTimeFormatOptions = { day: '2-digit', month: '2-digit', year: 'numeric' }
const timeOptions: Intl.DateTimeFormatOptions = { hour: '2-digit', minute: '2-digit', hour12: false }

const wallDate = new Intl.DateTimeFormat(LOCALE, { ...dateOptions, timeZone: 'UTC' })
const wallTime = new Intl.DateTimeFormat(LOCALE, { ...timeOptions, timeZone: 'UTC' })
const zurichDate = new Intl.DateTimeFormat(LOCALE, { ...dateOptions, timeZone: ZURICH })
const zurichTime = new Intl.DateTimeFormat(LOCALE, { ...timeOptions, timeZone: ZURICH })

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/
const TIME_ONLY = /^(\d{1,2}):(\d{2})(?::\d{2}(?:\.\d+)?)?$/

type Value = string | Date | null | undefined

function format(value: Value, wall: Intl.DateTimeFormat, zoned: Intl.DateTimeFormat): string {
  if (value == null || value === '') return ''
  if (typeof value === 'string') {
    const d = DATE_ONLY.exec(value)
    if (d) return wall.format(Date.UTC(Number(d[1]), Number(d[2]) - 1, Number(d[3])))
    const t = TIME_ONLY.exec(value)
    if (t) return wall.format(Date.UTC(2000, 0, 1, Number(t[1]), Number(t[2])))
  }
  const date = value instanceof Date ? value : new Date(value)
  return Number.isNaN(date.getTime()) ? '' : zoned.format(date)
}

/** "2026-10-18" → "18.10.2026". Empty string for missing or invalid input. */
export function formatDate(value: Value): string {
  return format(value, wallDate, zurichDate)
}

/** "14:00:00" → "14:00". Empty string for missing or invalid input. */
export function formatTime(value: Value): string {
  return format(value, wallTime, zurichTime)
}

/** Timestamp → "15.10.2026 10:34" (Zurich time). */
export function formatDateTime(value: Value): string {
  const date = formatDate(value)
  const time = formatTime(value)
  return date && time ? `${date} ${time}` : date
}

/** "20:00", "22:00" → "20:00–22:00"; either end may be missing. */
export function formatTimeRange(start: Value, end: Value): string {
  const from = formatTime(start)
  const to = formatTime(end)
  return from && to ? `${from}–${to}` : from || to
}

/** Today's date in Zurich as "yyyy-mm-dd", for comparing with API dates. */
export function todayIso(now: Date = new Date()): string {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat(LOCALE, { timeZone: ZURICH, year: 'numeric', month: '2-digit', day: '2-digit' })
      .formatToParts(now)
      .map((p) => [p.type, p.value]),
  )
  return `${parts.year}-${parts.month}-${parts.day}`
}
