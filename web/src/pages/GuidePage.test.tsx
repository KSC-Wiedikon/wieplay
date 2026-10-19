import { screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import i18n from '../i18n'
import { renderRoute } from '../test/render'
import { GuidePage } from './GuidePage'
import { GuideLink } from './tag/GuideLink'

afterEach(async () => {
  await i18n.changeLanguage('en')
})

describe('GuidePage', () => {
  it('explains why reviewing matters, the review keys, marking reviewed and the tagging basics', async () => {
    renderRoute(<GuidePage />, { path: '/how-to-help', url: '/how-to-help' })
    expect(await screen.findByRole('heading', { level: 1, name: 'How to help' })).toBeInTheDocument()

    const nav = screen.getByRole('navigation', { name: 'On this page' })
    expect(within(nav).getAllByRole('link').map((a) => a.getAttribute('href'))).toEqual([
      '#why',
      '#start',
      '#review',
      '#done',
      '#tag',
      '#regrade',
      '#sheet',
      '#actions',
      '#identify',
      '#rights',
    ])

    const why = screen.getByRole('region', { name: 'Why it matters' })
    expect(within(why).getByText(/it learns from people/)).toBeInTheDocument()

    const review = screen.getByRole('region', { name: 'Review rally suggestions' })
    const keys = within(review).getByRole('table', { name: 'Keys while reviewing' })
    const rows = within(keys).getAllByRole('row').map((row) => row.textContent)
    for (const expected of [
      'EnterAccept',
      'XReject',
      '←→Move the start',
      'Shift + ←Shift + →Move the end',
      'MMerge',
      'SSplit',
      'UUndo',
      '12Who served',
      '34Who won the rally',
    ]) {
      expect(rows.some((text) => text?.startsWith(expected))).toBe(true)
    }

    expect(screen.getByRole('region', { name: 'Mark the rallies reviewed' })).toHaveTextContent('Mark rallies reviewed')
    const tag = screen.getByRole('region', { name: 'Tag the basics' })
    expect(within(tag).getByText('[a] number · skill · [grade]')).toBeInTheDocument()
    for (const entry of ['7A#', 'a12S=', 'aS']) expect(within(tag).getByText(entry)).toBeInTheDocument()
    expect(within(within(tag).getByRole('table', { name: 'Skills' })).getAllByRole('row')).toHaveLength(8)
    expect(within(within(tag).getByRole('table', { name: 'Grades' })).getAllByRole('row')).toHaveLength(7)

    const regrade = screen.getByRole('region', { name: 'Grade in a second pass' })
    const regradeKeys = within(regrade).getByRole('table', { name: 'Keys while regrading' })
    const keyRows = within(regradeKeys).getAllByRole('row').map((row) => row.textContent)
    for (const expected of ['123456The grade', '0No grade', 'SEnterSkip', 'UUndo']) {
      expect(keyRows.some((text) => text?.startsWith(expected))).toBe(true)
    }

    const rights = screen.getByRole('region', { name: 'What helpers can and can’t do' })
    expect(within(rights).getByText('download videos')).toBeInTheDocument()
    expect(within(rights).getByText('add other helpers')).toBeInTheDocument()
  })

  it('is in German for German readers', async () => {
    await i18n.changeLanguage('de')
    renderRoute(<GuidePage />, { path: '/how-to-help', url: '/how-to-help' })
    expect(await screen.findByRole('heading', { level: 1, name: 'So hilfst du' })).toBeInTheDocument()
    const review = screen.getByRole('region', { name: 'Vorgeschlagene Ballwechsel prüfen' })
    expect(within(review).getByText('Wer hat den Ballwechsel gewonnen: 3 wir, 4 sie')).toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'Die Grundlagen taggen' })).toBeInTheDocument()
  })
})

describe('GuideLink', () => {
  it('opens the guide in a new tab, so a review keeps its place', async () => {
    renderRoute(<GuideLink />)
    const link = await screen.findByRole('link', { name: 'How to help' })
    expect(link).toHaveAttribute('href', '/how-to-help')
    expect(link).toHaveAttribute('target', '_blank')
  })
})
