import type { ComponentProps } from 'react'
import { Link } from 'react-router'
import { buttonClasses, type ButtonStyle } from './buttonStyles'

export function Button({ variant, size, className, type = 'button', ...props }: ComponentProps<'button'> & ButtonStyle) {
  return <button type={type} className={buttonClasses({ variant, size }, className)} {...props} />
}

export function ButtonLink({ variant, size, className, ...props }: ComponentProps<typeof Link> & ButtonStyle) {
  return <Link className={buttonClasses({ variant, size }, className)} {...props} />
}
