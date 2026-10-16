import '../i18n/pages'
import { useId } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { buttonClasses } from '../components/buttonStyles'
import { ChartIcon, DownloadIcon } from '../components/icons'
import { EmptyState, LoadError } from '../components/Message'
import { SectionHeader } from '../components/PageHeader'
import { Spinner } from '../components/Spinner'
import { StatsLegend, StatsTable, TeamTable, type TeamTableRow } from '../components/StatsTable'
import { formatDate } from '../lib/format'
import { reelLink, teamCsvUrl, useTeamStats, type MatchLine } from '../lib/stats'
import type { TeamDetail } from '../lib/types'

/** Matches won and lost, from the official results ("2:1"); null when none is known. */
function record(matches: readonly MatchLine[]): string | null {
  const known = matches.filter((m) => m.tagged && m.result_us !== null && m.result_them !== null)
  if (known.length === 0) return null
  const won = known.filter((m) => (m.result_us ?? 0) > (m.result_them ?? 0)).length
  return `${won}:${known.length - won}`
}

/**
 * The season of a team row (PLAN §9.7): every ready match added up. A number opens the smart
 * reel builder with its query, so the moments play across the matches. Loaded on demand by
 * the team page.
 */
export function SeasonStats({ team }: { team: TeamDetail }) {
  const { t, i18n } = useTranslation()
  const id = useId()
  const stats = useTeamStats(team.id, true)
  const data = stats.data
  const tagged = (data?.tagged ?? 0) > 0
  const lang = i18n.language === 'de' ? 'de' : 'en'

  const rows: TeamTableRow[] = data
    ? [
        ...data.matches
          .filter((m) => m.tagged)
          .map((m) => ({
            key: m.video_id,
            name: m.title,
            label: (
              <span className="flex min-w-0 flex-col">
                <Link to={`/videos/${m.video_id}`} className="max-w-full truncate font-semibold link" title={m.title}>
                  {m.title}
                </Link>
                <span className="text-xs text-muted tabular-nums">{formatDate(m.recorded_on)}</span>
              </span>
            ),
            score: m.result_us !== null && m.result_them !== null ? `${m.result_us}:${m.result_them}` : null,
            line: m.summary,
          })),
        { key: 'season', name: t('stats.season'), label: t('stats.team.total'), score: record(data.matches), line: data.season_box.summary, total: true },
      ]
    : []

  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-3">
      <SectionHeader
        id={`${id}-title`}
        title={t('stats.seasonTitle')}
        hint={
          data && data.videos > 0
            ? [t('stats.tagged', { tagged: data.tagged, count: data.videos }), tagged ? t('stats.hintSeason') : null].filter(Boolean).join(' · ')
            : undefined
        }
        action={
          tagged ? (
            <a href={teamCsvUrl(team.id, lang)} download className={buttonClasses({ variant: 'secondary' })}>
              <DownloadIcon className="size-4" />
              {t('stats.csv')}
            </a>
          ) : null
        }
      />
      {stats.isPending ? (
        <Spinner />
      ) : stats.isError || !data ? (
        <LoadError onRetry={() => void stats.refetch()} />
      ) : !tagged ? (
        <EmptyState icon={<ChartIcon />} title={t('stats.seasonEmpty.title')}>
          {t('stats.seasonEmpty.body')}
        </EmptyState>
      ) : (
        <div className="flex flex-col gap-6">
          <StatsTable
            caption={`${t('stats.seasonTitle')} · ${team.name} ${team.season}`}
            columns={data.columns}
            players={data.season_box.players}
            team={data.season_box.team}
            opponent={data.season_box.opponent}
            showMatches
            target={(row, column) => {
              const to = reelLink(team, row.filter, column)
              return to ? { kind: 'link', to } : null
            }}
          />
          <div className="flex flex-col gap-2">
            <h3 className="font-semibold">{t('stats.team.seasonTitle')}</h3>
            <TeamTable
              caption={t('stats.team.seasonTitle')}
              first={t('stats.team.match')}
              scoreLabel={t('stats.team.result')}
              rows={rows}
              target={() => null}
            />
          </div>
          <StatsLegend columns={data.columns} matches />
        </div>
      )}
    </section>
  )
}
