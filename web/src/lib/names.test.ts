import { describe, expect, it } from 'vitest'
import { firstName, initials } from './names'

describe('initials', () => {
  it('takes the first letters of the first and the last name', () => {
    expect(initials('Luca Canepa')).toBe('LC')
    expect(initials('Anna-Lena  von Allmen')).toBe('AA')
    expect(initials('özlem yilmaz')).toBe('ÖY')
  })

  it('handles one name and none', () => {
    expect(initials('Cher')).toBe('C')
    expect(initials('  ')).toBe('')
  })
})

describe('firstName', () => {
  it('is the first word', () => {
    expect(firstName('Anna-Lena Müller')).toBe('Anna-Lena')
    expect(firstName(' Luca  Canepa ')).toBe('Luca')
    expect(firstName('')).toBe('')
  })
})
