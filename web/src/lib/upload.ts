// Browser upload straight to object storage with S3 multipart (PLAN §7.2).
//
// The API creates the multipart uploads and signs each part URL; the bytes go
// from the browser to the bucket. Parts of all files share one pool of
// CONCURRENCY uploads. URLs are signed lazily, a few parts ahead, so none
// expires in a long queue. Every part is retried with back-off (a 403 means
// the URL expired: it is signed again). An interrupted upload resumes: before
// a file starts, the parts already stored are listed and skipped.

import { useCallback, useEffect, useState, useSyncExternalStore } from 'react'
import { api, ApiError } from './api'
import type { PartUrl, UploadedPart, Video, VideoFile } from './types'

export const CONCURRENCY = 4
export const SIGN_AHEAD = 8
export const RETRIES = 5
/** Part URLs are valid for 1 h; sign again well before that. */
export const URL_MAX_AGE_MS = 45 * 60_000
const SPEED_WINDOW_MS = 10_000
const EMIT_INTERVAL_MS = 200

export interface PartPlan {
  part: number
  start: number
  end: number
}

/** Byte ranges of a file's parts, numbered from 1. Every part but the last is partSize long. */
export function planParts(size: number, partSize: number): PartPlan[] {
  const count = Math.max(1, Math.ceil(size / partSize))
  return Array.from({ length: count }, (_, i) => ({
    part: i + 1,
    start: i * partSize,
    end: Math.min(size, (i + 1) * partSize),
  }))
}

/** Parts still to upload: not stored yet, or stored with the wrong size (uploaded again). */
export function pendingParts(plan: readonly PartPlan[], stored: readonly UploadedPart[]): PartPlan[] {
  const sizes = new Map(stored.map((p) => [p.part, p.size]))
  return plan.filter((p) => sizes.get(p.part) !== p.end - p.start)
}

export interface FilePair {
  file: VideoFile
  blob: Blob
}

export interface FileMatch {
  pairs: FilePair[]
  /** Selected files that are not one of the video's files still to upload. */
  unmatched: File[]
  /** Files of the video still to upload that were not selected. */
  missing: VideoFile[]
}

/** Matches picked files to the video's files still to upload, by name and size. */
export function matchFiles(videoFiles: readonly VideoFile[], selected: readonly File[]): FileMatch {
  const open = videoFiles.filter((f) => !f.uploaded).sort((a, b) => a.part_no - b.part_no)
  const free = [...selected]
  const pairs: FilePair[] = []
  const missing: VideoFile[] = []
  for (const file of open) {
    const index = free.findIndex((blob) => blob.name === file.name && blob.size === file.size)
    if (index === -1) missing.push(file)
    else pairs.push({ file, blob: free.splice(index, 1)[0] as File })
  }
  return { pairs, unmatched: free, missing }
}

const nameOrder = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' })

/** Split camera files of one recording, in recording order (by name: GX010042, GX020042, …). */
export function sortByName<T extends { name: string }>(files: readonly T[]): T[] {
  return [...files].sort((a, b) => nameOrder.compare(a.name, b.name))
}

/** A PUT of a part to object storage failed; status 0 is a network error. */
export class PutError extends Error {
  readonly status: number

  constructor(status: number) {
    super(status ? `Part upload failed (HTTP ${status})` : 'Part upload failed (network)')
    this.name = 'PutError'
    this.status = status
  }
}

function isAbort(error: unknown): boolean {
  return error instanceof DOMException && error.name === 'AbortError'
}

/** Network trouble, server errors and expired URLs are retried; refusals from the API are not. */
export function isRetryable(error: unknown): boolean {
  if (isAbort(error)) return false
  // 404 from storage: the multipart upload is gone (cancelled elsewhere).
  if (error instanceof PutError) return error.status !== 404
  if (error instanceof ApiError) return error.status >= 500 || error.status === 408 || error.status === 429
  // fetch() rejects with a TypeError when the network is down.
  return error instanceof TypeError
}

export interface UploadTransport {
  uploadedParts(videoId: string, fileId: string, signal: AbortSignal): Promise<UploadedPart[]>
  signParts(videoId: string, fileId: string, parts: number[], signal: AbortSignal): Promise<PartUrl[]>
  putPart(url: string, body: Blob, onProgress: (loaded: number) => void, signal: AbortSignal): Promise<void>
  complete(videoId: string, fileId: string, signal: AbortSignal): Promise<Video>
  remove(videoId: string): Promise<void>
}

