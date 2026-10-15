// The tagging workspace's data: the server's bundle with the outbox's queued changes on top
// (docs/PLAN.md §9.6), undo and redo over this page's own changes, and the rally being played
// (which exists only here until it ends: the API needs an end time).

import type { Change, ChangeKind, Outbox, OutboxItem } from './outbox'
import { byStart, rescore, serveCodes, sortedPeriods, type OpenRally } from './rallies'
import { readStorage, writeStorage } from './storage'
import type {
  ChangesOut,
  EventIn,
  Period,
  PeriodIn,
  RosterEntry,
  Segment,
  SegmentIn,
  Tag,
  Tagging,
  Taxonomy,
} from './types'

export { newId } from './id'

// The *ToIn helpers fill base_version with the item's version; TaggingStore.commit() sets
// the real base (the version last received from the server) when it queues the change.

export function eventToIn(tag: Tag): EventIn {
  return {
    id: tag.id,
    base_version: tag.version,
    code: tag.code,
    t_ms: tag.t_ms,
    frame: tag.frame,
    time_precision: tag.time_precision,
    side: tag.side,
    number: tag.number,
    attributes: tag.attributes,
    // The server links tags to rallies itself, by time.
    segment_id: null,
    clip_pre_ms: tag.clip_pre_ms,
    clip_post_ms: tag.clip_post_ms,
    note: tag.note,
    review: tag.review,
  }
}

export function segmentToIn(segment: Segment): SegmentIn {
  return {
    id: segment.id,
    base_version: segment.version,
    kind: segment.kind,
    ...(segment.label != null ? { label: segment.label } : {}),
    start_ms: segment.start_ms,
    end_ms: segment.end_ms,
    won_by: segment.won_by,
    review: segment.review,
  }
}

export function periodToIn(period: Period): PeriodIn {
  return {
    id: period.id,
    base_version: period.version,
    no: period.no,
    start_ms: period.start_ms,
    end_ms: period.end_ms,
    start_us: period.start_us,
    start_them: period.start_them,
    first_serve: period.first_serve,
    our_court_side_at_start: period.our_court_side_at_start,
    side_switch_at_score: period.side_switch_at_score,
  }
}

function memberFor(roster: readonly RosterEntry[], side: Tag['side'], number: number | null): number | null {
  if (side !== 'us' || number === null) return null
  return roster.find((r) => r.side === 'us' && r.number === number)?.member_id ?? null
}

function eventFromIn(p: EventIn, prev: Tag | undefined, roster: readonly RosterEntry[]): Tag {
  const number = p.number ?? null
  return {
    id: p.id,
    code: p.code,
    t_ms: p.t_ms,
    frame: p.frame ?? null,
    time_precision: p.time_precision,
    side: p.side,
    number,
    member_id: memberFor(roster, p.side, number),
    attributes: p.attributes,
    // Derived by the server; known once it has seen the tag.
    segment_id: prev?.segment_id ?? null,
    clip_pre_ms: p.clip_pre_ms ?? null,
    clip_post_ms: p.clip_post_ms ?? null,
    note: p.note ?? null,
    source: prev?.source ?? 'manual',
    review: p.review ?? prev?.review ?? 'unreviewed',
    version: prev?.version ?? 0,
    updated_at: prev?.updated_at ?? '',
    deleted_at: null,
  }
}

function segmentFromIn(p: SegmentIn, prev: Segment | undefined): Segment {
  return {
    id: p.id,
    kind: p.kind,
    label: p.label ?? null,
    start_ms: p.start_ms,
    end_ms: p.end_ms,
    period_id: prev?.period_id ?? null,
    serving: prev?.serving ?? null,
    won_by: p.won_by ?? null,
    score_us_after: prev?.score_us_after ?? null,
    score_them_after: prev?.score_them_after ?? null,
    score_source: prev?.score_source ?? null,
    source: prev?.source ?? 'manual',
    review: p.review ?? prev?.review ?? 'unreviewed',
    version: prev?.version ?? 0,
    deleted_at: null,
  }
}

function periodFromIn(p: PeriodIn, prev: Period | undefined): Period {
  return {
    id: p.id,
    no: p.no,
    start_ms: p.start_ms,
    end_ms: p.end_ms ?? null,
    start_us: p.start_us,
    start_them: p.start_them,
    first_serve: p.first_serve ?? null,
    our_court_side_at_start: p.our_court_side_at_start ?? null,
    side_switch_at_score: p.side_switch_at_score ?? null,
    final_us: prev?.final_us ?? null,
    final_them: prev?.final_them ?? null,
    version: prev?.version ?? 0,
    deleted_at: null,
  }
}

