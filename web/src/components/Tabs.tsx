import { useRef, type KeyboardEvent, type ReactNode } from 'react'
import { cx } from '../lib/cx'

export interface TabOption<T extends string> {
  value: T
  label: ReactNode
}

interface TabsProps<T extends string> {
  /** Names the tab list. */
  label: string
  /** Prefix of the tab and panel ids (`${id}-tab-${value}`, `${id}-panel`). */
  id: string
  value: T
  options: readonly TabOption<T>[]
  onChange: (value: T) => void
  className?: string
}

/**
 * Tabs over one panel (WAI-ARIA tabs, automatic activation): the arrow keys, Home and End move
 * between them. Their panel is a TabPanel with the same id and value.
 */
export function Tabs<T extends string>({ label, id, value, options, onChange, className }: TabsProps<T>) {
  const list = useRef<HTMLDivElement>(null)
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const at = options.findIndex((o) => o.value === value)
    const step: Record<string, number> = { ArrowRight: 1, ArrowLeft: -1, Home: -at, End: options.length - 1 - at }
    if (!(event.key in step)) return
    event.preventDefault()
    const next = options[(at + (step[event.key] ?? 0) + options.length) % options.length]
    if (!next) return
    onChange(next.value)
    list.current?.ownerDocument.getElementById(tabId(id, next.value))?.focus()
  }
  return (
    // Scrolls sideways when the tabs don't fit (a phone), never up and down: the line under the tabs
    // is an inset shadow the chosen tab's underline covers, so nothing reaches past the box.
    <div
      ref={list}
      role="tablist"
      aria-label={label}
      onKeyDown={onKeyDown}
      className={cx('flex gap-1 overflow-x-auto overflow-y-hidden shadow-[inset_0_-1px_0_var(--color-hairline)]', className)}
    >
      {options.map((option) => {
        const on = option.value === value
        return (
          <button
            key={option.value}
            id={tabId(id, option.value)}
            type="button"
            role="tab"
            aria-selected={on}
            aria-controls={`${id}-panel`}
            tabIndex={on ? 0 : -1}
            onClick={() => onChange(option.value)}
            className={cx(
              'inline-flex min-h-11 cursor-pointer items-center border-b-2 px-3 text-sm font-semibold whitespace-nowrap sm:min-h-10',
              'focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring',
              on ? 'border-accent text-fg' : 'border-transparent text-muted hover:border-hairline-strong hover:text-fg',
            )}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}

function tabId(id: string, value: string): string {
  return `${id}-tab-${value}`
}

/** The panel of a Tabs with the same `id`, showing the content of the chosen tab. */
export function TabPanel({ id, value, children, className }: { id: string; value: string; children: ReactNode; className?: string }) {
  return (
    <div role="tabpanel" id={`${id}-panel`} aria-labelledby={tabId(id, value)} className={className}>
      {children}
    </div>
  )
}
