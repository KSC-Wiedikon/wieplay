import type { TFunction } from 'i18next'
import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { Card } from '../../components/Card'
import { Select, TextInput } from '../../components/Field'
import { AlertIcon, CheckIcon, CloseIcon, SparkleIcon } from '../../components/icons'
import { Modal } from '../../components/Modal'
import { ScoreHead, SuggestedValue } from '../../components/ScoringMarks'
import { Table, TBody, Td, Th, THead } from '../../components/Table'
import { useAnalysis } from '../../lib/analysis'
import { cx } from '../../lib/cx'
import { formatClock, periodLabel, sideLabel } from '../../lib/labels'
import { usePlayhead } from '../../lib/playhead'
import { periodAt } from '../../lib/rallies'
import { ROMAN } from '../../lib/rotations'
import { useSideNames } from '../../lib/sides'
import { formatPercent, isAuto, setToCheck } from '../../lib/suggestions'
import { newId, periodToIn } from '../../lib/taggingStore'
import type { CourtSide, Period, PeriodIn, Segment, Side } from '../../lib/types'
import { useView, useWorkspace } from './context'

/** The bar's words: sets (volleyball) or quarters and overtimes (basketball). */
function periodTexts(t: TFunction, sport: string) {
  const name = (no: number) => periodLabel(t, sport, no)
  if (sport !== 'basketball') {
    return {
      title: t('tagging.sets.title'),
      none: t('tagging.sets.none'),
      name,
      add: (no: number, time: string) => t('tagging.sets.add', { no, time }),
      end: (no: number, time: string) => t('tagging.sets.end', { no, time }),
      edit: (no: number) => t('tagging.sets.edit', { no }),
      newTitle: (no: number) => t('tagging.sets.newTitle', { no }),
      remove: (no: number) => t('tagging.sets.delete', { no }),
      official: (us: number, them: number) => t('tagging.sets.official', { us, them }),
      matches: t('tagging.sets.matches'),
      mismatch: (us: number, them: number) => t('tagging.sets.mismatch', { us, them }),
      number: t('tagging.sets.number'),
      startScore: t('tagging.sets.startScore'),
      save: t('tagging.sets.save'),
      duplicate: (no: number) => t('tagging.sets.errors.duplicate', { no }),
      order: t('tagging.sets.errors.order'),
    }
  }
  return {
    title: t('tagging.basketball.quarters.title'),
    none: t('tagging.basketball.quarters.none'),
    name,
    add: (no: number, time: string) => t('tagging.basketball.quarters.add', { period: name(no), time }),
    end: (no: number, time: string) => t('tagging.basketball.quarters.end', { period: name(no), time }),
    edit: (no: number) => t('tagging.basketball.quarters.edit', { period: name(no) }),
    newTitle: (no: number) => t('tagging.basketball.quarters.newTitle', { period: name(no) }),
    remove: (no: number) => t('tagging.basketball.quarters.delete', { period: name(no) }),
    official: (us: number, them: number) => t('tagging.basketball.quarters.official', { us, them }),
    matches: t('tagging.basketball.quarters.matches'),
    mismatch: (us: number, them: number) => t('tagging.basketball.quarters.mismatch', { us, them }),
    number: t('tagging.basketball.quarter'),
    startScore: t('tagging.basketball.quarters.startScore'),
    save: t('tagging.basketball.quarters.save'),
    duplicate: (no: number) => t('tagging.basketball.quarters.errors.duplicate', { period: name(no) }),
    order: t('tagging.basketball.quarters.errors.order'),
  }
}

type Texts = ReturnType<typeof periodTexts>

/**
 * The sets (basketball: quarters): add one at the playhead, end it, and compare its final score
 * with the official one. A basketball game has one official result, the final: it is compared
 * with the last quarter once that is the fourth or later and has ended. Volleyball: the sets
 * WiePlay found count; those it is unsure of are marked to check (accept or reject them), and per
 * set what the automatic score needs.
 */
