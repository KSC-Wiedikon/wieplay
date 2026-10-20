import '../i18n/pages'
import { useTranslation } from 'react-i18next'
import { cx } from '../lib/cx'
import { formatPercent, isAuto, isReviewedProposal, isToCheck } from '../lib/suggestions'
import type { Segment } from '../lib/types'
import { AlertIcon, SparkleIcon } from './icons'

/**
 * Where a rally (or a break's label) comes from, when WiePlay tagged it: "To check · 62%" while
 * WiePlay is unsure and nobody has checked it (a person's rally whose winner is unsure too); a
 * small WiePlay mark on a sure one, and once a person accepted or corrected it (it counts like
 * any other either way). A part split off in this review session waits for an answer too.
 */
export function SuggestionChip({ segment, splitOff = false, className }: { segment: Segment; splitOff?: boolean; className?: string }) {
  const { t } = useTranslation()
  if (splitOff || isToCheck(segment)) {
    const confidence = formatPercent(segment.kind === 'rally' ? (segment.score_confidence ?? segment.confidence) : segment.confidence)
    return (
      <span
        data-suggestion="check"
        className={cx(
          'inline-flex items-center gap-1 rounded-full border border-accent bg-accent-soft px-2 py-0.5 text-xs leading-4 font-semibold whitespace-nowrap text-on-accent-soft',
          className,
        )}
      >
        {splitOff ? <SparkleIcon className="size-3.5" /> : <AlertIcon className="size-3.5" />}
        {splitOff
          ? t('tagging.suggestions.splitOff')
          : confidence
            ? t('tagging.suggestions.toCheckConfidence', { confidence })
            : t('tagging.suggestions.toCheck')}
      </span>
    )
  }
  if (isAuto(segment)) {
    const full = t('tagging.suggestions.auto')
    return (
      <span data-suggestion="auto" title={full} className={cx('inline-flex items-center gap-1 text-xs whitespace-nowrap text-muted', className)}>
        <SparkleIcon className="size-3.5" />
        <span className="sr-only">{full}</span>
      </span>
    )
  }
  if (isReviewedProposal(segment)) {
    const corrected = segment.review === 'corrected'
    const full = corrected ? t('tagging.suggestions.corrected') : t('tagging.suggestions.confirmed')
    return (
      <span data-suggestion={segment.review} title={full} className={cx('inline-flex items-center gap-1 text-xs whitespace-nowrap text-muted', className)}>
        <SparkleIcon className="size-3.5" />
        <span aria-hidden="true">{corrected ? t('tagging.suggestions.correctedShort') : t('tagging.suggestions.confirmedShort')}</span>
        <span className="sr-only">{full}</span>
      </span>
    )
  }
  return null
}
