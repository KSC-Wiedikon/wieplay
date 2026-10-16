import { cx } from '../lib/cx'

interface SwitchProps {
  checked: boolean
  onChange: (checked: boolean) => void
  /** Accessible name, e.g. "H3 uses Nomal". */
  label: string
  disabled?: boolean
  /** Id of a text that says more about the setting. */
  describedBy?: string
}

export function Switch({ checked, onChange, label, disabled, describedBy }: SwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      aria-describedby={describedBy}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cx(
        // The button is the 44px (36px sm+) hit area; the track sits inside it.
        'group inline-flex min-h-11 min-w-11 cursor-pointer items-center justify-center rounded-control sm:min-h-9',
        'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring',
        'disabled:cursor-not-allowed disabled:opacity-60',
      )}
    >
      <span
        className={cx(
          'relative inline-flex h-6 w-10 items-center rounded-full border transition-colors',
          checked ? 'border-transparent bg-accent' : 'border-hairline bg-sunken',
        )}
      >
        <span
          className={cx(
            'absolute size-4 rounded-full shadow-sm transition-transform motion-reduce:transition-none',
            checked ? 'translate-x-5 bg-on-accent' : 'translate-x-1 bg-muted',
          )}
        />
      </span>
    </button>
  )
}
