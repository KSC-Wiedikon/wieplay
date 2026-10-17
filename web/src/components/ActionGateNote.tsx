import '../i18n/pages'
import { useTranslation } from 'react-i18next'
import { gateText } from '../lib/actions'
import { cx } from '../lib/cx'
import type { ActionGate } from '../lib/types'
import { SparkleIcon } from './icons'

/**
 * Why there are no suggested actions yet (PLAN §10.4a): WiePlay writes them only once its score on
 * games whose actions a coach reviewed passes the bar. Nothing while the bar is passed.
 */
export function ActionGateNote({ gate, className }: { gate: ActionGate | null | undefined; className?: string }) {
  const { t } = useTranslation()
  const text = gateText(t, gate)
  if (!text) return null
  return (
    <p data-action-gate className={cx('flex items-start gap-1.5 text-sm text-muted', className)}>
      <SparkleIcon className="mt-0.5 size-4 shrink-0" />
      <span>{text}</span>
    </p>
  )
}
