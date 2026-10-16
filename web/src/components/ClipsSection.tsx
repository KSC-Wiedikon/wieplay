import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { byStart } from '../lib/clips'
import { cx } from '../lib/cx'
import { formatDuration } from '../lib/format'
import { formatClock } from '../lib/labels'
import { useDeleteClip } from '../lib/library'
import type { Playhead } from '../lib/playhead'
import { useToast } from '../lib/toast'
import type { Clip, ReelItemIn } from '../lib/types'
import { AddToReelDialog } from './AddToReelDialog'
import { Badge } from './Badge'
import { Button } from './Button'
import { Card } from './Card'
import { ConfirmDialog } from './ConfirmDialog'
import { ExportButton } from './ExportButton'
import { PencilIcon, PlusIcon, ScissorsIcon, TrashIcon } from './icons'
import { LoadError } from './Message'
import { Spinner } from './Spinner'
import { Switch } from './Switch'
import { Table, TBody, Td, Th, THead, Tr } from './Table'

interface ClipBarProps {
  playhead: Playhead
  clipIn: number | null
  clipOut: number | null
  onMark: (which: 'in' | 'out', t_ms: number) => void
  onClear: () => void
  onSave: () => void
}

/** Marks a clip's start and end at the playhead, then saves it (PLAN §6.8). */
export function ClipBar({ playhead, clipIn, clipOut, onMark, onClear, onSave }: ClipBarProps) {
  const { t } = useTranslation()
  const marked = clipIn !== null || clipOut !== null
  const summary = [
    clipIn !== null ? t('clips.bar.inAt', { time: formatClock(clipIn) }) : null,
    clipOut !== null ? t('clips.bar.outAt', { time: formatClock(clipOut) }) : null,
  ].filter(Boolean)
  return (
    <div role="group" aria-label={t('clips.bar.label')} className="flex flex-wrap items-center gap-2">
      <Button onClick={() => onMark('in', playhead.getState().t_ms)}>
        <ScissorsIcon className="size-4" />
        {t('clips.bar.in')}
      </Button>
      <Button onClick={() => onMark('out', playhead.getState().t_ms)}>{t('clips.bar.out')}</Button>
      <Button variant="primary" disabled={!marked} onClick={onSave}>
        {t('clips.bar.save')}
      </Button>
      <p className="text-sm text-muted tabular-nums" aria-live="polite">
        {marked ? summary.join(' · ') : t('clips.bar.none')}
      </p>
      {marked && (
        <Button variant="ghost" onClick={onClear}>
          {t('clips.bar.clear')}
        </Button>
      )}
    </div>
  )
}

interface ClipsSectionProps {
  clips: { data: Clip[] | undefined; isPending: boolean; isError: boolean; refetch: () => unknown }
  /** Coaches and team managers delete the team's shared clips too. */
  canManage: boolean
  canExport: boolean
  /** The clip playing now. */
  playingId: string | null
  onPlay: (clips: Clip[], index: number, all: boolean) => void
  onEdit: (clip: Clip) => void
}

