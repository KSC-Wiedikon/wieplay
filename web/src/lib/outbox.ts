// Never lose work (docs/PLAN.md §9.6). Every tagging change is written to an IndexedDB outbox
// first, then sent in batches to POST /api/videos/{id}/changes:
//
// - Each item is the full new state of one tag, segment or set, keyed by its client-made id,
//   so a newer change to the same id replaces the queued one (last state wins) and resending
//   is harmless (the API upserts by id).
// - Sending is debounced (1 s) and retried with back-off after network or server errors, and
//   at once when the browser comes back online.
// - Each change carries the server version it was based on (base_version, set by the caller).
//   The API skips a change based on an older version and lists it as `stale`: such items leave
//   the outbox (the server's state wins, the caller takes it over) and are reported. A newer
//   change to an item that was just saved is rebased on the version the answer brings.
// - A 422 rejects the whole batch without saying which item is wrong, so the batch is split
//   until the bad item is alone; that one is dropped and reported, the rest are saved.
// - 401, 403 and 409 (logged out, not allowed, video not ready) stop sending; the items stay
//   until retry() or the next page load.
// - On page load, open() reads what is still queued, so the workspace can show it over the
//   server's data and send it.
//
// Without IndexedDB (blocked site data, some private windows) the outbox works in memory.

import { deleteDB, openDB, type IDBPDatabase } from 'idb'
import { ApiError } from './api'
import type { ChangesIn, ChangesOut, EventIn, PeriodIn, SegmentIn } from './types'

export type Change =
  | { kind: 'event'; payload: EventIn }
  | { kind: 'segment'; payload: SegmentIn }
  | { kind: 'period'; payload: PeriodIn }

export type ChangeKind = Change['kind']

export type OutboxItem = Change & {
  /** `${videoId}:${kind}:${id}` */
  key: string
  videoId: string
  id: string
  /** Increases with every change; tells a resent item from a newer one. */
  seq: number
  queuedAt: number
}

export interface Rejected {
  item: OutboxItem
  detail: string
}

export type OutboxPhase =
  /** Nothing queued. */
  | 'idle'
  /** Changes wait for the debounce. */
  | 'waiting'
  | 'sending'
  /** The last attempt failed (network or server); a retry is scheduled. */
  | 'retrying'
  /** Stopped: logged out, not allowed or the video is not ready (see `blocked`). */
  | 'blocked'

export interface OutboxState {
  pending: number
  phase: OutboxPhase
  /** False after a network failure, until a request gets through again. */
  online: boolean
  blocked: { status: number; detail: string } | null
  /** Items the API refused (422); dropped from the outbox. */
  rejected: Rejected[]
  /** Changes skipped because someone saved a newer version first; dropped from the outbox. */
  stale: OutboxItem[]
  /** False when IndexedDB is not available: changes are kept in memory only. */
  persistent: boolean
}

export interface OutboxOptions {
  videoId: string
  send: (body: ChangesIn) => Promise<ChangesOut>
  /** Called with each successful answer, before the sent items leave the outbox. */
  onSynced?: (out: ChangesOut, sent: readonly OutboxItem[]) => void
  debounceMs?: number
  /** First retry delay; doubles up to maxBackoffMs. */
  backoffMs?: number
  maxBackoffMs?: number
  dbName?: string
}

const STORE = 'changes'
const KIND_ORDER: Record<ChangeKind, number> = { period: 0, segment: 1, event: 2 }
/** The API's limits per request. */
const LIMITS: Record<ChangeKind, number> = { period: 20, segment: 500, event: 500 }

type Db = IDBPDatabase

// Until 17.10.2026 the app was called Nomal and queued changes in this database. The first
// outbox opened after the rename takes over everything still queued there (every video's).
export const LEGACY_DB = 'nomal-outbox'

