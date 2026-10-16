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

/** Today's date in Zurich as "yyyy-mm-dd", for comparing with API dates. */
export function todayIso(now: Date = new Date()): string {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat(LOCALE, { timeZone: ZURICH, year: 'numeric', month: '2-digit', day: '2-digit' })
      .formatToParts(now)
      .map((p) => [p.type, p.value]),
  )
  return `${parts.year}-${parts.month}-${parts.day}`
}

/** "2026-10-15", -60 → "2026-08-16" (calendar arithmetic, no time zone involved). */
export function addDaysIso(iso: string, days: number): string {
  const d = DATE_ONLY.exec(iso)
  if (!d) return iso
  const date = new Date(Date.UTC(Number(d[1]), Number(d[2]) - 1, Number(d[3]) + days))
  return date.toISOString().slice(0, 10)
}

const pad2 = (n: number) => String(n).padStart(2, '0')

/** Milliseconds → "1:32:10" or "4:05". Empty string for missing input. */
export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return ''
  const total = Math.round(ms / 1000)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  return h > 0 ? `${h}:${pad2(m)}:${pad2(s)}` : `${m}:${pad2(s)}`
}

const UNITS = ['B', 'kB', 'MB', 'GB', 'TB'] as const
const oneDecimal = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 1 })
const noDecimals = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 })

/** Bytes → "1.4 GB" (decimal units, as in Finder and on camera boxes). */
export function formatBytes(bytes: number): string {
  let value = Math.max(0, bytes)
  let unit = 0
  while (value >= 1000 && unit < UNITS.length - 1) {
    value /= 1000
    unit += 1
  }
  const number = unit === 0 || value >= 100 ? noDecimals.format(value) : oneDecimal.format(value)
  return `${number} ${UNITS[unit]}`
}

/** Seconds → "40 s", "12 min", "2 h 05 min". */
export function formatSeconds(seconds: number): string {
  const s = Math.max(0, Math.round(seconds))
  if (s < 60) return `${s} s`
  const minutes = Math.round(s / 60)
  if (minutes < 60) return `${minutes} min`
  return `${Math.floor(minutes / 60)} h ${pad2(minutes % 60)} min`
}
