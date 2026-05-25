import { createI18n } from 'vue-i18n'
import zh from './locales/zh.json'
import ja from './locales/ja.json'
import en from './locales/en.json'

export type Locale = 'zh' | 'ja' | 'en'

export const SUPPORTED_LOCALES: Locale[] = ['zh', 'ja', 'en']
const STORAGE_KEY = 'codexmind.locale'

function detectLocale(): Locale {
  const saved = localStorage.getItem(STORAGE_KEY) as Locale | null
  if (saved && SUPPORTED_LOCALES.includes(saved)) return saved
  const nav = navigator.language.toLowerCase()
  if (nav.startsWith('zh')) return 'zh'
  if (nav.startsWith('ja')) return 'ja'
  return 'en'
}

export const i18n = createI18n({
  legacy: false,
  locale: detectLocale(),
  fallbackLocale: 'en',
  messages: { zh, ja, en },
})

export function setLocale(locale: Locale) {
  i18n.global.locale.value = locale
  localStorage.setItem(STORAGE_KEY, locale)
  document.documentElement.lang = locale
}

// 用于非组件代码（store / api / composable）的便捷 t()
export function t(key: string, named?: Record<string, unknown>): string {
  return (i18n.global.t as any)(key, named ?? {})
}
