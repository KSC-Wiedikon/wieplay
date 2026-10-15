// @mentions in comments: the autocomplete while typing, and finding the mentions in a body to
// show them. The API takes member ids; the text keeps the names ("@Mia Keller").

import type { Person } from './clips'

/** Names are matched up to this length after the @. */
const MAX_QUERY = 30

export interface MentionQuery {
  /** Index of the @. */
  start: number
  /** What was typed after it. */
  query: string
}

/** The mention being typed at the caret: an @ at the start or after a space, no line break since. */
export function mentionAt(text: string, caret: number): MentionQuery | null {
  const before = text.slice(0, caret)
  const at = before.lastIndexOf('@')
  if (at === -1) return null
  if (at > 0 && !/\s/.test(before[at - 1] ?? '')) return null
  const query = before.slice(at + 1)
  if (query.length > MAX_QUERY || /[\n@]/.test(query) || query.startsWith(' ')) return null
  return { start: at, query }
}

function words(value: string): string[] {
  return value.toLocaleLowerCase('de-CH').split(/\s+/).filter(Boolean)
}

/** People whose name words start with each typed word ("mi k" finds Mia Keller); at most `limit`. */
export function matchPeople(people: readonly Person[], query: string, limit = 6): Person[] {
  const typed = words(query)
  return people
    .filter((person) => {
      const name = words(person.name)
      return typed.every((word) => name.some((part) => part.startsWith(word)))
    })
    .slice(0, limit)
}

/** The text with the typed mention replaced by "@Name ", and where the caret goes. */
export function insertMention(text: string, caret: number, query: MentionQuery, name: string): { text: string; caret: number } {
  const inserted = `@${name} `
  const after = text.slice(caret).replace(/^ /, '')
  return { text: text.slice(0, query.start) + inserted + after, caret: query.start + inserted.length }
}

/** Ids of the picked people whose "@Name" is still in the text. */
export function mentionedIds(text: string, picked: readonly Person[]): number[] {
  const ids = new Set<number>()
  for (const person of picked) if (text.includes(`@${person.name}`)) ids.add(person.member_id)
  return [...ids]
}

export type BodyPart = { text: string; mention: false } | { text: string; mention: true; member_id: number }

/** A comment's body split into text and "@Name" parts of the mentioned people (longest names first). */
export function splitMentions(body: string, mentioned: readonly Person[]): BodyPart[] {
  const names = [...mentioned].sort((a, b) => b.name.length - a.name.length)
  const parts: BodyPart[] = []
  let rest = body
  let plain = ''
  while (rest.length > 0) {
    const person = rest.startsWith('@') ? names.find((p) => rest.startsWith(`@${p.name}`)) : undefined
    if (person) {
      if (plain) parts.push({ text: plain, mention: false })
      plain = ''
      parts.push({ text: `@${person.name}`, mention: true, member_id: person.member_id })
      rest = rest.slice(person.name.length + 1)
    } else {
      plain += rest[0]
      rest = rest.slice(1)
    }
  }
  if (plain) parts.push({ text: plain, mention: false })
  return parts
}
