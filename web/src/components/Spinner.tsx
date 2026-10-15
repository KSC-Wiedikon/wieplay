import { useTranslation } from 'react-i18next'
import { cx } from '../lib/cx'

export function Spinner({ label, className }: { label?: string; className?: string }) {
  const { t } = useTranslation()
  return (
    <span role="status" className={cx('inline-flex items-center text-muted', className)}>
      <svg viewBox="0 0 24 24" aria-hidden="true" className="size-5 motion-safe:animate-spin">
        <circle cx="12" cy="12" r="9" fill="none" stroke="currentColor" strokeOpacity="0.25" strokeWidth="3" />
        <path d="M21 12a9 9 0 0 0-9-9" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
      </svg>
      <span className="sr-only">{label ?? t('common.loading')}</span>
    </span>
  )
}

export function PageSpinner() {
  return (
    <div className="grid min-h-48 place-items-center">
      <Spinner />
    </div>
  )
}
