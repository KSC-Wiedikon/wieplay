import '../i18n/pages'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { cx } from '../lib/cx'
import { scoreNoteText } from '../lib/labels'
import type { Segment } from '../lib/types'
import { AlertIcon } from './icons'

/**
 * A value from Nomal rather than a coach (who served, who won, the score): muted and italic,
 * "suggested" on hover and for screen readers. A fact shows as it is.
 */
export function SuggestedValue({ suggested, children, className }: { suggested: boolean; children: ReactNode; className?: string }) {
  const { t } = useTranslation()
  if (!suggested) return <span className={className}>{children}</span>
  return (
    <span data-suggested="true" title={t('tagging.scoring.suggestedTitle')} className={cx('text-muted italic', className)}>
      {children}{' '}
      <span className="sr-only">{`(${t('tagging.scoring.suggested')})`}</span>
    </span>
  )
}

/** "Check": the automatic score disagrees with something at this rally; the reason on hover. */
export function CheckMark({ rally, className }: { rally: Pick<Segment, 'score_conflict' | 'score_note'>; className?: string }) {
  const { t } = useTranslation()
  if (!rally.score_conflict) return null
  const reason = scoreNoteText(t, rally.score_note)
  return (
    <span data-check="true" title={reason} className={cx('inline-flex items-center gap-1 text-xs font-semibold whitespace-nowrap text-danger', className)}>
      <AlertIcon className="size-3.5" />
      {t('tagging.scoring.check')}
      <span className="sr-only">{`: ${reason}`}</span>
    </span>
  )
}
