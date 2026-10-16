import type { ReactNode } from 'react'
import { cx } from '../lib/cx'

/** The top of a page: an optional line above the title, the title, a subtitle and the page's actions. */
export function PageHeader({
  title,
  subtitle,
  eyebrow,
  actions,
  className,
}: {
  title: ReactNode
  subtitle?: ReactNode
  eyebrow?: ReactNode
  actions?: ReactNode
  className?: string
}) {
  return (
    <div className={cx('flex flex-wrap items-end justify-between gap-x-6 gap-y-4', className)}>
      <div className="flex min-w-0 flex-col gap-1.5">
        {eyebrow && <div className="flex flex-wrap items-center gap-2 text-sm text-muted">{eyebrow}</div>}
        <h1 className="text-2xl font-bold tracking-tight wrap-break-word sm:text-3xl">{title}</h1>
        {subtitle && <div className="text-muted">{subtitle}</div>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  )
}

/** A section heading with an optional hint below it and actions on the right. */
export function SectionHeader({
  id,
  title,
  hint,
  action,
  as: Heading = 'h2',
}: {
  id?: string
  title: ReactNode
  hint?: ReactNode
  action?: ReactNode
  as?: 'h2' | 'h3'
}) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div className="flex min-w-0 flex-col gap-1">
        <Heading id={id} className={Heading === 'h2' ? 'text-lg font-semibold' : 'font-semibold'}>
          {title}
        </Heading>
        {hint && <p className="text-sm text-muted">{hint}</p>}
      </div>
      {action}
    </div>
  )
}
