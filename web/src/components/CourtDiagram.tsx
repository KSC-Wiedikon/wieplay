import { useId, type KeyboardEvent } from 'react'
import { LENGTH_M, LINES, NET_Y, WIDTH_M, type Arrow, type Outcome } from '../lib/court'

const COLOUR: Record<Outcome, string> = {
  win: 'var(--color-outcome-win)',
  error: 'var(--color-outcome-error)',
  play: 'var(--color-outcome-play)',
}
const OUTCOMES: readonly Outcome[] = ['play', 'error', 'win']

/** Court metres to the drawing: the acting team at the bottom (y = 0 is the bottom end line). */
const sx = (x: number) => x
const sy = (y: number) => LENGTH_M - y

/** Stops the arrow's line short of its tip, so the head ends on the landing point. */
function shorten(from: [number, number], to: [number, number], by: number): [number, number] {
  const dx = to[0] - from[0]
  const dy = to[1] - from[1]
  const length = Math.hypot(dx, dy)
  if (length <= by) return to
  return [to[0] - (dx / length) * by, to[1] - (dy / length) * by]
}

interface CourtDiagramProps {
  arrows: readonly Arrow[]
  /** The drawing's accessible name. */
  label: string
  netLabel: string
  describe: (arrow: Arrow) => string
  onSelect: (arrow: Arrow) => void
  selected?: string | null
}

/**
 * A volleyball court seen from above (9 x 18 m, the free zone around it), with an arrow per
 * serve or attack from where it started to where it went, coloured by what came of it. Each
 * arrow is a button: Enter or a click plays its moment.
 */
export function CourtDiagram({ arrows, label, netLabel, describe, onSelect, selected = null }: CourtDiagramProps) {
  const id = useId().replace(/:/g, '')
  const onKey = (event: KeyboardEvent, arrow: Arrow) => {
    if (event.key !== 'Enter' && event.key !== ' ') return
    event.preventDefault()
    onSelect(arrow)
  }
  // Errors and points on top of the many "in play" arrows.
  const ordered = [...arrows].sort((a, b) => OUTCOMES.indexOf(a.outcome) - OUTCOMES.indexOf(b.outcome))
  return (
    <svg viewBox="-3 -3 15 24" role="group" aria-label={label} className="h-auto w-full max-w-sm select-none" data-testid="court-diagram">
      <defs>
        {OUTCOMES.map((o) => (
          <marker key={o} id={`${id}-${o}`} viewBox="0 0 10 10" refX="1" refY="5" markerWidth="4" markerHeight="4" orient="auto-start-reverse">
            <path d="M0,0 L10,5 L0,10 z" fill={COLOUR[o]} />
          </marker>
        ))}
      </defs>
      <rect x={-3} y={-3} width={15} height={24} rx={0.6} fill="var(--color-sunken)" />
      <rect x={0} y={0} width={WIDTH_M} height={LENGTH_M} fill="var(--color-court-floor)" />
      {LINES.map(([x1, y1, x2, y2], i) => (
        <line key={i} x1={sx(x1)} y1={sy(y1)} x2={sx(x2)} y2={sy(y2)} stroke="var(--color-court-line)" strokeWidth={0.07} />
      ))}
      <line x1={-0.8} y1={sy(NET_Y)} x2={WIDTH_M + 0.8} y2={sy(NET_Y)} stroke="var(--color-fg)" strokeWidth={0.16} strokeLinecap="round" />
      <text x={WIDTH_M + 1} y={sy(NET_Y) - 0.25} fontSize={0.65} fill="var(--color-muted)">
        {netLabel}
      </text>
      {ordered.map((arrow) => {
        const colour = COLOUR[arrow.outcome]
        const from: [number, number] = [sx(arrow.from[0]), sy(arrow.from[1])]
        const tip: [number, number] = [sx(arrow.to[0]), sy(arrow.to[1])]
        const end = shorten(from, tip, 0.45)
        const on = selected === arrow.tag.id
        return (
          <g
            key={arrow.tag.id}
            role="button"
            tabIndex={0}
            aria-label={describe(arrow)}
            aria-pressed={on}
            data-outcome={arrow.outcome}
            data-evidence={arrow.evidence}
            onClick={() => onSelect(arrow)}
            onKeyDown={(event) => onKey(event, arrow)}
            className="cursor-pointer outline-none [&:focus-visible_.halo]:opacity-100 [&:hover_.halo]:opacity-60"
          >
            <title>{describe(arrow)}</title>
            <line className="halo opacity-0" x1={from[0]} y1={from[1]} x2={tip[0]} y2={tip[1]} stroke="var(--color-ring)" strokeWidth={0.42} strokeLinecap="round" style={on ? { opacity: 1 } : undefined} />
            {/* A wider invisible line: easier to hit with a finger. */}
            <line x1={from[0]} y1={from[1]} x2={tip[0]} y2={tip[1]} stroke="transparent" strokeWidth={0.9} />
            <line
              x1={from[0]}
              y1={from[1]}
              x2={end[0]}
              y2={end[1]}
              stroke={colour}
              strokeWidth={0.14}
              strokeLinecap="round"
              strokeDasharray={arrow.evidence === 'zones' ? '0.35 0.25' : undefined}
              markerEnd={`url(#${id}-${arrow.outcome})`}
              opacity={0.9}
            />
            {arrow.evidence === 'ball' && <circle cx={tip[0]} cy={tip[1]} r={0.2} fill={colour} stroke="var(--color-surface)" strokeWidth={0.06} />}
          </g>
        )
      })}
    </svg>
  )
}