export function SetsBar() {
  const { t } = useTranslation()
  const { playhead, controller, bundle, taxonomy } = useWorkspace()
  const view = useView()
  const seconds = usePlayhead(playhead, (s) => Math.floor(s.t_ms / 1000))
  const now = seconds * 1000
  const [form, setForm] = useState<PeriodIn | null>(null)
  const texts = periodTexts(t, taxonomy.sport)
  const basketball = taxonomy.sport === 'basketball'
  const last = view.periods.at(-1)
  const officialOf = (p: Period): [number, number] | null => {
    if (!basketball) return bundle.official_sets?.[p.no - 1] ?? null
    return p.id === last?.id && p.no >= 4 ? bundle.official_final : null
  }

  const running = periodAt(view.periods, now)
  const nextNo = Math.min(9, Math.max(0, ...view.periods.map((p) => p.no)) + 1)
  // A new set starts after the last one started.
  const lastStart = Math.max(-1, ...view.periods.map((p) => p.start_ms))
  const canAdd = view.periods.length < 9 && !view.periods.some((p) => p.no === nextNo) && now > lastStart

  return (
    <section aria-labelledby="sets-title" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="sets-title" className="mr-1 font-semibold">
          {texts.title}
        </h2>
        {view.periods.length === 0 && <p className="text-sm text-muted">{texts.none}</p>}
        <ul className="contents">
          {view.periods.map((p) => (
            <li key={p.id}>
              <SetChip period={p} official={officialOf(p)} current={running?.id === p.id} texts={texts} onEdit={() => setForm(periodToIn(p))} />
              {setToCheck(p) && (
                <span className="ml-1 inline-flex gap-0.5 align-middle">
                  <Button variant="ghost" size="icon" aria-label={t('tagging.sets.accept', { no: p.no })} title={t('tagging.sets.accept', { no: p.no })} onClick={() => controller.acceptPeriod(p.id)}>
                    <CheckIcon className="size-4" />
                  </Button>
                  <Button variant="ghostDanger" size="icon" aria-label={t('tagging.sets.reject', { no: p.no })} title={t('tagging.sets.reject', { no: p.no })} onClick={() => controller.deletePeriod(p.id)}>
                    <CloseIcon className="size-4" />
                  </Button>
                </span>
              )}
            </li>
          ))}
        </ul>
        {running && running.end_ms === null && now > running.start_ms && (
          <Button onClick={() => controller.endPeriod(running.id)}>{texts.end(running.no, formatClock(now, { tenths: false }))}</Button>
        )}
        {canAdd && (
          <Button
            onClick={() =>
              setForm({ id: newId(), base_version: 0, no: nextNo, start_ms: now, end_ms: null, start_us: 0, start_them: 0, first_serve: null })
            }
          >
            {texts.add(nextNo, formatClock(now, { tenths: false }))}
          </Button>
        )}
      </div>
      {!basketball && view.periods.length > 0 && <SetDetails official={officialOf} />}
      {form && (
        <SetForm
          key={form.id}
          initial={form}
          isNew={!view.periods.some((p) => p.id === form.id)}
          periods={view.periods}
          texts={texts}
          basketball={basketball}
          onCancel={() => setForm(null)}
          onSave={(period) => {
            controller.savePeriod(period)
            setForm(null)
          }}
          onDelete={() => {
            controller.deletePeriod(form.id)
            setForm(null)
          }}
        />
      )}
    </section>
  )
}