/** The clips of a video I can see (mine and the team's): play, edit, add to a reel, download, delete. */
export function ClipsSection({ clips, canManage, canExport, playingId, onPlay, onEdit }: ClipsSectionProps) {
  const { t } = useTranslation()
  const id = useId()
  const toast = useToast()
  const remove = useDeleteClip()
  const [all, setAll] = useState(false)
  const [deleting, setDeleting] = useState<Clip | null>(null)
  const [adding, setAdding] = useState<Clip | null>(null)
  const list = byStart(clips.data ?? [])

  const item: ReelItemIn | null = adding
    ? { video_id: adding.video_id, start_ms: adding.start_ms, end_ms: adding.end_ms, clip_id: adding.id, caption: adding.title }
    : null

  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
        <h2 id={`${id}-title`} className="text-lg font-semibold">
          {t('clips.title')}
        </h2>
        {list.length > 1 && (
          <label className="flex cursor-pointer items-center gap-1 text-sm font-semibold">
            <Switch checked={all} onChange={setAll} label={t('clips.playAll')} />
            <span aria-hidden="true">{t('clips.playAll')}</span>
          </label>
        )}
      </div>
      {clips.isPending ? (
        <Spinner />
      ) : clips.isError ? (
        <LoadError onRetry={() => void clips.refetch()} />
      ) : list.length === 0 ? (
        <p className="text-sm text-muted">{t('clips.empty')}</p>
      ) : (
        <Card className="overflow-hidden">
          <Table caption={t('clips.title')}>
            <THead>
              <tr>
                <Th>{t('clips.columns.clip')}</Th>
                <Th className="hidden sm:table-cell">{t('clips.columns.time')}</Th>
                <Th className="hidden sm:table-cell">{t('clips.columns.shared')}</Th>
                <Th className="text-right">
                  <span className="sr-only">{t('tagging.table.actions')}</span>
                </Th>
              </tr>
            </THead>
            <TBody>
              {list.map((clip, index) => {
                const playing = clip.id === playingId
                const canDelete = clip.mine || (clip.visibility === 'team' && canManage)
                const range = `${formatClock(clip.start_ms, { tenths: false })}–${formatClock(clip.end_ms, { tenths: false })}`
                return (
                  <Tr
                    key={clip.id}
                    onActivate={() => onPlay(list, index, all)}
                    aria-current={playing ? 'true' : undefined}
                    className={cx(playing && 'bg-accent-soft')}
                  >
                    <Td tight>
                      <button
                        type="button"
                        onClick={() => onPlay(list, index, all)}
                        aria-label={t('clips.play', { title: clip.title })}
                        className="min-h-11 cursor-pointer rounded-lg px-1 text-left font-semibold link sm:min-h-9"
                      >
                        {clip.title}
                      </button>
                      <span className="block px-1 text-xs text-muted tabular-nums sm:hidden">
                        {`${range} · ${formatDuration(clip.end_ms - clip.start_ms)}`}
                        {clip.visibility === 'team' && ` · ${t('clips.visibility.team')}`}
                      </span>
                      {!clip.mine && clip.owner_name && <span className="block px-1 text-xs text-muted">{t('clips.by', { name: clip.owner_name })}</span>}
                      {clip.note && <span className="block px-1 pb-1 text-xs wrap-break-word text-muted">{clip.note}</span>}
                    </Td>
                    <Td className="hidden whitespace-nowrap text-muted tabular-nums sm:table-cell">
                      {range}
                      <span className="block text-xs">{formatDuration(clip.end_ms - clip.start_ms)}</span>
                    </Td>
                    <Td className="hidden sm:table-cell">
                      <Badge tone={clip.visibility === 'team' ? 'accent' : 'neutral'}>{t(`clips.visibility.${clip.visibility}`)}</Badge>
                    </Td>
                    <Td tight className="text-right">
                      {/* Two by two on phones, so the title keeps its room. */}
                      <div className="ml-auto flex max-w-22 flex-wrap justify-end sm:max-w-none sm:flex-nowrap">
                        {clip.mine && (
                          <Button variant="ghost" size="icon" aria-label={t('clips.edit', { title: clip.title })} onClick={() => onEdit(clip)}>
                            <PencilIcon className="size-4" />
                          </Button>
                        )}
                        <Button variant="ghost" size="icon" aria-label={t('clips.addToReel', { title: clip.title })} title={t('reels.addToReel')} onClick={() => setAdding(clip)}>
                          <PlusIcon className="size-4" />
                        </Button>
                        {canExport && (
                          <ExportButton iconOnly request={{ kind: 'clip', clip_id: clip.id }} label={t('clips.download', { title: clip.title })} />
                        )}
                        {canDelete && (
                          <Button variant="ghostDanger" size="icon" aria-label={t('clips.delete', { title: clip.title })} onClick={() => setDeleting(clip)}>
                            <TrashIcon className="size-4" />
                          </Button>
                        )}
                      </div>
                    </Td>
                  </Tr>
                )
              })}
            </TBody>
          </Table>
        </Card>
      )}
      <ConfirmDialog
        open={deleting !== null}
        title={t('clips.deleteTitle')}
        confirmLabel={t('clips.deleteConfirm')}
        danger
        busy={remove.isPending}
        onCancel={() => setDeleting(null)}
        onConfirm={() =>
          deleting &&
          remove.mutate(deleting, {
            onSuccess: () => toast.success(t('clips.deleted')),
            onError: () => toast.error(t('clips.deleteFailed')),
            onSettled: () => setDeleting(null),
          })
        }
      >
        {t('clips.deleteBody', { title: deleting?.title ?? '' })}
      </ConfirmDialog>
      <AddToReelDialog item={item} suggestedTitle={adding?.title ?? ''} onClose={() => setAdding(null)} />
    </section>
  )
}
