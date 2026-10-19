import '../i18n/pages'
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { cx } from '../lib/cx'
import { firstReasonText } from '../lib/labels'
import { useSideNames } from '../lib/sides'
import type { Segment } from '../lib/types'
import { AlertIcon } from './icons'
import { Th } from './Table'

/**
 * A value from WiePlay rather than a coach (who served, who won, the score): muted and italic,
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

/**
 * "Check": the automatic score is not sure of this rally or disagrees with something here
 * (docs/PLAN.md §10.3); the first reason on hover.
 */
export function CheckMark({
  rally,
  className,
}: {
  rally: Pick<Segment, 'score_conflict' | 'score_note' | 'score_reasons'>
  className?: string
}) {
  const { t } = useTranslation()
  const names = useSideNames()
  if (!rally.score_conflict) return null
  const reason = firstReasonText(t, rally, names)
  return (
    <span data-check="true" title={reason} className={cx('inline-flex items-center gap-1 text-xs font-semibold whitespace-nowrap text-danger', className)}>
      <AlertIcon className="size-3.5" />
      {t('tagging.scoring.check')}
      <span className="sr-only">{`: ${reason}`}</span>
    </span>
  )
}

/** The score column's head: the teams in the score's order ("KSCW H3 – Volero"), else "Score". */
export function ScoreHead({ className }: { className?: string }) {
  const { t } = useTranslation()
  const names = useSideNames()
  if (!names) return <Th className={className}>{t('tagging.condensed.score')}</Th>
  return (
    // Two short lines rather than one wide column: the scores below are short.
    <Th className={cx('min-w-24 leading-4 whitespace-normal!', className)} title={`${names.usFull} – ${names.themFull}`}>
      <span className="sr-only">{`${t('tagging.condensed.score')}:`}</span> {`${names.us} – ${names.them}`}
    </Th>
  )
}
