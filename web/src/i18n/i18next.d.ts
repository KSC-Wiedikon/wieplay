import 'i18next'
import type { actionsEn } from './actions.en'
import type { courtEn } from './court.en'
import type { en } from './en'
import type { libraryEn } from './library.en'
import type { memoryEn } from './memory.en'
import type { playersEn } from './players.en'
import type { regradeEn } from './regrade.en'
import type { scoreboardEn } from './scoreboard.en'
import type { searchEn } from './search.en'
import type { setupEn } from './setup.en'
import type { sheetEn } from './sheet.en'
import type { statsEn } from './stats.en'
import type { videoEn } from './video.en'

declare module 'i18next' {
  interface CustomTypeOptions {
    defaultNS: 'translation'
    // The video, library and stats strings are registered by src/i18n/pages.ts when their pages load.
    resources: { translation: typeof en & typeof videoEn & typeof libraryEn & typeof statsEn & typeof courtEn & typeof actionsEn & typeof playersEn & typeof scoreboardEn & typeof setupEn & typeof searchEn & typeof regradeEn & typeof sheetEn & typeof memoryEn }
  }
}
