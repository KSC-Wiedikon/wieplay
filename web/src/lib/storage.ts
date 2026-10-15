// localStorage can throw (private mode, blocked site data), so every access is guarded.

export function readStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key)
  } catch {
    return null
  }
}

export function writeStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value)
  } catch {
    // Not persisted; the choice still applies for this page load.
  }
}
