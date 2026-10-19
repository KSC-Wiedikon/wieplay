import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import '../i18n'
import '../i18n/pages'
import type { SheetPlayer } from '../lib/stats'
import { SheetPlayerStats } from './SheetPlayerStats'

const player = (side: 'us' | 'them', number: number, patch: Partial<SheetPlayer> = {}): SheetPlayer => ({
  side,
  number,
  member_id: null,
  name: null,
  served: 10,
  won_on_serve: 4,
  won_on_serve_pct: 0.4,
  longest_run: 3,
  aces: 0,
  errors: 0,
  tagged: 0,
  on_court: 45,
  plus_minus: 5,
  by_rotation: [1, 2, 0, -1, 2, 1],
  ...patch,
})

describe('the scoresheet’s numbers per player', () => {
  it('shows serves, runs and the plus/minus for both teams', () => {
    render(
      <SheetPlayerStats
        data={{
          sets: [1, 2],
          by_setter: true,
          players: [player('us', 7, { name: 'Alex Muster', aces: 2, errors: 1, tagged: 5 }), player('them', 4, { plus_minus: -5 })],
        }}
      />,
    )
    const [ours, theirs] = screen.getAllByRole('table')
    const row = within(ours as HTMLElement).getByRole('row', { name: /#7/ })
    expect(row).toHaveTextContent('Alex Muster')
    expect(row).toHaveTextContent('4 (40%)')
    expect(row).toHaveTextContent('+5')
    expect(within(ours as HTMLElement).getByText('P1')).toBeInTheDocument() // our rotations by the setter
    expect(within(theirs as HTMLElement).getByRole('row', { name: /#4/ })).toHaveTextContent('−5')
    expect(within(theirs as HTMLElement).getByText('R1')).toBeInTheDocument()
  })

  it('leaves aces and errors open when no serve tag graded the serves', () => {
    render(<SheetPlayerStats data={{ sets: [1], by_setter: false, players: [player('us', 9)] }} />)
    const row = screen.getByRole('row', { name: /#9/ })
    expect(within(row).getAllByText('–').length).toBeGreaterThanOrEqual(2)
  })
})
