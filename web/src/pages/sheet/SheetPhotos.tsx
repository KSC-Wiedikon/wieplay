import type { TFunction } from 'i18next'
import { useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/Button'
import { buttonClasses } from '../../components/buttonStyles'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { CameraIcon, ChevronDownIcon, ChevronUpIcon, ImageIcon, TrashIcon } from '../../components/icons'
import { ApiError } from '../../lib/api'
import { cx } from '../../lib/cx'
import { useMediaQuery } from '../../lib/mediaQuery'
import { formatDateTime } from '../../lib/format'
import { photoForSet, refusalOf, useAddSheetPhoto, useRemoveSheetPhoto, type SheetPhoto } from '../../lib/sheetPhotos'
import { useToast } from '../../lib/toast'
import { PhotoRedactor } from './PhotoRedactor'
import { PhotoViewer } from './PhotoViewer'

const REFUSALS = ['metadata', 'type', 'too_large', 'too_small', 'too_many_pixels', 'unreadable', 'too_many', 'not_redacted'] as const

function photoName(t: TFunction, photo: SheetPhoto): string {
  return photo.set_no !== null ? t('sheet.set', { no: photo.set_no }) : t('sheet.photo.page', { no: photo.page })
}

/**
 * Taking or choosing a photo, then the redaction step, then the upload. Phones offer the camera
 * directly; a computer picks a file.
 */
function AddPhoto({ videoId, sets, compact = false }: { videoId: string; sets: number; compact?: boolean }) {
  const { t } = useTranslation()
  const toast = useToast()
  const add = useAddSheetPhoto(videoId)
  const touch = useMediaQuery('(pointer: coarse)', false)
  const [file, setFile] = useState<File | null>(null)
  const [error, setError] = useState<string | null>(null)
  const camera = useRef<HTMLInputElement>(null)
  const picker = useRef<HTMLInputElement>(null)

  const chosen = (input: HTMLInputElement | null) => {
    const picked = input?.files?.[0] ?? null
    if (input) input.value = '' // the same file again opens the editor again
    if (!picked) return
    setError(null)
    setFile(picked)
  }

  const input = (ref: typeof camera, capture: boolean) => (
    <input
      ref={ref}
      type="file"
      accept="image/*"
      {...(capture ? { capture: 'environment' as const } : {})}
      className="sr-only"
      onChange={(event) => chosen(event.currentTarget)}
    />
  )

  return (
    <>
      {touch && (
        <label className={buttonClasses({ variant: compact ? 'ghost' : 'primary', size: compact ? 'icon' : 'md' })} title={t('sheet.photo.take')}>
          {input(camera, true)}
          <CameraIcon />
          <span className={cx(compact && 'sr-only')}>{t('sheet.photo.take')}</span>
        </label>
      )}
      <label
        className={buttonClasses({ variant: compact ? 'ghost' : touch ? 'secondary' : 'primary', size: compact ? 'icon' : 'md' })}
        title={touch ? t('sheet.photo.choose') : t('sheet.photo.add')}
      >
        {input(picker, false)}
        <ImageIcon />
        <span className={cx(compact && 'sr-only')}>{touch ? t('sheet.photo.choose') : t('sheet.photo.add')}</span>
      </label>
      {file && (
        <PhotoRedactor
          file={file}
          sets={sets}
          initialSet={null}
          busy={add.isPending}
          error={error}
          onCancel={() => {
            setFile(null)
            setError(null)
          }}
          onUpload={(photo) =>
            add.mutate(photo, {
              onSuccess: () => {
                setFile(null)
                toast.success(t('sheet.photo.added'))
              },
              onError: (e) => {
                const code = refusalOf(e)
                const known = REFUSALS.find((r) => r === code)
                setError(
                  known
                    ? t(`sheet.photo.refused.${known}`)
                    : t('sheet.photo.failed', { detail: e instanceof ApiError ? e.detail : t('common.error') }),
                )
              },
            })
          }
        />
      )}
    </>
  )
}

/** No photo yet: what it is for, who sees it, and the buttons. */
export function PhotoPrompt({ videoId, sets }: { videoId: string; sets: number; set?: number }) {
  const { t } = useTranslation()
  return (
    <section aria-labelledby="sheet-photo-title" className="flex flex-wrap items-center gap-x-5 gap-y-3 rounded-xl border border-dashed border-hairline-strong bg-surface p-4">
      <div className="flex min-w-0 flex-1 basis-72 flex-col gap-1">
        <h2 id="sheet-photo-title" className="font-semibold">
          {t('sheet.photo.title')}
        </h2>
        <p className="text-sm text-muted">{t('sheet.photo.prompt')}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        <AddPhoto videoId={videoId} sets={sets} />
      </div>
    </section>
  )
}

/**
 * The photos of the paper sheet beside the form (`side`, wide screens: as tall as the window,
 * sticky) or as a strip above the set tabs (`strip`, phones: collapsible, taller on demand).
 * The photo of the set being typed is shown; the chips switch between pages.
 */
export function SheetPhotos({
  videoId,
  photos,
  canAdd,
  set,
  sets,
  variant,
}: {
  videoId: string
  photos: SheetPhoto[]
  canAdd: boolean
  set: number
  sets: number
  variant: 'side' | 'strip'
}) {
  const { t } = useTranslation()
  const toast = useToast()
  const remove = useRemoveSheetPhoto(videoId)
  const [picked, setPicked] = useState<{ set: number; id: string } | null>(null)
  const [asking, setAsking] = useState<SheetPhoto | null>(null)
  const [open, setOpen] = useState(true)
  const [tall, setTall] = useState(false)
  const chosen = picked && picked.set === set ? photos.find((p) => p.id === picked.id) : undefined
  const shown = chosen ?? photoForSet(photos, set)

  if (!shown) return null
  const strip = variant === 'strip'

  const chips = photos.length > 1 && (
    <div role="group" aria-label={t('sheet.photo.pages')} className="flex min-w-0 gap-1 overflow-x-auto">
      {photos.map((p) => (
        <button
          key={p.id}
          type="button"
          aria-pressed={p.id === shown.id}
          onClick={() => setPicked({ set, id: p.id })}
          className={cx(
            'min-h-9 shrink-0 rounded-full border px-3 text-xs font-semibold whitespace-nowrap',
            p.id === shown.id ? 'border-accent bg-accent-soft text-on-accent-soft' : 'border-hairline bg-surface text-muted hover:bg-hover',
          )}
        >
          {photoName(t, p)}
        </button>
      ))}
    </div>
  )

  return (
    <section
      aria-labelledby="sheet-photos-title"
      className={cx('flex min-h-0 flex-col gap-2', !strip && 'h-full rounded-card border border-hairline bg-surface p-3 shadow-card')}
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <h2 id="sheet-photos-title" className="mr-auto text-sm font-semibold">
          {t('sheet.photo.title')}
        </h2>
        {canAdd && (
          <span className="flex items-center gap-0.5">
            <AddPhoto videoId={videoId} sets={sets} compact />
            <Button variant="ghost" size="icon" aria-label={t('sheet.photo.remove')} title={t('sheet.photo.remove')} onClick={() => setAsking(shown)}>
              <TrashIcon />
            </Button>
          </span>
        )}
        {strip && (
          <Button variant="ghost" size="icon" aria-expanded={open} aria-label={open ? t('sheet.photo.hide') : t('sheet.photo.show')} onClick={() => setOpen((o) => !o)}>
            {open ? <ChevronUpIcon /> : <ChevronDownIcon />}
          </Button>
        )}
      </div>
      {(!strip || open) && (
        <>
          {chips}
          <PhotoViewer
            key={shown.id}
            url={shown.url}
            alt={t('sheet.photo.alt', { name: photoName(t, shown) })}
            className={strip ? (tall ? 'h-[70dvh]' : 'h-56') : 'min-h-0 flex-1'}
            toolbar={
              strip ? (
                <Button variant="ghost" onClick={() => setTall((v) => !v)}>
                  {tall ? t('sheet.photo.smaller') : t('sheet.photo.bigger')}
                </Button>
              ) : undefined
            }
          />
          <p className="text-xs text-muted">
            {shown.uploaded_by
              ? t('sheet.photo.addedBy', { name: shown.uploaded_by, date: formatDateTime(shown.uploaded_at) })
              : t('sheet.photo.addedOn', { date: formatDateTime(shown.uploaded_at) })}
            {' · '}
            {t('sheet.photo.private')}
          </p>
        </>
      )}
      <ConfirmDialog
        open={asking !== null}
        title={t('sheet.photo.removeTitle')}
        confirmLabel={t('sheet.photo.remove')}
        onCancel={() => setAsking(null)}
        onConfirm={() => {
          if (!asking) return
          remove.mutate(asking, {
            onSuccess: () => {
              setAsking(null)
              toast.success(t('sheet.photo.removed'))
            },
            onError: (e) => toast.error(t('sheet.photo.removeFailed', { detail: e instanceof ApiError ? e.detail : t('common.error') })),
          })
        }}
      >
        {t('sheet.photo.removeBody')}
      </ConfirmDialog>
    </section>
  )
}