function SetChip({
  period,
  official,
  current,
  texts,
  onEdit,
}: {
  period: Period
  official: [number, number] | null
  current: boolean
  texts: Texts
  onEdit: () => void
}) {
  const { t } = useTranslation()
  const hasFinal = period.final_us !== null && period.final_them !== null
  const check = setToCheck(period)
  const auto = period.source === 'model' && isAuto({ source: period.source, review: period.review ?? 'unreviewed' })
  const confidence = formatPercent(period.confidence)
  // A running set is not compared yet.
  const mismatch =
    hasFinal && period.end_ms !== null && official !== null && (official[0] !== period.final_us || official[1] !== period.final_them)
  const range = `${formatClock(period.start_ms, { tenths: false })}–${period.end_ms === null ? t('tagging.sets.running') : formatClock(period.end_ms, { tenths: false })}`
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <Button
        onClick={onEdit}
        data-suggestion={check ? 'check' : auto ? 'auto' : undefined}
        className={cx(current && 'ring-2 ring-accent', check && 'border-2 border-danger')}
        aria-label={texts.edit(period.no)}
      >
        {(check || auto) && <SparkleIcon className="size-3.5" />}
        {auto && <span className="sr-only">{t('tagging.suggestions.auto')}</span>}
        <span className="font-bold">{texts.name(period.no)}</span>
        {check && (
          <span className="text-xs font-semibold text-danger">{confidence ? t('tagging.sets.toCheckConfidence', { confidence }) : t('tagging.sets.toCheck')}</span>
        )}
        <span className="font-normal text-muted tabular-nums">{range}</span>
        {hasFinal && <span className="tabular-nums">{`${period.final_us}:${period.final_them}`}</span>}
      </Button>
      {official && hasFinal && period.end_ms !== null && !mismatch && (
        <span className="inline-flex items-center gap-1 text-xs text-muted" title={texts.official(official[0], official[1])}>
          <CheckIcon className="size-4" />
          <span className="sr-only">{texts.matches}</span>
        </span>
      )}
      {mismatch && official && (
        <Badge tone="danger" className="gap-1">
          <AlertIcon className="size-3.5" />
          {texts.mismatch(official[0], official[1])}
        </Badge>
      )}
    </span>
  )
}

/**
 * Per set: the official score against the final of WiePlay's score line, who served first and
 * where we started (WiePlay's guess shown until a coach clicks), and our starting rotation. Each
 * is an ordinary set change; the server scores the match again with it.
 */
/** Below this, WiePlay asks the coach who served first in set 1 and where we started. */
const SIDES_SURE = 0.9

