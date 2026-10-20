// The search bar (docs/PLAN.md §9.7): a few words over tags and rallies, e.g. "#3 attack #" or
// "set 3, rotation 2, reception -". English and German words both work, whatever the app's
// language; commas are optional. What can be named:
//
//   players    #7 (ours; after "them": theirs), a12 (their #12), or a bare 7
//   sides      us / them (wir / gegner)
//   skills     serve reception set attack block dig freeball, or their letters S R E A B D F
//              (aufschlag annahme zuspiel angriff block abwehr freeball)
//   grades     # + ! - / =, "ungraded" (unbewertet); ace, kill and blocked name skill and grade
//   entries    the tagging grammar: 7A#, a12S=, 3A#46 (zones from 4 to 6)
//   zones      from 4, to 1, zone 5 (von 4, nach 1, zone 5): where a tag started, went, either
//   outcomes   point, error, in play (punkt, fehler, im spiel): the tag won the rally, lost it,
//              or neither (the taxonomy's ends_rally_on)
//   rallies    set 3 (satz 3), rotation 2 (rot 2, P2): our rotation from the rotation walk;
//              won, lost (gewonnen, verloren): who won the rally; break (we served),
//              side-out (they served)
//
// Any case reads ("Attack", "attack"); the hints and the words written back for a chip are
// capitalised. Words of one kind are alternatives ("attack block": either), kinds together must
// all hold.
// A tag matches when its own terms and its rally's hold; a rally when its terms hold and, with
// tag terms, one of its tags matches them. The server reads the same `TagSearch` for the stats
// and smart reels (backend/wieplay/core/search.py) and counts the same way.

import { parseEntry, skillByCode } from './grammar'
import { capitalize } from './labels'
import { periodAt, ralliesOf } from './rallies'
import type { Period, RosterEntry, Segment, Side, Tag, Taxonomy } from './types'

export type Outcome = 'point' | 'error' | 'play'
export type ZoneEnd = 'from' | 'to' | 'any'

export interface PlayerRef {
  side: Side
  number: number
}

/** What a search keeps; an empty list puts no condition on its kind (the server's TagSearch). */
export interface TagSearch {
  sides: Side[]
  players: PlayerRef[]
  codes: string[]
  /** null: a tag without a grade. */
  grades: (string | null)[]
  zones_from: number[]
  zones_to: number[]
  /** From or to. */
  zones: number[]
  outcomes: Outcome[]
  sets: number[]
  rotations: number[]
  won_by: Side[]
  serving: Side[]
}

export type SearchTerm =
  | { kind: 'side'; side: Side }
  | { kind: 'player'; side: Side; number: number }
  | { kind: 'skill'; code: string }
  | { kind: 'grade'; grade: string | null }
  | { kind: 'zone'; end: ZoneEnd; zone: number }
  | { kind: 'outcome'; outcome: Outcome }
  | { kind: 'set'; no: number }
  | { kind: 'rotation'; rotation: number }
  | { kind: 'won'; side: Side }
  | { kind: 'serving'; side: Side }

export interface ParsedSearch {
  text: string
  terms: SearchTerm[]
  /** Words nobody understood (left out of the search). */
  unknown: string[]
  search: TagSearch
}

export function emptySearch(): TagSearch {
  return { sides: [], players: [], codes: [], grades: [], zones_from: [], zones_to: [], zones: [], outcomes: [], sets: [], rotations: [], won_by: [], serving: [] }
}

export function isEmptySearch(search: TagSearch): boolean {
  return !hasTagTerms(search) && !hasRallyTerms(search)
}

export function hasTagTerms(s: TagSearch): boolean {
  return s.sides.length + s.players.length + s.codes.length + s.grades.length + s.zones_from.length + s.zones_to.length + s.zones.length + s.outcomes.length > 0
}

export function hasRallyTerms(s: TagSearch): boolean {
  return s.sets.length + s.rotations.length + s.won_by.length + s.serving.length > 0
}

// --- words -------------------------------------------------------------------------------------

