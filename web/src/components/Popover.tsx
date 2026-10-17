import { useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { cx } from '../lib/cx'
import { Button } from './Button'
import type { ButtonSize, ButtonVariant } from './buttonStyles'
import { ChevronDownIcon } from './icons'

interface PopoverProps {
  /** The button's content (icon and text). */
  label: ReactNode
  /** The button's accessible name when it shows an icon only. */
  ariaLabel?: string
  title?: string
  variant?: ButtonVariant
  size?: ButtonSize
  /** Where the panel opens: under the button's left or right edge. */
  align?: 'start' | 'end'
  /** A chevron after the label (a menu). */
  chevron?: boolean
  className?: string
  panelClassName?: string
  /** The panel; `close` closes it (and gives the focus back to the button). */
  children: (close: () => void) => ReactNode
}

/**
 * A button that opens a small panel below it (a disclosure: the menu of downloads, the keys).
 * A click outside and Escape close it; Escape gives the focus back to the button.
 */
export function Popover({ label, ariaLabel, title, variant = 'secondary', size = 'md', align = 'start', chevron = false, className, panelClassName, children }: PopoverProps) {
  const id = useId()
  const [open, setOpen] = useState(false)
  const root = useRef<HTMLDivElement>(null)
  // State, not a ref: `close` hands the focus back and may be passed on during render.
  const [button, setButton] = useState<HTMLButtonElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onPointer = (event: PointerEvent) => {
      if (!root.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      event.stopPropagation()
      setOpen(false)
      button?.focus()
    }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey, { capture: true })
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey, { capture: true })
    }
  }, [open, button])

  const close = () => {
    setOpen(false)
    button?.focus()
  }

  return (
    <div ref={root} className={cx('relative', className)}>
      <Button
        ref={setButton}
        variant={variant}
        size={size}
        aria-expanded={open}
        aria-controls={id}
        aria-label={ariaLabel}
        title={title}
        onClick={() => setOpen((v) => !v)}
      >
        {label}
        {chevron && <ChevronDownIcon className={cx('size-4 transition-transform motion-reduce:transition-none', open && 'rotate-180')} />}
      </Button>
      {open && (
        <div
          id={id}
          className={cx(
            'absolute top-full z-30 mt-2 w-72 max-w-[calc(100vw-2rem)] rounded-card border border-hairline bg-surface p-1.5 shadow-raised',
            align === 'end' ? 'right-0' : 'left-0',
            panelClassName,
          )}
        >
          {children(close)}
        </div>
      )}
    </div>
  )
}
