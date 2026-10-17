import { useTranslation } from 'react-i18next'
import { Link } from 'react-router'
import { cx } from '../../lib/cx'

/**
 * "How to help": the guide for helpers (reviewing, keys, tagging basics). Opens in a new tab,
 * so the review keeps its place.
 */
export function GuideLink({ className }: { className?: string }) {
  const { t } = useTranslation()
  return (
    <Link
      to="/how-to-help"
      target="_blank"
      rel="noopener"
      title={t('guide.linkHint')}
      className={cx('link self-start text-sm font-semibold', className)}
    >
      {t('guide.link')}
    </Link>
  )
}
