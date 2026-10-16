import '../i18n/pages'
import { useTranslation } from 'react-i18next'
import { cx } from '../lib/cx'
import { formatPercent, isProposal, isReviewedProposal } from '../lib/suggestions'
import type { Segment } from '../lib/types'
import { SparkleIcon } from './icons'

/**
 * Where a rally (or a break's label) comes from, when Nomal suggested it: "Suggested · 87%"
 * with a dashed outline while nobody has answered it; a small marker once it is accepted or
 * corrected (it is a rally like any other then). A part split off in this review session waits
 * for an answer too.
 */
export function SuggestionChip({ segment, splitOff = false, className }: { segment: Segment; splitOff?: boolean; className?: string }) {
  const { t } = useTranslation()
  if (splitOff || isProposal(segment)) {
    const confidence = formatPercent(segment.confidence)
    return (
      <span
        data-suggestion="open"
        className={cx(
          'inline-flex items-center gap-1 rounded-full border border-dashed border-accent bg-accent-soft px-2 py-0.5 text-xs leading-4 font-semibold whitespace-nowrap text-on-accent-soft',
          className,
        )}
      >
        <SparkleIcon className="size-3.5" />
        {splitOff
          ? t('tagging.suggestions.splitOff')
          : confidence
            ? t('tagging.suggestions.suggestedConfidence', { confidence })
            : t('tagging.suggestions.suggested')}
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
