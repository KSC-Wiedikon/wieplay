// The app was called Nomal until 17.10.2026 and kept what a browser remembers under
// `nomal-…` keys (theme, language, filters, the open rally, the login choice). On the first
// load after the rename they move to `wieplay-…`, so nobody loses a setting. Imported first
// in main.tsx, before any module reads its key. Unsynced tags (IndexedDB) move in outbox.ts.

const OLD = 'nomal-'
const NEW = 'wieplay-'

export function moveLegacyKeys(storage: Storage): number {
  const old: string[] = []
  for (let i = 0; i < storage.length; i += 1) {
    const key = storage.key(i)
    if (key?.startsWith(OLD)) old.push(key)
  }
  for (const key of old) {
    const value = storage.getItem(key)
    const renamed = NEW + key.slice(OLD.length)
    if (value !== null && storage.getItem(renamed) === null) storage.setItem(renamed, value)
    storage.removeItem(key)
  }
  return old.length
}

try {
  if (typeof window !== 'undefined') {
    moveLegacyKeys(window.localStorage)
    moveLegacyKeys(window.sessionStorage)
  }
} catch {
  // Storage blocked (private mode, settings): nothing to move.
}
