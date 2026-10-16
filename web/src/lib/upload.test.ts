import { describe, expect, it, vi } from 'vitest'
import { ApiError } from './api'
import type { UploadedPart, Video, VideoFile } from './types'
import {
  isRetryable,
  matchFiles,
  pendingParts,
  planParts,
  PutError,
  sortByName,
  Uploader,
  type FilePair,
  type UploadTransport,
} from './upload'

const PART = 10

function videoFile(id: string, size: number, partNo = 1, uploaded = false): VideoFile {
  return {
    id,
    part_no: partNo,
    name: `GX0${partNo}0001.MP4`,
    size,
    part_size: PART,
    part_count: Math.max(1, Math.ceil(size / PART)),
    uploaded,
  }
}

function video(files: VideoFile[], status: Video['status'] = 'uploading'): Video {
  return {
    id: 'v1',
    team_id: 92,
    team_name: 'H3',
    team_season: '2026/27',
    sport: 'volleyball',
    kind: 'match',
    game_id: 501,
    title: 'KSCW – Volley Näfels',
    recorded_on: '2026-10-11',
    source: 'file',
    imported_from: null,
    status,
    duration_ms: null,
    width: null,
    height: null,
    fps: null,
    has_audio: null,
    meets_standard: null,
    standard_issues: [],
    renditions: [],
    has_thumbs: false,
    progress: null,
    error: null,
    created_at: '2026-10-11T20:00:00Z',
    created_by: 1,
    can_delete: true,
    can_upload: true,
    files,
  }
}

function pair(file: VideoFile): FilePair {
  return { file, blob: new Blob(['x'.repeat(file.size)]) }
}

interface Put {
  file: string
  part: number
  size: number
  url: string
}

/** In-memory transport. `failPut` decides per attempt whether a PUT fails (with a status). */
function fakeTransport({
  stored = {},
  failPut = () => null,
}: {
  stored?: Record<string, UploadedPart[]>
  failPut?: (part: number, attempt: number, url: string) => number | null
} = {}) {
  const signs: { file: string; parts: number[] }[] = []
  const puts: Put[] = []
  const completes: string[] = []
  const removes: string[] = []
  const attempts = new Map<string, number>()
  let inFlight = 0
  let maxInFlight = 0
  let signed = 0

  const transport: UploadTransport = {
    uploadedParts: async (_video, file) => stored[file] ?? [],
    signParts: async (_video, file, parts) => {
      signs.push({ file, parts })
      signed += 1
      return parts.map((part) => ({ part, url: `https://bucket.example/${file}/${part}?sig=${signed}` }))
    },
    putPart: (url, body, onProgress, signal) =>
      new Promise((resolve, reject) => {
        const [, file = '', partText = ''] = /example\/([^/]+)\/(\d+)/.exec(url) ?? []
        const part = Number(partText)
        const key = `${file}/${part}`
        const attempt = (attempts.get(key) ?? 0) + 1
        attempts.set(key, attempt)
        inFlight += 1
        maxInFlight = Math.max(maxInFlight, inFlight)
        const onAbort = () => {
          inFlight -= 1
          reject(new DOMException('Aborted', 'AbortError'))
        }
        signal.addEventListener('abort', onAbort, { once: true })
        setTimeout(() => {
          if (signal.aborted) return
          signal.removeEventListener('abort', onAbort)
          inFlight -= 1
          const status = failPut(part, attempt, url)
          if (status !== null) {
            reject(new PutError(status))
            return
          }
          onProgress(body.size)
          puts.push({ file, part, size: body.size, url })
          resolve()
        }, 1)
      }),
    complete: async (_video, file) => {
      completes.push(file)
      return video([], 'queued')
    },
    remove: async (id) => {
      removes.push(id)
    },
  }
  return { transport, signs, puts, completes, removes, maxInFlight: () => maxInFlight }
}

const options = { sleep: vi.fn<(ms: number, signal: AbortSignal) => Promise<void>>(async () => {}), backoff: (attempt: number) => attempt * 100 }