async function takeOverLegacy(db: Db): Promise<void> {
  if (typeof indexedDB === 'undefined' || typeof indexedDB.databases !== 'function') return
  try {
    if (!(await indexedDB.databases()).some((d) => d.name === LEGACY_DB)) return
    const old = await openStore(LEGACY_DB)
    if (!old) return
    const items = (await old.getAll(STORE)) as OutboxItem[]
    old.close()
    if (items.length > 0) {
      const tx = db.transaction(STORE, 'readwrite')
      await Promise.all([...items.map((item) => tx.store.put(item)), tx.done])
    }
    await deleteDB(LEGACY_DB)
  } catch {
    // Left in place: the next page load tries again.
  }
}

async function openStore(name: string): Promise<Db | null> {
  if (typeof indexedDB === 'undefined') return null
  try {
    return await openDB(name, 1, {
      upgrade(db) {
        const store = db.createObjectStore(STORE, { keyPath: 'key' })
        store.createIndex('videoId', 'videoId')
      },
    })
  } catch {
    return null
  }
}

function itemOrder(a: OutboxItem, b: OutboxItem): number {
  return KIND_ORDER[a.kind] - KIND_ORDER[b.kind] || a.seq - b.seq
}

/** Splits into requests within the API's limits, keeping sets and segments ahead of tags. */
export function chunk(items: readonly OutboxItem[]): OutboxItem[][] {
  const byKind: Record<ChangeKind, OutboxItem[]> = { period: [], segment: [], event: [] }
  for (const item of [...items].sort(itemOrder)) byKind[item.kind].push(item)
  const rounds = Math.max(...(Object.keys(byKind) as ChangeKind[]).map((k) => Math.ceil(byKind[k].length / LIMITS[k])))
  const out: OutboxItem[][] = []
  for (let i = 0; i < rounds; i += 1) {
    out.push((['period', 'segment', 'event'] as const).flatMap((k) => byKind[k].slice(i * LIMITS[k], (i + 1) * LIMITS[k])))
  }
  return out
}

export function toBody(items: readonly OutboxItem[]): ChangesIn {
  const body: ChangesIn = { events: [], segments: [], periods: [] }
  for (const item of items) {
    if (item.kind === 'event') body.events.push(item.payload)
    else if (item.kind === 'segment') body.segments.push(item.payload)
    else body.periods.push(item.payload)
  }
  return body
}

type Outcome = 'ok' | 'retry' | 'blocked'

export class Outbox {
  private readonly options: Required<Omit<OutboxOptions, 'onSynced' | 'dbName'>> & Pick<OutboxOptions, 'onSynced'>
  private readonly db: Db | null
  private readonly memory = new Map<string, OutboxItem>()
  private readonly listeners = new Set<() => void>()
  private seq: number
  private timer: ReturnType<typeof setTimeout> | null = null
  private attempt = 0
  private running: Promise<void> | null = null
  private again = false
  private closed = false
  private state: OutboxState
  /** IndexedDB writes run one after another, so a delete never overtakes the put before it. */
  private io: Promise<void> = Promise.resolve()

  private constructor(options: OutboxOptions, db: Db | null, items: OutboxItem[]) {
    this.options = {
      debounceMs: 1000,
      backoffMs: 1000,
      maxBackoffMs: 30_000,
      ...options,
    }
    this.db = db
    for (const item of items) this.memory.set(item.key, item)
    this.seq = Math.max(0, ...items.map((i) => i.seq)) + 1
    this.state = {
      pending: items.length,
      phase: items.length > 0 ? 'waiting' : 'idle',
      online: true,
      blocked: null,
      rejected: [],
      stale: [],
      persistent: db !== null,
    }
    if (typeof window !== 'undefined') window.addEventListener('online', this.onOnline)
  }

  /** Opens the outbox of a video and picks up what an earlier page left queued. */
  static async open(options: OutboxOptions): Promise<Outbox> {
    const db = await openStore(options.dbName ?? 'wieplay-outbox')
    if (db && options.dbName === undefined) await takeOverLegacy(db)
    let items: OutboxItem[] = []
    if (db) {
      try {
        items = (await db.getAllFromIndex(STORE, 'videoId', options.videoId)) as OutboxItem[]
      } catch {
        items = []
      }
    }
    const outbox = new Outbox(options, db, items)
    if (items.length > 0) outbox.schedule(0)
    return outbox
  }

