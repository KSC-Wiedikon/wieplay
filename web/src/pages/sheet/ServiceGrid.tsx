import { useId, type ChangeEvent, type KeyboardEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/Button'
import { PlusIcon, TrashIcon } from '../../components/icons'
import { cx } from '../../lib/cx'
import { sideLabel } from '../../lib/labels'
import {
  boxesFor,
  boxName,
  cells,
  isDeciding,
  POSITIONS,
  valueAt,
  withBox,
  type SheetSetDoc,
  type SubDoc,
  type TimeoutDoc,
} from '../../lib/scoresheet'
import { altsText, boxField, lineupField, markOf, readClass, subField, timeoutKey, type ReadCell } from '../../lib/sheetRead'
import type { SideNames } from '../../lib/sides'
import type { Side } from '../../lib/types'
import { boxId, boxKey, focusBox, parseDigits, type BoxMark } from './boxes'

/** What the photo's reading proposed in a draft set (SS4), and what the coach looked at. */
export interface ReadView {
  cells: Record<string, ReadCell> | undefined
  accepted: ReadonlySet<string>
  onFocus: (key: string) => void
}

/** The props a read field's input gets: its key, its mark, the reader's alternatives. */
function readProps(view: ReadView | undefined, key: string) {
  const mark = markOf(view?.cells, view?.accepted ?? new Set(), key)
  const cell = view?.cells?.[key]
  return {
    mark,
    attrs: {
      'data-field': key,
      'data-read': mark ?? undefined,
      title: cell && mark ? altsText(cell) : undefined,
    },
    onFocus: () => view?.onFocus(key),
  }
}

const CIRCLE_KEYS = new Set(['*', 'o', 'O', '+'])

interface TeamBoxesProps {
  set: SheetSetDoc
  side: Side
  names: SideNames | null
  /** Id prefix of this set's boxes (both teams). */
  prefix: string
  marks: Record<string, BoxMark>
  /** Boxes the board log and the video say otherwise (the scoring's correction): what they say. */
  suggest?: Record<string, { value: string; title: string }>
  readOnly: boolean
  onChange: (set: SheetSetDoc) => void
  /** Enter on an empty box: this team's boxes are done, go to the other team. */
  onOther: () => void
  onFocusBox: (side: Side, position: number, box: number) => void
  read?: ReadView
}

/**
 * One team's part of a set, shaped like the paper sheet: the starting lineup above the columns
 * I-VI, under each its service boxes (two columns of four: 1/5, 2/6, 3/7, 4/8; three rows in a
 * deciding set), then the timeouts and the substitutions. Typing follows the sheet's order (box 1
 * of positions I-VI, then box 2, ...), which is the order of the service turns.
 */
export function TeamBoxes({ set, side, names, prefix, marks, suggest, readOnly, onChange, onOther, onFocusBox, read }: TeamBoxesProps) {
  const { t } = useTranslation()
  const deciding = isDeciding(set)
  const per = boxesFor(deciding)
  const half = per / 2
  const first = set.first_serve === side
  const receiver = set.first_serve !== null && !first
  const order = cells(set.first_serve === null ? true : first, deciding)
  const team = sideLabel(t, side, names)
  const lineup = (side === 'us' ? set.lineup_us : set.lineup_them) ?? [null, null, null, null, null, null]
  const circled = side === 'us' ? set.circled_us : set.circled_them

  const at = (position: number, box: number) => order.findIndex(([p, b]) => p === position && b === box)
  const go = (k: number) => {
    const cell = order[k]
    return cell ? focusBox(prefix, side, cell[0], cell[1]) : false
  }
  const setValue = (position: number, box: number, value: number | null) => onChange(withBox(set, side, position, box, value))
  const toggleCircle = (position: number, box: number) => {
    const on = circled?.[0] === position && circled[1] === box
    onChange({ ...set, [side === 'us' ? 'circled_us' : 'circled_them']: on ? null : [position, box] })
  }

  const onKey = (position: number, box: number) => (event: KeyboardEvent<HTMLInputElement>) => {
    const k = at(position, box)
    const empty = event.currentTarget.value === ''
    const move = (to: number) => {
      if (go(to)) event.preventDefault()
    }
    if (CIRCLE_KEYS.has(event.key)) {
      event.preventDefault()
      toggleCircle(position, box)
    } else if (event.key === 'Enter') {
      event.preventDefault()
      if (empty) onOther()
      else go(k + 1)
    } else if (event.key === ' ' || event.key === 'ArrowRight') {
      event.preventDefault()
      go(k + 1)
    } else if (event.key === 'Tab') {
      move(event.shiftKey ? k - 1 : k + 1)
    } else if (event.key === 'ArrowLeft' || (event.key === 'Backspace' && empty)) {
      move(k - 1)
    } else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      focusBox(prefix, side, position, box + (event.key === 'ArrowDown' ? 1 : -1))
    }
  }
  const onInput = (position: number, box: number) => (event: ChangeEvent<HTMLInputElement>) => {
    const digits = event.target.value.replace(/\D/g, '')
    setValue(position, box, parseDigits(digits))
    if (digits.length >= 2) go(at(position, box) + 1) // two digits: on to the next box
  }

  const setLineup = (position: number, value: number | null) => {
    const next = [...lineup]
    next[position - 1] = value
    onChange({ ...set, [side === 'us' ? 'lineup_us' : 'lineup_them']: next })
  }
  const lineupId = (position: number) => `${prefix}-lineup-${side}-${position}`

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h3 className="text-base font-semibold">{team}</h3>
        {set.first_serve && <span className="text-sm text-muted">{t(first ? 'sheet.team.servedFirst' : 'sheet.team.receivedFirst')}</span>}
      </div>
      <div role="group" aria-label={`${team}: ${t('sheet.boxes')}`} className="grid grid-cols-3 gap-x-3 gap-y-4 sm:grid-cols-6 sm:gap-x-2">
        {POSITIONS.map((roman, i) => {
          const position = i + 1
          return (
            <div key={roman} className="flex min-w-0 flex-col gap-1.5">
              <label htmlFor={lineupId(position)} className="flex flex-col items-center gap-1">
                <span className="text-xs font-bold text-muted">{roman}</span>
                <input
                  {...readProps(read, lineupField(side, position)).attrs}
                  onFocus={readProps(read, lineupField(side, position)).onFocus}
                  id={lineupId(position)}
                  inputMode="numeric"
                  autoComplete="off"
                  aria-label={t('sheet.lineupField', { team, position: roman })}
                  value={lineup[i] ?? ''}
                  disabled={readOnly}
                  onChange={(event) => {
                    const digits = event.target.value.replace(/\D/g, '')
                    setLineup(position, parseDigits(digits))
                    if (digits.length >= 2) document.getElementById(lineupId(position + 1))?.focus()
                  }}
                  onKeyDown={(event) => {
                    if (event.key === 'Enter') {
                      event.preventDefault()
                      document.getElementById(lineupId(position + 1))?.focus()
                    }
                  }}
                  className={cx(
                    'h-11 w-full min-w-0 rounded-control border border-hairline-strong bg-sunken text-center font-mono text-base font-bold tabular-nums focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring sm:h-9',
                    readClass(readProps(read, lineupField(side, position)).mark),
                  )}
                />
              </label>
              <div className="grid grid-cols-2 gap-1">
                {Array.from({ length: half }, (_, r) =>
                  [r + 1, r + 1 + half].map((box) => {
                    const isX = receiver && position === 1 && box === 1
                    const key = boxKey(side, position, box)
                    if (isX) {
                      const xr = readProps(read, boxField(side, position, box))
                      return (
                        <div
                          key={key}
                          role="img"
                          {...xr.attrs}
                          tabIndex={xr.mark ? 0 : undefined}
                          onFocus={xr.onFocus}
                          aria-label={t('sheet.boxX', { team, box: boxName({ position, box }) })}
                          className={cx(
                            'flex h-11 items-center justify-center rounded-md border border-hairline bg-sunken font-mono text-lg font-bold text-muted sm:h-10',
                            readClass(xr.mark),
                          )}
                        >
                          X
                        </div>
                      )
                    }
                    const br = readProps(read, boxField(side, position, box))
                    const value = valueAt(set, side, position, box)
                    const isCircled = circled?.[0] === position && circled[1] === box
                    const mark = marks[key]
                    const other = suggest?.[key]
                    return (
                      <div key={key} className="relative" title={other?.title}>
                        <span aria-hidden="true" className="pointer-events-none absolute top-0.5 left-1 text-[9px] leading-none text-muted">
                          {box}
                        </span>
                        {other && (
                          <span
                            aria-hidden="true"
                            className="pointer-events-none absolute -top-2 -right-1.5 z-10 rounded-full bg-danger px-1.5 font-mono text-[11px] leading-4 font-bold text-white tabular-nums shadow-sm"
                          >
                            {other.value}
                          </span>
                        )}
                        {isCircled && (
                          <span aria-hidden="true" className="pointer-events-none absolute inset-1 rounded-full border-2 border-fg/70" />
                        )}
                        <input
                          {...br.attrs}
                          id={boxId(prefix, side, position, box)}
                          inputMode="numeric"
                          autoComplete="off"
                          aria-label={`${t('sheet.box', { team, box: boxName({ position, box }) })}${isCircled ? `, ${t('sheet.circled')}` : ''}${other ? `, ${other.title}` : ''}`}
                          aria-invalid={mark === 'error' || Boolean(other) || undefined}
                          value={value ?? ''}
                          disabled={readOnly}
                          onChange={onInput(position, box)}
                          onKeyDown={onKey(position, box)}
                          onFocus={(event) => {
                            event.currentTarget.select()
                            onFocusBox(side, position, box)
                            br.onFocus()
                          }}
                          className={cx(
                            'h-11 w-full min-w-0 rounded-md border bg-surface pt-1 text-center font-mono text-base font-semibold tabular-nums sm:h-10',
                            'focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring',
                            mark === 'error'
                              ? 'border-danger bg-danger/10 text-danger'
                              : other
                                ? 'border-2 border-danger bg-danger/5'
                                : br.mark
                                ? readClass(br.mark)
                                : mark === 'next'
                                  ? 'border-dashed border-accent'
                                  : mark === 'warning'
                                    ? 'border-accent'
                                    : 'border-hairline-strong',
                          )}
                        />
                      </div>
                    )
                  }),
                )}
              </div>
            </div>
          )
        })}
      </div>
      <Timeouts set={set} side={side} names={names} readOnly={readOnly} onChange={onChange} read={read} />
      <Subs set={set} side={side} names={names} readOnly={readOnly} onChange={onChange} read={read} />
    </div>
  )
}