/** Lower case, umlauts and accents dropped: "Aufschläge" and "aufschlage" are one word. */
function norm(word: string): string {
  return word
    .toLowerCase()
    .normalize('NFD')
    .replace(/[̀-ͯ]/g, '')
}

/** Volleyball's skills by category, when no taxonomy is at hand (DataVolley letters). */
const DEFAULT_CODES: Record<string, string> = { serve: 'S', reception: 'R', set: 'E', attack: 'A', block: 'B', dig: 'D', freeball: 'F' }

const SIDE_WORDS: Record<string, Side> = { us: 'us', we: 'us', our: 'us', wir: 'us', uns: 'us', them: 'them', they: 'them', opponent: 'them', opp: 'them', gegner: 'them' }

const SKILL_WORDS: Record<string, string> = {
  serve: 'serve',
  serves: 'serve',
  service: 'serve',
  aufschlag: 'serve',
  aufschlage: 'serve',
  reception: 'reception',
  receptions: 'reception',
  receive: 'reception',
  annahme: 'reception',
  annahmen: 'reception',
  set: 'set',
  sets: 'set',
  zuspiel: 'set',
  zuspiele: 'set',
  attack: 'attack',
  attacks: 'attack',
  hit: 'attack',
  hits: 'attack',
  spike: 'attack',
  angriff: 'attack',
  angriffe: 'attack',
  block: 'block',
  blocks: 'block',
  dig: 'dig',
  digs: 'dig',
  abwehr: 'dig',
  freeball: 'freeball',
  freeballs: 'freeball',
}

/** Words naming a skill and a grade at once. */
const SKILL_GRADE_WORDS: Record<string, [string, string]> = {
  ace: ['serve', '#'],
  aces: ['serve', '#'],
  ass: ['serve', '#'],
  asse: ['serve', '#'],
  kill: ['attack', '#'],
  kills: ['attack', '#'],
  blocked: ['attack', '/'],
  geblockt: ['attack', '/'],
}

const UNGRADED = new Set(['ungraded', 'nograde', 'unbewertet'])
const OUTCOME_WORDS: Record<string, Outcome> = { point: 'point', points: 'point', punkt: 'point', punkte: 'point', error: 'error', errors: 'error', fehler: 'error', inplay: 'play', 'in-play': 'play', imspiel: 'play', 'im-spiel': 'play' }
/** Two words: "in play", "im Spiel". */
const OUTCOME_PAIRS: Record<string, Outcome> = { 'in play': 'play', 'im spiel': 'play' }
const WON_WORDS: Record<string, Side> = { won: 'us', win: 'us', wins: 'us', gewonnen: 'us', lost: 'them', loss: 'them', verloren: 'them' }
const SERVING_WORDS: Record<string, Side> = { break: 'us', breaks: 'us', 'break-point': 'us', breakpoint: 'us', breakpunkt: 'us', 'side-out': 'them', sideout: 'them', 'side-outs': 'them' }

type NumberKind = 'set' | 'rotation' | 'from' | 'to' | 'zone'
const NUMBER_WORDS: Record<string, NumberKind> = { set: 'set', satz: 'set', rotation: 'rotation', rot: 'rotation', zone: 'zone', from: 'from', von: 'from', to: 'to', nach: 'to' }
const NUMBER_RANGE: Record<NumberKind, [number, number]> = { set: [1, 9], rotation: [1, 6], zone: [1, 9], from: [1, 9], to: [1, 9] }

const GRADES = ['#', '+', '!', '-', '/', '=']

function codeOf(category: string, taxonomy: Taxonomy | null): string | null {
  if (!taxonomy) return DEFAULT_CODES[category] ?? null
  return taxonomy.skills.find((s) => s.category === category)?.code ?? null
}

function knownCode(letter: string, taxonomy: Taxonomy | null): string | null {
  if (taxonomy) return skillByCode(taxonomy, letter)?.code ?? null
  const upper = letter.toUpperCase()
  return Object.values(DEFAULT_CODES).includes(upper) ? upper : null
}

