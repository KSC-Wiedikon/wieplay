import '../i18n/pages'
import { useId, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { cx } from '../lib/cx'
import { Link } from 'react-router'
import { formatClock, pieceLabel, playerLabel, playerName } from '../lib/labels'
import { ralliesOf } from '../lib/rallies'
import {
  rallyWindow,
  rotationRallies,
  rotationTags,
  setterPlays,
  setWindow,
  statRallies,
  statTags,
  tagWindow,
  useVideoStats,
  videoCsvUrl,
  type BoxScore,
  type CsvPart,
  type RallyFilter,
  type RotationLine,
  type SetFilter,
  type SetPlay,
  type StatColumn,
  type StatPlay,
  type StatRow,
  type VideoStats as Stats,
} from '../lib/stats'
import type { Segment, Tag, Tagging, Video } from '../lib/types'
import { buttonClasses } from './buttonStyles'
import { ActionGateNote } from './ActionGateNote'
import { ChartIcon, DownloadIcon, SparkleIcon } from './icons'
import { EmptyState, LoadError } from './Message'
import { SectionHeader } from './PageHeader'
import { RotationStats } from './RotationStats'
import { SetterStats } from './SetterStats'
import { Spinner } from './Spinner'
import { StatsLegend, StatsTable, TeamTable, type CellTarget, type TeamKey, type TeamTableRow } from './StatsTable'
import { TabPanel, Tabs } from './Tabs'

/** The section's id, for "See the stats" links. */
export const VIDEO_STATS_ID = 'video-stats'

/** The stats' tabs (Balltime-style): players, rotations, setter. */
export type StatsTab = 'players' | 'rotations' | 'setter'
const CSV_PART: Record<StatsTab, CsvPart> = { players: 'box', rotations: 'rotations', setter: 'setter' }

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
  const [tab, setTab] = useState<StatsTab>('players')

  return (
    <section id={VIDEO_STATS_ID} aria-labelledby={`${id}-title`} className="flex scroll-mt-4 flex-col gap-3">
      <SectionHeader
        id={`${id}-title`}
        title={t('stats.title')}
        hint={tagged ? t('stats.hintVideo') : undefined}
        action={
          tagged ? (
            <a href={videoCsvUrl(video.id, lang, CSV_PART[tab])} download className={buttonClasses({ variant: 'secondary' })}>
              <DownloadIcon className="size-4" />
              {t('stats.csv')}
            </a>
          ) : null
        }
      />
      {(stats.data?.suggested ?? 0) > 0 ? <SuggestedHint count={stats.data?.suggested ?? 0} /> : <ActionGateNote gate={stats.data?.actions_gate} />}
      {stats.isPending ? (
        <Spinner />
      ) : stats.isError ? (
        <LoadError onRetry={() => void stats.refetch()} />
      ) : !tagged ? (
        <EmptyState icon={<ChartIcon />} title={t('stats.empty.title')}>
          {tagging?.can_tag ? t('stats.empty.tagger') : t('stats.empty.body')}
        </EmptyState>
      ) : (
        <Numbers data={stats.data} video={video} tagging={tagging} onPlay={onPlay} tab={tab} onTab={setTab} />
      )}
    </section>
  )
}

/** Nomal's suggested actions are not in the numbers until a coach accepts them (PLAN §10.4a). */
export function SuggestedHint({ count }: { count: number }) {
  const { t } = useTranslation()
  return (
    <p data-suggested-hint className="flex items-center gap-1.5 text-sm text-muted">
      <SparkleIcon className="size-4 shrink-0" />
      {t('actions.statsHint', { count })}
    </p>
  )
}

