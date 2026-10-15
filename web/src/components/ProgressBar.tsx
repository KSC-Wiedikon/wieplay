import { cx } from '../lib/cx'

/** 0..1, or null while the amount is not known (shown as a pulsing bar). */
export function ProgressBar({ value, label, className }: { value: number | null; label: string; className?: string }) {
  const percent = value === null ? null : Math.round(Math.min(1, Math.max(0, value)) * 100)
  return (
    <div
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent ?? undefined}
      className={cx('h-2 w-full overflow-hidden rounded-full bg-sunken ring-1 ring-hairline ring-inset', className)}
    >
      <div
        className={cx(
          'h-full rounded-full bg-accent motion-safe:transition-[width]',
          percent === null && 'w-1/3 motion-safe:animate-pulse',
        )}
        style={percent === null ? undefined : { width: `${percent}%` }}
      />
    </div>
  )
}