  /** Queued items, oldest change first. */
  items(): OutboxItem[] {
    return [...this.memory.values()].sort((a, b) => a.seq - b.seq)
  }

  getState = (): OutboxState => this.state

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  /**
   * Queues changes: they are in items() at once (for the optimistic view) and written to
   * IndexedDB before the promise resolves.
   */
  async enqueue(changes: readonly Change[]): Promise<void> {
    if (changes.length === 0) return
    const now = Date.now()
    const items = changes.map((change) => {
      const item = {
        ...change,
        key: `${this.options.videoId}:${change.kind}:${change.payload.id}`,
        videoId: this.options.videoId,
        id: change.payload.id,
        seq: this.seq++,
        queuedAt: now,
      } as OutboxItem
      this.memory.set(item.key, item)
      return item
    })
    this.update({ pending: this.memory.size, phase: this.state.phase === 'idle' ? 'waiting' : this.state.phase })
    if (this.state.phase !== 'blocked') this.schedule(this.options.debounceMs)
    await this.write(async (db) => {
      const tx = db.transaction(STORE, 'readwrite')
      await Promise.all([...items.map((item) => tx.store.put(item)), tx.done])
    })
  }

  /** Sends everything queued now. Resolves when this round is done (or failed). */
  flush(): Promise<void> {
    if (this.timer !== null) {
      clearTimeout(this.timer)
      this.timer = null
    }
    if (this.running) {
      this.again = true
      return this.running
    }
    this.running = this.run().finally(() => {
      this.running = null
      if (this.closed) return
      if (this.again) {
        this.again = false
        this.schedule(0)
      } else if (this.memory.size > 0 && this.state.phase !== 'blocked' && this.state.phase !== 'retrying') {
        this.schedule(this.options.debounceMs)
      }
    })
    return this.running
  }

  /** After a stop (blocked) or to skip the back-off wait. */
  retry(): Promise<void> {
    this.attempt = 0
    this.update({ blocked: null, phase: this.memory.size > 0 ? 'waiting' : 'idle' })
    return this.flush()
  }

  /** Clears the reported rejected and stale changes. */
  dismissNotes(): void {
    this.update({ rejected: [], stale: [] })
  }

  close(): void {
    this.closed = true
    if (this.timer !== null) clearTimeout(this.timer)
    this.timer = null
    if (typeof window !== 'undefined') window.removeEventListener('online', this.onOnline)
    this.listeners.clear()
    this.db?.close()
  }

  private onOnline = () => {
    if (this.memory.size > 0 && this.state.phase !== 'blocked') void this.retry()
  }

  private schedule(ms: number): void {
    if (this.closed) return
    if (this.timer !== null) clearTimeout(this.timer)
    this.timer = setTimeout(() => {
      this.timer = null
      void this.flush()
    }, ms)
  }

  private update(patch: Partial<OutboxState>): void {
    this.state = { ...this.state, ...patch }
    for (const listener of [...this.listeners]) listener()
  }

  private async run(): Promise<void> {
    const snapshot = this.items()
    if (snapshot.length === 0) {
      this.update({ pending: 0, phase: 'idle' })
      return
    }
    this.update({ phase: 'sending' })
    for (const batch of chunk(snapshot)) {
      const outcome = await this.sendBatch(batch)
      if (outcome !== 'ok') return
    }
    this.attempt = 0
    this.update({ pending: this.memory.size, phase: this.memory.size > 0 ? 'waiting' : 'idle', online: true })
  }

  private async sendBatch(batch: OutboxItem[]): Promise<Outcome> {
    let out: ChangesOut
    try {
      out = await this.options.send(toBody(batch))
    } catch (error) {
      return this.failed(batch, error)
    }
    this.options.onSynced?.(out, batch)
    await this.settle(batch, out)
    this.update({ pending: this.memory.size, online: true })
    return 'ok'
  }

