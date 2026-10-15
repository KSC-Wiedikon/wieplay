// Copies the committed brand assets from ../docs/brand/ into public/.
// Run after the icons in docs/brand/ are regenerated: npm run sync-brand
import { copyFile, mkdir, readdir } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const webRoot = join(dirname(fileURLToPath(import.meta.url)), '..')
const brand = join(webRoot, '..', 'docs', 'brand')
const pub = join(webRoot, 'public')

/** [source relative to docs/brand, target relative to public] */
const files = [
  ['icons/site.webmanifest', 'icons/site.webmanifest'],
  ['nomal-mark-vb.svg', 'brand/nomal-mark-vb.svg'],
  ['nomal-mark-bb.svg', 'brand/nomal-mark-bb.svg'],
  ['icons/vb/mark-on-dark.svg', 'brand/nomal-mark-vb-dark.svg'],
  ['icons/bb/mark-on-dark.svg', 'brand/nomal-mark-bb-dark.svg'],
]
for (const sport of ['vb', 'bb']) {
  for (const name of await readdir(join(brand, 'icons', sport))) {
    files.push([`icons/${sport}/${name}`, `icons/${sport}/${name}`])
  }
}

for (const [from, to] of files) {
  const target = join(pub, to)
  await mkdir(dirname(target), { recursive: true })
  await copyFile(join(brand, from), target)
  console.log(`${from} -> public/${to}`)
}
console.log(`Copied ${files.length} files.`)
