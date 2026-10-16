import type { ComponentProps } from 'react'
import { cx } from '../lib/cx'

/** A panel on the page: hairline border, a soft shadow and the card radius. */
export function Card({ className, ...props }: ComponentProps<'div'>) {
  return <div className={cx('rounded-card border border-hairline bg-surface shadow-card', className)} {...props} />
}