function Numbers({ data, video, tagging, onPlay, tab, onTab }: { data: Stats; tab: StatsTab; onTab: (tab: StatsTab) => void } & VideoStatsProps) {
  const { t } = useTranslation()
  const tabsId = useId()
  const [setNo, setSetNo] = useState<number | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const set = setNo === null ? null : (data.sets.find((s) => s.no === setNo) ?? null)
  const box: BoxScore = set ?? data.match
  const scope = set === null ? null : setNo
  const scopeName = scope === null ? null : t('stats.setNo', { no: scope })

  const rallyNumbers = useMemo(() => new Map(ralliesOf(tagging?.segments ?? []).map((r, i) => [r.id, i + 1])), [tagging])
  const periodOf = useMemo(() => new Map((tagging?.periods ?? []).map((p) => [p.no, p.id])), [tagging])
  const events = useMemo(() => new Map((tagging?.events ?? []).map((e) => [e.id, e])), [tagging])
  // Shirt numbers' names for the rotation charts: the roster snapshot's, else the box score's.
  const names = useMemo(() => {
    const out = new Map(data.match.players.flatMap((p) => (p.number !== null && p.name ? [[p.number, p.name] as const] : [])))
    for (const r of tagging?.roster ?? []) if (r.side === 'us' && r.name) out.set(r.number, r.name)
    return out
  }, [data, tagging])

  // What a moment shows: a tag with its player, skill and grade; a rally with its number and score.
  const tagPlay = (tag: Tag): StatPlay => {
    const skill = tagging?.taxonomy?.skills.find((s) => s.code === tag.code) ?? null
    const label = pieceLabel(
      t,
      {
        caption: null,
        code: tag.code,
        grade: tag.attributes.grade ?? null,
        side: tag.side,
        number: tag.number,
        player_name: playerName(tagging?.roster ?? [], tag.side, tag.number, tag.member_id),
        video_title: null,
      },
      skill,
    )
    return { key: tag.id, label: `${label} · ${formatClock(tag.t_ms, { tenths: false })}`, ...tagWindow(tag, tagging?.taxonomy ?? null, video.duration_ms) }
  }
  const rallyPlay = (rally: Segment): StatPlay => {
    const score = rally.score_us_after !== null && rally.score_them_after !== null ? `${rally.score_us_after}:${rally.score_them_after}` : null
    const label = [t('stats.rallyLabel', { n: rallyNumbers.get(rally.id) ?? 0 }), score].filter(Boolean).join(' · ')
    return { key: rally.id, label, ...rallyWindow(rally, video.duration_ms) }
  }
  const setPlay = (play: SetPlay): StatPlay[] => {
    const setTag = events.get(play.set_id)
    if (!setTag || !tagging) return []
    const attack = play.attack_id ? (events.get(play.attack_id) ?? null) : null
    const who = (tag: Tag) => playerLabel(t, tag.side, tag.number, tagging.roster, tag.member_id)
    const time = formatClock(setTag.t_ms, { tenths: false })
    const outcome = t(`stats.setter.outcomes.${play.outcome ?? 'none'}`)
    const label = attack ? t('stats.setter.play', { setter: who(setTag), attacker: who(attack), outcome, time }) : t('stats.setter.playNoAttack', { setter: who(setTag), outcome, time })
    return [{ key: play.set_id, label, ...setWindow(setTag, attack, tagging.taxonomy, video.duration_ms) }]
  }

  const playTags = (row: StatRow, column: StatColumn): CellTarget | null => {
    if (!tagging) return null
    return { kind: 'play', onPlay: () => onPlay(statTags(tagging, row.filter, column, scope).map(tagPlay)) }
  }

  const playRallies = (row: TeamTableRow, key: TeamKey): CellTarget | null => {
    const filter: RallyFilter | undefined = data.team_filters[key.replace(/_pct$/, '_won')]
    const periodId = row.key === 'match' ? null : periodOf.get(Number(row.key.slice(3)))
    if (!tagging || !filter || periodId === undefined) return null
    return { kind: 'play', onPlay: () => onPlay(statRallies(tagging.segments, filter, periodId).map(rallyPlay)) }
  }

  const rotationTeam = (line: RotationLine, key: TeamKey): CellTarget | null => {
    const filter: RallyFilter | undefined = data.team_filters[key.replace(/_pct$/, '_won')]
    if (!tagging || !filter || !line.plays?.length) return null
    const plays = line.plays
    return { kind: 'play', onPlay: () => onPlay(rotationRallies(tagging.segments, plays, filter).map(rallyPlay)) }
  }
  const rotationSkill = (line: RotationLine, column: StatColumn): CellTarget | null => {
    if (!tagging || !line.plays?.length) return null
    const plays = line.plays
    return { kind: 'play', onPlay: () => onPlay(rotationTags(tagging, plays, column).map(tagPlay)) }
  }
  const setterTarget = (filter: SetFilter): CellTarget | null => {
    const plays = data.match.setter?.plays
    if (!tagging || !plays) return null
    return { kind: 'play', onPlay: () => onPlay(setterPlays(plays, filter, scope).flatMap(setPlay)) }
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
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3">
        <Tabs
          id={tabsId}
          label={t('stats.tabs.label')}
          value={tab}
          options={[
            { value: 'players', label: t('stats.tabs.players') },
            { value: 'rotations', label: t('stats.tabs.rotations') },
            { value: 'setter', label: t('stats.tabs.setter') },
          ]}
          onChange={(value) => {
            onTab(value)
            setSelected(null)
          }}
        />
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
      <TabPanel id={tabsId} value={tab} className="flex flex-col gap-6">
        {tab === 'players' && (
          <>
            <div className="flex flex-col gap-2">
              <h3 className="font-semibold">{t('stats.boxScore')}</h3>
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
          </>
        )}
        {tab === 'rotations' && (
          <RotationStats
            rotations={box.rotations}
            columns={data.rotation_columns ?? []}
            scope={scopeName}
            names={names}
            hint={t('stats.rotations.hint')}
            team={rotationTeam}
            skill={rotationSkill}
            selected={selected}
            onSelect={setSelected}
            emptyAction={
              tagging?.can_tag ? (
                <Link to={`/videos/${video.id}/tag`} className={buttonClasses({ variant: 'secondary' })}>
                  {t('stats.rotations.openTagging')}
                </Link>
              ) : undefined
            }
          />
        )}
        {tab === 'setter' && <SetterStats setter={box.setter} target={setterTarget} hint={t('stats.setter.hint')} scope={scopeName} selected={selected} onSelect={setSelected} />}
      </TabPanel>
    </div>
  )
}
