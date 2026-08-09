import { HANT_PHRASES } from './generated-hant.ts'

export type UiLocale = 'zh-Hant' | 'zh-Hans'

const HANT_CATALOG = new Map<string, string>(HANT_PHRASES)
let activeLocale: UiLocale = 'zh-Hant'

function toTraditional(value: string): string {
  const direct = HANT_CATALOG.get(value)
  if (direct) return direct
  const trimmed = value.trim()
  const translated = HANT_CATALOG.get(trimmed)
  return translated ? value.replace(trimmed, translated) : value
}

export function setRuntimeLocale(locale: UiLocale) {
  activeLocale = locale
}

export function getFormatLocale(): 'zh-Hant-TW' | 'zh-Hans-CN' {
  return activeLocale === 'zh-Hant' ? 'zh-Hant-TW' : 'zh-Hans-CN'
}

export function localizeText(value: string): string {
  return activeLocale === 'zh-Hant' ? toTraditional(value) : value
}

export function localeSearchText(value: string): string {
  const translated = HANT_CATALOG.get(value)
  return translated && translated !== value ? `${value} ${translated}` : value
}

export function localizeProps(props: unknown): unknown {
  if (!props || typeof props !== 'object' || Array.isArray(props)) return props
  const source = props as Record<string, unknown>
  if (source['data-no-localize'] !== undefined) return props
  const next = { ...source }
  if (typeof next.children === 'string') next.children = localizeText(next.children)
  if (Array.isArray(next.children)) {
    next.children = next.children.map((child) => typeof child === 'string' ? localizeText(child) : child)
  }
  for (const attribute of ['placeholder', 'title', 'aria-label', 'alt'] as const) {
    if (typeof next[attribute] === 'string') next[attribute] = localizeText(next[attribute])
  }
  return next
}
