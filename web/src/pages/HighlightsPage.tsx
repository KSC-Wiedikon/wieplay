import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useParams } from 'react-router'
import { Button, ButtonLink } from '../components/Button'
import { LoadError, Message } from '../components/Message'
import { ReelPlayer } from '../components/ReelPlayer'
import { PageSpinner, Spinner } from '../components/Spinner'
import { ApiError } from '../lib/api'
import { formatDuration } from '../lib/format'
import { useReelPreview, useSaveReel, useTaxonomy } from '../lib/library'
import { useMe, useTeam } from '../lib/queries'
import { reelTimeline } from '../lib/reelPlayer'
import { sportOf, useSportAccent } from '../lib/sport'
import { useToast } from '../lib/toast'
import type { ReelQuery } from '../lib/types'
import { NotFoundPage } from './NotFoundPage'

/** /teams/:id/highlights — a player's tagged moments of the team's current season (a smart reel preview). */
export function HighlightsPage() {
  const { id } = useParams()
  if (!id || !/^\d+$/.test(id)) return <NotFoundPage />
  return <Highlights teamId={Number(id)} />
}

function Highlights({ teamId }: { teamId: number }) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const toast = useToast()
  const me = useMe()
  const team = useTeam(teamId)
  const save = useSaveReel()
  useSportAccent(team.data ? sportOf(team.data.sport) : 'vb')
  const memberId = me.data?.member.id
  const query: ReelQuery | null =
    team.data && memberId !== undefined
      ? { lineage_id: team.data.lineage_id, season: team.data.season, member_id: memberId, limit: 200 }
      : null
  const preview = useReelPreview(query)
  const taxonomy = useTaxonomy(team.data?.sport ?? null)

  if (team.isPending || me.isPending) return <PageSpinner />
  if (team.isError || !team.data) return <LoadError onRetry={() => void team.refetch()} />
  const { name, season } = team.data
  const title = `${t('highlights.pageTitle')} · ${name} ${season}`
  const pieces = preview.data ?? []
  const builder = `/reels/new?team=${teamId}&member=${memberId ?? ''}&season=${encodeURIComponent(season)}`

  return (
    <div className="flex flex-col gap-6">
      <title>{`${title} · nomal`}</title>
      <div className="flex flex-col gap-1">
        <Link to={`/teams/${teamId}`} className="text-sm font-semibold text-link underline-offset-2 hover:underline">
          {`${name} ${season}`}
        </Link>
        <h1 className="text-2xl font-bold">{t('highlights.pageTitle')}</h1>
        <p className="text-sm text-muted">{t('highlights.hint')}</p>
        {preview.data && pieces.length > 0 && (
          <p className="text-sm text-muted tabular-nums">{`${t('reels.count', { count: pieces.length })} · ${formatDuration(reelTimeline(pieces).total_ms)}`}</p>
        )}
      </div>
      {preview.isPending ? (
        <Spinner />
      ) : preview.isError ? (
        <LoadError onRetry={() => void preview.refetch()} />
      ) : pieces.length === 0 ? (
        <Message action={<ButtonLink to={builder}>{t('highlights.change')}</ButtonLink>}>{t('highlights.empty')}</Message>
      ) : (
        <>
          <ReelPlayer pieces={pieces} title={title} taxonomy={taxonomy.data ?? null} />
          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              disabled={save.isPending || !query}
              onClick={() =>
                query &&
                save.mutate(
                  { body: { title: `${t('highlights.title')} · ${name} ${season}`, kind: 'smart', query, visibility: 'private', lineage_id: query.lineage_id } },
                  {
                    onSuccess: (reel) => {
                      toast.success(t('reels.builder.saved'))
                      void navigate(`/reels/${reel.id}`)
                    },
                    onError: (e) => toast.error(t('reels.builder.failed', { detail: e instanceof ApiError ? e.detail : t('common.error') })),
                  },
                )
              }
            >
              {t('highlights.save')}
            </Button>
            <ButtonLink to={builder}>{t('highlights.change')}</ButtonLink>
          </div>
        </>
      )}
    </div>
  )
}
