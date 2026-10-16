// Registers the strings of the pages loaded on demand: the video page and tagging workspace,
// clips, reels, comments, downloads and stats. Imported for its effect by the modules that use them,
// so the main bundle doesn't carry them.

import i18n from './index'
import { libraryDe } from './library.de'
import { libraryEn } from './library.en'
import { statsDe } from './stats.de'
import { statsEn } from './stats.en'
import { videoDe } from './video.de'
import { videoEn } from './video.en'

for (const [lng, bundles] of [
  ['en', [videoEn, libraryEn, statsEn]],
  ['de', [videoDe, libraryDe, statsDe]],
] as const) {
  for (const bundle of bundles) i18n.addResourceBundle(lng, 'translation', bundle, true, false)
}
