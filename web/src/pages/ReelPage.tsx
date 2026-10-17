import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router'
import { Badge } from '../components/Badge'
import { Button, ButtonLink } from '../components/Button'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { ExportButton } from '../components/ExportButton'
import { LoadError, Message } from '../components/Message'
import { PageHeader } from '../components/PageHeader'
import { ReelPlayer } from '../components/ReelPlayer'
import { PageSpinner, Spinner } from '../components/Spinner'
import { NotFoundError } from '../lib/api'
import { formatDuration } from '../lib/format'
import { useDeleteReel, useReel, useReelItems } from '../lib/library'
import { useToast } from '../lib/toast'
import { isUuid } from '../lib/video'

/** /reels/:id — plays a reel; its owner edits or deletes it; anyone can try "Download as MP4". */
export function ReelPage() {
  const { t } = useTranslation()
  const { id } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const reelId = isUuid(id) ? id : ''
  const detail = useReel(reelId, reelId !== '')
  const items = useReelItems(reelId, reelId !== '')
  const remove = useDeleteReel()
  const [confirm, setConfirm] = useState(false)

  const notFound = <Message action={<ButtonLink to="/reels">{t('reels.back')}</ButtonLink>}>{t('reels.notFound')}</Message>
  if (!reelId) return notFound
  if (detail.isPending) return <PageSpinner />
  // Missing and not readable look the same on purpose.
  if (detail.isError) return detail.error instanceof NotFoundError ? notFound : <LoadError onRetry={() => void detail.refetch()} />

  const { reel, item_count, total_ms } = detail.data
  const pieces = items.data ?? []
  return (
    <div className="flex flex-col gap-6">
      <title>{`${reel.title} · WiePlay`}</title>
      <PageHeader
        eyebrow={
          <Link to="/reels" className="font-semibold link">
            {t('reels.back')}
          </Link>
        }
        title={reel.title}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <Badge>{t(`reels.kind.${reel.kind}`)}</Badge>
            <Badge tone={reel.visibility === 'team' ? 'accent' : 'neutral'}>{t(`reels.shared.${reel.visibility}`)}</Badge>
            <span className="text-sm tabular-nums">{`${t('reels.count', { count: item_count })} · ${formatDuration(total_ms)}`}</span>
          </span>
        }
        actions={
          <>
            {item_count > 0 && <ExportButton request={{ kind: 'reel', reel_id: reel.id }} />}
            {reel.mine && (
              <>
                <ButtonLink to={`/reels/${reel.id}/edit`}>{t('reels.edit')}</ButtonLink>
                <Button variant="ghostDanger" onClick={() => setConfirm(true)}>
                  {t('reels.delete')}
                </Button>
              </>
            )}
          </>
        }
      />
      {items.isPending ? (
        <Spinner />
      ) : items.isError ? (
        <LoadError onRetry={() => void items.refetch()} />
      ) : pieces.length === 0 ? (
        <Message>{t('reels.noItems')}</Message>
      ) : (
        <ReelPlayer pieces={pieces} title={reel.title} />
      )}
      <ConfirmDialog
        open={confirm}
        title={t('reels.deleteTitle')}
        confirmLabel={t('reels.delete')}
        danger
        busy={remove.isPending}
        onCancel={() => setConfirm(false)}
        onConfirm={() =>
          remove.mutate(reel, {
            onSuccess: () => {
              toast.success(t('reels.deleted'))
              void navigate('/reels', { replace: true })
            },
            onError: () => toast.error(t('reels.deleteFailed')),
            onSettled: () => setConfirm(false),
          })
        }
      >
        {t('reels.deleteBody', { title: reel.title })}
      </ConfirmDialog>
    </div>
  )
}
