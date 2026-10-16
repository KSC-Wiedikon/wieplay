import 'i18next'
import type { en } from './en'
import type { libraryEn } from './library.en'
import type { statsEn } from './stats.en'
import type { videoEn } from './video.en'

declare module 'i18next' {
  interface CustomTypeOptions {
    defaultNS: 'translation'
    // The video, library and stats strings are registered by src/i18n/pages.ts when their pages load.
    resources: { translation: typeof en & typeof videoEn & typeof libraryEn & typeof statsEn }
  }
}