export function ScorePair({
  own,
  opp,
  label,
  ownLabel,
  oppLabel,
  readOnly,
  onChange,
  read,
  field,
}: {
  own: number | null
  opp: number | null
  label: string
  ownLabel: string
  oppLabel: string
  readOnly: boolean
  onChange: (own: number | null, opp: number | null) => void
  read?: ReadView
  /** Its key among the fields the photo's reading proposed. */
  field?: string
}) {
  const r = field ? readProps(read, field) : null
  const input = cx(
    'h-11 w-12 rounded-control border border-hairline-strong bg-surface text-center font-mono tabular-nums focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-ring sm:h-9',
    r && readClass(r.mark),
  )
  return (
    <span role="group" aria-label={label} className="inline-flex items-center gap-1" {...(r?.attrs ?? {})} onFocus={r?.onFocus}>
      <input
        inputMode="numeric"
        autoComplete="off"
        aria-label={`${label}: ${ownLabel}`}
        value={own ?? ''}
        disabled={readOnly}
        className={input}
        onChange={(e) => onChange(parseDigits(e.target.value), opp)}
      />
      <span aria-hidden="true" className="font-bold text-muted">
        :
      </span>
      <input
        inputMode="numeric"
        autoComplete="off"
        aria-label={`${label}: ${oppLabel}`}
        value={opp ?? ''}
        disabled={readOnly}
        className={input}
        onChange={(e) => onChange(own, parseDigits(e.target.value))}
      />
    </span>
  )
}

