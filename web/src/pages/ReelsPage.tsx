import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router'
import { Badge } from '../components/Badge'
import { Button, ButtonLink } from '../components/Button'
import { Card } from '../components/Card'
import { ConfirmDialog } from '../components/ConfirmDialog'
import { PencilIcon, PlusIcon, TrashIcon } from '../components/icons'
import { LoadError, Message } from '../components/Message'
import { PageSpinner } from '../components/Spinner'
import { Table, TBody, Td, Th, THead, Tr } from '../components/Table'
import { formatDate } from '../lib/format'
import { useDeleteReel, useReels } from '../lib/library'
import { useToast } from '../lib/toast'
import type { Reel } from '../lib/types'

/** /reels — my reels and the reels shared with my teams; start a highlight reel or one from clips. */
export function ReelsPage() {
  const { t } = useTranslation()
  const reels = useReels()
  const toast = useToast()
  const remove = useDeleteReel()
  const [deleting, setDeleting] = useState<Reel | null>(null)
  const mine = reels.data?.filter((r) => r.mine) ?? []
  const team = reels.data?.filter((r) => !r.mine) ?? []

  return (
    <div className="flex flex-col gap-8">
      <title>{`${t('reels.title')} · nomal`}</title>
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="text-2xl font-bold">{t('reels.title')}</h1>
          <p className="text-sm text-muted">{t('reels.hint')}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <ButtonLink to="/reels/new" variant="primary">
            <PlusIcon className="size-4" />
            {t('reels.newSmart')}
          </ButtonLink>
          <ButtonLink to="/reels/new?kind=manual">
            <PlusIcon className="size-4" />
            {t('reels.newManual')}
          </ButtonLink>
        </div>
      </div>
      {reels.isPending ? (
        <PageSpinner />
      ) : reels.isError ? (
        <LoadError onRetry={() => void reels.refetch()} />
      ) : (
        <>
          <section className="flex flex-col gap-3" aria-labelledby="reels-mine">
            <h2 id="reels-mine" className="text-lg font-semibold">
              {t('reels.mine')}
            </h2>
            {mine.length === 0 ? <Message>{t('reels.empty')}</Message> : <ReelTable reels={mine} caption={t('reels.mine')} onDelete={setDeleting} />}
          </section>
          <section className="flex flex-col gap-3" aria-labelledby="reels-team">
            <h2 id="reels-team" className="text-lg font-semibold">
              {t('reels.team')}
            </h2>
            {team.length === 0 ? <Message>{t('reels.teamEmpty')}</Message> : <ReelTable reels={team} caption={t('reels.team')} onDelete={setDeleting} />}
          </section>
        </>
      )}
      <ConfirmDialog
        open={deleting !== null}
        title={t('reels.deleteTitle')}
        confirmLabel={t('reels.delete')}
        danger
        busy={remove.isPending}
        onCancel={() => setDeleting(null)}
        onConfirm={() =>
          deleting &&
          remove.mutate(deleting, {
            onSuccess: () => toast.success(t('reels.deleted')),
            onError: () => toast.error(t('reels.deleteFailed')),
            onSettled: () => setDeleting(null),
          })
        }
      >
        {t('reels.deleteBody', { title: deleting?.title ?? '' })}
      </ConfirmDialog>
    </div>
  )
}

function ReelTable({ reels, caption, onDelete }: { reels: Reel[]; caption: string; onDelete: (reel: Reel) => void }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  return (
    <Card className="overflow-hidden">
      <Table caption={caption}>
        <THead>
          <tr>
            <Th>{t('reels.columns.reel')}</Th>
            <Th className="hidden sm:table-cell">{t('reels.columns.kind')}</Th>
            <Th className="hidden sm:table-cell">{t('reels.columns.updated')}</Th>
            <Th className="text-right">
              <span className="sr-only">{t('tagging.table.actions')}</span>
            </Th>
          </tr>
        </THead>
        <TBody>
          {reels.map((reel) => (
            <Tr key={reel.id} onActivate={() => void navigate(`/reels/${reel.id}`)}>
              <Td>
                <Link to={`/reels/${reel.id}`} className="font-semibold text-link underline-offset-2 hover:underline">
                  {reel.title}
                </Link>
                <span className="mt-1 flex flex-wrap gap-1">
                  <Badge className="sm:hidden">{t(`reels.kind.${reel.kind}`)}</Badge>
                  <Badge tone={reel.visibility === 'team' ? 'accent' : 'neutral'}>{t(`reels.shared.${reel.visibility}`)}</Badge>
                </span>
              </Td>
              <Td className="hidden sm:table-cell">{t(`reels.kind.${reel.kind}`)}</Td>
              <Td className="hidden whitespace-nowrap tabular-nums sm:table-cell">{formatDate(reel.updated_at)}</Td>
              <Td tight className="text-right whitespace-nowrap">
                {reel.mine && (
                  <>
                    <ButtonLink to={`/reels/${reel.id}/edit`} variant="ghost" size="icon" aria-label={t('reels.editFor', { title: reel.title })}>
                      <PencilIcon className="size-4" />
                    </ButtonLink>
                    <Button variant="ghostDanger" size="icon" aria-label={t('reels.deleteFor', { title: reel.title })} onClick={() => onDelete(reel)}>
                      <TrashIcon className="size-4" />
                    </Button>
                  </>
                )}
              </Td>
            </Tr>
          ))}
        </TBody>
      </Table>
    </Card>
  )
}
