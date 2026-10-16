import type { TFunction } from 'i18next'
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/Button'
import { Card } from '../../components/Card'
import { Select, TextInput } from '../../components/Field'
import { AddToReelDialog } from '../../components/AddToReelDialog'
import { PencilIcon, PlusIcon, TrashIcon } from '../../components/icons'
import { Table, TBody, Td, Th, THead, Tr } from '../../components/Table'
import { TagFilters } from '../../components/TagFilters'
import { filterTags, isFiltered, lastAtOrBefore, NO_FILTER, type TagFilter } from '../../lib/condensed'
import { cx } from '../../lib/cx'
import { formatEntry, skillByCode } from '../../lib/grammar'
import { formatClock, gradeLabel, periodLabel, playerLabel, sideLabel, skillLabel } from '../../lib/labels'
import { usePlayhead } from '../../lib/playhead'
import { periodAt } from '../../lib/rallies'
import type { ReelItemIn, RosterEntry, Side, Tag, TagAttributes, Taxonomy } from '../../lib/types'
import { useUi, useView, useWorkspace } from './context'

/** Keeps a row visible inside the scrolling box without scrolling the page. */
function reveal(container: HTMLElement | null, id: string | null): void {
  if (!container || !id) return
  const row = container.querySelector<HTMLElement>(`[data-tag="${id}"]`)
  if (!row) return
  const header = container.querySelector('thead')?.getBoundingClientRect().height ?? 0
  const top = row.offsetTop - header
  const bottom = row.offsetTop + row.offsetHeight
  if (top < container.scrollTop) container.scrollTop = top
  else if (bottom > container.scrollTop + container.clientHeight) container.scrollTop = bottom - container.clientHeight
}

/** Tags synced to the playhead (PLAN §9.1): click to seek, edit in place, delete with undo, filters. */
/** A tag's moment as a reel item: its clip window (its own, else the skill's), captioned. */
function reelItemOf(t: TFunction, tag: Tag, video: { id: string; duration_ms: number | null }, taxonomy: Taxonomy, roster: readonly RosterEntry[]): ReelItemIn {
  const skill = skillByCode(taxonomy, tag.code)
  const pre = tag.clip_pre_ms ?? skill?.pre_ms ?? 3000
  const post = tag.clip_post_ms ?? skill?.post_ms ?? 2000
  return {
    video_id: video.id,
    start_ms: Math.max(0, tag.t_ms - pre),
    end_ms: Math.min(video.duration_ms ?? Infinity, tag.t_ms + post),
    event_id: tag.id,
    caption: `${formatEntry(tag)} · ${playerLabel(t, tag.side, tag.number, roster, tag.member_id)}`.slice(0, 200),
  }
}

export function EventTable() {
  const { t } = useTranslation()
  const { playhead, taxonomy, video } = useWorkspace()
  const view = useView()
  const ui = useUi()
  const [filter, setFilter] = useState<TagFilter>(NO_FILTER)
  const [adding, setAdding] = useState<Tag | null>(null)
  const item = adding ? reelItemOf(t, adding, video, taxonomy, view.roster) : null
  const shown = useMemo(() => filterTags(view.events, view.periods, filter), [view.events, view.periods, filter])
  const currentId = usePlayhead(playhead, (s) => lastAtOrBefore(shown, s.t_ms)?.id ?? null)
  const container = useRef<HTMLDivElement>(null)
  const pointerInside = useRef(false)
  // Basketball has quarters and no zones.
  const basketball = taxonomy.sport === 'basketball'
  const zones = taxonomy.zones.length > 0

  // Follow the playhead, unless the mouse is in the table (someone is reading or clicking).
  useEffect(() => {
    if (!pointerInside.current) reveal(container.current, currentId)
  }, [currentId])
  useEffect(() => reveal(container.current, ui.selectedId), [ui.selectedId])

  return (
    <section aria-labelledby="tags-title" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="tags-title" className="font-semibold">
          {t('tagging.table.title')}
        </h2>
        <p className="text-sm text-muted tabular-nums">
          {isFiltered(filter)
            ? t('tagging.table.count', { shown: shown.length, total: view.events.length })
            : t('tagging.tagCount', { count: view.events.length })}
        </p>
      </div>
      <TagFilters filter={filter} onChange={setFilter} taxonomy={taxonomy} tags={view.events} periods={view.periods} roster={view.roster} />
      <Card className="overflow-hidden">
        <div
          ref={container}
          className="relative max-h-[28rem] overflow-y-auto"
          onMouseEnter={() => (pointerInside.current = true)}
          onMouseLeave={() => (pointerInside.current = false)}
        >
          {shown.length === 0 ? (
            <p className="p-4 text-sm text-muted">{view.events.length === 0 ? t('tagging.table.empty') : t('tagging.table.emptyFiltered')}</p>
          ) : (
            <Table caption={t('tagging.table.title')}>
              <THead className="sticky top-0 z-10">
                <tr>
                  <Th>{t('tagging.table.time')}</Th>
                  <Th>{basketball ? t('tagging.basketball.quarter') : t('tagging.filters.set')}</Th>
                  <Th>{t('tagging.table.entry')}</Th>
                  <Th>{t('tagging.filters.player')}</Th>
                  <Th>{t('tagging.filters.skill')}</Th>
                  <Th>{t('tagging.filters.grade')}</Th>
                  {zones && <Th>{t('tagging.table.zones')}</Th>}
                  <Th className="text-right">
                    <span className="sr-only">{t('tagging.table.actions')}</span>
                  </Th>
                </tr>
              </THead>
              <TBody>
                {shown.map((tag) =>
                  tag.id === ui.editingId ? (
                    <EditRow key={tag.id} tag={tag} />
                  ) : (
                    <Row key={tag.id} tag={tag} current={tag.id === currentId} selected={tag.id === ui.selectedId} onAddToReel={setAdding} />
                  ),
                )}
              </TBody>
            </Table>
          )}
        </div>
      </Card>
      <AddToReelDialog item={item} suggestedTitle={item?.caption ?? ''} onClose={() => setAdding(null)} />
    </section>
  )
}

