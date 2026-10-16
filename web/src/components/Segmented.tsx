import type { MouseEvent, ReactNode } from 'react'
import { cx } from '../lib/cx'

export interface SegmentedOption<T extends string> {
  value: T
  label: ReactNode
  /** Accessible name when the label is not plain text. */
  ariaLabel?: string
}

interface SegmentedProps<T extends string> {
  /** Names the group, e.g. "Show". */
  label: string
  value: T
  options: readonly SegmentedOption<T>[]
  onChange: (value: T, event: MouseEvent<HTMLButtonElement>) => void
  className?: string
}

/** A row of mutually exclusive buttons (aria-pressed): a mode, a view or a filter. */
export function Segmented<T extends string>({ label, value, options, onChange, className }: SegmentedProps<T>) {
  return (
    <div role="group" aria-label={label} className={cx('inline-flex flex-wrap gap-1 rounded-control bg-sunken p-1 ring-1 ring-hairline ring-inset', className)}>
      {options.map((option) => {
        const on = option.value === value
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={on}
            aria-label={option.ariaLabel}
            onClick={(event) => onChange(option.value, event)}
            className={cx(
              'inline-flex min-h-11 cursor-pointer items-center justify-center gap-1.5 rounded-[calc(var(--radius-control)-0.25rem)] px-3 text-sm font-semibold whitespace-nowrap sm:min-h-8',
              'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
              on ? 'bg-surface text-fg shadow-card ring-1 ring-hairline' : 'text-muted hover:bg-hover hover:text-fg',
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
