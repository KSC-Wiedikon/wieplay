import '../i18n/pages'
import { useId, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { ActionGateNote } from '../components/ActionGateNote'
import { buttonClasses } from '../components/buttonStyles'
import { ChartIcon, DownloadIcon } from '../components/icons'
import { EmptyState, LoadError } from '../components/Message'
import { SectionHeader } from '../components/PageHeader'
import { RotationStats } from '../components/RotationStats'
import { SetterStats } from '../components/SetterStats'
import { Spinner } from '../components/Spinner'
import { StatsLegend, StatsTable, TeamTable, type TeamTableRow } from '../components/StatsTable'
import { SearchBar } from '../components/SearchBar'
import { TabPanel, Tabs } from '../components/Tabs'
import { SuggestedHint, type StatsTab } from '../components/VideoStats'
import { ZoneStats } from '../components/ZoneStats'
import { formatDate } from '../lib/format'
import { useTaxonomy } from '../lib/library'
import { formatTerms, parseSearch, searchParam } from '../lib/search'
import { CSV_PART, reelLink, teamCsvUrl, useTeamStats, useTeamZones, type MatchLine } from '../lib/stats'
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
  // The search bar (PLAN §9.7): every number counts only what it keeps.
  const [text, setText] = useState('')
  const taxonomy = useTaxonomy(team.sport).data ?? null
  const parsed = useMemo(() => parseSearch(text, taxonomy), [text, taxonomy])
  const search = searchParam(parsed.search)
  // The search's words as a reel builder reads them back (its `q`).
  const words = search ? formatTerms(parsed.terms, 'en', taxonomy) : ''
  const stats = useTeamStats(team.id, true, search)
  const data = stats.data
  const tagged = (data?.tagged ?? 0) > 0
  const lang = i18n.language === 'de' ? 'de' : 'en'
  const [tab, setTab] = useState<StatsTab>('players')
  const zones = useTeamZones(team.id, tab === 'zones' && tagged, search)
  const nothing = search !== null && data?.selected === 0
  const names = useMemo(() => new Map((data?.season_box.players ?? []).flatMap((p) => (p.number !== null && p.name ? [[p.number, p.name] as const] : []))), [data])

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
            ? [
                t('stats.tagged', { tagged: data.tagged, count: data.videos }),
                search !== null && data.selected != null ? t('stats.search.selected', { count: data.selected }) : null,
                tagged && (tab === 'players' || tab === 'zones') ? t('stats.hintSeason') : null,
              ]
                .filter(Boolean)
                .join(' · ')
            : undefined
        }
        action={
          tagged ? (
            <a href={teamCsvUrl(team.id, lang, CSV_PART[tab], search)} download className={buttonClasses({ variant: 'secondary' })}>
              <DownloadIcon className="size-4" />
              {t('stats.csv')}
            </a>
          ) : null
        }
      />
      {(data?.suggested ?? 0) > 0 ? <SuggestedHint count={data?.suggested ?? 0} /> : <ActionGateNote gate={data?.actions_gate} />}
      {stats.isPending ? (
        <Spinner />
      ) : stats.isError || !data ? (
        <LoadError onRetry={() => void stats.refetch()} />
      ) : !tagged ? (
        <EmptyState icon={<ChartIcon />} title={t('stats.seasonEmpty.title')}>
          {t('stats.seasonEmpty.body')}
        </EmptyState>
      ) : (
        <div className="flex flex-col gap-4">
          <SearchBar
            value={text}
            onChange={setText}
            taxonomy={taxonomy}
            team={{ id: team.id, canShare: team.can_share, label: `${team.name} ${team.season}` }}
          />
          <Tabs
            id={id}
            label={t('stats.tabs.label')}
            value={tab}
            options={[
              { value: 'players', label: t('stats.tabs.players') },
              { value: 'rotations', label: t('stats.tabs.rotations') },
              { value: 'setter', label: t('stats.tabs.setter') },
              { value: 'zones', label: t('stats.tabs.zones') },
            ]}
            onChange={setTab}
          />
          <TabPanel id={id} value={tab} className="flex flex-col gap-6">
            {nothing && <p className="text-sm text-muted">{t('search.none')}</p>}
            {tab === 'players' && !nothing && (
              <>
                <StatsTable
                  caption={`${t('stats.seasonTitle')} · ${team.name} ${team.season}`}
                  columns={data.columns}
                  players={data.season_box.players}
                  team={data.season_box.team}
                  opponent={data.season_box.opponent}
                  showMatches
                  target={(row, column) => {
                    const to = reelLink(team, row.filter, column, words)
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
              </>
            )}
            {tab === 'rotations' && !nothing && (
              <RotationStats
                rotations={data.season_box.rotations}
                columns={data.rotation_columns ?? []}
                scope={null}
                names={names}
                hint={`${t('stats.rotations.hint')} ${t('stats.rotations.seasonHint')}`}
                team={() => null}
                // Our serves, receptions and attacks in a rotation open as a reel (the search names it).
                skill={(line, column) => {
                  const to = reelLink(team, { side: 'us' }, column, `${words} rotation ${line.rotation}`)
                  return to ? { kind: 'link', to } : null
                }}
                playable={false}
              />
            )}
            {tab === 'setter' && !nothing && <SetterStats setter={data.season_box.setter} target={() => null} hint={t('stats.setter.seasonHint')} scope={null} />}
            {tab === 'zones' && !nothing && (
              <ZoneStats
                zones={zones.data}
                pending={zones.isPending}
                error={zones.isError}
                onRetry={() => void zones.refetch()}
                setNo={null}
                target={(row, _skill, end, cell, code) => {
                  // A zone opens the reel builder with its query; "no zone" is not a search word.
                  if (cell.zone === null) return null
                  const to = reelLink(team, row.filter, { codes: [code], grades: null }, `${words} ${end} ${cell.zone}`)
                  return to ? { kind: 'link', to } : null
                }}
              />
            )}
          </TabPanel>
        </div>
      )}
    </section>
  )
}