function SetDetails({ official }: { official: (p: Period) => [number, number] | null }) {
  const { t } = useTranslation()
  const names = useSideNames()
  const { controller, video } = useWorkspace()
  const view = useView()
  const scoring = useAnalysis(video.id, true).data?.scoring ?? null
  // Set 1's first serve and our side decide the score of the whole match (the rules derive the
  // other sets): when WiePlay is unsure about them and no coach set them, ask for that one click.
  const first = view.periods.find((p) => p.no === 1) ?? view.periods[0] ?? null
  const sides = scoring?.sides_confidence ?? null
  const unsure = scoring !== null && (sides === null || sides < SIDES_SURE)
  const invite = unsure && first !== null && (first.first_serve === null || first.our_court_side_at_start === null) ? first : null
  const [lineup, setLineup] = useState<Period | null>(null)
  return (
    <div className="flex flex-col gap-1">
      {lineup && (
        <LineupDialog
          key={lineup.id}
          period={lineup}
          onClose={() => setLineup(null)}
          onSave={(patch) => {
            controller.updatePeriod(lineup.id, patch)
            setLineup(null)
          }}
        />
      )}
      {invite && (
        <div role="note" aria-label={t('tagging.sets.sidesTitle')} className="flex items-start gap-2 rounded-xl border border-accent bg-accent-soft p-3 text-sm text-on-accent-soft">
          <SparkleIcon className="mt-0.5 size-4 shrink-0" />
          <div className="flex flex-col gap-0.5">
            <p className="font-semibold">{t('tagging.sets.sidesTitle')}</p>
            <p>{sides === null ? t('tagging.sets.sidesBodyUnknown') : t('tagging.sets.sidesBody', { confidence: formatPercent(sides) })}</p>
          </div>
        </div>
      )}
      <Card className="overflow-hidden">
        <Table caption={t('tagging.sets.details')}>
          <THead>
            <tr>
              <Th>{t('tagging.sets.set')}</Th>
              <ScoreHead />
              <Th>{t('tagging.sets.firstServe')}</Th>
              <Th>{t('tagging.sets.weStart', { team: sideLabel(t, 'us', names) })}</Th>
              <Th>
                <abbr title={t('tagging.sets.rotationHint')} className="no-underline">
                  {t('tagging.sets.rotation')}
                </abbr>
              </Th>
              <Th>
                <abbr title={t('tagging.sets.lineupHint')} className="no-underline">
                  {t('tagging.sets.lineup')}
                </abbr>
              </Th>
            </tr>
          </THead>
          <TBody>
            {view.periods.map((p) => (
              <tr key={p.id} data-set={p.no} className={cx(invite?.id === p.id && 'bg-accent-soft/60 outline-2 -outline-offset-2 outline-accent')}>
                <Td className="font-semibold whitespace-nowrap">{periodLabel(t, 'volleyball', p.no)}</Td>
                <Td className="whitespace-nowrap tabular-nums">
                  <SetScore period={p} official={official(p)} segments={view.segments} />
                </Td>
                <Td tight>
                  <Choice<Side>
                    label={t('tagging.sets.firstServeOf', { no: p.no })}
                    value={p.first_serve}
                    guess={p.model_first_serve ?? null}
                    options={[
                      { value: 'us', label: sideLabel(t, 'us', names) },
                      { value: 'them', label: sideLabel(t, 'them', names) },
                    ]}
                    onChange={(first_serve) => controller.updatePeriod(p.id, { first_serve })}
                  />
                </Td>
                <Td tight>
                  <Choice<CourtSide>
                    label={t('tagging.sets.weStartOf', { no: p.no, team: sideLabel(t, 'us', names) })}
                    value={p.our_court_side_at_start}
                    guess={p.model_court_side ?? null}
                    options={[
                      { value: 'near', label: t('tagging.sets.near') },
                      { value: 'far', label: t('tagging.sets.far') },
                    ]}
                    onChange={(our_court_side_at_start) => controller.updatePeriod(p.id, { our_court_side_at_start })}
                  />
                </Td>
                <Td tight>
                  <Select
                    inline
                    aria-label={t('tagging.sets.rotationOf', { no: p.no })}
                    value={p.start_rotation ?? ''}
                    onChange={(e) => controller.updatePeriod(p.id, { start_rotation: e.target.value === '' ? null : Number(e.target.value) })}
                  >
                    <option value="">{t('tagging.sets.rotationNone')}</option>
                    {[1, 2, 3, 4, 5, 6].map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </Select>
                </Td>
                <Td tight>
                  <Button aria-label={t('tagging.sets.lineupOf', { no: p.no })} onClick={() => setLineup(p)} className="tabular-nums">
                    {p.start_lineup?.some((n) => n !== null) ? p.start_lineup.map((n) => n ?? '–').join(' · ') : t('tagging.sets.lineupAdd')}
                  </Button>
                </Td>
              </tr>
            ))}
          </TBody>
        </Table>
      </Card>
      <p className="text-xs text-muted">{t('tagging.sets.detailsHint')}</p>
    </div>
  )
}

/** Where each zone's field sits on the dialog's court: front row IV III II, back row V VI I. */
const LINEUP_ORDER = [3, 2, 1, 4, 5, 0] as const

/**
 * A set's starting lineup: our six shirt numbers in zones I-VI at the first serve, laid out
 * like the court seen from behind our end line, and the setter, whose zone is the starting
 * rotation. Optional; the rotation charts on the video page show who stands where.
 */
function LineupDialog({ period, onClose, onSave }: { period: Period; onClose: () => void; onSave: (patch: Partial<PeriodIn>) => void }) {
  const { t } = useTranslation()
  const id = useId()
  const { bundle } = useWorkspace()
  const [numbers, setNumbers] = useState<string[]>(() => Array.from({ length: 6 }, (_, i) => (period.start_lineup?.[i] ?? '').toString()))
  const [setter, setSetter] = useState<number | null>(period.start_rotation ?? null)
  const [error, setError] = useState<string | null>(null)
  const roster = bundle.roster.filter((r) => r.side === 'us').sort((a, b) => a.number - b.number)

  const save = () => {
    const lineup: (number | null)[] = []
    for (const text of numbers) {
      const trimmed = text.trim()
      if (trimmed === '') {
        lineup.push(null)
        continue
      }
      const n = Number(trimmed)
      if (!/^\d{1,2}$/.test(trimmed) || !Number.isInteger(n)) {
        setError(t('tagging.sets.lineupErrors.number'))
        return
      }
      if (lineup.includes(n)) {
        setError(t('tagging.sets.lineupErrors.duplicate', { number: n }))
        return
      }
      lineup.push(n)
    }
    onSave({ start_lineup: lineup.some((n) => n !== null) ? lineup : null, start_rotation: setter })
  }

  return (
    <Modal
      open
      title={t('tagging.sets.lineupOf', { no: period.no })}
      onClose={onClose}
      footer={
        <>
          {period.start_lineup && (
            <Button variant="ghostDanger" className="mr-auto" onClick={() => onSave({ start_lineup: null })}>
              {t('tagging.sets.lineupClear')}
            </Button>
          )}
          <Button onClick={onClose}>{t('common.cancel')}</Button>
          <Button variant="primary" type="submit" form={`${id}-form`}>
            {t('tagging.sets.lineupSave')}
          </Button>
        </>
      }
    >
      <form
        id={`${id}-form`}
        className="flex flex-col gap-3"
        onSubmit={(e) => {
          e.preventDefault()
          save()
        }}
      >
        <p className="text-sm text-muted">{t('tagging.sets.lineupBody')}</p>
        <div className="rounded-card bg-sunken p-3">
          <p className="mb-1 text-center text-xs font-semibold text-muted">{t('tagging.sets.net')}</p>
          <div aria-hidden="true" className="mb-2 h-1.5 rounded-full bg-fg" />
          <div className="grid grid-cols-3 gap-2">
            {LINEUP_ORDER.map((zone) => {
              const roman = ROMAN[zone]
              return (
                <div key={zone} data-zone={zone + 1} className={cx('flex flex-col items-center gap-1.5 rounded-control border border-hairline bg-court-floor p-2', zone === 3 || zone === 2 || zone === 1 ? 'mb-2' : '')}>
                  <label htmlFor={`${id}-z${zone}`} className="text-xs font-semibold text-muted">
                    {t('tagging.sets.zone', { zone: roman })}
                  </label>
                  <TextInput
                    id={`${id}-z${zone}`}
                    inline
                    list={`${id}-roster`}
                    inputMode="numeric"
                    autoComplete="off"
                    aria-label={t('tagging.sets.numberIn', { zone: roman })}
                    className="w-16 text-center text-base font-semibold tabular-nums"
                    value={numbers[zone] ?? ''}
                    onChange={(e) => {
                      const next = [...numbers]
                      next[zone] = e.target.value
                      setNumbers(next)
                      setError(null)
                    }}
                  />
                  <label className="inline-flex min-h-11 cursor-pointer items-center gap-1.5 text-xs font-semibold sm:min-h-8">
                    <input
                      type="radio"
                      name={`${id}-setter`}
                      className="size-4 accent-[var(--color-accent)]"
                      checked={setter === zone + 1}
                      aria-label={t('tagging.sets.setterIn', { zone: roman })}
                      onChange={() => setSetter(zone + 1)}
                    />
                    {t('tagging.sets.setter')}
                  </label>
                </div>
              )
            })}
          </div>
        </div>
        <datalist id={`${id}-roster`}>
          {roster.map((r) => (
            <option key={r.number} value={r.number}>
              {r.name ?? ''}
            </option>
          ))}
        </datalist>
        {error && (
          <p role="alert" className="text-sm text-danger">
            {error}
          </p>
        )}
      </form>
    </Modal>
  )
}

/** "Official 25:21 · WiePlay 25:21 ✓": the federation's score against the final of the score line. */
function SetScore({ period, official, segments }: { period: Period; official: [number, number] | null; segments: readonly Segment[] }) {
  const { t } = useTranslation()
  if (period.final_us === null || period.final_them === null) return official ? `${official[0]}:${official[1]}` : '–'
  const model = `${period.final_us}:${period.final_them}`
  // WiePlay's winners went into it where no coach decided a rally.
  const suggested = segments.some((s) => s.kind === 'rally' && s.period_id === period.id && s.score_source === 'model')
  const same = official !== null && official[0] === period.final_us && official[1] === period.final_them
  const text = official
    ? t('tagging.sets.officialVsWiePlay', { official: `${official[0]}:${official[1]}`, model })
    : t('tagging.sets.wieplayOnly', { model })
  return (
    <span className={cx('inline-flex items-center gap-1', official && !same && 'font-semibold text-danger')}>
      <SuggestedValue suggested={suggested}>{text}</SuggestedValue>
      {same && (
        <span title={t('tagging.sets.sameAsOfficial')} className="inline-flex text-fg">
          <CheckIcon className="size-4" />
          <span className="sr-only">{t('tagging.sets.sameAsOfficial')}</span>
        </span>
      )}
      {official && !same && <AlertIcon className="size-4" />}
    </span>
  )
}

/**
 * Two buttons for one choice ("Us | Them", "Near | Far"). WiePlay's guess shows as a dashed
 * suggestion until a coach picks one; picking it confirms it.
 */
function Choice<T extends string>({
  label,
  value,
  guess,
  options,
  onChange,
}: {
  label: string
  value: T | null
  guess: T | null
  options: { value: T; label: string }[]
  onChange: (value: T) => void
}) {
  const { t } = useTranslation()
  return (
    <div role="group" aria-label={label} className="inline-flex gap-1 rounded-control bg-sunken p-1 ring-1 ring-hairline ring-inset">
      {options.map((option) => {
        const on = value === option.value
        const suggested = value === null && guess === option.value
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={on}
            data-suggested={suggested ? 'true' : undefined}
            title={suggested ? t('tagging.sets.guess', { value: option.label }) : undefined}
            onClick={() => {
              if (!on) onChange(option.value)
            }}
            className={cx(
              'inline-flex min-h-9 cursor-pointer items-center justify-center rounded-[calc(var(--radius-control)-0.25rem)] px-3 text-sm font-semibold whitespace-nowrap sm:min-h-7',
              'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
              on
                ? 'bg-surface text-fg shadow-card ring-1 ring-hairline'
                : suggested
                  ? 'border border-dashed border-accent bg-accent-soft text-on-accent-soft italic'
                  : 'text-muted hover:bg-hover hover:text-fg',
            )}
          >
            {option.label}
            {suggested && (
              <>
                {' '}
                <span className="sr-only">{`(${t('tagging.scoring.suggested')})`}</span>
              </>
            )}
          </button>
        )
      })}
    </div>
  )
}

