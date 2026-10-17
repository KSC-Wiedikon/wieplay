import '../i18n/pages'
import { useTranslation } from 'react-i18next'
import { cx } from '../lib/cx'
import { sideLabel } from '../lib/labels'
import type { SideNames } from '../lib/sides'
import { effectiveWinner } from '../lib/suggestions'
import type { Segment, Side } from '../lib/types'

interface WinnerPickerProps {
  rally: Segment
  /** The rally's number in the list (its accessible name). */
  n: number
  names: SideNames | null
  onPick: (won_by: Side | null) => void
  disabled?: boolean
  className?: string
}

/**
 * Who won a rally, for those who tag (the rally lists): the coach's call by the teams' names,
 * or none, which leaves Nomal's suggestion (shown muted in the empty choice). A fact the
 * scoring keeps to: the score line follows.
 */
export function WinnerPicker({ rally, n, names, onPick, disabled = false, className }: WinnerPickerProps) {
  const { t } = useTranslation()
  const winner = effectiveWinner(rally)
  const label = (side: Side) => sideLabel(t, side, names)
  const empty = winner.suggested && winner.value ? t('winner.nomal', { team: label(winner.value) }) : t('winner.none')
  return (
    <select
      aria-label={t('winner.label', { n })}
      value={rally.won_by ?? ''}
      disabled={disabled}
      onChange={(event) => onPick(event.target.value === '' ? null : (event.target.value as Side))}
      className={cx(
        'min-h-11 w-full max-w-40 min-w-24 cursor-pointer truncate rounded-control border border-hairline-strong bg-surface px-2 text-xs sm:min-h-8',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:cursor-not-allowed disabled:opacity-60',
        rally.won_by ? 'font-semibold text-fg' : 'text-muted italic',
        className,
      )}
    >
      <option value="">{empty}</option>
      <option value="us">{label('us')}</option>
      <option value="them">{label('them')}</option>
    </select>
  )
}