/** PUT with XMLHttpRequest, for upload progress. No Content-Type: the part URL is signed without one. */
export function putWithXhr(
  url: string,
  body: Blob,
  onProgress: (loaded: number) => void,
  signal: AbortSignal,
): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException('Aborted', 'AbortError'))
      return
    }
    const xhr = new XMLHttpRequest()
    const onAbort = () => xhr.abort()
    const done = () => signal.removeEventListener('abort', onAbort)
    xhr.open('PUT', url)
    xhr.upload.onprogress = (event) => onProgress(event.loaded)
    xhr.onload = () => {
      done()
      if (xhr.status >= 200 && xhr.status < 300) resolve()
      else reject(new PutError(xhr.status))
    }
    xhr.onerror = () => {
      done()
      reject(new PutError(0))
    }
    xhr.ontimeout = xhr.onerror
    xhr.onabort = () => {
      done()
      reject(new DOMException('Aborted', 'AbortError'))
    }
    signal.addEventListener('abort', onAbort)
    xhr.send(body)
  })
}

export const httpTransport: UploadTransport = {
  uploadedParts: (videoId, fileId, signal) => api.uploadedParts(videoId, fileId, signal),
  signParts: (videoId, fileId, parts, signal) => api.signParts(videoId, fileId, parts, signal),
  putPart: putWithXhr,
  complete: (videoId, fileId, signal) => api.completeFile(videoId, fileId, signal),
  remove: (videoId) => api.deleteVideo(videoId),
}

function sleepFor(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const onAbort = () => {
      window.clearTimeout(timer)
      reject(new DOMException('Aborted', 'AbortError'))
    }
    const timer = window.setTimeout(() => {
      signal.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    signal.addEventListener('abort', onAbort, { once: true })
  })
}

/** 1, 2, 4, 8, 16 s (at most 30 s), ±25 % so parallel parts don't retry in lockstep. */
export function backoffMs(attempt: number): number {
  return Math.min(30_000, 1000 * 2 ** (attempt - 1)) * (0.75 + Math.random() * 0.5)
}

export interface UploadOptions {
  concurrency?: number
  signAhead?: number
  retries?: number
  urlMaxAgeMs?: number
  backoff?: (attempt: number) => number
  sleep?: (ms: number, signal: AbortSignal) => Promise<void>
  now?: () => number
}

/**
 * running → done | error | stopped (paused, resumable) | cancelling → cancelled.
 */
export type UploadState = 'running' | 'done' | 'error' | 'stopped' | 'cancelling' | 'cancelled'

export interface FileProgress {
  id: string
  name: string
  size: number
  loaded: number
  done: boolean
}

export interface UploadSnapshot {
  state: UploadState
  files: FileProgress[]
  loaded: number
  total: number
  /** Null until there is enough data to say. */
  bytesPerSecond: number | null
  secondsLeft: number | null
  error: unknown
  /** The video as of the last completed file. */
  video: Video
}

interface Task {
  file: VideoFile
  blob: Blob
  plan: PartPlan[]
  queue: PartPlan[]
  inFlight: number
  doneBytes: number
  partLoaded: Map<number, number>
  urls: Map<number, { url: string; at: number }>
  /** Parts handed to a worker that have no URL yet; signed first. */
  needUrl: Set<number>
  signing: Promise<void> | null
  prepared: Promise<void> | null
  completing: Promise<void> | null
  completed: boolean
}

export class Uploader {
  private readonly controller = new AbortController()
  private readonly tasks: Task[]
  private readonly listeners = new Set<() => void>()
  private readonly concurrency: number
  private readonly signAhead: number
  private readonly retries: number
  private readonly urlMaxAgeMs: number
  private readonly backoff: (attempt: number) => number
  private readonly sleep: (ms: number, signal: AbortSignal) => Promise<void>
  private readonly now: () => number
  private state: UploadState = 'running'
  private error: unknown = null
  private video: Video
  private sent = 0
  private samples: { t: number; sent: number }[] = []
  private snapshot: UploadSnapshot
  private emitTimer: number | null = null
  private running: Promise<void> | null = null
  private readonly transport: UploadTransport

  constructor(
    video: Video,
    pairs: readonly FilePair[],
    transport: UploadTransport = httpTransport,
    options: UploadOptions = {},
  ) {
    this.video = video
    this.transport = transport
    this.concurrency = options.concurrency ?? CONCURRENCY
    this.signAhead = options.signAhead ?? SIGN_AHEAD
    this.retries = options.retries ?? RETRIES
    this.urlMaxAgeMs = options.urlMaxAgeMs ?? URL_MAX_AGE_MS
    this.backoff = options.backoff ?? backoffMs
    this.sleep = options.sleep ?? sleepFor
    this.now = options.now ?? (() => performance.now())
    this.tasks = [...pairs]
      .filter((p) => !p.file.uploaded)
      .sort((a, b) => a.file.part_no - b.file.part_no)
      .map(({ file, blob }) => ({
        file,
        blob,
        plan: planParts(file.size, file.part_size),
        queue: [],
        inFlight: 0,
        doneBytes: 0,
        partLoaded: new Map(),
        urls: new Map(),
        needUrl: new Set(),
        signing: null,
        prepared: null,
        completing: null,
        completed: false,
      }))
    this.snapshot = this.build()
  }

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  getSnapshot = (): UploadSnapshot => this.snapshot