interface SetFormProps {
  initial: PeriodIn
  isNew: boolean
  periods: readonly Period[]
  texts: Texts
  /** No first serve; a starting score only for the first quarter (the score runs on from there). */
  basketball: boolean
  onSave: (period: PeriodIn) => void
  onCancel: () => void
  onDelete: () => void
}

function SetForm({ initial, isNew, periods, texts, basketball, onSave, onCancel, onDelete }: SetFormProps) {
  const { t } = useTranslation()
  const names = useSideNames()
  const id = useId()
  const { playhead } = useWorkspace()
  const [value, setValue] = useState(initial)
  const [error, setError] = useState<string | null>(null)
  const set = (patch: Partial<PeriodIn>) => setValue((v) => ({ ...v, ...patch }))
  const here = () => playhead.getState().t_ms
  const score = (text: string) => Math.min(99, Math.max(0, Number.parseInt(text, 10) || 0))

  // The server seeds basketball's running score with the first quarter's starting score only.
  const first = !periods.some((p) => p.id !== value.id && (p.start_ms < value.start_ms || (p.start_ms === value.start_ms && p.no < value.no)))
  const startScore = !basketball || first

  const save = () => {
    if (periods.some((p) => p.no === value.no && p.id !== value.id)) {
      setError(texts.duplicate(value.no))
      return
    }
    if (value.end_ms != null && value.end_ms <= value.start_ms) {
      setError(texts.order)
      return
    }
    onSave(value)
  }

  return (
    <form
      className="flex flex-col gap-3 rounded-card border border-hairline bg-surface shadow-card p-4"
      aria-label={isNew ? texts.newTitle(value.no) : texts.edit(value.no)}
      onSubmit={(e) => {
        e.preventDefault()
        save()
      }}
    >
      <div className="flex flex-wrap items-end gap-4">
        <label className="flex flex-col gap-1 text-sm font-semibold" htmlFor={`${id}-no`}>
          {texts.number}
          <Select id={`${id}-no`} inline value={value.no} onChange={(e) => set({ no: Number(e.target.value) })}>
            {[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => (
              <option key={n} value={n}>
                {basketball ? texts.name(n) : n}
              </option>
            ))}
          </Select>
        </label>
        <div className="flex flex-col gap-1 text-sm">
          <span className="font-semibold">{t('tagging.sets.start')}</span>
          <span className="flex items-center gap-2">
            <span className="w-16 tabular-nums">{formatClock(value.start_ms)}</span>
            <Button onClick={() => set({ start_ms: here() })}>{t('tagging.sets.toPlayhead')}</Button>
          </span>
        </div>
        <div className="flex flex-col gap-1 text-sm">
          <span className="font-semibold">{t('tagging.sets.endLabel')}</span>
          <span className="flex items-center gap-2">
            <span className="w-16 tabular-nums">{value.end_ms == null ? t('tagging.sets.running') : formatClock(value.end_ms)}</span>
            <Button onClick={() => set({ end_ms: here() })}>{t('tagging.sets.toPlayhead')}</Button>
            {value.end_ms != null && (
              <Button variant="ghost" onClick={() => set({ end_ms: null })}>
                {t('tagging.sets.clearEnd')}
              </Button>
            )}
          </span>
        </div>
        {startScore && (
          <fieldset className="flex flex-col gap-1 text-sm">
            <legend className="mb-1 font-semibold">{texts.startScore}</legend>
            <span className="flex items-center gap-1">
              <TextInput
                inline
                className="w-14 tabular-nums"
                inputMode="numeric"
                aria-label={t('tagging.sets.startScoreOf', { side: sideLabel(t, 'us', names) })}
                value={value.start_us}
                onChange={(e) => set({ start_us: score(e.target.value) })}
              />
              <span aria-hidden="true">:</span>
              <TextInput
                inline
                className="w-14 tabular-nums"
                inputMode="numeric"
                aria-label={t('tagging.sets.startScoreOf', { side: sideLabel(t, 'them', names) })}
                value={value.start_them}
                onChange={(e) => set({ start_them: score(e.target.value) })}
              />
            </span>
          </fieldset>
        )}
        {!basketball && (
          <label className="flex flex-col gap-1 text-sm font-semibold" htmlFor={`${id}-serve`}>
            {t('tagging.sets.firstServe')}
            <Select
              id={`${id}-serve`}
              inline
              value={value.first_serve ?? ''}
              onChange={(e) => set({ first_serve: e.target.value === '' ? null : (e.target.value as Side) })}
            >
              <option value="">{t('tagging.sets.unknown')}</option>
              <option value="us">{sideLabel(t, 'us', names)}</option>
              <option value="them">{sideLabel(t, 'them', names)}</option>
            </Select>
          </label>
        )}
      </div>
      {basketball && first && <p className="text-xs text-muted">{t('tagging.basketball.quarters.startScoreHint')}</p>}
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant="primary">
          {texts.save}
        </Button>
        <Button onClick={onCancel}>{t('common.cancel')}</Button>
        {!isNew && (
          <Button variant="ghostDanger" onClick={onDelete} className="ml-auto">
            {texts.remove(value.no)}
          </Button>
        )}
      </div>
    </form>
  )
}
