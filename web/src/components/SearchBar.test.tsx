import { fireEvent, screen, waitFor, within } from '@testing-library/react'
import { useState } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { SavedFilter } from '../lib/filters'
import { renderRoute, stubFetch } from '../test/render'
import { roster, volleyball } from '../test/tagging'
import { SearchBar } from './SearchBar'

function Bar({ canShare = true }: { canShare?: boolean }) {
  const [text, setText] = useState('')
  return (
    <>
      <SearchBar value={text} onChange={setText} taxonomy={volleyball} roster={roster} sets={[1, 2, 3]} team={{ id: 92, canShare, label: 'H3 2026/27' }} />
      <output data-testid="text">{text}</output>
    </>
  )
}

const filter = (patch: Partial<SavedFilter>): SavedFilter => ({
  id: 'f1',
  title: 'Kills',
  query: '#7 kill',
  visibility: 'private',
  lineage_id: null,
  owner: 7,
  owner_name: 'Coach Test',
  mine: true,
  can_edit: true,
  can_delete: true,
  created_at: '',
  updated_at: '',
  ...patch,
})

function show(canShare = true, saved: SavedFilter[] = []) {
  const posts: unknown[] = []
  const deleted: string[] = []
  vi.stubGlobal(
    'fetch',
    vi.fn(
      stubFetch({
        '/api/filters?team_id=92': { body: saved },
        'POST /api/filters': {
          status: 201,
          body: ({ body }: { body: unknown }) => {
            posts.push(body)
            return filter({ id: 'f9', ...(body as object) })
          },
        },
        'DELETE /api/filters/f1': {
          status: 204,
          body: ({ path }: { path: string }) => {
            deleted.push(path)
            return undefined
          },
        },
      }),
    ),
  )
  renderRoute(<Bar canShare={canShare} />)
  return { posts, deleted }
}

const input = () => screen.getByRole('combobox', { name: 'Search' })
const text = () => screen.getByTestId('text').textContent

beforeEach(() => {
  window.localStorage.clear()
})

describe('SearchBar', () => {
  it('suggests words and numbers while typing; arrows and Enter pick one', () => {
    show()
    fireEvent.focus(input())
    // Empty: a few words to start with.
    expect(within(screen.getByRole('listbox', { name: 'Suggestions' })).getAllByRole('option')).toHaveLength(6)
    fireEvent.change(input(), { target: { value: 'rot' } })
    expect(screen.getByRole('option', { name: /rotation …/ })).toHaveAttribute('aria-selected', 'true')
    fireEvent.keyDown(input(), { key: 'Enter' })
    expect(text()).toBe('rotation ')
    // After the keyword, its numbers.
    const options = within(screen.getByRole('listbox')).getAllByRole('option')
    expect(options.map((o) => o.textContent)).toEqual([1, 2, 3, 4, 5, 6].map((n) => `rotation ${n}Our rotation (setter zone 1–6)`))
    fireEvent.keyDown(input(), { key: 'ArrowDown' })
    fireEvent.keyDown(input(), { key: 'Enter' })
    expect(text()).toBe('rotation 2 ')
    expect(screen.getByText('Rotation 2')).toBeInTheDocument()
    // # suggests our players by name.
    fireEvent.change(input(), { target: { value: 'rotation 2 #' } })
    fireEvent.mouseDown(screen.getByRole('option', { name: /#7Luca Brunner/ }))
    expect(text()).toBe('rotation 2 #7 ')
    expect(screen.getByText('#7 Luca Brunner')).toBeInTheDocument()
    // Escape closes the list.
    fireEvent.keyDown(input(), { key: 'Escape' })
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('names a grade by what it means for the skill', () => {
    show()
    fireEvent.change(input(), { target: { value: 'attack # them' } })
    expect(screen.getByText('# Kill')).toBeInTheDocument()
    expect(screen.getByText('Them')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Remove “Attack”' }))
    expect(text()).toBe('# them')
    expect(screen.getByText('Grade #')).toBeInTheDocument()
  })

  it('saves the search for oneself or the team, and uses and deletes saved ones', async () => {
    const { posts, deleted } = show(true, [filter({}), filter({ id: 'f2', title: 'Side-outs', query: 'side-out', visibility: 'team', mine: false, owner_name: 'Jo Trainer', can_delete: false })])
    fireEvent.change(input(), { target: { value: 'set 3, reception -' } })
    fireEvent.click(screen.getByRole('button', { name: 'Saved filters' }))
    expect(await screen.findByText('Shared with the team by Jo Trainer')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Delete “Side-outs”' })).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Name'), { target: { value: 'Set 3 passing' } })
    fireEvent.click(screen.getByLabelText('Share with the team (H3 2026/27)'))
    fireEvent.click(screen.getByRole('button', { name: 'Save' }))
    await waitFor(() => expect(posts).toEqual([{ title: 'Set 3 passing', query: 'set 3, reception -', visibility: 'team', team_id: 92 }]))

    fireEvent.click(screen.getByRole('button', { name: 'Delete “Kills”' }))
    await waitFor(() => expect(deleted).toEqual(['/api/filters/f1']))
    fireEvent.click(screen.getByRole('button', { name: 'Use “Side-outs”' }))
    expect(text()).toBe('side-out')
  })

  it("doesn't offer sharing to players", async () => {
    show(false)
    fireEvent.click(screen.getByRole('button', { name: 'Saved filters' }))
    expect(await screen.findByText('No saved filters yet. Type a search and save it here.')).toBeInTheDocument()
    expect(screen.queryByLabelText(/Share with the team/)).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled()
  })
})