function Row({ tag, current, selected, onAddToReel }: { tag: Tag; current: boolean; selected: boolean; onAddToReel: (tag: Tag) => void }) {
  const { t } = useTranslation()
  const { controller, taxonomy } = useWorkspace()
  const view = useView()
  const skill = skillByCode(taxonomy, tag.code)
  const entry = formatEntry(tag)
  const set = periodAt(view.periods, tag.t_ms)
  const { zone_from: from, zone_to: to } = tag.attributes
  const basketball = taxonomy.sport === 'basketball'

  return (
    <Tr
      data-tag={tag.id}
      onActivate={() => controller.select(tag.id, { seek: true })}
      aria-current={current ? 'true' : undefined}
      aria-selected={selected}
      className={cx(current && 'bg-accent-soft', selected && 'outline-2 -outline-offset-2 outline-accent')}
    >
      <Td tight>
        <button
          type="button"
          onClick={() => controller.select(tag.id, { seek: true })}
          aria-label={t('tagging.table.go', { entry, time: formatClock(tag.t_ms) })}
          className="min-h-9 cursor-pointer rounded-lg px-1 font-mono text-sm link tabular-nums"
        >
          {formatClock(tag.t_ms)}
        </button>
      </Td>
      <Td className="tabular-nums">{set ? (basketball ? periodLabel(t, taxonomy.sport, set.no) : set.no) : '–'}</Td>
      <Td className="font-mono font-bold whitespace-nowrap">
        {entry}
        {tag.time_precision === 'frame' && (
          <span className="ml-1 text-xs font-normal text-muted" title={t('tagging.table.frame')}>
            {t('tagging.table.frameShort')}
          </span>
        )}
      </Td>
      <Td>{playerLabel(t, tag.side, tag.number, view.roster, tag.member_id)}</Td>
      <Td>{skillLabel(t, skill, tag.code)}</Td>
      <Td>{tag.attributes.grade ? `${tag.attributes.grade} ${gradeLabel(t, skill, tag.attributes.grade)}` : '–'}</Td>
      {taxonomy.zones.length > 0 && (
        <Td className="tabular-nums">{from !== undefined ? (to !== undefined ? `${from} → ${to}` : String(from)) : '–'}</Td>
      )}
      <Td tight className="text-right whitespace-nowrap">
        <Button variant="ghost" size="icon" aria-label={t('tagging.table.edit', { entry })} onClick={() => controller.edit(tag.id)}>
          <PencilIcon className="size-4" />
        </Button>
        <Button variant="ghost" size="icon" aria-label={t('tagging.table.addToReel', { entry })} title={t('reels.addToReel')} onClick={() => onAddToReel(tag)}>
          <PlusIcon className="size-4" />
        </Button>
        <Button variant="ghostDanger" size="icon" aria-label={t('tagging.table.delete', { entry })} onClick={() => controller.deleteTag(tag.id)}>
          <TrashIcon className="size-4" />
        </Button>
      </Td>
    </Tr>
  )
}