/** The team's two timeout boxes: the score when it asked, its own first. */
function Timeouts({
  set,
  side,
  names,
  readOnly,
  onChange,
  read,
}: {
  set: SheetSetDoc
  side: Side
  names: SideNames | null
  readOnly: boolean
  onChange: (set: SheetSetDoc) => void
  read?: ReadView
}) {
  const { t } = useTranslation()
  const team = sideLabel(t, side, names)
  const mine = set.timeouts.filter((x) => x.side === side)
  const others = set.timeouts.filter((x) => x.side !== side)
  const update = (k: number, own: number | null, opp: number | null) => {
    const slots: (TimeoutDoc | null)[] = [mine[0] ?? null, mine[1] ?? null]
    slots[k] = own === null && opp === null ? null : { side, own: own ?? 0, opp: opp ?? 0 }
    onChange({ ...set, timeouts: [...others, ...slots.filter((x): x is TimeoutDoc => x !== null)] })
  }
  return (
    <div className="flex flex-col gap-1.5">
      <h4 className="text-sm font-semibold">{t('sheet.timeouts')}</h4>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        {[0, 1].map((k) => (
          <span key={k} className="inline-flex items-center gap-2 text-sm">
            <span className="font-semibold text-muted">T{k + 1}</span>
            <ScorePair
              own={mine[k]?.own ?? null}
              opp={mine[k]?.opp ?? null}
              label={t('sheet.timeout', { n: k + 1 })}
              ownLabel={t('sheet.own', { team })}
              oppLabel={t('sheet.opp')}
              readOnly={readOnly}
              onChange={(own, opp) => update(k, own, opp)}
              read={read}
              field={timeoutKey(read?.cells, side, k, mine[k]?.own ?? null, mine[k]?.opp ?? null)}
            />
          </span>
        ))}
      </div>
      <p className="text-xs text-muted">{t('sheet.timeoutHint', { team })}</p>
    </div>
  )
}

