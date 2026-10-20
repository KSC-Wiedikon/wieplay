// Registers the strings of the pages loaded on demand: the video page and tagging workspace,
// clips, reels, comments, downloads and stats, the search bar, the regrade queue and the scoresheet. Imported for
// its effect by the modules that use them, so the main bundle doesn't carry them.

import { actionsDe } from './actions.de'
import { actionsEn } from './actions.en'
import { courtDe } from './court.de'
import { courtEn } from './court.en'
import i18n from './index'
import { libraryDe } from './library.de'
import { libraryEn } from './library.en'
import { memoryDe } from './memory.de'
import { memoryEn } from './memory.en'
import { playersDe } from './players.de'
import { playersEn } from './players.en'
import { regradeDe } from './regrade.de'
import { regradeEn } from './regrade.en'
import { scoreboardDe } from './scoreboard.de'
import { scoreboardEn } from './scoreboard.en'
import { searchDe } from './search.de'
import { searchEn } from './search.en'
import { setupDe } from './setup.de'
import { setupEn } from './setup.en'
import { sheetDe } from './sheet.de'
import { sheetEn } from './sheet.en'
import { statsDe } from './stats.de'
import { statsEn } from './stats.en'
import { videoDe } from './video.de'
import { videoEn } from './video.en'

for (const [lng, bundles] of [
  ['en', [videoEn, libraryEn, statsEn, courtEn, actionsEn, playersEn, scoreboardEn, setupEn, searchEn, regradeEn, sheetEn, memoryEn]],
  ['de', [videoDe, libraryDe, statsDe, courtDe, actionsDe, playersDe, scoreboardDe, setupDe, searchDe, regradeDe, sheetDe, memoryDe]],
] as const) {
  for (const bundle of bundles) i18n.addResourceBundle(lng, 'translation', bundle, true, false)
}