function numberTerm(kind: NumberKind, n: number): SearchTerm | null {
  const [lo, hi] = NUMBER_RANGE[kind]
  if (!Number.isInteger(n) || n < lo || n > hi) return null
  if (kind === 'set') return { kind: 'set', no: n }
  if (kind === 'rotation') return { kind: 'rotation', rotation: n }
  return { kind: 'zone', end: kind === 'zone' ? 'any' : kind, zone: n }
}

/** The words of a text, split at spaces and commas. */
function words(text: string): string[] {
  return text.split(/[\s,;]+/).filter(Boolean)
}

const VOLLEYBALL_TAXONOMY_HINT: Taxonomy = {
  sport: 'volleyball',
  version: 1,
  grades: GRADES,
  zones: [1, 2, 3, 4, 5, 6, 7, 8, 9],
  skills: Object.entries(DEFAULT_CODES).map(([category, code]) => ({
    code,
    name_key: `skill.${category}`,
    category,
    lead_ms: 0,
    pre_ms: 0,
    post_ms: 0,
    starts_rally: category === 'serve',
    ends_rally_on: {},
    points: 0,
    graded: true,
  })),
}

/** An entry of the tagging grammar (7A#, a12S=, 3A#46, a12) as terms; null when it is none. */
function entryTerms(word: string, taxonomy: Taxonomy | null): SearchTerm[] | null {
  const entry = parseEntry(word, taxonomy ?? VOLLEYBALL_TAXONOMY_HINT)
  if (entry.error || (entry.skillCode !== null && entry.skill === null)) return null
  // Without a number only "a" + skill counts (aA#: their kills); "A#" is read elsewhere.
  if (entry.number === null && (entry.side !== 'them' || entry.skill === null || word.length < 2)) return null
  const out: SearchTerm[] = []
  if (entry.number !== null) out.push({ kind: 'player', side: entry.side, number: entry.number })
  else out.push({ kind: 'side', side: entry.side })
  if (entry.skill) out.push({ kind: 'skill', code: entry.skill.code })
  if (entry.grade) out.push({ kind: 'grade', grade: entry.grade })
  if (entry.zoneFrom !== null) out.push({ kind: 'zone', end: 'from', zone: entry.zoneFrom })
  if (entry.zoneTo !== null) out.push({ kind: 'zone', end: 'to', zone: entry.zoneTo })
  return out
}

