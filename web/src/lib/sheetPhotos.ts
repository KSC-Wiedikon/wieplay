// Photos of the paper scoresheet (docs/PLAN.md §10.3a, SS3): added from the phone's camera or a
// file through the redaction editor (src/lib/redact.ts), shown beside the typed form, removed at
// any time. For the team's coaches, team managers, sport admins and admins only: the sheet's GET
// lists them (`images`) for them alone. Each `url` answers with a link signed for minutes, and
// every view is in the audit log.

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, apiRequest, ForbiddenError, NotFoundError, UnauthorizedError } from './api'
import { keys } from './queryClient'
import { scoresheetKey } from './scoresheet'

export interface SheetPhoto {
  id: string
  page: number
  /** A photo of one set's panel, or null for the whole sheet (or a page of it). */
  set_no: number | null
  width: number
  height: number
  bytes: number
  uploaded_by: string | null
  uploaded_at: string
  /** The API path: a 302 to the image, signed for minutes. */
  url: string
}

export interface PhotoUpload {
  blob: Blob
  setNo: number | null
  boxes: number
  rotation: number
  cropped: boolean
}

/** Why the server refused a photo (`detail.code`), for a message in the reader's language. */
export function refusalOf(error: unknown): string | null {
  if (!(error instanceof ApiError)) return null
  const body = error.body
  if (body && typeof body === 'object' && 'detail' in body) {
    const detail = body.detail
    if (detail && typeof detail === 'object' && 'code' in detail && typeof detail.code === 'string') return detail.code
  }
  return null
}

async function upload(videoId: string, { blob, setNo, boxes, rotation, cropped }: PhotoUpload): Promise<SheetPhoto> {
  const query = new URLSearchParams({ redacted: 'true', boxes: String(boxes), rotation: String(rotation), cropped: String(cropped) })
  if (setNo !== null) query.set('set_no', String(setNo))
  const res = await fetch(`/api/videos/${videoId}/scoresheet/images?${query.toString()}`, {
    method: 'POST',
    headers: { Accept: 'application/json', 'Content-Type': blob.type || 'image/jpeg' },
    body: blob,
    credentials: 'same-origin',
  })
  if (res.ok) return (await res.json()) as SheetPhoto
  let body: unknown = null
  try {
    body = await res.json()
  } catch {
    // not JSON
  }
  const detail =
    body && typeof body === 'object' && 'detail' in body
      ? typeof body.detail === 'string'
        ? body.detail
        : body.detail && typeof body.detail === 'object' && 'message' in body.detail && typeof body.detail.message === 'string'
          ? body.detail.message
          : res.statusText
      : res.statusText || `HTTP ${res.status}`
  if (res.status === 401) throw new UnauthorizedError(detail, body)
  if (res.status === 403) throw new ForbiddenError(detail, body)
  if (res.status === 404) throw new NotFoundError(detail, body)
  throw new ApiError(res.status, detail, body)
}

export function useAddSheetPhoto(videoId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (photo: PhotoUpload) => upload(videoId, photo),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: scoresheetKey(videoId) })
      void client.invalidateQueries({ queryKey: keys.video(videoId) }) // "no scoresheet" is off now
    },
  })
}

export function useRemoveSheetPhoto(videoId: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (photo: SheetPhoto) => apiRequest<undefined>(photo.url, { method: 'DELETE' }),
    onSuccess: () => void client.invalidateQueries({ queryKey: scoresheetKey(videoId) }),
  })
}

/** The photo to show for a set: its own, else the first one of the whole sheet, else the first. */
export function photoForSet(photos: SheetPhoto[], set: number): SheetPhoto | null {
  return photos.find((p) => p.set_no === set) ?? photos.find((p) => p.set_no === null) ?? photos[0] ?? null
}
