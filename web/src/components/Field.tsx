import type { ComponentProps, ReactNode } from 'react'
import { cx } from '../lib/cx'

// Same height scale as Button: 44px on phones, 36px from sm up.
const control =
  'rounded-control border border-hairline-strong bg-surface px-3 text-sm text-fg focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring disabled:opacity-60'

/** `inline` sizes the control to its content instead of the full width. */
type Sizing = { inline?: boolean }

export function TextInput({ className, inline, ...props }: ComponentProps<'input'> & Sizing) {
  return <input className={cx(control, 'min-h-11 sm:min-h-9', !inline && 'w-full', className)} {...props} />
}

export function Select({ className, inline, ...props }: ComponentProps<'select'> & Sizing) {
  return <select className={cx(control, 'min-h-11 sm:min-h-9', !inline && 'w-full', className)} {...props} />
}

/** A labelled control with an optional hint and error below it. */
export function Field({
  label,
  hint,
  error,
  htmlFor,
  children,
  className,
}: {
  label: string
  hint?: string
  error?: string | null
  htmlFor: string
  children: ReactNode
  className?: string
}) {
  return (
    <div className={cx('flex flex-col gap-1.5', className)}>
      <label htmlFor={htmlFor} className="text-sm font-semibold">
        {label}
      </label>
      {children}
      {hint && <p className="text-xs text-muted">{hint}</p>}
      {error && (
        <p role="alert" className="text-xs text-danger">
          {error}
        </p>
      )}
    </div>
  )
}
