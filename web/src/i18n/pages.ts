// Registers the strings of the pages loaded on demand: the video page and tagging workspace,
// clips, reels, comments, downloads and stats. Imported for its effect by the modules that use them,
// so the main bundle doesn't carry them.

import { actionsDe } from './actions.de'
import { actionsEn } from './actions.en'
import { courtDe } from './court.de'
import { courtEn } from './court.en'
import i18n from './index'
import { libraryDe } from './library.de'
import { libraryEn } from './library.en'
import { statsDe } from './stats.de'
import { statsEn } from './stats.en'
import { videoDe } from './video.de'
import { videoEn } from './video.en'

for (const [lng, bundles] of [
  ['en', [videoEn, libraryEn, statsEn, courtEn, actionsEn]],
  ['de', [videoDe, libraryDe, statsDe, courtDe, actionsDe]],
] as const) {
  for (const bundle of bundles) i18n.addResourceBundle(lng, 'translation', bundle, true, false)
}
