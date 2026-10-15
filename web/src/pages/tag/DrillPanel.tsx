import '../../i18n/pages'
import { useId, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Button } from '../../components/Button'
import { TextInput } from '../../components/Field'
import { byStart } from '../../lib/clips'
import { formatClock } from '../../lib/labels'
import { useUi, useView, useWorkspace } from './context'

/** Trainings: mark a drill's start and end at the playhead, with its name; list the drills. */
export function DrillPanel() {
  const { t } = useTranslation()
  const id = useId()
  const { controller } = useWorkspace()
  const view = useView()
  const ui = useUi()
  const drills = useMemo(() => byStart(view.segments.filter((s) => s.kind === 'drill')), [view.segments])
  const [name, setName] = useState('')
  const suggested = t('drills.defaultName', { n: drills.length + 1 })

  return (
    <section aria-labelledby={`${id}-title`} className="flex flex-col gap-2 rounded-2xl border border-hairline bg-surface px-4 py-3">
      <h2 id={`${id}-title`} className="font-semibold">
        {t('drills.panel')}
      </h2>
      {ui.drillStart === null ? (
        <>
          <p className="text-xs text-muted">{t('drills.hint')}</p>
          <div>
            <Button onClick={controller.startDrill}>{t('drills.start')}</Button>
          </div>
        </>
      ) : (
        <form
          className="flex flex-col gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            if (controller.endDrill(name.trim() || suggested)) setName('')
          }}
        >
          <p className="text-sm tabular-nums">{t('drills.started', { time: formatClock(ui.drillStart) })}</p>
          <label htmlFor={`${id}-name`} className="text-sm font-semibold">
            {t('drills.name')}
          </label>
          <TextInput id={`${id}-name`} value={name} maxLength={80} placeholder={suggested} onChange={(e) => setName(e.target.value)} />
          <div className="flex flex-wrap gap-2">
            <Button type="submit" variant="primary">
              {t('drills.end')}
            </Button>
            <Button variant="ghost" onClick={controller.cancelDrill}>
              {t('common.cancel')}
            </Button>
          </div>
        </form>
      )}
      {drills.length === 0 ? (
        <p className="text-sm text-muted">{t('drills.none')}</p>
      ) : (
        <ul className="flex flex-col gap-0.5 text-sm">
          {drills.map((drill, index) => {
            const label = drill.label || t('drills.defaultName', { n: index + 1 })
            return (
              <li key={drill.id} className="flex items-center justify-between gap-2">
                <button
                  type="button"
                  onClick={() => {
                    controller.seek(drill.start_ms)
                    controller.selectSegment(drill.id)
                  }}
                  aria-label={t('drills.go', { label })}
                  className="min-h-9 cursor-pointer truncate rounded-lg px-1 text-left font-semibold text-link underline-offset-2 hover:underline"
                >
                  {label}
                </button>
                <span className="text-xs whitespace-nowrap text-muted tabular-nums">
                  {`${formatClock(drill.start_ms, { tenths: false })}–${formatClock(drill.end_ms, { tenths: false })}`}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