describe('planParts', () => {
  it('cuts a file into numbered byte ranges, the last one shorter', () => {
    expect(planParts(25, 10)).toEqual([
      { part: 1, start: 0, end: 10 },
      { part: 2, start: 10, end: 20 },
      { part: 3, start: 20, end: 25 },
    ])
    expect(planParts(20, 10)).toHaveLength(2)
    expect(planParts(3, 10)).toEqual([{ part: 1, start: 0, end: 3 }])
  })
})

describe('pendingParts', () => {
  it('skips parts already stored with the right size', () => {
    const plan = planParts(25, 10)
    expect(pendingParts(plan, [{ part: 1, size: 10 }]).map((p) => p.part)).toEqual([2, 3])
    expect(pendingParts(plan, [{ part: 3, size: 5 }]).map((p) => p.part)).toEqual([1, 2])
  })

  it('uploads a part again when its stored size is wrong', () => {
    const plan = planParts(25, 10)
    expect(pendingParts(plan, [{ part: 1, size: 10 }, { part: 2, size: 7 }]).map((p) => p.part)).toEqual([2, 3])
  })
})

describe('matchFiles', () => {
  const a = videoFile('a', 25, 1)
  const b = videoFile('b', 5, 2)
  const done = videoFile('c', 8, 3, true)

  it('pairs picked files by name and size, ignoring uploaded ones', () => {
    const fa = new File(['x'.repeat(25)], a.name)
    const fb = new File(['x'.repeat(5)], b.name)
    const result = matchFiles([done, b, a], [fb, fa])
    expect(result.pairs.map((p) => p.file.id)).toEqual(['a', 'b'])
    expect(result.unmatched).toEqual([])
    expect(result.missing).toEqual([])
  })

  it('refuses a file with the same name but another size, and reports what is missing', () => {
    const wrong = new File(['x'.repeat(24)], a.name)
    const result = matchFiles([a, b], [wrong])
    expect(result.pairs).toEqual([])
    expect(result.unmatched).toEqual([wrong])
    expect(result.missing.map((f) => f.id)).toEqual(['a', 'b'])
  })
})

describe('sortByName', () => {
  it('orders split camera files naturally', () => {
    expect(sortByName([{ name: 'clip10.mp4' }, { name: 'clip2.mp4' }, { name: 'clip1.mp4' }]).map((f) => f.name)).toEqual(
      ['clip1.mp4', 'clip2.mp4', 'clip10.mp4'],
    )
  })
})

describe('isRetryable', () => {
  it('retries network trouble and server errors, not refusals', () => {
    expect(isRetryable(new PutError(0))).toBe(true)
    expect(isRetryable(new PutError(503))).toBe(true)
    expect(isRetryable(new PutError(403))).toBe(true)
    expect(isRetryable(new PutError(404))).toBe(false)
    expect(isRetryable(new ApiError(502, 'Bad gateway'))).toBe(true)
    expect(isRetryable(new ApiError(409, 'Not uploading'))).toBe(false)
    expect(isRetryable(new TypeError('Failed to fetch'))).toBe(true)
    expect(isRetryable(new DOMException('Aborted', 'AbortError'))).toBe(false)
  })
})

