import type { ComponentProps } from 'react'
import { Link } from 'react-router'
import { cx } from '../lib/cx'

type Variant = 'primary' | 'secondary' | 'ghost'
type Size = 'md' | 'icon'

interface StyleProps {
  variant?: Variant
  size?: Size
}

// 44px touch targets on phones, 36px from sm up.
const sizes: Record<Size, string> = {
  md: 'min-h-11 px-4 sm:min-h-9',
  icon: 'size-11 sm:size-9',
}

const variants: Record<Variant, string> = {
  primary: 'bg-accent text-on-accent hover:brightness-95 active:brightness-90',
  secondary: 'border border-hairline bg-surface text-fg hover:bg-sunken',
  ghost: 'text-fg hover:bg-sunken',
}

function classes({ variant = 'secondary', size = 'md' }: StyleProps, className?: string) {
  return cx(
    // Only filter is animated, so colours switch with the theme instantly.
    'inline-flex shrink-0 cursor-pointer items-center justify-center gap-2 rounded-xl text-sm font-semibold transition-[filter]',
    'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
    'disabled:cursor-not-allowed disabled:opacity-60',
    sizes[size],
    variants[variant],
    className,
  )
}

export function Button({ variant, size, className, type = 'button', ...props }: ComponentProps<'button'> & StyleProps) {
  return <button type={type} className={classes({ variant, size }, className)} {...props} />
}

export function ButtonLink({ variant, size, className, ...props }: ComponentProps<typeof Link> & StyleProps) {
  return <Link className={classes({ variant, size }, className)} {...props} />
}