interface Base {
  events: Map<string, Tag>
  segments: Map<string, Segment>
  periods: Map<string, Period>
}

export interface MergedData {
  events: Tag[]
  segments: Segment[]
  periods: Period[]
  /** Scores were computed here (changes are still queued); the server's replace them after the sync. */
  provisional: boolean
}

/** The server's data with queued changes on top, in queue order (the last state wins). */
export function mergeView(
  base: Base,
  items: readonly OutboxItem[],
  roster: readonly RosterEntry[],
  taxonomy: Taxonomy | null,
): MergedData {
  const events = new Map(base.events)
  const segments = new Map(base.segments)
  const periods = new Map(base.periods)
  for (const item of items) {
    if (item.kind === 'event') {
      if (item.payload.deleted) events.delete(item.id)
      else events.set(item.id, eventFromIn(item.payload, base.events.get(item.id), roster))
    } else if (item.kind === 'segment') {
      if (item.payload.deleted) segments.delete(item.id)
      else segments.set(item.id, segmentFromIn(item.payload, base.segments.get(item.id)))
    } else if (item.payload.deleted) {
      periods.delete(item.id)
    } else {
      periods.set(item.id, periodFromIn(item.payload, base.periods.get(item.id)))
    }
  }
  const tags = [...events.values()].sort((a, b) => a.t_ms - b.t_ms)
  const provisional = items.length > 0
  if (!provisional) {
    return { events: tags, segments: [...segments.values()].sort(byStart), periods: sortedPeriods([...periods.values()]), provisional }
  }
  const scored = rescore([...periods.values()], [...segments.values()], tags, serveCodes(taxonomy))
  return { events: tags, segments: scored.segments, periods: scored.periods, provisional }
}

export interface TaggingView extends MergedData {
  roster: RosterEntry[]
  openRally: OpenRally | null
  canUndo: boolean
  canRedo: boolean
}

interface Step {
  changes: { before: Change | null; after: Change }[]
  openRally?: { before: OpenRally | null; after: OpenRally | null }
}

const MAX_UNDO = 200

function openRallyKey(videoId: string): string {
  return `nomal-open-rally:${videoId}`
}

function readOpenRally(videoId: string): OpenRally | null {
  try {
    const raw = readStorage(openRallyKey(videoId))
    if (!raw) return null
    const value = JSON.parse(raw) as unknown
    if (value && typeof value === 'object' && 'id' in value && 'start_ms' in value) {
      const { id, start_ms } = value as Record<string, unknown>
      if (typeof id === 'string' && typeof start_ms === 'number') return { id, start_ms }
    }
  } catch {
    // Ignore a broken entry.
  }
  return null
}

export class TaggingStore {
  readonly videoId: string
  readonly taxonomy: Taxonomy | null
  private base: Base
  private roster: RosterEntry[]
  private openRally: OpenRally | null
  private undoStack: Step[] = []
  private redoStack: Step[] = []
  private view: TaggingView
  private readonly listeners = new Set<() => void>()
  private outbox: Outbox | null = null
  private unsubscribe: () => void = () => {}
  /** Versions of deleted items (the server lists them only right after the delete): undo restores them. */
  private readonly deletedVersions = new Map<string, number>()

  constructor(videoId: string, bundle: Tagging) {
    this.videoId = videoId
    this.taxonomy = bundle.taxonomy
    this.base = this.toBase(bundle)
    this.roster = bundle.roster
    this.openRally = readOpenRally(videoId)
    this.view = this.compute()
  }

  /** Connects the outbox; its queued items show over the server's data from now on. */
  attach(outbox: Outbox): void {
    this.unsubscribe()
    this.outbox = outbox
    this.unsubscribe = outbox.subscribe(() => this.refresh())
    this.refresh()
  }

  detach(): void {
    this.unsubscribe()
    this.unsubscribe = () => {}
    this.outbox = null
  }

  getView = (): TaggingView => this.view

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  /** Applies changes now (optimistically) and queues them; one undo step. */
  commit(changes: readonly Change[], openRally?: OpenRally | null): void {
    const step: Step = { changes: changes.map((after) => ({ before: this.current(after), after })) }
    if (openRally !== undefined) step.openRally = { before: this.openRally, after: openRally }
    this.undoStack.push(step)
    if (this.undoStack.length > MAX_UNDO) this.undoStack.shift()
    this.redoStack = []
    this.apply(step.changes.map((c) => c.after), openRally)
  }

