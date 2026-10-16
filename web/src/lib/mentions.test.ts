import { describe, expect, it } from 'vitest'
import type { Person } from './clips'
import { commentMoments, sortComments } from './comments'
import { insertMention, matchPeople, mentionAt, mentionedIds, splitMentions } from './mentions'
import type { Comment } from './types'

const people: Person[] = [
  { member_id: 103, name: 'Mia Keller', number: 3 },
  { member_id: 107, name: 'Luca Brunner', number: 7 },
  { member_id: 108, name: 'Luca Müller', number: 8 },
]

describe('mentionAt', () => {
  it('finds the @ being typed at the caret', () => {
    expect(mentionAt('Look @mi', 8)).toEqual({ start: 5, query: 'mi' })
    expect(mentionAt('@', 1)).toEqual({ start: 0, query: '' })
    expect(mentionAt('Look @Mia K', 11)).toEqual({ start: 5, query: 'Mia K' })
  })

  it('ignores an @ inside a word, after a line break, or far back', () => {
    expect(mentionAt('mail@kscw', 9)).toBeNull()
    expect(mentionAt('@mia\nok', 7)).toBeNull()
    expect(mentionAt('@ mia', 5)).toBeNull()
    expect(mentionAt(`@${'x'.repeat(31)}`, 32)).toBeNull()
    expect(mentionAt('no mention', 10)).toBeNull()
  })
})

it('matches people by the start of their name words', () => {
  expect(matchPeople(people, 'mi').map((p) => p.member_id)).toEqual([103])
  expect(matchPeople(people, 'luca').map((p) => p.member_id)).toEqual([107, 108])
  expect(matchPeople(people, 'luca m').map((p) => p.member_id)).toEqual([108])
  expect(matchPeople(people, 'ke').map((p) => p.member_id)).toEqual([103])
  expect(matchPeople(people, '')).toHaveLength(3)
  expect(matchPeople(people, 'x')).toEqual([])
})

it('puts the picked name in place of what was typed', () => {
  const text = 'Look @mi at this'
  expect(insertMention(text, 8, { start: 5, query: 'mi' }, 'Mia Keller')).toEqual({ text: 'Look @Mia Keller at this', caret: 17 })
  expect(insertMention('@lu', 3, { start: 0, query: 'lu' }, 'Luca Brunner')).toEqual({ text: '@Luca Brunner ', caret: 14 })
})

it('sends only the mentions still in the text', () => {
  expect(mentionedIds('Great @Mia Keller', people)).toEqual([103])
  expect(mentionedIds('Great job', people)).toEqual([])
})

it('splits a body into text and mentions, longest names first', () => {
  expect(splitMentions('Hi @Luca Brunner and @Mia Keller.', people)).toEqual([
    { text: 'Hi ', mention: false },
    { text: '@Luca Brunner', mention: true, member_id: 107 },
    { text: ' and ', mention: false },
    { text: '@Mia Keller', mention: true, member_id: 103 },
    { text: '.', mention: false },
  ])
  expect(splitMentions('mail @someone', people)).toEqual([{ text: 'mail @someone', mention: false }])
})

describe('comments', () => {
  const comment = (id: string, t_ms: number | null, created_at: string) => ({ id, t_ms, created_at }) as Comment
  it('lists comments on the whole video first, then by moment and time written', () => {
    const list = [
      comment('late', 50_000, '2026-10-12T10:00:00Z'),
      comment('general', null, '2026-10-12T12:00:00Z'),
      comment('early-2', 10_000, '2026-10-12T11:00:00Z'),
      comment('early-1', 10_000, '2026-10-12T09:00:00Z'),
    ]
    expect(sortComments(list).map((c) => c.id)).toEqual(['general', 'early-1', 'early-2', 'late'])
    expect(commentMoments(list)).toEqual([
      { id: 'late', t_ms: 50_000 },
      { id: 'early-2', t_ms: 10_000 },
      { id: 'early-1', t_ms: 10_000 },
    ])
  })
})
