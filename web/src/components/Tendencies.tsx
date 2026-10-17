import '../i18n/pages'
import { useId, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useBallTrack } from '../lib/ballTrack'
import { playerKey } from '../lib/condensed'
import { countOutcomes, diagramArrows, skillCategory, useCourt, type Arrow, type Outcome } from '../lib/court'
import { formatClock, gradeLabel, periodLabel, playerLabel, sideLabel, skillLabel } from '../lib/labels'
import { useSideNames } from '../lib/sides'
import { tagWindow, type StatPlay } from '../lib/stats'
import type { Tag, Tagging, Video } from '../lib/types'
import { CourtDiagram } from './CourtDiagram'
import { Select } from './Field'
import { SectionHeader } from './PageHeader'
import { Segmented } from './Segmented'

type Skill = 'serve' | 'attack'
const SWATCH: Record<Outcome, string> = {
  win: 'bg-outcome-win',
  error: 'bg-outcome-error',
  play: 'bg-outcome-play',
}

interface TendenciesProps {
  video: Video
  tagging: Tagging | undefined
  onPlay: (plays: StatPlay[]) => void
}

/**
 * "Tendencies" (PLAN §10.7, §12): where a match's serves and attacks went, on a court
 * diagram, filterable by player and set like the rest of the page. With the video's court set
 * up, the ball's landings place them; else the tags' zones do. Selecting an arrow plays it.
 */