/** Reads a search as typed. Never throws; words it doesn't know are listed in `unknown`. */
export function parseSearch(text: string, taxonomy: Taxonomy | null = null): ParsedSearch {
  const list = words(text)
  const terms: SearchTerm[] = []
  const unknown: string[] = []
  // A number names a player of the side named last ("them #12"); ours by default.
  let side: Side = 'us'
  for (let i = 0; i < list.length; i += 1) {
    const raw = list[i] as string
    const word = norm(raw)
    const next = list[i + 1] !== undefined ? norm(list[i + 1] as string) : undefined
    const pair = next !== undefined ? OUTCOME_PAIRS[`${word} ${next}`] : undefined
    if (pair) {
      terms.push({ kind: 'outcome', outcome: pair })
      i += 1
      continue
    }
    // set 3, rotation 2, from 4 …: the keyword and its number, apart or together (set3, P2).
    const numberWord = NUMBER_WORDS[word]
    if (numberWord && next !== undefined && /^\d{1,2}$/.test(next)) {
      const term = numberTerm(numberWord, Number(next))
      if (term) terms.push(term)
      else unknown.push(`${raw} ${list[i + 1] as string}`)
      i += 1
      continue
    }
    const glued = /^(set|satz|rotation|rot|p|zone|from|von|to|nach)(\d{1,2})$/.exec(word)
    if (glued) {
      const kind = glued[1] === 'p' ? 'rotation' : (NUMBER_WORDS[glued[1] as string] as NumberKind)
      const term = numberTerm(kind, Number(glued[2]))
      if (term) terms.push(term)
      else unknown.push(raw)
      continue
    }
    if (word in SIDE_WORDS) {
      side = SIDE_WORDS[word] as Side
      terms.push({ kind: 'side', side })
      continue
    }
    if (word in SKILL_WORDS) {
      const code = codeOf(SKILL_WORDS[word] as string, taxonomy)
      if (code) terms.push({ kind: 'skill', code })
      else unknown.push(raw)
      continue
    }
    if (word in SKILL_GRADE_WORDS) {
      const [category, grade] = SKILL_GRADE_WORDS[word] as [string, string]
      const code = codeOf(category, taxonomy)
      if (code) terms.push({ kind: 'skill', code }, { kind: 'grade', grade })
      else unknown.push(raw)
      continue
    }
    if (UNGRADED.has(word)) {
      terms.push({ kind: 'grade', grade: null })
      continue
    }
    if (word in OUTCOME_WORDS) {
      terms.push({ kind: 'outcome', outcome: OUTCOME_WORDS[word] as Outcome })
      continue
    }
    if (word in WON_WORDS) {
      terms.push({ kind: 'won', side: WON_WORDS[word] as Side })
      continue
    }
    if (word in SERVING_WORDS) {
      terms.push({ kind: 'serving', side: SERVING_WORDS[word] as Side })
      continue
    }
    const player = /^#?(\d{1,2})$/.exec(word)
    if (player) {
      terms.push({ kind: 'player', side, number: Number(player[1]) })
      continue
    }
    if (GRADES.includes(raw)) {
      terms.push({ kind: 'grade', grade: raw })
      continue
    }
    // One letter: a skill (A attack, E set …); a letter and a grade: that skill so graded.
    const letter = /^([a-z])([#+!\-/=])?$/i.exec(raw)
    if (letter) {
      const code = knownCode(letter[1] as string, taxonomy)
      if (code) {
        terms.push({ kind: 'skill', code })
        if (letter[2]) terms.push({ kind: 'grade', grade: letter[2] })
        continue
      }
    }
    const entry = entryTerms(raw, taxonomy)
    if (entry) {
      terms.push(...entry)
      continue
    }
    unknown.push(raw)
  }
  return { text, terms, unknown, search: searchOf(terms) }
}

function add<T>(list: T[], value: T, same: (a: T, b: T) => boolean = (a, b) => a === b): void {
  if (!list.some((v) => same(v, value))) list.push(value)
}

/** The TagSearch of a list of terms (each value once). */
export function searchOf(terms: readonly SearchTerm[]): TagSearch {
  const s = emptySearch()
  for (const term of terms) {
    switch (term.kind) {
      case 'side':
        add(s.sides, term.side)
        break
      case 'player':
        add(s.players, { side: term.side, number: term.number }, (a, b) => a.side === b.side && a.number === b.number)
        break
      case 'skill':
        add(s.codes, term.code)
        break
      case 'grade':
        add(s.grades, term.grade)
        break
      case 'zone':
        add(term.end === 'from' ? s.zones_from : term.end === 'to' ? s.zones_to : s.zones, term.zone)
        break
      case 'outcome':
        add(s.outcomes, term.outcome)
        break
      case 'set':
        add(s.sets, term.no)
        break
      case 'rotation':
        add(s.rotations, term.rotation)
        break
      case 'won':
        add(s.won_by, term.side)
        break
      case 'serving':
        add(s.serving, term.side)
        break
    }
  }
  return s
}

/** Only the kinds that are set, for the server (?search=, a smart reel's query). */
export function compactSearch(search: TagSearch): Partial<TagSearch> {
  return Object.fromEntries(Object.entries(search).filter(([, v]) => (v as unknown[]).length > 0)) as Partial<TagSearch>
}

/** ?search= for the stats: null when the search keeps everything. */
export function searchParam(search: TagSearch | null): string | null {
  if (!search || isEmptySearch(search)) return null
  return JSON.stringify(compactSearch(search))
}

// --- writing terms back as words -----------------------------------------------------------------

// Written capitalised, as the hints offer them (the parser reads any case).
const WORDS: Record<'en' | 'de', { side: Record<Side, string>; skill: Record<string, string>; ungraded: string; outcome: Record<Outcome, string>; set: string; rotation: string; zone: Record<ZoneEnd, string>; won: Record<Side, string>; serving: Record<Side, string> }> = {
  en: {
    side: { us: 'Us', them: 'Them' },
    skill: { serve: 'Serve', reception: 'Reception', set: 'Set', attack: 'Attack', block: 'Block', dig: 'Dig', freeball: 'Freeball' },
    ungraded: 'Ungraded',
    outcome: { point: 'Point', error: 'Error', play: 'In play' },
    set: 'Set',
    rotation: 'Rotation',
    zone: { from: 'From', to: 'To', any: 'Zone' },
    won: { us: 'Won', them: 'Lost' },
    serving: { us: 'Break', them: 'Side-out' },
  },
  de: {
    side: { us: 'Wir', them: 'Gegner' },
    skill: { serve: 'Aufschlag', reception: 'Annahme', set: 'Zuspiel', attack: 'Angriff', block: 'Block', dig: 'Abwehr', freeball: 'Freeball' },
    ungraded: 'Unbewertet',
    outcome: { point: 'Punkt', error: 'Fehler', play: 'Im Spiel' },
    set: 'Satz',
    rotation: 'Rotation',
    zone: { from: 'Von', to: 'Nach', any: 'Zone' },
    won: { us: 'Gewonnen', them: 'Verloren' },
    serving: { us: 'Break', them: 'Side-out' },
  },
}

/** The words of one term, as the parser reads them back (players as #7, theirs as a12). */
export function termText(term: SearchTerm, lang: 'en' | 'de', taxonomy: Taxonomy | null = null): string {
  const w = WORDS[lang]
  switch (term.kind) {
    case 'side':
      return w.side[term.side]
    case 'player':
      return term.side === 'them' ? `a${term.number}` : `#${term.number}`
    case 'skill': {
      const category = (taxonomy ?? VOLLEYBALL_TAXONOMY_HINT).skills.find((s) => s.code === term.code)?.category ?? ''
      return w.skill[category] ?? term.code
    }
    case 'grade':
      return term.grade ?? w.ungraded
    case 'zone':
      return `${w.zone[term.end]} ${term.zone}`
    case 'outcome':
      return w.outcome[term.outcome]
    case 'set':
      return `${w.set} ${term.no}`
    case 'rotation':
      return `${w.rotation} ${term.rotation}`
    case 'won':
      return w.won[term.side]
    case 'serving':
      return w.serving[term.side]
  }
}

/**
 * Terms as text that reads back as the same search (what a chip's removal leaves in the box).
 * Players come first: a side word would make a following #7 theirs.
 */
export function formatTerms(terms: readonly SearchTerm[], lang: 'en' | 'de', taxonomy: Taxonomy | null = null): string {
  const players = terms.filter((t) => t.kind === 'player')
  const rest = terms.filter((t) => t.kind !== 'player')
  return [...players, ...rest].map((term) => termText(term, lang, taxonomy)).join(' ')
}

// --- matching ------------------------------------------------------------------------------------

/** Point for the tagged side, its error, or neither (the rally went on). */
export function outcomeOf(tag: Pick<Tag, 'code' | 'attributes'>, taxonomy: Taxonomy | null): Outcome {
  const grade = tag.attributes.grade
  const skill = taxonomy?.skills.find((s) => s.code === tag.code)
  const ends = grade ? skill?.ends_rally_on[grade] : undefined
  if (ends === 'self') return 'point'
  return ends === 'other' ? 'error' : 'play'
}

export function tagTermsHold(tag: Pick<Tag, 'side' | 'number' | 'code' | 'attributes'>, s: TagSearch, taxonomy: Taxonomy | null): boolean {
  if (s.sides.length > 0 && !s.sides.includes(tag.side)) return false
  if (s.players.length > 0 && !s.players.some((p) => p.side === tag.side && p.number === tag.number)) return false
  if (s.codes.length > 0 && !s.codes.includes(tag.code)) return false
  if (s.grades.length > 0 && !s.grades.includes(tag.attributes.grade ?? null)) return false
  const from = tag.attributes.zone_from ?? null
  const to = tag.attributes.zone_to ?? null
  if (s.zones_from.length > 0 && (from === null || !s.zones_from.includes(from))) return false
  if (s.zones_to.length > 0 && (to === null || !s.zones_to.includes(to))) return false
  if (s.zones.length > 0 && !((from !== null && s.zones.includes(from)) || (to !== null && s.zones.includes(to)))) return false
  return s.outcomes.length === 0 || s.outcomes.includes(outcomeOf(tag, taxonomy))
}

/** The rally terms for a rally, or for a tag outside every rally (only its set is known). */
export function rallyTermsHold(rally: Pick<Segment, 'serving' | 'won_by'> | null, setNo: number | null, rotation: number | null, s: TagSearch): boolean {
  if (s.sets.length > 0 && (setNo === null || !s.sets.includes(setNo))) return false
  if (s.rotations.length > 0 && (rotation === null || !s.rotations.includes(rotation))) return false
  if (s.won_by.length > 0 && (rally?.won_by == null || !s.won_by.includes(rally.won_by))) return false
  return s.serving.length === 0 || (rally?.serving != null && s.serving.includes(rally.serving))
}

/** After a side-out the setter moves one zone on: 1 → 6 → 5 → 4 → 3 → 2 → 1. */
export function nextRotation(rotation: number): number {
  return rotation === 1 ? 6 : rotation - 1
}

/**
 * Our rotation in each rally where it is known: each set with a starting rotation is walked
 * through its rallies, turning on every side-out we win; a winner nobody entered is the next
 * rally's server; where neither is known the walk stops (the server's stats.walk).
 */
export function rotationOfRallies(segments: readonly Segment[], periods: readonly Period[]): Map<string, number> {
  const out = new Map<string, number>()
  const rallies = ralliesOf(segments)
  for (const period of periods) {
    const start = period.start_rotation
    if (start == null || start < 1 || start > 6) continue
    const played = rallies.filter((r) => r.period_id === period.id)
    let rotation = start
    for (let n = 0; n < played.length; n += 1) {
      const rally = played[n] as Segment
      const won = rally.won_by ?? played[n + 1]?.serving ?? null
      if (won === null || rally.serving === null) break
      out.set(rally.id, rotation)
      if (rally.serving === 'them' && won === 'us') rotation = nextRotation(rotation)
    }
  }
  return out
}

/** Where the tags and rallies of a match stand: what the rally terms read. */
export interface SearchContext {
  /** Tag id → its rally (linked, or starting up to 2 s after it). */
  rallyOf: Map<string, Segment | null>
  /** Tag id → its set number (its rally's, else the set running at its time). */
  setOfTag: Map<string, number | null>
  setOfRally: Map<string, number | null>
  rotationOf: Map<string, number>
}

/** A tag up to this long before a rally belongs to it (the serve toss), as on the server. */
const RALLY_LEAD_MS = 2000

export function searchContext(tags: readonly Tag[], segments: readonly Segment[], periods: readonly Period[]): SearchContext {
  const rallies = ralliesOf(segments)
  const byId = new Map(rallies.map((r) => [r.id, r]))
  const numbers = new Map(periods.map((p) => [p.id, p.no]))
  const setOfRally = new Map<string, number | null>()
  for (const r of rallies) setOfRally.set(r.id, r.period_id ? (numbers.get(r.period_id) ?? null) : (periodAt(periods, r.start_ms)?.no ?? null))
  const rallyOf = new Map<string, Segment | null>()
  const setOfTag = new Map<string, number | null>()
  for (const tag of tags) {
    const rally =
      tag.segment_id !== null ? (byId.get(tag.segment_id) ?? null) : (rallies.find((r) => r.start_ms - RALLY_LEAD_MS <= tag.t_ms && tag.t_ms <= r.end_ms) ?? null)
    rallyOf.set(tag.id, rally)
    const fromRally = rally?.period_id ? numbers.get(rally.period_id) : undefined
    setOfTag.set(tag.id, fromRally ?? periodAt(periods, tag.t_ms)?.no ?? null)
  }
  return { rallyOf, setOfTag, setOfRally, rotationOf: rotationOfRallies(segments, periods) }
}

/** The tags a search keeps, in their order. */
export function searchTags<T extends Tag>(tags: readonly T[], s: TagSearch, ctx: SearchContext, taxonomy: Taxonomy | null): T[] {
  if (isEmptySearch(s)) return [...tags]
  return tags.filter((tag) => {
    if (!tagTermsHold(tag, s, taxonomy)) return false
    if (!hasRallyTerms(s)) return true
    const rally = ctx.rallyOf.get(tag.id) ?? null
    return rallyTermsHold(rally, ctx.setOfTag.get(tag.id) ?? null, rally ? (ctx.rotationOf.get(rally.id) ?? null) : null, s)
  })
}

/** The rallies a search keeps: their terms hold and, with tag terms, one of their tags matches. */
export function searchRallies<T extends Segment>(rallies: readonly T[], tags: readonly Tag[], s: TagSearch, ctx: SearchContext, taxonomy: Taxonomy | null): T[] {
  if (isEmptySearch(s)) return [...rallies]
  const withHit = new Set<string>()
  if (hasTagTerms(s)) {
    for (const tag of tags) {
      const rally = ctx.rallyOf.get(tag.id)
      if (rally && tagTermsHold(tag, s, taxonomy)) withHit.add(rally.id)
    }
  }
  return rallies.filter(
    (r) => rallyTermsHold(r, ctx.setOfRally.get(r.id) ?? null, ctx.rotationOf.get(r.id) ?? null, s) && (!hasTagTerms(s) || withHit.has(r.id)),
  )
}

// --- hints while typing --------------------------------------------------------------------------

export type HintKind = 'player' | 'side' | 'skill' | 'grade' | 'skillGrade' | 'outcome' | 'set' | 'rotation' | 'zoneFrom' | 'zoneTo' | 'zone' | 'won' | 'serving'

export interface SearchHint {
  /** The text that replaces the word being typed (a keyword waiting for its number ends in a space). */
  insert: string
  /** What the list shows. */
  label: string
  kind: HintKind
  /** A player's name, a set's score … */
  detail?: string
}

export interface HintOptions {
  lang: 'en' | 'de'
  roster?: readonly RosterEntry[]
  /** Set numbers of the match (a season: 1–5). */
  sets?: readonly number[]
  limit?: number
}

/** What the hints offer, capitalised ("Set" the skill, "Set …" the set number); every one parses. */
export const SEARCH_KEYWORDS: Readonly<Record<'en' | 'de', readonly (readonly [string, HintKind])[]>> = {
  en: [
    ['Attack', 'skill'],
    ['Serve', 'skill'],
    ['Reception', 'skill'],
    ['Set', 'skill'],
    ['Block', 'skill'],
    ['Dig', 'skill'],
    ['Freeball', 'skill'],
    ['Set ', 'set'],
    ['Rotation ', 'rotation'],
    ['From ', 'zoneFrom'],
    ['To ', 'zoneTo'],
    ['Zone ', 'zone'],
    ['Us', 'side'],
    ['Them', 'side'],
    ['Kill', 'skillGrade'],
    ['Ace', 'skillGrade'],
    ['Blocked', 'skillGrade'],
    ['Point', 'outcome'],
    ['Error', 'outcome'],
    ['In play', 'outcome'],
    ['Ungraded', 'grade'],
    ['Won', 'won'],
    ['Lost', 'won'],
    ['Side-out', 'serving'],
    ['Break', 'serving'],
  ],
  de: [
    ['Angriff', 'skill'],
    ['Aufschlag', 'skill'],
    ['Annahme', 'skill'],
    ['Zuspiel', 'skill'],
    ['Block', 'skill'],
    ['Abwehr', 'skill'],
    ['Freeball', 'skill'],
    ['Satz ', 'set'],
    ['Rotation ', 'rotation'],
    ['Von ', 'zoneFrom'],
    ['Nach ', 'zoneTo'],
    ['Zone ', 'zone'],
    ['Wir', 'side'],
    ['Gegner', 'side'],
    ['Kill', 'skillGrade'],
    ['Ass', 'skillGrade'],
    ['Geblockt', 'skillGrade'],
    ['Punkt', 'outcome'],
    ['Fehler', 'outcome'],
    ['Im Spiel', 'outcome'],
    ['Unbewertet', 'grade'],
    ['Gewonnen', 'won'],
    ['Verloren', 'won'],
    ['Side-out', 'serving'],
    ['Break', 'serving'],
  ],
}

const NUMBER_HINT: Record<NumberKind, HintKind> = { set: 'set', rotation: 'rotation', from: 'zoneFrom', to: 'zoneTo', zone: 'zone' }

/** The word being typed: what follows the last space or comma. */
export function currentWord(text: string): { before: string; word: string } {
  const match = /[^\s,;]*$/.exec(text)
  const word = match ? match[0] : ''
  return { before: text.slice(0, text.length - word.length), word }
}

/** Replaces the word being typed with a hint's text (a keyword's number comes next). */
export function applyHint(text: string, hint: Pick<SearchHint, 'insert'>): string {
  const { before } = currentWord(text)
  return `${before}${hint.insert}${hint.insert.endsWith(' ') ? '' : ' '}`
}

/**
 * Suggestions for the word being typed: after "set", "rotation", "from" … its numbers; after #
 * or a digit our players; else the words that start like it (the app's language first). An
 * empty word after a finished one shows a few starters.
 */
export function searchHints(text: string, { lang, roster = [], sets = [1, 2, 3, 4, 5], limit = 8 }: HintOptions): SearchHint[] {
  const { before, word } = currentWord(text)
  const previous = norm(words(before).at(-1) ?? '')
  const typed = norm(word)
  const numberKind = /\s$/.test(before) || before === '' ? NUMBER_WORDS[previous] : undefined
  if (numberKind && /^\d{0,2}$/.test(typed)) {
    const choices = numberKind === 'set' ? sets : numberKind === 'rotation' ? [1, 2, 3, 4, 5, 6] : [1, 2, 3, 4, 5, 6, 7, 8, 9]
    return choices
      .filter((n) => String(n).startsWith(typed))
      .slice(0, 9)
      .map((n) => ({ insert: String(n), label: `${capitalize(words(before).at(-1) ?? '')} ${n}`, kind: NUMBER_HINT[numberKind] }))
  }
  if (/^#?\d{0,2}$/.test(typed) && (typed.startsWith('#') || typed !== '')) {
    const digits = typed.replace('#', '')
    return roster
      .filter((r) => r.side === 'us' && String(r.number).startsWith(digits))
      .sort((a, b) => a.number - b.number)
      .slice(0, limit)
      .map((r) => ({ insert: `#${r.number}`, label: `#${r.number}`, kind: 'player' as const, detail: r.name ?? undefined }))
  }
  const first = SEARCH_KEYWORDS[lang]
  const other = SEARCH_KEYWORDS[lang === 'en' ? 'de' : 'en']
  const seen = new Set<string>()
  const out: SearchHint[] = []
  for (const [keyword, kind] of [...first, ...other]) {
    const key = keyword.trim()
    if (seen.has(`${key}|${kind}`)) continue
    if (typed === '' ? !first.some(([k]) => k === keyword) : !norm(key).startsWith(typed) || norm(key) === typed && !keyword.endsWith(' ')) continue
    seen.add(`${key}|${kind}`)
    out.push({ insert: keyword, label: keyword.endsWith(' ') ? `${key} …` : key, kind })
  }
  return out.slice(0, typed === '' ? Math.min(limit, 6) : limit)
}