  private async failed(batch: OutboxItem[], error: unknown): Promise<Outcome> {
    if (error instanceof ApiError && error.status === 422) {
      if (batch.length === 1) {
        const [item] = batch as [OutboxItem]
        await this.remove(batch)
        this.update({ pending: this.memory.size, rejected: [...this.state.rejected, { item, detail: error.detail }] })
        return 'ok'
      }
      const half = Math.ceil(batch.length / 2)
      const first = await this.sendBatch(batch.slice(0, half))
      if (first !== 'ok') return first
      return this.sendBatch(batch.slice(half))
    }
    if (error instanceof ApiError && [401, 403, 409].includes(error.status)) {
      this.update({ phase: 'blocked', blocked: { status: error.status, detail: error.detail }, online: true })
      return 'blocked'
    }
    // Network down (fetch throws a TypeError), 5xx, 429: try again later.
    const offline = !(error instanceof ApiError)
    const delay = Math.min(this.options.maxBackoffMs, this.options.backoffMs * 2 ** this.attempt)
    this.attempt += 1
    this.update({ phase: 'retrying', online: !offline })
    this.schedule(delay)
    return 'retry'
  }

  /**
   * After a successful answer: sent items leave the outbox; a newer change to one of them is
   * rebased on the version just saved; stale items (and anything queued after them for the
   * same id) are dropped and reported.
   */
  private async settle(sent: readonly OutboxItem[], out: ChangesOut): Promise<void> {
    const stale = new Set(out.stale ?? [])
    const versions = new Map<string, number>()
    for (const e of out.events) versions.set(`event:${e.id}`, e.version)
    for (const seg of out.segments) versions.set(`segment:${seg.id}`, seg.version)
    for (const p of out.periods) versions.set(`period:${p.id}`, p.version)

    const done: OutboxItem[] = []
    const dropped: OutboxItem[] = []
    const rebased: OutboxItem[] = []
    const skipped: OutboxItem[] = []
    for (const item of sent) {
      const current = this.memory.get(item.key)
      if (stale.has(item.id)) {
        skipped.push(item)
        if (current) {
          this.memory.delete(item.key)
          dropped.push(current)
        }
      } else if (current?.seq === item.seq) {
        this.memory.delete(item.key)
        done.push(item)
      } else if (current) {
        const version = versions.get(`${item.kind}:${item.id}`)
        if (version !== undefined) {
          const next = { ...current, payload: { ...current.payload, base_version: version } } as OutboxItem
          this.memory.set(next.key, next)
          rebased.push(next)
        }
      }
    }
    if (skipped.length > 0) this.update({ stale: [...this.state.stale, ...skipped] })
    if (done.length + dropped.length + rebased.length === 0) return
    await this.write(async (db) => {
      const tx = db.transaction(STORE, 'readwrite')
      await Promise.all([
        ...[...done, ...dropped].map(async (item) => {
          const stored = (await tx.store.get(item.key)) as OutboxItem | undefined
          if (stored && stored.seq === item.seq) await tx.store.delete(item.key)
        }),
        ...rebased.map((item) => tx.store.put(item)),
        tx.done,
      ])
    })
  }

  /** Drops sent items, unless a newer change to the same id came in meanwhile. */
  private async remove(sent: readonly OutboxItem[]): Promise<void> {
    const done = sent.filter((item) => this.memory.get(item.key)?.seq === item.seq)
    for (const item of done) this.memory.delete(item.key)
    if (done.length === 0) return
    await this.write(async (db) => {
      const tx = db.transaction(STORE, 'readwrite')
      await Promise.all([
        ...done.map(async (item) => {
          const stored = (await tx.store.get(item.key)) as OutboxItem | undefined
          if (stored && stored.seq === item.seq) await tx.store.delete(item.key)
        }),
        tx.done,
      ])
    })
  }

  /** Queues an IndexedDB write. A failure (quota, closed database) leaves the memory copy. */
  private write(fn: (db: Db) => Promise<void>): Promise<void> {
    const db = this.db
    if (!db) return Promise.resolve()
    this.io = this.io.then(() => fn(db)).catch(() => {})
    return this.io
  }
}
