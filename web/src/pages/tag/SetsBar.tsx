import { useId, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Badge } from '../../components/Badge'
import { Button } from '../../components/Button'
import { Select, TextInput } from '../../components/Field'
import { AlertIcon, CheckIcon } from '../../components/icons'
import { cx } from '../../lib/cx'
import { formatClock, sideLabel } from '../../lib/labels'
import { usePlayhead } from '../../lib/playhead'
import { periodAt } from '../../lib/rallies'
import { newId, periodToIn } from '../../lib/taggingStore'
import type { Period, PeriodIn, Side } from '../../lib/types'
import { useView, useWorkspace } from './context'

/** The sets: add one at the playhead, end it, and compare its final score with the official one. */
export function SetsBar() {
  const { t } = useTranslation()
  const { playhead, controller, bundle } = useWorkspace()
  const view = useView()
  const seconds = usePlayhead(playhead, (s) => Math.floor(s.t_ms / 1000))
  const now = seconds * 1000
  const [form, setForm] = useState<PeriodIn | null>(null)
  const official = bundle.official_sets

  const running = periodAt(view.periods, now)
  const nextNo = Math.min(9, Math.max(0, ...view.periods.map((p) => p.no)) + 1)
  // A new set starts after the last one started.
  const lastStart = Math.max(-1, ...view.periods.map((p) => p.start_ms))
  const canAdd = view.periods.length < 9 && !view.periods.some((p) => p.no === nextNo) && now > lastStart

  return (
    <section aria-labelledby="sets-title" className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="sets-title" className="mr-1 font-semibold">
          {t('tagging.sets.title')}
        </h2>
        {view.periods.length === 0 && <p className="text-sm text-muted">{t('tagging.sets.none')}</p>}
        <ul className="contents">
          {view.periods.map((p) => (
            <li key={p.id}>
              <SetChip period={p} official={official?.[p.no - 1] ?? null} current={running?.id === p.id} onEdit={() => setForm(periodToIn(p))} />
            </li>
          ))}
        </ul>
        {running && running.end_ms === null && now > running.start_ms && (
          <Button onClick={() => controller.endPeriod(running.id)}>
            {t('tagging.sets.end', { no: running.no, time: formatClock(now, { tenths: false }) })}
          </Button>
        )}
        {canAdd && (
          <Button
            onClick={() =>
              setForm({ id: newId(), base_version: 0, no: nextNo, start_ms: now, end_ms: null, start_us: 0, start_them: 0, first_serve: null })
            }
          >
            {t('tagging.sets.add', { no: nextNo, time: formatClock(now, { tenths: false }) })}
          </Button>
        )}
      </div>
      {form && (
        <SetForm
          key={form.id}
          initial={form}
          isNew={!view.periods.some((p) => p.id === form.id)}
          periods={view.periods}
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

function SetChip({ period, official, current, onEdit }: { period: Period; official: [number, number] | null; current: boolean; onEdit: () => void }) {
  const { t } = useTranslation()
  const hasFinal = period.final_us !== null && period.final_them !== null
  // A running set is not compared yet.
  const mismatch =
    hasFinal && period.end_ms !== null && official !== null && (official[0] !== period.final_us || official[1] !== period.final_them)
  const range = `${formatClock(period.start_ms, { tenths: false })}–${period.end_ms === null ? t('tagging.sets.running') : formatClock(period.end_ms, { tenths: false })}`
  return (
    <span className="inline-flex flex-wrap items-center gap-1">
      <Button onClick={onEdit} className={cx(current && 'ring-2 ring-accent')} aria-label={t('tagging.sets.edit', { no: period.no })}>
        <span className="font-bold">{t('tagging.setNo', { no: period.no })}</span>
        <span className="font-normal text-muted tabular-nums">{range}</span>
        {hasFinal && <span className="tabular-nums">{`${period.final_us}:${period.final_them}`}</span>}
      </Button>
      {official && hasFinal && period.end_ms !== null && !mismatch && (
        <span className="inline-flex items-center gap-1 text-xs text-muted" title={t('tagging.sets.official', { us: official[0], them: official[1] })}>
          <CheckIcon className="size-4" />
          <span className="sr-only">{t('tagging.sets.matches')}</span>
        </span>
      )}
      {mismatch && official && (
        <Badge tone="danger" className="gap-1">
          <AlertIcon className="size-3.5" />
          {t('tagging.sets.mismatch', { us: official[0], them: official[1] })}
        </Badge>
      )}
    </span>
  )
}

interface SetFormProps {
  initial: PeriodIn
  isNew: boolean
  periods: readonly Period[]
  onSave: (period: PeriodIn) => void
  onCancel: () => void
  onDelete: () => void
}

function SetForm({ initial, isNew, periods, onSave, onCancel, onDelete }: SetFormProps) {
  const { t } = useTranslation()
  const id = useId()
  const { playhead } = useWorkspace()
  const [value, setValue] = useState(initial)
  const [error, setError] = useState<string | null>(null)
  const set = (patch: Partial<PeriodIn>) => setValue((v) => ({ ...v, ...patch }))
  const here = () => playhead.getState().t_ms
  const score = (text: string) => Math.min(99, Math.max(0, Number.parseInt(text, 10) || 0))

  const save = () => {
    if (periods.some((p) => p.no === value.no && p.id !== value.id)) {
      setError(t('tagging.sets.errors.duplicate', { no: value.no }))
      return
    }
    if (value.end_ms != null && value.end_ms <= value.start_ms) {
      setError(t('tagging.sets.errors.order'))
      return
    }
    onSave(value)
  }

  return (
    <form
      className="flex flex-col gap-3 rounded-2xl border border-hairline bg-surface p-4"
      aria-label={isNew ? t('tagging.sets.newTitle', { no: value.no }) : t('tagging.sets.edit', { no: value.no })}
      onSubmit={(e) => {
        e.preventDefault()
        save()
      }}
    >
      <div className="flex flex-wrap items-end gap-4">
        <label className="flex flex-col gap-1 text-sm font-semibold" htmlFor={`${id}-no`}>
          {t('tagging.sets.number')}
          <Select id={`${id}-no`} inline value={value.no} onChange={(e) => set({ no: Number(e.target.value) })}>
            {[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => (
              <option key={n} value={n}>
                {n}
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
        <fieldset className="flex flex-col gap-1 text-sm">
          <legend className="mb-1 font-semibold">{t('tagging.sets.startScore')}</legend>
          <span className="flex items-center gap-1">
            <TextInput
              inline
              className="w-14 tabular-nums"
              inputMode="numeric"
              aria-label={t('tagging.sets.startScoreOf', { side: sideLabel(t, 'us') })}
              value={value.start_us}
              onChange={(e) => set({ start_us: score(e.target.value) })}
            />
            <span aria-hidden="true">:</span>
            <TextInput
              inline
              className="w-14 tabular-nums"
              inputMode="numeric"
              aria-label={t('tagging.sets.startScoreOf', { side: sideLabel(t, 'them') })}
              value={value.start_them}
              onChange={(e) => set({ start_them: score(e.target.value) })}
            />
          </span>
        </fieldset>
        <label className="flex flex-col gap-1 text-sm font-semibold" htmlFor={`${id}-serve`}>
          {t('tagging.sets.firstServe')}
          <Select
            id={`${id}-serve`}
            inline
            value={value.first_serve ?? ''}
            onChange={(e) => set({ first_serve: e.target.value === '' ? null : (e.target.value as Side) })}
          >
            <option value="">{t('tagging.sets.unknown')}</option>
            <option value="us">{t('tagging.us')}</option>
            <option value="them">{t('tagging.them')}</option>
          </Select>
        </label>
      </div>
      {error && (
        <p role="alert" className="text-sm text-danger">
          {error}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant="primary">
          {t('tagging.sets.save')}
        </Button>
        <Button onClick={onCancel}>{t('common.cancel')}</Button>
        {!isNew && (
          <Button variant="ghostDanger" onClick={onDelete} className="ml-auto">
            {t('tagging.sets.delete', { no: value.no })}
          </Button>
        )}
      </div>
    </form>
  )
}
