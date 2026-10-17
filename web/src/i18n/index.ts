import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import { readStorage, writeStorage } from '../lib/storage'
import { de } from './de'
import { en } from './en'

export type Language = 'en' | 'de'

const KEY = 'wieplay-lang'

function syncDocumentLang(lng: string): void {
  document.documentElement.lang = lng === 'de' ? 'de-CH' : 'en'
}

// English is the default; German is a remembered choice from the header.
const saved = readStorage(KEY)

void i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, de: { translation: de } },
  lng: saved === 'de' ? 'de' : 'en',
  fallbackLng: 'en',
  initAsync: false,
  interpolation: { escapeValue: false },
})
syncDocumentLang(i18n.language)
i18n.on('languageChanged', syncDocumentLang)

export function setLanguage(lng: Language): void {
  writeStorage(KEY, lng)
  void i18n.changeLanguage(lng)
}

export default i18n
