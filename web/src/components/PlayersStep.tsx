import '../i18n/pages'
import { useTranslation } from 'react-i18next'
import { useAnalysis } from '../lib/analysis'
import { cx } from '../lib/cx'
import { useFindPlayers } from '../lib/players'
import { formatPercent } from '../lib/suggestions'
import type { Video } from '../lib/types'
import { Badge } from './Badge'
import { Button, ButtonLink } from './Button'
import { Card } from './Card'
import { AlertIcon, UsersIcon } from './icons'

/**
 * "Players in this video" (docs/PLAN.md §10.5, beta) in short, as a step after the rallies:
 * how many cards WiePlay made (honestly: many more than the players), how many have their
 * number, how many actions have a player, and the way to the gallery. Finding the players
 * is on demand (about 2 h of the club server per match): "Find players" when nobody has.
 * On the video page and below the review of the suggestions.
 */
export function PlayersStep({ video, canEdit, className }: { video: Video; canEdit: boolean; className?: string }) {
  const { t } = useTranslation()
  const analysis = useAnalysis(video.id, video.sport === 'volleyball')
  const find = useFindPlayers(video.id)
  const players = analysis.data?.players
  if (!players) return null

  const busy = players.status === 'queued' || players.status === 'running'
  const failed = players.status === 'failed'
  const done = players.run_id !== null
  const canFind = canEdit && (analysis.data?.can_run ?? true) && !done && !busy
  return (
    <Card role="region" aria-label={t('players.step.title')} className={cx('flex flex-wrap items-center justify-between gap-3 p-4', className)}>
      <div className="flex min-w-0 flex-[1_1_18rem] flex-col gap-1">
        <h2 className={cx('flex items-center gap-2 font-semibold', failed && 'text-danger')}>
          {failed ? <AlertIcon className="size-5" /> : <UsersIcon className="size-5" />}
          {t('players.step.title')}
          <Badge>{t('players.beta')}</Badge>
        </h2>
        <p role="status" className="text-sm text-muted">
          {failed
            ? t('players.step.failed')
            : busy && !done
              ? t('players.step.busy')
              : done
                ? t('players.step.found', {
                    total: players.clusters,
                    us: players.clusters_us,
                    them: players.clusters_them,
                    named: players.named,
                  })
                : t('players.step.none')}
        </p>
        {done && players.actions > 0 && (
          <p className="text-sm font-semibold tabular-nums">{t('players.step.identified', { value: formatPercent(players.identified_share) })}</p>
        )}
      </div>
      {done ? (
        <ButtonLink to={`/videos/${video.id}/players`} variant={canEdit && players.named < players.clusters ? 'primary' : 'secondary'}>
          {canEdit ? t('players.step.open') : t('players.step.openReadOnly')}
        </ButtonLink>
      ) : canFind ? (
        <Button
          variant="secondary"
          disabled={find.isPending}
          title={t('players.findHint')}
          onClick={() => find.mutate(undefined, { onSuccess: () => void analysis.refetch() })}
        >
          {t('players.find')}
        </Button>
      ) : null}
    </Card>
  )
}