  /** Runs the upload; resolves when it is done, failed, stopped or cancelled (never rejects). */
  start(): Promise<void> {
    this.running ??= this.run()
    return this.running
  }

  /** Stops the transfers and keeps what is stored, so the upload can resume later. */
  stop(): void {
    if (this.state !== 'running' && this.state !== 'error') return
    this.state = 'stopped'
    this.controller.abort()
    this.emit(true)
  }

  /** Stops the transfers and deletes the video. Throws if the delete fails (the upload stays stopped). */
  async cancel(): Promise<void> {
    if (this.state === 'cancelled' || this.state === 'cancelling') return
    this.state = 'cancelling'
    this.controller.abort()
    this.emit(true)
    await this.running
    try {
      await this.transport.remove(this.video.id)
      this.state = 'cancelled'
    } catch (error) {
      this.state = 'stopped'
      throw error
    } finally {
      this.emit(true)
    }
  }

  private async run(): Promise<void> {
    try {
      await Promise.all(Array.from({ length: this.concurrency }, () => this.worker()))
      if (this.state === 'running') this.state = 'done'
    } catch (error) {
      if (this.state === 'running') {
        this.state = 'error'
        this.error = error
        // Stop the other transfers; a retry starts a new Uploader, which resumes.
        this.controller.abort()
      }
    } finally {
      this.emit(true)
    }
  }

  private get signal(): AbortSignal {
    return this.controller.signal
  }

  private checkAborted(): void {
    if (this.signal.aborted) throw new DOMException('Aborted', 'AbortError')
  }

  private async worker(): Promise<void> {
    for (;;) {
      const next = await this.next()
      if (!next) return
      await this.uploadPart(next.task, next.part)
      await this.completeIfDone(next.task)
    }
  }

  /** The next part to upload, across files in order; prepares (lists stored parts of) each file once. */
  private async next(): Promise<{ task: Task; part: PartPlan } | null> {
    for (const task of this.tasks) {
      this.checkAborted()
      if (task.completed) continue
      await this.prepare(task)
      const part = task.queue.shift()
      if (part) {
        task.inFlight += 1
        task.needUrl.add(part.part)
        return { task, part }
      }
      // Everything was stored already, or the last parts are in flight elsewhere.
      await this.completeIfDone(task)
    }
    return null
  }

  private prepare(task: Task): Promise<void> {
    task.prepared ??= (async () => {
      const stored = await this.withRetries(() =>
        this.transport.uploadedParts(this.video.id, task.file.id, this.signal),
      )
      task.queue = pendingParts(task.plan, stored)
      const pending = new Set(task.queue.map((p) => p.part))
      task.doneBytes = task.plan.filter((p) => !pending.has(p.part)).reduce((sum, p) => sum + p.end - p.start, 0)
      this.emit(true)
    })()
    return task.prepared
  }

  private async completeIfDone(task: Task): Promise<void> {
    if (task.completed || task.queue.length > 0 || task.inFlight > 0) return
    task.completing ??= (async () => {
      this.video = await this.withRetries(() => this.transport.complete(this.video.id, task.file.id, this.signal))
      task.completed = true
      this.emit(true)
    })()
    await task.completing
  }

  private async uploadPart(task: Task, part: PartPlan): Promise<void> {
    try {
      for (let attempt = 0; ; attempt++) {
        this.checkAborted()
        try {
          const url = await this.urlFor(task, part.part)
          await this.transport.putPart(
            url,
            task.blob.slice(part.start, part.end),
            (loaded) => this.progress(task, part.part, loaded),
            this.signal,
          )
          task.partLoaded.delete(part.part)
          task.urls.delete(part.part)
          task.doneBytes += part.end - part.start
          this.emit(false)
          return
        } catch (error) {
          this.progress(task, part.part, 0)
          if (this.signal.aborted || !isRetryable(error) || attempt >= this.retries) throw error
          if (error instanceof PutError && error.status === 403) {
            // The signed URL expired (or the clock is off): sign it again right away.
            task.urls.delete(part.part)
          } else {
            await this.sleep(this.backoff(attempt + 1), this.signal)
          }
        }
      }
    } finally {
      task.inFlight -= 1
    }
  }

  private fresh(entry: { at: number } | undefined): boolean {
    return entry !== undefined && this.now() - entry.at < this.urlMaxAgeMs
  }

