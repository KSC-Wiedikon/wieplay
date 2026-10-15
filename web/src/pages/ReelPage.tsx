import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router'
import { Badge } from '../components/Badge'
import { Button, ButtonLink } from '../components/Button'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { ExportButton } from '../components/ExportButton'
import { LoadError, Message } from '../components/Message'
import { ReelPlayer } from '../components/ReelPlayer'
import { PageSpinner, Spinner } from '../components/Spinner'
import { formatDuration } from '../lib/format'
import { useDeleteReel, useReelItems, useReels } from '../lib/library'
import { reelTimeline } from '../lib/reelPlayer'
import { useToast } from '../lib/toast'
import { isUuid } from '../lib/video'

/** /reels/:id — plays a reel; its owner edits or deletes it; anyone can try "Download as MP4". */
export function ReelPage() {
  const { t } = useTranslation()
  const { id } = useParams()
  const navigate = useNavigate()
  const toast = useToast()
  const reels = useReels()
  const reelId = isUuid(id) ? id : ''
  const reel = reels.data?.find((r) => r.id === reelId)
  const items = useReelItems(reelId, reel !== undefined)
  const remove = useDeleteReel()
  const [confirm, setConfirm] = useState(false)

  if (reels.isPending) return <PageSpinner />
  if (reels.isError) return <LoadError onRetry={() => void reels.refetch()} />
  if (!reel) return <Message action={<ButtonLink to="/reels">{t('reels.back')}</ButtonLink>}>{t('reels.notFound')}</Message>

  const pieces = items.data ?? []
  return (
    <div className="flex flex-col gap-6">
      <title>{`${reel.title} · nomal`}</title>
      <div className="flex flex-col gap-2">
        <Link to="/reels" className="text-sm font-semibold text-link underline-offset-2 hover:underline">
          {t('reels.back')}
        </Link>
        <h1 className="text-2xl font-bold wrap-break-word">{reel.title}</h1>
        <div className="flex flex-wrap items-center gap-2">
          <Badge>{t(`reels.kind.${reel.kind}`)}</Badge>
          <Badge tone={reel.visibility === 'team' ? 'accent' : 'neutral'}>{t(`reels.shared.${reel.visibility}`)}</Badge>
          {items.data && (
            <span className="text-sm text-muted tabular-nums">
              {`${t('reels.count', { count: pieces.length })} · ${formatDuration(reelTimeline(pieces).total_ms)}`}
            </span>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          {pieces.length > 0 && <ExportButton request={{ kind: 'reel', reel_id: reel.id }} />}
          {reel.mine && (
            <>
              <ButtonLink to={`/reels/${reel.id}/edit`}>{t('reels.edit')}</ButtonLink>
              <Button variant="ghostDanger" onClick={() => setConfirm(true)}>
                {t('reels.delete')}
              </Button>
            </>
          )}
        </div>
      </div>
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