  undo(): boolean {
    const step = this.undoStack.pop()
    if (!step) return false
    this.redoStack.push(step)
    const changes = step.changes.map(({ before, after }) => before ?? deletedCopy(after)).reverse()
    this.apply(changes, step.openRally ? step.openRally.before : undefined)
    return true
  }

  redo(): boolean {
    const step = this.redoStack.pop()
    if (!step) return false
    this.undoStack.push(step)
    this.apply(
      step.changes.map((c) => c.after),
      step.openRally ? step.openRally.after : undefined,
    )
    return true
  }

  /**
   * The server's answer to a sent batch: the tags as saved (the server's state for stale ones),
   * all live segments and sets rescored, and the deleted ones the batch touched.
   */
  applySynced(out: ChangesOut): void {
    for (const tag of out.events) this.keep('event', this.base.events, tag)
    this.base.segments = this.live('segment', out.segments)
    this.base.periods = this.live('period', out.periods)
    this.refresh()
  }

  /** A fresh bundle from the server (after a roster change); queued changes stay on top. */
  replaceBase(bundle: Tagging): void {
    this.base = this.toBase(bundle)
    this.roster = bundle.roster
    this.refresh()
  }

  private toBase(bundle: Pick<Tagging, 'events' | 'segments' | 'periods'>): Base {
    return {
      events: this.live('event', bundle.events),
      segments: this.live('segment', bundle.segments),
      periods: this.live('period', bundle.periods),
    }
  }

  private live<T extends { id: string; version: number; deleted_at?: string | null }>(kind: ChangeKind, items: readonly T[]): Map<string, T> {
    const map = new Map<string, T>()
    for (const item of items) this.keep(kind, map, item)
    return map
  }

  /** A live item goes into the map; a deleted one leaves it, and its version is kept for undo. */
  private keep<T extends { id: string; version: number; deleted_at?: string | null }>(kind: ChangeKind, map: Map<string, T>, item: T): void {
    const key = `${kind}:${item.id}`
    if (item.deleted_at) {
      map.delete(item.id)
      this.deletedVersions.set(key, item.version)
    } else {
      map.set(item.id, item)
      this.deletedVersions.delete(key)
    }
  }

  /** The version the server last told us about: what a change to this item is based on. */
  private baseVersion(kind: ChangeKind, id: string): number {
    const map = kind === 'event' ? this.base.events : kind === 'segment' ? this.base.segments : this.base.periods
    return map.get(id)?.version ?? this.deletedVersions.get(`${kind}:${id}`) ?? 0
  }

  private current(change: Change): Change | null {
    const id = change.payload.id
    if (change.kind === 'event') {
      const tag = this.view.events.find((e) => e.id === id)
      return tag ? { kind: 'event', payload: eventToIn(tag) } : null
    }
    if (change.kind === 'segment') {
      const segment = this.view.segments.find((s) => s.id === id)
      return segment ? { kind: 'segment', payload: segmentToIn(segment) } : null
    }
    const period = this.view.periods.find((p) => p.id === id)
    return period ? { kind: 'period', payload: periodToIn(period) } : null
  }

  private apply(changes: readonly Change[], openRally: OpenRally | null | undefined): void {
    if (openRally !== undefined) {
      this.openRally = openRally
      writeStorage(openRallyKey(this.videoId), openRally ? JSON.stringify(openRally) : '')
    }
    const based = changes.map(
      (c) => ({ ...c, payload: { ...c.payload, base_version: this.baseVersion(c.kind, c.payload.id) } }) as Change,
    )
    if (based.length > 0 && this.outbox) void this.outbox.enqueue(based)
    // enqueue() notifies synchronously (refresh), but an open-rally-only step needs its own.
    this.refresh()
  }

  private refresh(): void {
    this.view = this.compute()
    for (const listener of [...this.listeners]) listener()
  }

  private compute(): TaggingView {
    const merged = mergeView(this.base, this.outbox?.items() ?? [], this.roster, this.taxonomy)
    return {
      ...merged,
      roster: this.roster,
      openRally: this.openRally,
      canUndo: this.undoStack.length > 0,
      canRedo: this.redoStack.length > 0,
    }
  }
}

function deletedCopy(change: Change): Change {
  return { ...change, payload: { ...change.payload, deleted: true } } as Change
}
