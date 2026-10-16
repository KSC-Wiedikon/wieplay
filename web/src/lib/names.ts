// People's names as Wiedisync gives them: "First Last", sometimes with several first or last names.

function words(name: string): string[] {
  return name.trim().split(/\s+/).filter(Boolean)
}

/** "Luca Canepa" → "LC"; a single name → its first letter; empty → "". */
export function initials(name: string): string {
  const parts = words(name)
  const first = parts[0]
  const last = parts.length > 1 ? parts[parts.length - 1] : undefined
  const letter = (word: string | undefined) => (word ? ([...word][0] ?? '') : '')
  return `${letter(first)}${letter(last)}`.toLocaleUpperCase('de-CH')
}

/** "Anna-Lena Müller" → "Anna-Lena"; empty → "". */
export function firstName(name: string): string {
  return words(name)[0] ?? ''
}
