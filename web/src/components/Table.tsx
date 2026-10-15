import type { ComponentProps, MouseEvent } from 'react'
import { cx } from '../lib/cx'

/** Lists of records are always tables. Put secondary columns on `hidden sm:table-cell`. */
export function Table({ caption, className, children, ...props }: ComponentProps<'table'> & { caption?: string }) {
  return (
    <div className="overflow-x-auto">
      <table className={cx('w-full border-collapse text-left text-sm', className)} {...props}>
        {caption && <caption className="sr-only">{caption}</caption>}
        {children}
      </table>
    </div>
  )
}

export function THead(props: ComponentProps<'thead'>) {
  return <thead {...props} />
}

export function TBody({ className, ...props }: ComponentProps<'tbody'>) {
  return <tbody className={cx('divide-y divide-hairline', className)} {...props} />
}

interface RowProps extends ComponentProps<'tr'> {
  /** Makes the whole row clickable; keep a real link in the row for keyboard users. */
  onActivate?: () => void
}

export function Tr({ onActivate, className, ...props }: RowProps) {
  const onClick = onActivate
    ? (event: MouseEvent<HTMLTableRowElement>) => {
        if (!(event.target as Element).closest('a, button')) onActivate()
      }
    : undefined
  return (
    <tr onClick={onClick} className={cx(onActivate && 'cursor-pointer hover:bg-sunken', className)} {...props} />
  )
}

export function Th({ className, ...props }: ComponentProps<'th'>) {
  return (
    <th
      scope="col"
      className={cx('border-b border-hairline px-3 py-2 text-xs font-semibold whitespace-nowrap text-muted', className)}
      {...props}
    />
  )
}

/** `tight` is for cells holding a 44px control, so the row doesn't grow. */
export function Td({ className, tight, ...props }: ComponentProps<'td'> & { tight?: boolean }) {
  return <td className={cx('px-3 align-top wrap-break-word', tight ? 'py-1' : 'py-3', className)} {...props} />
}
