import { describe, expect, it } from 'vitest'
import { channelDisplay, inboxFolder, inTeamFolder, isVideoFileName, parseVideoLink, platformName } from './video'

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

describe('parseVideoLink', () => {
  // The same cases as the API's (backend/tests/test_imports.py), and a few more.
  it.each([
    ['https://www.youtube.com/watch?v=NXNsZ_UNM6w', 'youtube', 'NXNsZ_UNM6w'],
    ['youtube.com/watch?v=NXNsZ_UNM6w&t=120s&si=x', 'youtube', 'NXNsZ_UNM6w'],
    ['https://www.youtube.com/watch?feature=share&v=NXNsZ_UNM6w', 'youtube', 'NXNsZ_UNM6w'],
    ['https://m.youtube.com/watch?v=NXNsZ_UNM6w', 'youtube', 'NXNsZ_UNM6w'],
    ['HTTPS://WWW.YouTube.com/watch?v=NXNsZ_UNM6w', 'youtube', 'NXNsZ_UNM6w'],
    ['https://youtu.be/NXNsZ_UNM6w?si=abc', 'youtube', 'NXNsZ_UNM6w'],
    ['https://www.youtube.com/live/NXNsZ_UNM6w', 'youtube', 'NXNsZ_UNM6w'],
    ['https://www.youtube.com/shorts/NXNsZ_UNM6w/', 'youtube', 'NXNsZ_UNM6w'],
    ['https://www.twitch.tv/videos/2245678901', 'twitch', '2245678901'],
    [' https://twitch.tv/videos/2245678901/ ', 'twitch', '2245678901'],
    ['m.twitch.tv/videos/12345', 'twitch', '12345'],
  ])('reads %s as a video', (text, platform, id) => {
    const link = parseVideoLink(text)
    expect(link).toMatchObject({ platform, id })
    // The canonical address is a link of its own kind.
    expect(parseVideoLink(link?.url ?? '')).toEqual(link)
  })

  it('gives the canonical address the API fetches', () => {
    expect(parseVideoLink('youtu.be/NXNsZ_UNM6w')?.url).toBe('https://www.youtube.com/watch?v=NXNsZ_UNM6w')
    expect(parseVideoLink('twitch.tv/videos/2245678901')?.url).toBe('https://www.twitch.tv/videos/2245678901')
  })

  it.each([
    '',
    'NXNsZ_UNM6w',
    'javascript:alert(1)',
    'javascript://www.youtube.com/watch?v=NXNsZ_UNM6w',
    'file:///etc/passwd',
    'ftp://www.youtube.com/watch?v=NXNsZ_UNM6w',
    'http://169.254.169.254/latest/meta-data/',
    'https://www.youtube.com/@lucazbindenvolley2500',
    'https://www.youtube.com/channel/UC1j3b8R-P4IROFGWWPvlllA',
    'https://www.youtube.com/playlist?list=PL123',
    'https://www.youtube.com/watch?list=PL123',
    'https://www.youtube.com/watch?v=',
    'https://www.youtube.com/watch?v=NXNsZ_UNM6w&v=AAAAAAAAAAA',
    'https://www.youtube.com/watch?v=NXNsZ_UNM6',
    'https://www.youtube.com/watch?v=NXNsZ_UNM6wX',
    'https://www.youtube-nocookie.com/embed/NXNsZ_UNM6w',
    'https://www.youtube.com.evil.example/watch?v=NXNsZ_UNM6w',
    'https://user@www.youtube.com/watch?v=NXNsZ_UNM6w',
    'https://www.youtube.com:8443/watch?v=NXNsZ_UNM6w',
    'https://www.youtube.com/watch?v=NXNsZ_UNM6w extra',
    `https://www.youtube.com/watch?v=NXNsZ_UNM6w&x=${'a'.repeat(500)}`,
    'https://www.twitch.tv/lucazbinden',
    'https://www.twitch.tv/videos/1234',
    'https://www.twitch.tv/videos/v2245678901',
    'https://clips.twitch.tv/SomeClip',
    'https://vimeo.com/123456',
  ])('refuses %s', (text) => {
    expect(parseVideoLink(text)).toBeNull()
  })
})

describe('channels', () => {
  it('writes allow-listed channels as people know them, with their page', () => {
    expect(channelDisplay({ platform: 'youtube', ref: 'lucazbindenvolley2500' })).toEqual({
      text: '@lucazbindenvolley2500',
      url: 'https://www.youtube.com/@lucazbindenvolley2500',
    })
    expect(channelDisplay({ platform: 'youtube', ref: 'UC1j3b8R-P4IROFGWWPvlllA' })).toEqual({
      text: 'UC1j3b8R-P4IROFGWWPvlllA',
      url: 'https://www.youtube.com/channel/UC1j3b8R-P4IROFGWWPvlllA',
    })
    expect(channelDisplay({ platform: 'twitch', ref: 'kscwiedikon' })).toEqual({
      text: 'kscwiedikon',
      url: 'https://www.twitch.tv/kscwiedikon',
    })
    expect(channelDisplay({ platform: 'vimeo', ref: 'x' })).toEqual({ text: 'x', url: null })
  })

  it('names the platforms', () => {
    expect(platformName('youtube')).toBe('YouTube')
    expect(platformName('twitch')).toBe('Twitch')
    expect(platformName('vimeo')).toBe('vimeo')
  })
})
