import { useEffect, useId, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/Button'
import { AlertIcon, CheckIcon, CircleIcon, PlusIcon, TrashIcon } from '../../components/icons'
import { Segmented } from '../../components/Segmented'
import { Switch } from '../../components/Switch'
import { cx } from '../../lib/cx'
import { sideLabel } from '../../lib/labels'
import {
  applyRepair,
  boxName,
  decode,
  firstIssue,
  isDeciding,
  normalised,
  SANCTION_KINDS,
  type Decoded,
  type SanctionKind,
  type SetCheckOut,
  type SheetSetDoc,
} from '../../lib/scoresheet'
import type { SheetPhoto } from '../../lib/sheetPhotos'
import { altsText, canConfirm, markOf, readClass, remaining, sureFields, type ReadCell, type ReadSet } from '../../lib/sheetRead'
import type { SideNames } from '../../lib/sides'
import type { Side } from '../../lib/types'
import { boxKey, focusBox, focusField, parseDigits, type BoxMark } from './boxes'
import { ScorePair, TeamBoxes, type ReadView } from './ServiceGrid'
import { issueText, repairText } from './text'

interface SetPanelProps {
  set: SheetSetDoc
  names: SideNames | null
  official: [number, number] | null
  /** The server's checks of this set as typed now (its repair), when they are current. */
  server: SetCheckOut | null
  /** The saved sheet's set: how the newest scoring used it. */
  saved: SetCheckOut | null
  readOnly: boolean
  onChange: (set: SheetSetDoc) => void
  onRemove: () => void
  /** SS4: this set is a draft read from the photo; what the coach looked at; the actions. */
  draft?: ReadSet
  accepted?: ReadonlySet<string>
  onAccept?: (keys: string[]) => void
  onReadFocus?: (key: string) => void
  onConfirm?: () => void
  confirming?: boolean
  /** Go to this field (the page's "next cell to check"); `seq` changes on every ask. */
  focusAsk?: { key: string; seq: number } | null
  /** The photos (for whoever may see them): a doubtful cell shows its crop. */
  photos?: SheetPhoto[] | null
}

/** The live check's marks on the boxes: the one a check doubts, the next one to type. */
function marksOf(decoded: Decoded): Record<string, BoxMark> {
  const out: Record<string, BoxMark> = {}
  for (const issue of decoded.issues) {
    if (!issue.box) continue
    const key = boxKey(issue.box.side, issue.box.position, issue.box.box)
    const mark: BoxMark = issue.severity === 'error' ? 'error' : issue.code === 'missing' || issue.code === 'final_missing' ? 'next' : 'warning'
    if (out[key] !== 'error') out[key] = mark
  }
  return out
}

/**
 * One set of the sheet: who served first, the times, the official result; both teams' lineups
 * and service boxes; the live check (the score as it grows, the first box that doesn't fit, the
 * server's suggested repair); sanctions and cancelled points; the points it decodes to.
 */
export function SetPanel({
  set,
  names,
  official,
  server,
  saved,
  readOnly,
  onChange,
  onRemove,
  draft,
  accepted = new Set<string>(),
  onAccept,
  onReadFocus,
  onConfirm,
  confirming = false,
  focusAsk = null,
  photos = null,
}: SetPanelProps) {
  const { t } = useTranslation()
  const prefix = useId().replace(/:/g, '')
  const deciding = isDeciding(set)
  const decoded = useMemo(() => decode(set, official), [set, official])
  const marks = useMemo(() => marksOf(decoded), [decoded])
  const [phoneSide, setPhoneSide] = useState<Side>('us')
  const [focused, setFocused] = useState<{ side: Side; position: number; box: number } | null>(null)
  const team = (side: Side) => sideLabel(t, side, names)
  const [readFocus, setReadFocus] = useState<string | null>(null)
  const read: ReadView | undefined = draft
    ? {
        cells: draft.cells,
        accepted,
        onFocus: (key) => {
          setReadFocus(key)
          onReadFocus?.(key)
        },
      }
    : undefined
  const timeMark = (key: 'start' | 'end') => markOf(draft?.cells, accepted, key)
  /** Focus a field; on a phone the team whose grid holds it is shown first. */
  const goTo = (key: string) => {
    const side = key.split('.')[1]
    if (side === 'us' || side === 'them') setPhoneSide(side)
    window.setTimeout(() => focusField(set.no, key), 0)
  }
  const [askSeen, setAskSeen] = useState<number | null>(null)
  if (focusAsk && focusAsk.seq !== askSeen) {
    setAskSeen(focusAsk.seq)
    const side = focusAsk.key.split('.')[1]
    if (side === 'us' || side === 'them') setPhoneSide(side)
  }
  useEffect(() => {
    if (focusAsk) window.setTimeout(() => focusField(set.no, focusAsk.key), 0)
  }, [focusAsk, set.no])

  const jumpTo = (side: Side, position: number, box: number) => {
    setPhoneSide(side)
    // The other team's grid may be hidden on a phone until the state above shows it.
    window.setTimeout(() => focusBox(prefix, side, position, box), 0)
  }
  /** Enter on an empty box: the other team's first empty box in its order. */
  const toOther = (from: Side) => () => {
    const side: Side = from === 'us' ? 'them' : 'us'
    const next = decoded.issues.find((i) => i.code === 'missing' && i.box?.side === side)?.box
    if (next) jumpTo(side, next.position, next.box)
    else jumpTo(side, side === set.first_serve ? 1 : 2, 1)
  }

  const setFirst = (first: Side) => onChange({ ...set, first_serve: first })
  const issue = firstIssue(decoded.issues)
  const warnings = decoded.issues.filter((i) => i !== issue && i.code !== 'empty')
  const repair = server?.repair ?? null
  const noRepair = server !== null && server.status === 'inconsistent' && !repair && decoded.issues.some((i) => i.severity === 'error')
  const score = `${decoded.final[0]}:${decoded.final[1]}`

  return (
    <div className="flex flex-col gap-6" data-set={set.no}>
      <div className="flex flex-wrap items-end gap-x-6 gap-y-4">
        <div className="flex flex-col gap-1.5">
          <span className="text-sm font-semibold" id={`${prefix}-first`}>
            {t('sheet.firstServe')}
          </span>
          <Segmented<Side | 'none'>
            label={t('sheet.firstServe')}
            value={set.first_serve ?? 'none'}
            options={[
              { value: 'us', label: team('us') },
              { value: 'them', label: team('them') },
            ]}
            onChange={(value) => !readOnly && value !== 'none' && setFirst(value)}
          />
          <span className="text-xs text-muted">{t('sheet.firstServeHint')}</span>
        </div>
        <label className="flex flex-col gap-1.5 text-sm font-semibold">
          {t('sheet.start')}
          <input
            type="time"
            data-field="start"
            value={set.start ?? ''}
            disabled={readOnly}
            onFocus={() => read?.onFocus('start')}
            onChange={(e) => onChange({ ...set, start: e.target.value || null })}
            className={cx('h-11 rounded-control border border-hairline-strong bg-surface px-2 font-normal tabular-nums sm:h-9', readClass(timeMark('start')))}
          />
        </label>
        <label className="flex flex-col gap-1.5 text-sm font-semibold">
          {t('sheet.end')}
          <input
            type="time"
            data-field="end"
            value={set.end ?? ''}
            disabled={readOnly}
            onFocus={() => read?.onFocus('end')}
            onChange={(e) => onChange({ ...set, end: e.target.value || null })}
            className={cx('h-11 rounded-control border border-hairline-strong bg-surface px-2 font-normal tabular-nums sm:h-9', readClass(timeMark('end')))}
          />
        </label>
        <div className="flex flex-col gap-1.5">
          <span className="text-sm font-semibold">{t('sheet.official')}</span>
          <span className="flex min-h-11 items-center font-mono text-lg font-bold tabular-nums sm:min-h-9">
            {official ? `${official[0]}:${official[1]}` : <span className="font-sans text-sm font-normal text-muted">{t('sheet.officialNone')}</span>}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Switch checked={set.use} disabled={readOnly} label={t('sheet.useSet')} describedBy={`${prefix}-use`} onChange={(use) => onChange({ ...set, use })} />
          <span className="flex flex-col">
            <span className="text-sm font-semibold">{t('sheet.useSet')}</span>
            <span id={`${prefix}-use`} className="text-xs text-muted">
              {t('sheet.useSetHint')}
            </span>
          </span>
        </div>
        {set.no !== 5 && (
          <label className="flex min-h-11 items-center gap-2 text-sm sm:min-h-9">
            <input
              type="checkbox"
              checked={deciding}
              disabled={readOnly}
              onChange={(e) => onChange(normalised({ ...set, deciding: e.target.checked }))}
              className="size-4 accent-current"
            />
            {t('sheet.deciding')}
          </label>
        )}
      </div>

      {draft && (
        <DraftBar
          no={set.no}
          draft={draft}
          accepted={accepted}
          readOnly={readOnly}
          busy={confirming}
          onAcceptSure={() => onAccept?.(sureFields(draft.cells))}
          onConfirm={() => onConfirm?.()}
        />
      )}

      <CheckLine
        decoded={decoded}
        official={official}
        score={score}
        issueTextValue={issue ? issueText(t, issue, names, deciding) : null}
        severity={issue?.severity ?? null}
        canGo={Boolean(issue?.box)}
        onGo={() => issue?.box && jumpTo(issue.box.side, issue.box.position, issue.box.box)}
        warnings={warnings.map((w) => issueText(t, w, names, deciding))}
        repair={repair && !readOnly ? repairText(t, repair, names) : null}
        onRepair={() => repair && onChange(applyRepair(set, repair))}
        noRepair={noRepair}
        names={names}
      />

      <div className="flex flex-col gap-2">
        <h3 className="text-sm font-semibold">{t('sheet.boxes')}</h3>
        <p className="max-w-4xl text-xs text-muted">{t('sheet.boxesHint')}</p>
      </div>
      <div className="sm:hidden">
        <Segmented<Side>
          label={t('sheet.phoneTeam')}
          value={phoneSide}
          options={[
            { value: 'us', label: team('us') },
            { value: 'them', label: team('them') },
          ]}
          onChange={setPhoneSide}
        />
      </div>
      <div className="grid gap-8 @min-[70rem]:grid-cols-2">
        {(['us', 'them'] as const).map((side) => (
          <div key={side} className={cx(side !== phoneSide && 'hidden sm:block')}>
            <TeamBoxes
              set={set}
              side={side}
              names={names}
              prefix={prefix}
              marks={marks}
              readOnly={readOnly}
              onChange={onChange}
              onOther={toOther(side)}
              onFocusBox={(s, position, box) => setFocused({ side: s, position, box })}
              read={read}
            />
          </div>
        ))}
      </div>
      {!readOnly && focused && (
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <Button
            variant="secondary"
            onClick={() => {
              const key = focused.side === 'us' ? 'circled_us' : 'circled_them'
              const now = set[key]
              const on = now?.[0] === focused.position && now[1] === focused.box
              onChange({ ...set, [key]: on ? null : [focused.position, focused.box] })
              focusBox(prefix, focused.side, focused.position, focused.box)
            }}
          >
            <CircleIcon className="size-4" />
            {`${t('sheet.circle')}: ${team(focused.side)} ${boxName(focused)}`}
          </Button>
          <span className="text-xs text-muted">{t('sheet.circleHint')}</span>
        </div>
      )}

      {draft && readFocus && !readOnly && markOf(draft.cells, accepted, readFocus) && (
        <div role="status" className="flex flex-wrap items-center gap-2 rounded-xl border border-hairline bg-sunken p-2 text-sm">
          <CellCrop cell={draft.cells?.[readFocus]} photos={photos} />
          <span>
            {t('sheet.read.cell.read', { alts: altsText(draft.cells?.[readFocus]) })}
            {draft.cells?.[readFocus]?.why ? ` · ${t(`sheet.read.cell.why.${draft.cells[readFocus].why ?? 'unsure'}`)}` : ''}
          </span>
          <Button
            variant="secondary"
            onClick={() => {
              onAccept?.([readFocus])
              const next = remaining(draft.cells, new Set([...accepted, readFocus])).check[0]
              if (next) goTo(next)
            }}
          >
            <CheckIcon className="size-4" />
            {t('sheet.read.cell.right')}
          </Button>
        </div>
      )}

      <Special set={set} names={names} readOnly={readOnly} onChange={onChange} />
      <PointsStrip decoded={decoded} names={names} />
      {saved && <ScoringLine saved={saved} />}
      {!readOnly && (
        <div>
          <Button variant="ghostDanger" onClick={onRemove}>
            <TrashIcon className="size-4" />
            {t('sheet.removeSet', { no: set.no })}
          </Button>
        </div>
      )}
    </div>
  )
}

function CheckLine({
  decoded,
  official,
  score,
  issueTextValue,
  severity,
  canGo,
  onGo,
  warnings,
  repair,
  onRepair,
  noRepair,
  names,
}: {
  decoded: Decoded
  official: [number, number] | null
  score: string
  issueTextValue: string | null
  severity: string | null
  canGo: boolean
  onGo: () => void
  warnings: string[]
  repair: string | null
  onRepair: () => void
  noRepair: boolean
  names: SideNames | null
}) {
  const { t } = useTranslation()
  const empty = decoded.issues.some((i) => i.code === 'empty')
  const sofar = decoded.last
    ? t('sheet.check.sofar', { score, team: sideLabel(t, decoded.last.side, names), box: boxName(decoded.last) })
    : null
  const ok = decoded.consistent
  return (
    <section
      aria-label={t('sheet.check.title')}
      className={cx(
        'flex flex-col gap-2 rounded-xl border p-3',
        severity === 'error' ? 'border-danger bg-danger/5' : ok ? 'border-accent bg-accent-soft/40' : 'border-hairline bg-sunken',
      )}
    >
      <div role="status" aria-live="polite" className="flex items-start gap-2">
        {ok ? (
          <CheckIcon className="mt-0.5 size-5 shrink-0 text-on-accent-soft" />
        ) : severity === 'error' ? (
          <AlertIcon className="mt-0.5 size-5 shrink-0 text-danger" />
        ) : (
          <CircleIcon className="mt-0.5 size-5 shrink-0 text-muted" />
        )}
        <div className="flex min-w-0 flex-col gap-0.5">
          {ok ? (
            <p className="font-semibold">{t(official ? 'sheet.check.fits' : 'sheet.check.fitsNoOfficial', { score })}</p>
          ) : empty ? (
            <p>{t('sheet.check.empty')}</p>
          ) : (
            <>
              {sofar && <p className="font-semibold tabular-nums">{sofar}</p>}
              {issueTextValue && <p className={cx(severity === 'error' && 'font-semibold text-danger')}>{issueTextValue}</p>}
            </>
          )}
        </div>
        {!ok && canGo && (
          <Button variant="ghost" className="ml-auto" onClick={onGo}>
            {t('sheet.check.goTo')}
          </Button>
        )}
      </div>
      {repair && (
        <div className="flex flex-wrap items-center gap-2 border-t border-hairline pt-2 text-sm">
          <span>{repair}</span>
          <Button variant="primary" onClick={onRepair}>
            {t('sheet.check.apply')}
          </Button>
        </div>
      )}
      {noRepair && <p className="border-t border-hairline pt-2 text-sm text-muted">{t('sheet.check.noRepair')}</p>}
      {warnings.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-muted">{t('sheet.check.more', { count: warnings.length })}</summary>
          <ul className="mt-1 list-disc pl-5">
            {warnings.map((w, k) => (
              <li key={k}>{w}</li>
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}

/** Sanctions (a penalty is a point without a rally) and points cancelled after a rotation fault. */
function Special({ set, names, readOnly, onChange }: { set: SheetSetDoc; names: SideNames | null; readOnly: boolean; onChange: (set: SheetSetDoc) => void }) {
  const { t } = useTranslation()
  const count = set.sanctions.length + set.cancelled.length
  const select = 'h-11 rounded-control border border-hairline-strong bg-surface px-2 sm:h-9'
  return (
    <details className="rounded-control border border-hairline" open={count > 0}>
      <summary className="flex min-h-11 cursor-pointer items-center gap-2 px-3 text-sm font-semibold sm:min-h-9">
        {t('sheet.special.title')}
        {count > 0 && <span className="text-muted tabular-nums">({count})</span>}
      </summary>
      <div className="flex flex-col gap-4 border-t border-hairline p-3 text-sm">
        <p className="text-xs text-muted">{t('sheet.special.hint')}</p>
        <div className="flex flex-col gap-2">
          <h4 className="font-semibold">{t('sheet.special.sanctions')}</h4>
          {set.sanctions.map((x, k) => (
            <div key={k} className="flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1">
                <span className="text-xs font-semibold text-muted">{t('sheet.special.team')}</span>
                <select
                  className={select}
                  value={x.side}
                  disabled={readOnly}
                  onChange={(e) => onChange({ ...set, sanctions: set.sanctions.map((y, i) => (i === k ? { ...y, side: e.target.value as Side } : y)) })}
                >
                  <option value="us">{sideLabel(t, 'us', names)}</option>
                  <option value="them">{sideLabel(t, 'them', names)}</option>
                </select>
              </label>
              <label className="flex flex-col gap-1">
                <span className="text-xs font-semibold text-muted">{t('sheet.special.kindLabel')}</span>
                <select
                  className={select}
                  value={x.kind}
                  disabled={readOnly}
                  onChange={(e) => onChange({ ...set, sanctions: set.sanctions.map((y, i) => (i === k ? { ...y, kind: e.target.value as SanctionKind } : y)) })}
                >
                  {SANCTION_KINDS.map((kind) => (
                    <option key={kind} value={kind}>
                      {t(`sheet.special.kind.${kind}`)}
                    </option>
                  ))}
                </select>
              </label>
              <span className="flex flex-col gap-1">
                <span className="text-xs font-semibold text-muted">{t('sheet.special.score')}</span>
                <ScorePair
                  own={x.own}
                  opp={x.opp}
                  label={t('sheet.special.score')}
                  ownLabel={sideLabel(t, x.side, names)}
                  oppLabel={t('sheet.opp')}
                  readOnly={readOnly}
                  onChange={(own, opp) =>
                    onChange({ ...set, sanctions: set.sanctions.map((y, i) => (i === k ? { ...y, own: own ?? 0, opp: opp ?? 0 } : y)) })
                  }
                />
              </span>
              {!readOnly && (
                <Button variant="ghost" size="icon" aria-label={t('sheet.special.remove')} title={t('sheet.special.remove')}
                  onClick={() => onChange({ ...set, sanctions: set.sanctions.filter((_, i) => i !== k) })}>
                  <TrashIcon className="size-4" />
                </Button>
              )}
            </div>
          ))}
          {!readOnly && (
            <div>
              <Button variant="ghost" onClick={() => onChange({ ...set, sanctions: [...set.sanctions, { side: 'them', kind: 'P', own: 0, opp: 0 }] })}>
                <PlusIcon className="size-4" />
                {t('sheet.special.addSanction')}
              </Button>
            </div>
          )}
        </div>
        <div className="flex flex-col gap-2">
          <h4 className="font-semibold">{t('sheet.special.cancelled')}</h4>
          {set.cancelled.map((c, k) => (
            <div key={k} className="flex flex-wrap items-end gap-3">
              <span className="flex flex-col gap-1">
                <span className="text-xs font-semibold text-muted">{t('sheet.special.after')}</span>
                <ScorePair
                  own={c.us}
                  opp={c.them}
                  label={t('sheet.special.after')}
                  ownLabel={sideLabel(t, 'us', names)}
                  oppLabel={sideLabel(t, 'them', names)}
                  readOnly={readOnly}
                  onChange={(us, them) =>
                    onChange({ ...set, cancelled: set.cancelled.map((y, i) => (i === k ? { ...y, us: us ?? 0, them: them ?? 0 } : y)) })
                  }
                />
              </span>
              <label className="flex flex-col gap-1">
                <span className="text-xs font-semibold text-muted">{t('sheet.special.rallies')}</span>
                <input
                  inputMode="numeric"
                  autoComplete="off"
                  value={c.rallies}
                  disabled={readOnly}
                  onChange={(e) =>
                    onChange({ ...set, cancelled: set.cancelled.map((y, i) => (i === k ? { ...y, rallies: Math.max(1, parseDigits(e.target.value) ?? 1) } : y)) })
                  }
                  className="h-11 w-16 rounded-control border border-hairline-strong bg-surface text-center font-mono tabular-nums sm:h-9"
                />
              </label>
              {!readOnly && (
                <Button variant="ghost" size="icon" aria-label={t('sheet.special.remove')} title={t('sheet.special.remove')}
                  onClick={() => onChange({ ...set, cancelled: set.cancelled.filter((_, i) => i !== k) })}>
                  <TrashIcon className="size-4" />
                </Button>
              )}
            </div>
          ))}
          {!readOnly && (
            <div>
              <Button variant="ghost" onClick={() => onChange({ ...set, cancelled: [...set.cancelled, { us: 0, them: 0, rallies: 1 }] })}>
                <PlusIcon className="size-4" />
                {t('sheet.special.addCancelled')}
              </Button>
            </div>
          )}
        </div>
      </div>
    </details>
  )
}

/** Every point the boxes give, in order: ours filled, theirs outlined, a penalty ringed. */
function PointsStrip({ decoded, names }: { decoded: Decoded; names: SideNames | null }) {
  const { t } = useTranslation()
  if (decoded.points.length === 0) return null
  return (
    <section className="flex flex-col gap-2">
      <h3 className="text-sm font-semibold">{t('sheet.points.title')}</h3>
      <p className="text-xs text-muted">{t('sheet.points.hint', { us: sideLabel(t, 'us', names) })}</p>
      <ol className="flex flex-wrap gap-1" aria-label={t('sheet.points.title')}>
        {decoded.points.map((p, k) => {
          const label = `${t('sheet.points.point', { n: k + 1, team: sideLabel(t, p.winner, names), score: `${p.us}:${p.them}` })}${p.penalty ? ` (${t('sheet.points.penalty')})` : ''}`
          return (
            <li
              key={k}
              title={label}
              aria-label={label}
              className={cx(
                'size-3.5 rounded-sm border',
                p.winner === 'us' ? 'border-accent bg-accent' : 'border-hairline-strong bg-surface',
                p.penalty && 'ring-2 ring-danger ring-offset-1 ring-offset-surface',
              )}
            />
          )
        })}
      </ol>
    </section>
  )
}

/** How the newest scoring used the saved set. */
function ScoringLine({ saved }: { saved: SetCheckOut }) {
  const { t } = useTranslation()
  const m = saved.scoring
  const n = (key: string) => (typeof m?.[key] === 'number' ? (m[key] as number) : 0)
  let lines: string[]
  if (saved.use === null) lines = [t('sheet.scoring.off')]
  else if (saved.use === 'hint') lines = [t('sheet.scoring.hint')]
  else if (!m || m.aligned === null || m.aligned === undefined) lines = [t('sheet.scoring.notYet')]
  else {
    lines = [t('sheet.scoring.path', { aligned: n('aligned'), points: n('points') })]
    if (n('missed')) lines.push(t('sheet.scoring.missed', { count: n('missed') }))
    if (n('penalties')) lines.push(t('sheet.scoring.penalties', { count: n('penalties') }))
    if (n('extra')) lines.push(t('sheet.scoring.extra', { count: n('extra') }))
  }
  return (
    <section className="flex flex-col gap-1 text-sm">
      <h3 className="font-semibold">{t('sheet.scoring.title')}</h3>
      {lines.map((line) => (
        <p key={line} className="text-muted">
          {line}
        </p>
      ))}
    </section>
  )
}

/** A set read from the photo: how many cells are left, accept the sure ones, confirm it. */
function DraftBar({
  no,
  draft,
  accepted,
  readOnly,
  busy,
  onAcceptSure,
  onConfirm,
}: {
  no: number
  draft: ReadSet
  accepted: ReadonlySet<string>
  readOnly: boolean
  busy: boolean
  onAcceptSure: () => void
  onConfirm: () => void
}) {
  const { t } = useTranslation()
  const left = remaining(draft.cells, accepted)
  const ready = canConfirm(draft.cells, accepted)
  return (
    <section aria-label={t('sheet.read.title')} className="flex flex-col gap-2 rounded-xl border-2 border-vb-yellow bg-vb-yellow/10 p-3 text-sm">
      <p className="font-semibold">
        {left.check.length > 0
          ? t('sheet.read.set.draft', { no, count: left.check.length })
          : ready
            ? t('sheet.read.set.ready', { no })
            : t('sheet.read.set.sure', { no, count: left.sure.length })}
      </p>
      {draft.decoded === false && <p>{t('sheet.read.set.notDecoded')}</p>}
      <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted">
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-block size-3.5 rounded border-2 border-vb-yellow bg-vb-yellow/20" />
          {t('sheet.read.legend.check')}
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden="true" className="inline-block size-3.5 rounded border border-dashed border-accent bg-accent-soft/40" />
          {t('sheet.read.legend.read')}
        </span>
      </p>
      {!readOnly && (
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="secondary" disabled={left.sure.length === 0} onClick={onAcceptSure}>
            <CheckIcon className="size-4" />
            {t('sheet.read.set.acceptSure', { count: left.sure.length })}
          </Button>
          <Button variant="primary" disabled={!ready || busy} onClick={onConfirm}>
            {busy ? t('sheet.saving') : t('sheet.read.set.confirm', { no })}
          </Button>
          <span className="text-xs text-muted">{t('sheet.read.set.confirmHint')}</span>
        </div>
      )}
    </section>
  )
}

/** The cell as it is on the photo: the part of the photo around its quad, enlarged. */
function CellCrop({ cell, photos }: { cell: ReadCell | undefined; photos: SheetPhoto[] | null }) {
  const { t } = useTranslation()
  const photo = photos?.find((p) => p.id === cell?.img)
  const q = cell?.quad
  if (!photo || !q || q.length !== 8) return null
  const xs = [q[0], q[2], q[4], q[6]] as number[]
  const ys = [q[1], q[3], q[5], q[7]] as number[]
  const pad = 0.004
  const x0 = Math.max(0, Math.min(...xs) - pad)
  const y0 = Math.max(0, Math.min(...ys) - pad)
  const x1 = Math.min(1, Math.max(...xs) + pad)
  const y1 = Math.min(1, Math.max(...ys) + pad)
  const W = 168
  const scale = W / Math.max(1, (x1 - x0) * photo.width)
  const H = Math.round((y1 - y0) * photo.height * scale)
  return (
    <span
      role="img"
      aria-label={t('sheet.read.cell.crop')}
      className="relative inline-block shrink-0 overflow-hidden rounded-md border border-hairline-strong bg-surface"
      style={{ width: W, height: Math.min(H, 120) }}
    >
      <img
        src={photo.url}
        alt=""
        referrerPolicy="no-referrer"
        draggable={false}
        className="absolute max-w-none select-none"
        style={{ width: photo.width * scale, height: photo.height * scale, left: -x0 * photo.width * scale, top: -y0 * photo.height * scale }}
      />
    </span>
  )
}
