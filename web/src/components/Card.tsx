import type { ComponentProps } from 'react'
import { cx } from '../lib/cx'

export function Card({ className, ...props }: ComponentProps<'div'>) {
  return <div className={cx('rounded-2xl border border-hairline bg-surface', className)} {...props} />
}
