import { describe, expect, it } from 'vitest'
import { isPdf, pageName, renderScale } from './pdfPages'
import { MAX_SIDE } from './redact'

describe('a PDF scan of the sheet', () => {
  it('is told from a photo by its type or name', () => {
    expect(isPdf(new File(['%PDF'], 'scan.pdf', { type: 'application/pdf' }))).toBe(true)
    expect(isPdf(new File(['%PDF'], 'SCAN.PDF', { type: '' }))).toBe(true)
    expect(isPdf(new File(['jpeg'], 'IMG_1.jpg', { type: 'image/jpeg' }))).toBe(false)
  })

  it('draws each page as large as a photo is uploaded, its pictures named by page', () => {
    // An A4 landscape page (842 × 595 pt), the size scans usually have: its longer side at
    // MAX_SIDE, so every box can be read.
    expect(842 * renderScale(842, 595)).toBeCloseTo(MAX_SIDE)
    expect(595 * renderScale(595, 842)).toBeLessThan(MAX_SIDE)
    expect(renderScale(100, 50)).toBe(8) // a tiny page is not blown up without end
    expect(pageName('Matchblatt 23.09.pdf', 2)).toBe('Matchblatt 23.09-p2.jpg')
  })
})
