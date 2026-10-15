import { describe, expect, it } from 'vitest'
import { inboxFolder, inTeamFolder, isVideoFileName } from './video'

describe('inboxFolder', () => {
  it('names the folder like the API: sport prefix, runs of other characters as one dash', () => {
    expect(inboxFolder({ id: 92, sport: 'volleyball', name: 'H3' })).toBe('vb-H3')
    expect(inboxFolder({ id: 10, sport: 'volleyball', name: 'DU23-1' })).toBe('vb-DU23-1')
    expect(inboxFolder({ id: 5, sport: 'basketball', name: 'Herren 1' })).toBe('bb-Herren-1')
    expect(inboxFolder({ id: 6, sport: 'volleyball', name: ' Damen Ü35 (A) ' })).toBe('vb-Damen-35-A')
    expect(inboxFolder({ id: 7, sport: 'basketball', name: 'Ü!' })).toBe('bb-7')
  })

  it('matches inbox paths by their first folder, ignoring case', () => {
    expect(inTeamFolder('vb-H3/2026-10-11/GX010042.MP4', 'vb-H3')).toBe(true)
    expect(inTeamFolder('VB-h3/GX010042.MP4', 'vb-H3')).toBe(true)
    expect(inTeamFolder('vb-H30/GX010042.MP4', 'vb-H3')).toBe(false)
    expect(inTeamFolder('GX010042.MP4', 'vb-H3')).toBe(false)
  })
})

describe('isVideoFileName', () => {
  it('goes by extension, like the API', () => {
    for (const name of ['a.mp4', 'B.MOV', 'c.m4v', 'd.MTS', 'e.m2ts', 'f.mkv', 'g.AVI']) expect(isVideoFileName(name)).toBe(true)
    for (const name of ['clip.webm', 'clip.3gp', 'mp4', 'notes.txt']) expect(isVideoFileName(name)).toBe(false)
  })
})