export function Tendencies({ video, tagging, onPlay }: TendenciesProps) {
  const { t } = useTranslation()
  const names = useSideNames()
  const id = useId()
  const court = useCourt(video.id)
  const calibration = court.data?.calibration ?? null
  const withBall = calibration !== null && court.data?.ball_track.available === true
  const track = useBallTrack(video.id, withBall)
  const [skill, setSkill] = useState<Skill>('serve')
  const [player, setPlayer] = useState<string>('us')
  const [set, setSet] = useState<string>('')
  const [selected, setSelected] = useState<string | null>(null)

  const taxonomy = tagging?.taxonomy ?? null
  const tags = useMemo(() => (tagging?.events ?? []).filter((e) => !e.deleted_at && skillCategory(e, taxonomy) === skill), [tagging, taxonomy, skill])
  const players = useMemo(() => {
    const seen = new Map<string, Tag>()
    for (const tag of tags) if (tag.number !== null) seen.set(playerKey(tag.side, tag.number), tag)
    return [...seen.entries()].sort(([, a], [, b]) => (a.side === b.side ? (a.number ?? 0) - (b.number ?? 0) : a.side === 'us' ? -1 : 1))
  }, [tags])
  const periods = useMemo(() => tagging?.periods ?? [], [tagging])
  const shown = useMemo(
    () =>
      tags.filter((tag) => {
        if (player === 'us' || player === 'them' ? tag.side !== player : playerKey(tag.side, tag.number) !== player) return false
        if (set) {
          const period = periods.find((p) => p.id === set)
          if (!period || tag.t_ms < period.start_ms || (period.end_ms !== null && tag.t_ms > period.end_ms)) return false
        }
        return true
      }),
    [tags, player, set, periods],
  )
  const arrows = useMemo(
    () => diagramArrows({ tags: shown, taxonomy, calibration: withBall ? calibration : null, track: track.data ?? null }),
    [shown, taxonomy, withBall, calibration, track.data],
  )
  // Shown once there is something to draw: zones on serves or attacks, or the ball's landings.
  const drawable =
    withBall ||
    (tagging?.events ?? []).some((e) => !e.deleted_at && e.attributes.zone_to !== undefined && ['serve', 'attack'].includes(skillCategory(e, taxonomy) ?? ''))
  if (!tagging || !drawable) return null

  const counts = countOutcomes(arrows)
  const skillName = skill === 'serve' ? t('court.tendencies.serves') : t('court.tendencies.attacks')
  const outcomeName = (o: Outcome) => t(`court.tendencies.${o}`)
  const describe = (arrow: Arrow) => {
    const tag = arrow.tag
    const spec = taxonomy?.skills.find((s) => s.code === tag.code) ?? null
    return t('court.tendencies.arrow', {
      player: playerLabel(t, tag.side, tag.number, tagging.roster, tag.member_id, names),
      skill: skillLabel(t, spec, tag.code),
      grade: tag.attributes.grade ? gradeLabel(t, spec, tag.attributes.grade) : '',
      outcome: outcomeName(arrow.outcome),
      time: formatClock(tag.t_ms, { tenths: false }),
    })
  }
  const select = (arrow: Arrow) => {
    setSelected(arrow.tag.id)
    onPlay([{ key: arrow.tag.id, label: describe(arrow), ...tagWindow(arrow.tag, taxonomy, video.duration_ms) }])
  }
  const ballCount = arrows.filter((a) => a.evidence === 'ball').length

  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-3">
      <SectionHeader id={`${id}-title`} title={t('court.tendencies.title')} hint={t('court.tendencies.hint')} />
      <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
        <Segmented
          label={t('court.tendencies.skill')}
          value={skill}
          options={[
            { value: 'serve', label: t('court.tendencies.serves') },
            { value: 'attack', label: t('court.tendencies.attacks') },
          ]}
          onChange={(value) => {
            setSkill(value)
            setSelected(null)
          }}
        />
        <label className="flex flex-col gap-1 text-xs font-semibold" htmlFor={`${id}-player`}>
          {t('court.tendencies.player')}
          <Select id={`${id}-player`} inline value={player} onChange={(e) => setPlayer(e.target.value)}>
            <option value="us">{t('court.tendencies.allUs', { team: sideLabel(t, 'us', names) })}</option>
            <option value="them">{t('court.tendencies.allThem', { team: sideLabel(t, 'them', names) })}</option>
            {players.map(([key, tag]) => (
              <option key={key} value={key}>
                {playerLabel(t, tag.side, tag.number, tagging.roster, tag.member_id, names)}
              </option>
            ))}
          </Select>
        </label>
        {periods.length > 0 && (
          <label className="flex flex-col gap-1 text-xs font-semibold" htmlFor={`${id}-set`}>
            {t('court.tendencies.set')}
            <Select id={`${id}-set`} inline value={set} onChange={(e) => setSet(e.target.value)}>
              <option value="">{t('court.tendencies.allSets')}</option>
              {periods.map((p) => (
                <option key={p.id} value={p.id}>
                  {periodLabel(t, tagging.sport, p.no)}
                </option>
              ))}
            </Select>
          </label>
        )}
      </div>
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start">
        <div className="w-full rounded-card border border-hairline bg-surface p-3 shadow-card sm:max-w-sm">
          <CourtDiagram
            arrows={arrows}
            label={t('court.tendencies.diagram', { skill: skillName, team: sideLabel(t, 'us', names) })}
            netLabel={t('court.tendencies.net')}
            describe={describe}
            onSelect={select}
            selected={selected}
          />
        </div>
        <div className="flex min-w-0 flex-col gap-3 text-sm">
          <p className="font-semibold tabular-nums" role="status">
            {arrows.length > 0 ? t('court.tendencies.count', { count: arrows.length, ...counts }) : t('court.tendencies.empty')}
          </p>
          <ul className="flex flex-col gap-1.5" aria-label={t('court.tendencies.title')}>
            {(['win', 'error', 'play'] as const).map((o) => (
              <li key={o} className="flex items-center gap-2">
                <span aria-hidden="true" className={`h-1 w-6 rounded-full ${SWATCH[o]}`} />
                {outcomeName(o)}
              </li>
            ))}
            <li className="flex items-center gap-2 text-muted">
              <svg aria-hidden="true" viewBox="0 0 24 4" className="h-1 w-6">
                <line x1="0" y1="2" x2="24" y2="2" stroke="currentColor" strokeWidth="3" />
              </svg>
              {`${t('court.tendencies.ball')}${ballCount ? ` (${ballCount})` : ''}`}
            </li>
            <li className="flex items-center gap-2 text-muted">
              <svg aria-hidden="true" viewBox="0 0 24 4" className="h-1 w-6">
                <line x1="0" y1="2" x2="24" y2="2" stroke="currentColor" strokeWidth="3" strokeDasharray="5 3" />
              </svg>
              {t('court.tendencies.zones')}
            </li>
          </ul>
          {!withBall && <p className="text-muted">{t('court.tendencies.noCourt')}</p>}
        </div>
      </div>
    </section>
  )
}