describe('Uploader', () => {
  it('uploads every part once, at most 4 at a time, then completes each file', async () => {
    const a = videoFile('a', 95, 1) // 10 parts
    const b = videoFile('b', 5, 2) // 1 part
    const fake = fakeTransport()
    const uploader = new Uploader(video([a, b]), [pair(b), pair(a)], fake.transport, options)

    await uploader.start()

    const snapshot = uploader.getSnapshot()
    expect(snapshot.state).toBe('done')
    expect(snapshot.loaded).toBe(100)
    expect(snapshot.total).toBe(100)
    expect(snapshot.files.map((f) => [f.id, f.done])).toEqual([
      ['a', true],
      ['b', true],
    ])
    expect(snapshot.video.status).toBe('queued')
    expect(fake.puts.filter((p) => p.file === 'a').map((p) => p.part).sort((x, y) => x - y)).toEqual([
      1, 2, 3, 4, 5, 6, 7, 8, 9, 10,
    ])
    expect(fake.puts.find((p) => p.file === 'a' && p.part === 10)?.size).toBe(5)
    expect(fake.puts.filter((p) => p.file === 'b').map((p) => p.part)).toEqual([1])
    expect(fake.maxInFlight()).toBeLessThanOrEqual(4)
    expect(fake.maxInFlight()).toBeGreaterThan(1)
    expect(fake.completes.sort()).toEqual(['a', 'b'])
  })

  it('signs URLs lazily, a few parts ahead, never the same part twice', async () => {
    const a = videoFile('a', 200, 1) // 20 parts
    const fake = fakeTransport()
    await new Uploader(video([a]), [pair(a)], fake.transport, options).start()

    expect(fake.signs.length).toBeGreaterThanOrEqual(3)
    for (const sign of fake.signs) expect(sign.parts.length).toBeLessThanOrEqual(8)
    expect(fake.signs[0]?.parts).toEqual([1, 2, 3, 4, 5, 6, 7, 8])
    const signed = fake.signs.flatMap((s) => s.parts)
    expect(new Set(signed).size).toBe(signed.length)
    expect(signed.sort((x, y) => x - y)).toEqual(Array.from({ length: 20 }, (_, i) => i + 1))
  })

  it('resumes: skips stored parts and uploads only the rest', async () => {
    const a = videoFile('a', 25, 1)
    const fake = fakeTransport({ stored: { a: [{ part: 1, size: 10 }, { part: 2, size: 10 }] } })
    const uploader = new Uploader(video([a]), [pair(a)], fake.transport, options)
    await uploader.start()

    expect(fake.puts.map((p) => p.part)).toEqual([3])
    expect(fake.signs.flatMap((s) => s.parts)).toEqual([3])
    expect(fake.completes).toEqual(['a'])
    expect(uploader.getSnapshot().loaded).toBe(25)
  })

  it('completes a file whose parts are all stored without uploading anything', async () => {
    const a = videoFile('a', 15, 1)
    const fake = fakeTransport({ stored: { a: [{ part: 1, size: 10 }, { part: 2, size: 5 }] } })
    await new Uploader(video([a]), [pair(a)], fake.transport, options).start()
    expect(fake.puts).toEqual([])
    expect(fake.completes).toEqual(['a'])
  })

  it('leaves files that are uploaded already alone', async () => {
    const a = videoFile('a', 15, 1, true)
    const b = videoFile('b', 5, 2)
    const fake = fakeTransport()
    await new Uploader(video([a, b]), [pair(a), pair(b)], fake.transport, options).start()
    expect(fake.puts.map((p) => p.file)).toEqual(['b'])
    expect(fake.completes).toEqual(['b'])
  })

  it('retries a failed part with back-off', async () => {
    const sleep = vi.fn<(ms: number, signal: AbortSignal) => Promise<void>>(async () => {})
    const a = videoFile('a', 25, 1)
    const fake = fakeTransport({ failPut: (part, attempt) => (part === 2 && attempt <= 2 ? 503 : null) })
    const uploader = new Uploader(video([a]), [pair(a)], fake.transport, { ...options, sleep })
    await uploader.start()

    expect(uploader.getSnapshot().state).toBe('done')
    expect(sleep.mock.calls.map(([ms]) => ms)).toEqual([100, 200])
    expect(fake.puts.map((p) => p.part).sort()).toEqual([1, 2, 3])
  })

  it('signs a part again when storage answers 403 (expired URL)', async () => {
    const sleep = vi.fn<(ms: number, signal: AbortSignal) => Promise<void>>(async () => {})
    const a = videoFile('a', 25, 1)
    const fake = fakeTransport({ failPut: (part, attempt) => (part === 1 && attempt === 1 ? 403 : null) })
    const uploader = new Uploader(video([a]), [pair(a)], fake.transport, { ...options, sleep })
    await uploader.start()

    expect(uploader.getSnapshot().state).toBe('done')
    expect(sleep).not.toHaveBeenCalled()
    const signsForPart1 = fake.signs.filter((s) => s.parts.includes(1))
    expect(signsForPart1).toHaveLength(2)
    const put1 = fake.puts.find((p) => p.part === 1)
    expect(put1?.url).not.toBe(`https://bucket.example/a/1?sig=1`)
  })

  it('signs again when a URL is older than the maximum age', async () => {
    let clock = 0
    const a = videoFile('a', 25, 1)
    const fake = fakeTransport()
    const slowSign: UploadTransport = {
      ...fake.transport,
      putPart: (url, body, onProgress, signal) => {
        clock += 10 * 60_000 // every PUT takes 10 minutes
        return fake.transport.putPart(url, body, onProgress, signal)
      },
    }
    await new Uploader(video([a]), [pair(a)], slowSign, {
      ...options,
      concurrency: 1,
      urlMaxAgeMs: 15 * 60_000,
      now: () => clock,
    }).start()
    // Parts 1–3 signed at t=0; part 3 starts at t=20 min, so it is signed again.
    expect(fake.signs.map((s) => s.parts)).toEqual([[1, 2, 3], [3]])
  })

  it('gives up after 5 retries and reports the error', async () => {
    const sleep = vi.fn<(ms: number, signal: AbortSignal) => Promise<void>>(async () => {})
    const a = videoFile('a', 5, 1)
    const fake = fakeTransport({ failPut: () => 500 })
    const uploader = new Uploader(video([a]), [pair(a)], fake.transport, { ...options, sleep })
    await uploader.start()

    const snapshot = uploader.getSnapshot()
    expect(snapshot.state).toBe('error')
    expect(snapshot.error).toBeInstanceOf(PutError)
    expect(sleep).toHaveBeenCalledTimes(5)
    expect(fake.completes).toEqual([])
  })

  it('does not retry a refusal from the API', async () => {
    const sleep = vi.fn<(ms: number, signal: AbortSignal) => Promise<void>>(async () => {})
    const a = videoFile('a', 5, 1)
    const fake = fakeTransport()
    const refusing: UploadTransport = {
      ...fake.transport,
      complete: async () => {
        throw new ApiError(409, 'Parts missing: [1]')
      },
    }
    const uploader = new Uploader(video([a]), [pair(a)], refusing, { ...options, sleep })
    await uploader.start()
    expect(uploader.getSnapshot().state).toBe('error')
    expect(uploader.getSnapshot().error).toMatchObject({ status: 409 })
    expect(sleep).not.toHaveBeenCalled()
  })

  it('cancel stops the transfers and deletes the video', async () => {
    const a = videoFile('a', 95, 1)
    const fake = fakeTransport()
    const uploader = new Uploader(video([a]), [pair(a)], fake.transport, options)
    const listener = vi.fn()
    uploader.subscribe(listener)
    const running = uploader.start()
    await new Promise((resolve) => setTimeout(resolve, 0))

    await uploader.cancel()
    await running

    expect(uploader.getSnapshot().state).toBe('cancelled')
    expect(fake.removes).toEqual(['v1'])
    expect(fake.completes).toEqual([])
    expect(fake.puts.length).toBeLessThan(10)
    expect(listener).toHaveBeenCalled()
  })

  it('stop keeps the video for a later resume', async () => {
    const a = videoFile('a', 95, 1)
    const fake = fakeTransport()
    const uploader = new Uploader(video([a]), [pair(a)], fake.transport, options)
    const running = uploader.start()
    uploader.stop()
    await running
    expect(uploader.getSnapshot().state).toBe('stopped')
    expect(fake.removes).toEqual([])
  })
})
