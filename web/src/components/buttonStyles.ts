import { cx } from '../lib/cx'

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'ghostDanger'
export type ButtonSize = 'md' | 'icon'

export interface ButtonStyle {
  variant?: ButtonVariant
  size?: ButtonSize
}

// 44px touch targets on phones, 36px from sm up.
const sizes: Record<ButtonSize, string> = {
  md: 'min-h-11 px-4 sm:min-h-9',
  icon: 'size-11 sm:size-9',
}

const variants: Record<ButtonVariant, string> = {
  primary: 'bg-accent text-on-accent shadow-card hover:brightness-95 active:brightness-90',
  secondary: 'border border-hairline-strong bg-surface text-fg shadow-card hover:bg-sunken active:brightness-95',
  ghost: 'text-fg hover:bg-hover active:brightness-95',
  // Page colour on the danger red: light text in light mode, navy in dark mode.
  danger: 'bg-danger text-page shadow-card hover:brightness-95 active:brightness-90',
  // Quiet entry point to a destructive action (the confirm dialog then uses `danger`).
  ghostDanger: 'text-danger hover:bg-hover active:brightness-95',
}

/**
 * The button look, for elements that must stay what they are (a file input's label, a plain
 * download link). A label wrapping an input gets the focus ring from the input.
 */
export function buttonClasses({ variant = 'secondary', size = 'md' }: ButtonStyle = {}, className?: string): string {
  return cx(
    // Colours are not animated, so they switch with the theme instantly; the press is.
    'inline-flex shrink-0 cursor-pointer items-center justify-center gap-2 rounded-control text-sm font-semibold transition-[filter,translate] duration-100',
    'active:translate-y-px motion-reduce:transition-none motion-reduce:active:translate-y-0',
    'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
    'has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-ring',
    'disabled:cursor-not-allowed disabled:opacity-55 disabled:shadow-none disabled:active:translate-y-0',
    sizes[size],
    variants[variant],
    className,
  )
}
