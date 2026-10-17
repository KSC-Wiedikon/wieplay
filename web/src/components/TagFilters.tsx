import '../i18n/pages'
import { useId, useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { isFiltered, playerKey, type TagFilter } from '../lib/condensed'
import { skillByCode } from '../lib/grammar'
import { gradeLabel, periodLabel, playerLabel, skillLabel } from '../lib/labels'
import { useSideNames } from '../lib/sides'
import type { Period, RosterEntry, Tag, Taxonomy } from '../lib/types'
import { Button } from './Button'
import { Select } from './Field'

interface TagFiltersProps {
  filter: TagFilter
  onChange: (filter: TagFilter) => void
  taxonomy: Taxonomy | null
  tags: readonly Tag[]
  periods: readonly Period[]
  roster: readonly RosterEntry[]
}

/** Player, skill, grade and set (basketball: quarter); each "All" by default. Players are those with tags. */
export function TagFilters({ filter, onChange, taxonomy, tags, periods, roster }: TagFiltersProps) {
  const { t } = useTranslation()
  const names = useSideNames()
  const id = useId()
  const sport = taxonomy?.sport ?? 'volleyball'

  const players = useMemo(() => {
    const seen = new Map<string, { side: Tag['side']; number: number | null }>()
    for (const tag of tags) seen.set(playerKey(tag.side, tag.number), { side: tag.side, number: tag.number })
    return [...seen.entries()].sort(([, a], [, b]) =>
      a.side === b.side ? (a.number ?? 100) - (b.number ?? 100) : a.side === 'us' ? -1 : 1,
    )
  }, [tags])

  const skill = filter.skill && taxonomy ? skillByCode(taxonomy, filter.skill) : null
  const set = (patch: Partial<TagFilter>) => onChange({ ...filter, ...patch })
  const value = (v: string) => (v === '' ? null : v)

  return (
    <div className="flex flex-wrap items-end gap-x-3 gap-y-2">
      <label className="flex flex-col gap-1 text-xs font-semibold" htmlFor={`${id}-player`}>
        {t('tagging.filters.player')}
        <Select id={`${id}-player`} inline value={filter.player ?? ''} onChange={(e) => set({ player: value(e.target.value) })}>
          <option value="">{t('tagging.filters.all')}</option>
          {players.map(([key, p]) => (
            <option key={key} value={key}>
              {playerLabel(t, p.side, p.number, roster, undefined, names)}
            </option>
          ))}
        </Select>
      </label>
      {taxonomy && (
        <label className="flex flex-col gap-1 text-xs font-semibold" htmlFor={`${id}-skill`}>
          {t('tagging.filters.skill')}
          <Select id={`${id}-skill`} inline value={filter.skill ?? ''} onChange={(e) => set({ skill: value(e.target.value) })}>
            <option value="">{t('tagging.filters.all')}</option>
            {taxonomy.skills.map((s) => (
              <option key={s.code} value={s.code}>
                {`${s.code} · ${skillLabel(t, s)}`}
              </option>
            ))}
          </Select>
        </label>
      )}
      {taxonomy && (
        <label className="flex flex-col gap-1 text-xs font-semibold" htmlFor={`${id}-grade`}>
          {t('tagging.filters.grade')}
          <Select id={`${id}-grade`} inline value={filter.grade ?? ''} onChange={(e) => set({ grade: value(e.target.value) })}>
            <option value="">{t('tagging.filters.all')}</option>
            {taxonomy.grades.map((g) => (
              <option key={g} value={g}>
                {skill ? `${g} ${gradeLabel(t, skill, g)}` : g}
              </option>
            ))}
          </Select>
        </label>
      )}
      {periods.length > 0 && (
        <label className="flex flex-col gap-1 text-xs font-semibold" htmlFor={`${id}-set`}>
          {sport === 'basketball' ? t('tagging.basketball.quarter') : t('tagging.filters.set')}
          <Select id={`${id}-set`} inline value={filter.set ?? ''} onChange={(e) => set({ set: value(e.target.value) })}>
            <option value="">{t('tagging.filters.all')}</option>
            {periods.map((p) => (
              <option key={p.id} value={p.id}>
                {periodLabel(t, sport, p.no)}
              </option>
            ))}
          </Select>
        </label>
      )}
      {isFiltered(filter) && (
        <Button variant="ghost" onClick={() => onChange({ player: null, skill: null, grade: null, set: null })}>
          {t('tagging.filters.clear')}
        </Button>
      )}
    </div>
  )
}
