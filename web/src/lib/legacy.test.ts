import { describe, expect, it } from 'vitest'
import { moveLegacyKeys } from './legacy'

describe('moveLegacyKeys', () => {
  it('moves what the old app (Nomal) saved to the new names, keeping newer values', () => {
    localStorage.clear()
    localStorage.setItem('nomal-theme', 'dark')
    localStorage.setItem('nomal-open-rally:v1', '{"id":"r1"}')
    localStorage.setItem('nomal-lang', 'en')
    localStorage.setItem('wieplay-lang', 'de') // set since the rename: it wins
    localStorage.setItem('other', 'x')
    expect(moveLegacyKeys(localStorage)).toBe(3)
    expect(localStorage.getItem('wieplay-theme')).toBe('dark')
    expect(localStorage.getItem('wieplay-open-rally:v1')).toBe('{"id":"r1"}')
    expect(localStorage.getItem('wieplay-lang')).toBe('de')
    expect(localStorage.getItem('other')).toBe('x')
    expect(Object.keys(localStorage).filter((k) => k.startsWith('nomal-'))).toEqual([])
    expect(moveLegacyKeys(localStorage)).toBe(0)
  })
})
