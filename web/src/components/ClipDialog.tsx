import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../lib/api'
import { clipProblem, MAX_CLIP_MS, type ClipDraft } from '../lib/clips'
import { cx } from '../lib/cx'
import { formatDuration } from '../lib/format'
import { newId } from '../lib/id'
import { formatClock } from '../lib/labels'
import { useSaveClip } from '../lib/library'
import { useToast } from '../lib/toast'
import type { Clip, Visibility } from '../lib/types'
import { Button } from './Button'
import { Field, TextInput } from './Field'
import { Modal } from './Modal'

interface ClipDialogProps {
  draft: ClipDraft | null
  /** Coaches, team managers and sport admins may share a clip with the team. */
  canShare: boolean
  duration_ms: number | null
  onClose: () => void
  onSaved?: (clip: Clip) => void
}

/** Title, note, who sees it, and the start and end (nudged by a second). */
export function ClipDialog({ draft, ...props }: ClipDialogProps) {
  const { t } = useTranslation()
  return (
    <Modal open={draft !== null} title={draft?.id ? t('clips.dialog.editTitle') : t('clips.dialog.newTitle')} onClose={props.onClose}>
      {draft && <ClipForm key={draft.id ?? `${draft.start_ms}-${draft.end_ms}`} draft={draft} {...props} />}
    </Modal>
  )
}

function ClipForm({ draft, canShare, duration_ms, onClose, onSaved }: ClipDialogProps & { draft: ClipDraft }) {
  const { t } = useTranslation()
  const id = useId()
  const toast = useToast()
  const save = useSaveClip()
  const [title, setTitle] = useState(draft.title)
  const [note, setNote] = useState(draft.note ?? '')
  const [visibility, setVisibility] = useState<Visibility>(draft.visibility)
  const [range, setRange] = useState({ start_ms: draft.start_ms, end_ms: draft.end_ms })
  const [error, setError] = useState<string | null>(null)
  // Someone who may not share keeps a team clip of theirs as it is (they can't make one).
  const showWho = canShare || draft.visibility === 'team'
  const end = duration_ms ?? Infinity

  const nudge = (field: 'start_ms' | 'end_ms', ms: number) =>
    setRange((r) => ({ ...r, [field]: Math.min(end, Math.max(0, r[field] + ms)) }))

  const submit = () => {
    const trimmed = title.trim()
    if (!trimmed) return setError(t('clips.errors.title'))
    const problem = clipProblem(range)
    if (problem) return setError(problem === 'order' ? t('clips.errors.order') : t('clips.errors.tooLong'))
    setError(null)
    save.mutate(
      {
        id: draft.id ?? newId(),
        body: {
          video_id: draft.video_id,
          start_ms: range.start_ms,
          end_ms: range.end_ms,
          title: trimmed.slice(0, 200),
          note: note.trim() ? note.trim().slice(0, 2000) : null,
          event_id: draft.event_id,
          visibility,
        },
      },
      {
        onSuccess: (clip) => {
          toast.success(t('clips.dialog.saved'))
          onSaved?.(clip)
          onClose()
        },
        onError: (e) => {
          if (e instanceof ApiError && e.status === 403) setError(t('clips.errors.forbidden'))
          else if (e instanceof ApiError && e.status === 409) setError(t('clips.errors.conflict'))
          else setError(t('clips.errors.failed', { detail: e instanceof ApiError ? e.detail : t('common.error') }))
        },
      },
    )
  }

  const length = range.end_ms - range.start_ms
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
    >
      <Field label={t('clips.dialog.title')} htmlFor={`${id}-title`}>
        <TextInput id={`${id}-title`} value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
      </Field>
      <fieldset className="flex flex-col gap-2">
        <legend className="mb-1.5 text-sm font-semibold">{t('clips.dialog.range')}</legend>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          {(['start_ms', 'end_ms'] as const).map((field) => (
            <span key={field} className="flex items-center gap-1">
              <span className="w-12 text-sm text-muted">{field === 'start_ms' ? t('clips.dialog.start') : t('clips.dialog.end')}</span>
              <Button
                size="icon"
                aria-label={field === 'start_ms' ? t('clips.dialog.startEarlier') : t('clips.dialog.endEarlier')}
                onClick={() => nudge(field, -1000)}
              >
                <span aria-hidden="true">−</span>
              </Button>
              <span className="w-16 text-center font-mono text-sm tabular-nums">{formatClock(range[field])}</span>
              <Button
                size="icon"
                aria-label={field === 'start_ms' ? t('clips.dialog.startLater') : t('clips.dialog.endLater')}
                onClick={() => nudge(field, 1000)}
              >
                <span aria-hidden="true">+</span>
              </Button>
            </span>
          ))}
        </div>
        <p className={cx('text-xs tabular-nums', length > MAX_CLIP_MS || length <= 0 ? 'text-danger' : 'text-muted')}>
          {t('clips.dialog.length', { length: formatDuration(Math.max(0, length)) })}
        </p>
      </fieldset>
      <Field label={t('clips.dialog.note')} htmlFor={`${id}-note`}>
        <textarea
          id={`${id}-note`}
          value={note}
          maxLength={2000}
          rows={3}
          onChange={(e) => setNote(e.target.value)}
          className="w-full rounded-xl border border-hairline bg-surface px-3 py-2 text-sm text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        />
      </Field>
      {showWho ? (
        <fieldset className="flex flex-col gap-1">
          <legend className="mb-1 text-sm font-semibold">{t('clips.dialog.who')}</legend>
          {(['private', 'team'] as const)
            .filter((v) => v === 'private' || canShare || draft.visibility === 'team')
            .map((v) => (
              <label key={v} className="flex min-h-11 cursor-pointer items-center gap-2 text-sm sm:min-h-9">
                <input
                  type="radio"
                  name={`${id}-who`}
                  value={v}
                  checked={visibility === v}
                  onChange={() => setVisibility(v)}
                  className="size-4 accent-accent"
                />
                {v === 'private' ? t('clips.dialog.private') : t('clips.dialog.team')}
              </label>
            ))}
        </fieldset>
      ) : (
        <p className="text-sm text-muted">{t('clips.dialog.privateOnly')}</p>
      )}
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <Button onClick={onClose}>{t('common.cancel')}</Button>
        <Button type="submit" variant="primary" disabled={save.isPending}>
          {save.isPending ? t('common.saving') : t('clips.dialog.save')}
        </Button>
      </div>
    </form>
  )
}
