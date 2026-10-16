import '../i18n/pages'
import { useId, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { cx } from '../lib/cx'
import { formatClock, pieceLabel, playerName } from '../lib/labels'
import { ralliesOf } from '../lib/rallies'
import {
  rallyWindow,
  statRallies,
  statTags,
  tagWindow,
  useVideoStats,
  videoCsvUrl,
  type RallyFilter,
  type StatColumn,
  type StatPlay,
  type StatRow,
  type VideoStats as Stats,
} from '../lib/stats'
import type { Tagging, Video } from '../lib/types'
import { buttonClasses } from './buttonStyles'
import { ChartIcon, DownloadIcon } from './icons'
import { EmptyState, LoadError } from './Message'
import { SectionHeader } from './PageHeader'
import { Spinner } from './Spinner'
import { StatsLegend, StatsTable, TeamTable, type CellTarget, type TeamKey, type TeamTableRow } from './StatsTable'

/** The section's id, for "See the stats" links. */
export const VIDEO_STATS_ID = 'video-stats'

interface VideoStatsProps {
  video: Video
  /** The video page's tagging data: the moments behind each number are played from it. */
  tagging: Tagging | undefined
  onPlay: (plays: StatPlay[]) => void
}

/** The match's box score and team numbers (PLAN §9.7); every number plays its moments. */
export function VideoStats({ video, tagging, onPlay }: VideoStatsProps) {
  const { t, i18n } = useTranslation()
  const id = useId()
  const stats = useVideoStats(video.id, true)
  const tagged = (stats.data?.tags ?? 0) > 0
  const lang = i18n.language === 'de' ? 'de' : 'en'

  return (
    <section id={VIDEO_STATS_ID} aria-labelledby={`${id}-title`} className="flex scroll-mt-4 flex-col gap-3">
      <SectionHeader
        id={`${id}-title`}
        title={t('stats.title')}
        hint={tagged ? t('stats.hintVideo') : undefined}
        action={
          tagged ? (
            <a href={videoCsvUrl(video.id, lang)} download className={buttonClasses({ variant: 'secondary' })}>
              <DownloadIcon className="size-4" />
              {t('stats.csv')}
            </a>
          ) : null
        }
      />
      {stats.isPending ? (
        <Spinner />
      ) : stats.isError ? (
        <LoadError onRetry={() => void stats.refetch()} />
      ) : !tagged ? (
        <EmptyState icon={<ChartIcon />} title={t('stats.empty.title')}>
          {tagging?.can_tag ? t('stats.empty.tagger') : t('stats.empty.body')}
        </EmptyState>
      ) : (
        <Numbers data={stats.data} video={video} tagging={tagging} onPlay={onPlay} />
      )}
    </section>
  )
}

function Numbers({ data, video, tagging, onPlay }: { data: Stats } & VideoStatsProps) {
  const { t } = useTranslation()
  const [setNo, setSetNo] = useState<number | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const box = (setNo === null ? null : data.sets.find((s) => s.no === setNo)) ?? data.match
  const scope = setNo === null || box === data.match ? null : setNo

  const rallyNumbers = useMemo(() => new Map(ralliesOf(tagging?.segments ?? []).map((r, i) => [r.id, i + 1])), [tagging])
  const periodOf = useMemo(() => new Map((tagging?.periods ?? []).map((p) => [p.no, p.id])), [tagging])

  const playTags = (row: StatRow, column: StatColumn): CellTarget | null => {
    if (!tagging) return null
    return {
      kind: 'play',
      onPlay: () => {
        const tags = statTags(tagging, row.filter, column, scope)
        onPlay(
          tags.map((tag) => {
            const skill = tagging.taxonomy?.skills.find((s) => s.code === tag.code) ?? null
            const label = pieceLabel(
              t,
              {
                caption: null,
                code: tag.code,
                grade: tag.attributes.grade ?? null,
                side: tag.side,
                number: tag.number,
                player_name: playerName(tagging.roster, tag.side, tag.number, tag.member_id),
                video_title: null,
              },
              skill,
            )
            return { key: tag.id, label: `${label} · ${formatClock(tag.t_ms, { tenths: false })}`, ...tagWindow(tag, tagging.taxonomy, video.duration_ms) }
          }),
        )
      },
    }
  }

  const playRallies = (row: TeamTableRow, key: TeamKey): CellTarget | null => {
    const filter: RallyFilter | undefined = data.team_filters[key.replace(/_pct$/, '_won')]
    const periodId = row.key === 'match' ? null : periodOf.get(Number(row.key.slice(3)))
    if (!tagging || !filter || periodId === undefined) return null
    return {
      kind: 'play',
      onPlay: () =>
        onPlay(
          statRallies(tagging.segments, filter, periodId).map((rally) => {
            const score = rally.score_us_after !== null && rally.score_them_after !== null ? `${rally.score_us_after}:${rally.score_them_after}` : null
            const label = [t('stats.rallyLabel', { n: rallyNumbers.get(rally.id) ?? 0 }), score].filter(Boolean).join(' · ')
            return { key: rally.id, label, ...rallyWindow(rally, video.duration_ms) }
          }),
        ),
    }
  }

  const finals = data.sets.filter((s) => s.score_us !== null && s.score_them !== null)
  const setsWon = finals.filter((s) => (s.score_us ?? 0) > (s.score_them ?? 0)).length
  const teamRows: TeamTableRow[] = [
    ...data.sets.map((s) => ({
      key: `set${s.no}`,
      label: t('stats.setNo', { no: s.no }),
      name: t('stats.setNo', { no: s.no }),
      score: s.score_us !== null && s.score_them !== null ? `${s.score_us}:${s.score_them}` : null,
      line: s.summary,
    })),
    {
      key: 'match',
      label: t('stats.match'),
      name: t('stats.match'),
      score: finals.length > 0 ? `${setsWon}:${finals.length - setsWon}` : null,
      line: data.match.summary,
      total: true,
    },
  ]

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <h3 className="font-semibold">{t('stats.boxScore')}</h3>
          {data.sets.length > 0 && (
            <div role="group" aria-label={t('stats.scope')} className="flex flex-wrap gap-1 sm:gap-1.5">
              {[null, ...data.sets.map((s) => s.no)].map((no) => {
                const on = scope === no
                return (
                  <button
                    key={no ?? 'match'}
                    type="button"
                    aria-pressed={on}
                    onClick={() => {
                      setSetNo(no)
                      setSelected(null)
                    }}
                    className={cx(
                      'min-h-11 cursor-pointer rounded-control border px-2.5 text-sm font-semibold sm:min-h-9 sm:px-3',
                      'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
                      on ? 'border-transparent bg-accent text-on-accent shadow-card' : 'border-hairline-strong bg-surface text-fg hover:bg-sunken',
                    )}
                  >
                    {no === null ? t('stats.match') : t('stats.setNo', { no })}
                  </button>
                )
              })}
            </div>
          )}
        </div>
        <StatsTable
          caption={scope === null ? t('stats.boxScore') : `${t('stats.boxScore')} · ${t('stats.setNo', { no: scope })}`}
          columns={data.columns}
          players={box.players}
          team={box.team}
          opponent={box.opponent}
          target={playTags}
          selected={selected}
          onSelect={setSelected}
        />
      </div>
      {data.rallies > 0 && (
        <div className="flex flex-col gap-2">
          <h3 className="font-semibold">{t('stats.team.title')}</h3>
          <TeamTable
            caption={t('stats.team.title')}
            first={t('stats.team.set')}
            scoreLabel={t('stats.team.score')}
            rows={teamRows}
            target={playRallies}
            selected={selected}
            onSelect={setSelected}
          />
        </div>
      )}
      <StatsLegend columns={data.columns} team={data.rallies > 0} />
    </div>
  )
}