  /** A signed URL for the part; signs it with the next few queued parts in one request. */
  private async urlFor(task: Task, part: number): Promise<string> {
    for (let own = 0; ; ) {
      const cached = task.urls.get(part)
      if (cached && this.fresh(cached)) {
        task.needUrl.delete(part)
        return cached.url
      }
      if (task.signing) {
        // Another part is being signed with the ones after it; this one may be among them.
        await task.signing
        continue
      }
      if (own >= 2) throw new Error(`No signed URL for part ${part}`)
      own += 1
      // This part, the other parts waiting for a URL, then the next queued ones.
      const others = [...new Set([...task.needUrl, ...task.queue.map((p) => p.part)])]
        .filter((p) => p !== part && !this.fresh(task.urls.get(p)))
        .slice(0, this.signAhead - 1)
      const batch = [part, ...others].sort((a, b) => a - b)
      task.signing = this.transport
        .signParts(this.video.id, task.file.id, batch, this.signal)
        .then((urls) => {
          const at = this.now()
          for (const { part: n, url } of urls) task.urls.set(n, { url, at })
        })
        .finally(() => {
          task.signing = null
        })
      await task.signing
    }
  }

  private async withRetries<T>(call: () => Promise<T>): Promise<T> {
    for (let attempt = 0; ; attempt++) {
      try {
        return await call()
      } catch (error) {
        if (this.signal.aborted || !isRetryable(error) || attempt >= this.retries) throw error
        await this.sleep(this.backoff(attempt + 1), this.signal)
      }
    }
  }

  private progress(task: Task, part: number, loaded: number): void {
    const before = task.partLoaded.get(part) ?? 0
    if (loaded > before) this.sent += loaded - before
    if (loaded > 0) task.partLoaded.set(part, loaded)
    else task.partLoaded.delete(part)
    this.emit(false)
  }

  private speed(now: number): number | null {
    this.samples.push({ t: now, sent: this.sent })
    while (this.samples.length > 2 && now - (this.samples[1]?.t ?? now) > SPEED_WINDOW_MS) this.samples.shift()
    const first = this.samples[0]
    if (!first || now - first.t < 1000) return null
    return ((this.sent - first.sent) * 1000) / (now - first.t)
  }

  private build(): UploadSnapshot {
    const files = this.tasks.map((task) => {
      let inFlight = 0
      for (const loaded of task.partLoaded.values()) inFlight += loaded
      return {
        id: task.file.id,
        name: task.file.name,
        size: task.file.size,
        loaded: Math.min(task.file.size, task.doneBytes + inFlight),
        done: task.completed,
      }
    })
    const loaded = files.reduce((sum, f) => sum + f.loaded, 0)
    const total = files.reduce((sum, f) => sum + f.size, 0)
    const bytesPerSecond = this.state === 'running' ? this.speed(this.now()) : null
    const secondsLeft = bytesPerSecond ? (total - loaded) / bytesPerSecond : null
    return {
      state: this.state,
      files,
      loaded,
      total,
      bytesPerSecond,
      secondsLeft,
      error: this.error,
      video: this.video,
    }
  }

  private emit(immediate: boolean): void {
    if (!immediate) {
      this.emitTimer ??= window.setTimeout(() => this.emit(true), EMIT_INTERVAL_MS)
      return
    }
    if (this.emitTimer !== null) {
      window.clearTimeout(this.emitTimer)
      this.emitTimer = null
    }
    this.snapshot = this.build()
    this.listeners.forEach((listener) => listener())
  }
}

const noop = () => () => {}

/**
 * One upload at a time for a page. `start` begins (or, after an error, resumes)
 * an upload; leaving the page stops it (resumable). Warns before the browser
 * tab is closed or reloaded while bytes are moving.
 */
export function useUpload(transport: UploadTransport = httpTransport) {
  const [uploader, setUploader] = useState<Uploader | null>(null)
  const snapshot = useSyncExternalStore(
    uploader ? uploader.subscribe : noop,
    () => uploader?.getSnapshot() ?? null,
  )
  const running = snapshot?.state === 'running'

  useEffect(() => () => uploader?.stop(), [uploader])

  useEffect(() => {
    if (!running) return
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      // Older browsers need returnValue set to show the prompt.
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [running])

  const start = useCallback(
    (video: Video, pairs: readonly FilePair[]) => {
      const next = new Uploader(video, pairs, transport)
      setUploader(next)
      return next.start()
    },
    [transport],
  )

  const cancel = useCallback(() => uploader?.cancel() ?? Promise.resolve(), [uploader])
  const stop = useCallback(() => uploader?.stop(), [uploader])

  return { snapshot, start, cancel, stop }
}