function EditRow({ tag }: { tag: Tag }) {
  const { t } = useTranslation()
  const { controller, taxonomy } = useWorkspace()
  const [side, setSide] = useState<Side>(tag.side)
  const [number, setNumber] = useState(tag.number === null ? '' : String(tag.number))
  const [code, setCode] = useState(tag.code)
  const [grade, setGrade] = useState(tag.attributes.grade ?? '')
  const [from, setFrom] = useState(tag.attributes.zone_from === undefined ? '' : String(tag.attributes.zone_from))
  const [to, setTo] = useState(tag.attributes.zone_to === undefined ? '' : String(tag.attributes.zone_to))
  const [error, setError] = useState(false)
  const first = useRef<HTMLSelectElement>(null)
  const entry = formatEntry(tag)
  const graded = skillByCode(taxonomy, code)?.graded ?? true

  useEffect(() => first.current?.focus(), [])

  const save = () => {
    const trimmed = number.trim()
    if (trimmed !== '' && !/^\d{1,2}$/.test(trimmed)) {
      setError(true)
      return
    }
    const attributes: TagAttributes = {}
    // A skill that takes no grade (basketball: all but shots) is saved without one.
    if (grade && graded) attributes.grade = grade
    if (from) attributes.zone_from = Number(from)
    if (to && from) attributes.zone_to = Number(to)
    controller.updateTag(tag.id, { side, number: trimmed === '' ? null : Number(trimmed), code, attributes })
    controller.edit(null)
  }
  const onKeyDown = (event: KeyboardEvent) => {
    if (event.key === 'Enter') {
      event.preventDefault()
      save()
    } else if (event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      controller.edit(null)
    }
  }
  const zones = taxonomy.zones

  return (
    <tr data-tag={tag.id} className="bg-sunken" onKeyDown={onKeyDown}>
      <Td className="font-mono text-sm tabular-nums">{formatClock(tag.t_ms)}</Td>
      <Td colSpan={2} tight>
        <Select ref={first} inline value={side} onChange={(e) => setSide(e.target.value as Side)} aria-label={t('tagging.table.side')}>
          <option value="us">{sideLabel(t, 'us')}</option>
          <option value="them">{sideLabel(t, 'them')}</option>
        </Select>
      </Td>
      <Td tight>
        <TextInput
          inline
          className={cx('w-16 tabular-nums', error && 'border-danger')}
          inputMode="numeric"
          maxLength={2}
          value={number}
          onChange={(e) => {
            setNumber(e.target.value)
            setError(false)
          }}
          aria-label={t('tagging.table.number')}
          aria-invalid={error}
        />
      </Td>
      <Td tight>
        <Select inline value={code} onChange={(e) => setCode(e.target.value)} aria-label={t('tagging.filters.skill')}>
          {taxonomy.skills.map((s) => (
            <option key={s.code} value={s.code}>
              {`${s.code} · ${skillLabel(t, s)}`}
            </option>
          ))}
        </Select>
      </Td>
      <Td tight>
        <Select inline value={graded ? grade : ''} disabled={!graded} onChange={(e) => setGrade(e.target.value)} aria-label={t('tagging.filters.grade')}>
          <option value="">–</option>
          {taxonomy.grades.map((g) => (
            <option key={g} value={g}>
              {`${g} ${gradeLabel(t, skillByCode(taxonomy, code), g)}`}
            </option>
          ))}
        </Select>
      </Td>
      {zones.length > 0 && (
        <Td tight className="whitespace-nowrap">
          <Select inline value={from} onChange={(e) => setFrom(e.target.value)} aria-label={t('tagging.table.zoneFrom')}>
            <option value="">–</option>
            {zones.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </Select>{' '}
          <Select inline value={to} disabled={!from} onChange={(e) => setTo(e.target.value)} aria-label={t('tagging.table.zoneTo')}>
            <option value="">–</option>
            {zones.map((z) => (
              <option key={z} value={z}>
                {z}
              </option>
            ))}
          </Select>
        </Td>
      )}
      <Td tight className="text-right whitespace-nowrap">
        <Button variant="primary" onClick={save} aria-label={t('tagging.table.saveEdit', { entry })}>
          {t('common.save')}
        </Button>{' '}
        <Button variant="ghost" onClick={() => controller.edit(null)}>
          {t('common.cancel')}
        </Button>
      </Td>
    </tr>
  )
}
