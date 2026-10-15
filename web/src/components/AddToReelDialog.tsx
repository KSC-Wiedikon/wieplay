import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ApiError } from '../lib/api'
import { useAddToReel, useReels } from '../lib/library'
import { useToast } from '../lib/toast'
import type { ReelItemIn } from '../lib/types'
import { Button } from './Button'
import { Field, TextInput } from './Field'
import { Modal } from './Modal'
import { Spinner } from './Spinner'

interface AddToReelDialogProps {
  /** The moment to add; null closes the dialog. */
  item: ReelItemIn | null
  /** Title for a new reel. */
  suggestedTitle: string
  onClose: () => void
}

const NEW = 'new'

/** Puts a clip or a tagged moment at the end of one of my reels from clips, or starts a new one. */
export function AddToReelDialog({ item, suggestedTitle, onClose }: AddToReelDialogProps) {
  const { t } = useTranslation()
  return (
    <Modal open={item !== null} title={t('reels.add.title')} onClose={onClose}>
      {item && <AddForm item={item} suggestedTitle={suggestedTitle} onClose={onClose} />}
    </Modal>
  )
}

function AddForm({ item, suggestedTitle, onClose }: { item: ReelItemIn; suggestedTitle: string; onClose: () => void }) {
  const { t } = useTranslation()
  const id = useId()
  const toast = useToast()
  const reels = useReels()
  const add = useAddToReel()
  const mine = (reels.data ?? []).filter((r) => r.mine && r.kind === 'manual')
  const [choice, setChoice] = useState<string | null>(null)
  const [title, setTitle] = useState(suggestedTitle)
  const [error, setError] = useState<string | null>(null)
  const picked = choice ?? mine[0]?.id ?? NEW

  const submit = () => {
    const reel = mine.find((r) => r.id === picked) ?? null
    const name = reel ? reel.title : title.trim()
    if (!name) {
      setError(t('reels.builder.titleMissing'))
      return
    }
    add.mutate(
      { reel, title: name.slice(0, 200), item },
      {
        onSuccess: (saved) => {
          toast.success(t('reels.add.added', { title: saved.title }))
          onClose()
        },
        onError: (e) => setError(t('reels.add.failed', { detail: e instanceof ApiError ? e.detail : t('common.error') })),
      },
    )
  }

  if (reels.isPending) return <Spinner />
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(e) => {
        e.preventDefault()
        submit()
      }}
    >
      <fieldset className="flex flex-col gap-1">
        <legend className="mb-1 text-sm font-semibold">{t('reels.add.pick')}</legend>
        {mine.length === 0 && <p className="text-sm text-muted">{t('reels.add.none')}</p>}
        {[...mine.map((r) => ({ value: r.id, label: r.title })), { value: NEW, label: t('reels.add.newReel') }].map((option) => (
          <label key={option.value} className="flex min-h-11 cursor-pointer items-center gap-2 text-sm sm:min-h-9">
            <input
              type="radio"
              name={`${id}-reel`}
              value={option.value}
              checked={picked === option.value}
              onChange={() => setChoice(option.value)}
              className="size-4 accent-accent"
            />
            <span className="wrap-break-word">{option.label}</span>
          </label>
        ))}
      </fieldset>
      {picked === NEW && (
        <Field label={t('reels.add.newTitle')} htmlFor={`${id}-title`}>
          <TextInput id={`${id}-title`} value={title} maxLength={200} onChange={(e) => setTitle(e.target.value)} />
        </Field>
      )}
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      <div className="flex flex-wrap justify-end gap-2">
        <Button onClick={onClose}>{t('common.cancel')}</Button>
        <Button type="submit" variant="primary" disabled={add.isPending}>
          {add.isPending ? t('common.saving') : t('reels.add.save')}
        </Button>
      </div>
    </form>
  )
}