/** Substitutions, under the starting player's column: who came in and when, when they went back. */
function Subs({
  set,
  side,
  names,
  readOnly,
  onChange,
  read,
}: {
  set: SheetSetDoc
  side: Side
  names: SideNames | null
  readOnly: boolean
  onChange: (set: SheetSetDoc) => void
  read?: ReadView
}) {
  const { t } = useTranslation()
  const id = useId()
  const team = sideLabel(t, side, names)
  const rows = set.subs.map((sub, k) => ({ sub, k })).filter(({ sub }) => sub.side === side)
  const put = (k: number, sub: SubDoc | null) => {
    const subs = set.subs.flatMap((s, i) => (i === k ? (sub ? [sub] : []) : [s]))
    onChange({ ...set, subs })
  }
  const pair = (value: [number, number] | null, own: number | null, opp: number | null): [number, number] | null =>
    own === null && opp === null ? null : [own ?? value?.[0] ?? 0, opp ?? value?.[1] ?? 0]
  return (
    <details className="group rounded-control border border-hairline" open={rows.length > 0}>
      <summary className="flex min-h-11 cursor-pointer items-center gap-2 px-3 text-sm font-semibold sm:min-h-9">
        {t('sheet.subs')}
        {rows.length > 0 && <span className="text-muted tabular-nums">({rows.length})</span>}
      </summary>
      <div className="flex flex-col gap-3 border-t border-hairline p-3">
        <p className="text-xs text-muted">{t('sheet.subsHint')}</p>
        {rows.map(({ sub, k }) => (
          <div key={k} role="group" aria-label={`${t('sheet.subs')} ${k + 1}`} className="flex flex-wrap items-end gap-x-4 gap-y-2 text-sm">
            <label className="flex flex-col gap-1">
              <span className="text-xs font-semibold text-muted">{t('sheet.sub.position')}</span>
              <select
                value={sub.position}
                disabled={readOnly}
                onChange={(e) => put(k, { ...sub, position: Number(e.target.value) })}
                className="h-11 rounded-control border border-hairline-strong bg-surface px-2 sm:h-9"
              >
                {POSITIONS.map((roman, i) => (
                  <option key={roman} value={i + 1}>
                    {roman}
                  </option>
                ))}
              </select>
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-xs font-semibold text-muted">{t('sheet.sub.number')}</span>
              <input
                {...readProps(read, subField(side, sub.position, 'number')).attrs}
                onFocus={readProps(read, subField(side, sub.position, 'number')).onFocus}
                inputMode="numeric"
                autoComplete="off"
                value={sub.number ?? ''}
                disabled={readOnly}
                onChange={(e) => put(k, { ...sub, number: parseDigits(e.target.value) })}
                className={cx(
                  'h-11 w-16 rounded-control border border-hairline-strong bg-surface text-center font-mono tabular-nums sm:h-9',
                  readClass(readProps(read, subField(side, sub.position, 'number')).mark),
                )}
              />
            </label>
            <span className="flex flex-col gap-1">
              <span className="text-xs font-semibold text-muted">{t('sheet.sub.at')}</span>
              <ScorePair
                own={sub.at?.[0] ?? null}
                opp={sub.at?.[1] ?? null}
                label={t('sheet.sub.at')}
                ownLabel={t('sheet.own', { team })}
                oppLabel={t('sheet.opp')}
                readOnly={readOnly}
                onChange={(own, opp) => put(k, { ...sub, at: pair(sub.at, own, opp) })}
                read={read}
                field={subField(side, sub.position, 'at')}
              />
            </span>
            <span className="flex flex-col gap-1">
              <span className="text-xs font-semibold text-muted">{t('sheet.sub.back')}</span>
              <ScorePair
                own={sub.back?.[0] ?? null}
                opp={sub.back?.[1] ?? null}
                label={t('sheet.sub.back')}
                ownLabel={t('sheet.own', { team })}
                oppLabel={t('sheet.opp')}
                readOnly={readOnly}
                onChange={(own, opp) => put(k, { ...sub, back: pair(sub.back, own, opp) })}
                read={read}
                field={subField(side, sub.position, 'back')}
              />
            </span>
            {!readOnly && (
              <Button variant="ghost" size="icon" aria-label={t('sheet.sub.remove')} title={t('sheet.sub.remove')} onClick={() => put(k, null)}>
                <TrashIcon className="size-4" />
              </Button>
            )}
          </div>
        ))}
        {!readOnly && (
          <div>
            <Button
              id={`${id}-add`}
              variant="ghost"
              onClick={() => onChange({ ...set, subs: [...set.subs, { side, position: 1, number: null, at: null, back: null }] })}
            >
              <PlusIcon className="size-4" />
              {t('sheet.sub.add')}
            </Button>
          </div>
        )}
      </div>
    </details>
  )
}
