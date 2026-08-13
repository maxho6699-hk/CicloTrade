import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  decodeFeatureCatalog,
  featureSearchText,
  filterFeatureCatalog,
  formatMorePageCopy,
  isValidPinnedSelection,
  localizeFeature,
  MORE_PAGE_COPY,
  recordRecentFeature,
  toggleDraftPin,
} from '../src/domain/featureCatalog.ts'

const payload = {
  catalog_version: '2026.08.13.1',
  items: [
    ...[
      ['today', '/today', 'review', 'ClipboardCheck'],
      ['discover', '/discover', 'discover', 'Grid2X2'],
      ['research', '/research', 'research', 'ChartCandlestick'],
      ['personal-paper', '/paper', 'simulate', 'WalletCards'],
      ['portfolio', '/portfolio', 'review', 'ClipboardCheck'],
      ['more', '/more', 'account', 'Sparkles'],
    ].map(([key, route, category, icon], index) => ({
      key, route, routes: [route], category,
      title_key: `feature.${key.replaceAll('-', '_')}.title`, description_key: `feature.${key.replaceAll('-', '_')}.description`,
      icon, capability: null, availability: 'available', access: 'open', reason: null,
      data_state: 'not_applicable', health: 'not_applicable', placements: ['more'], actions: {},
      pin_allowed: false, primary_nav: true, sort_order: index + 1, recommendation_rank: null,
    })),
    {
      key: 'stock-screener', route: '/discover?tool=screener', routes: ['/discover?tool=screener'], category: 'discover',
      title_key: 'feature.stock_screener.title', description_key: 'feature.stock_screener.description',
      icon: 'ListFilter', capability: 'stock_screener', availability: 'available', access: 'open',
      reason: null, data_state: 'ready', health: 'healthy', placements: ['more', 'secondary_nav'], actions: { research_url: '/discover?tool=screener' }, pin_allowed: true, primary_nav: false, sort_order: 100, recommendation_rank: null,
    },
    {
      key: 'option-live-automation', route: '/trade?mode=options', routes: ['/trade?mode=options'], category: 'automation',
      title_key: 'feature.option_live_automation.title', description_key: 'feature.option_live_automation.description',
      icon: 'ShieldCheck', capability: 'option_auto_live', availability: 'planned', access: 'wait',
      reason: '完成独立验收后开放申请。', data_state: 'not_applicable', health: 'not_applicable', placements: ['more'], actions: {}, pin_allowed: false,
      primary_nav: false, sort_order: 900, recommendation_rank: null,
    },
  ],
  preferences: { pinned: [], recent: ['stock-screener'], version: 2 },
}

test('decoder accepts only controlled routes, icons and preference ids', () => {
  const decoded = decodeFeatureCatalog(payload)
  assert.equal(decoded.items.find((item) => item.key === 'stock-screener')?.icon, 'ListFilter')
  assert.deepEqual(decoded.preferences.pinned, [])
  assert.throws(() => decodeFeatureCatalog({
    ...payload,
    items: [{ ...payload.items[0], route: 'javascript:alert(1)' }],
  }), /route/)
  assert.throws(() => decodeFeatureCatalog({
    ...payload,
    items: [{ ...payload.items[0], icon: '<svg onload=alert(1)>' }],
  }), /icon/)
})

test('decoder permits only the six primary navigation routes and inert actions', () => {
  const decoded = decodeFeatureCatalog(payload)
  assert.deepEqual(decoded.items.filter((item) => item.primaryNav).map((item) => item.route), [
    '/today', '/discover', '/research', '/paper', '/portfolio', '/more',
  ])
  assert.deepEqual(decoded.items.find((item) => item.key === 'stock-screener')?.actions, { researchUrl: '/discover?tool=screener' })
  assert.throws(() => decodeFeatureCatalog({
    ...payload,
    items: payload.items.map((item) => item.key === 'stock-screener' ? {
      ...item, actions: { paper_prefill: { idempotency_key: 'not-allowed' } },
    } : item),
  }), /action/)
  assert.throws(() => decodeFeatureCatalog({
    ...payload,
    items: payload.items.map((item) => item.key === 'today' ? { ...item, pin_allowed: true } : item),
  }), /primary navigation/)
})

test('simplified and traditional copy share searchable terms', () => {
  const decoded = decodeFeatureCatalog(payload)
  const screener = decoded.items.find((item) => item.key === 'stock-screener')!
  const hans = localizeFeature(screener, 'zh-Hans')
  const hant = localizeFeature(screener, 'zh-Hant')
  assert.equal(hans.title, '股票筛选器')
  assert.equal(hant.title, '股票篩選器')
  assert.match(featureSearchText(screener), /筛选器/)
  assert.match(featureSearchText(screener), /篩選器/)
  assert.equal(filterFeatureCatalog(decoded.items, '篩選', 'zh-Hant').filter((item) => item.key === 'stock-screener').length, 1)
})

test('planned entries remain visible but never actionable or pinnable', () => {
  const decoded = decodeFeatureCatalog(payload)
  const planned = decoded.items.find((item) => item.key === 'option-live-automation')!
  assert.equal(planned.availability, 'planned')
  assert.equal(planned.access, 'wait')
  assert.equal(planned.pinAllowed, false)
})

test('pin edits remain local until a complete zero or three-to-five selection can be saved', () => {
  let draft: string[] = []
  draft = toggleDraftPin(draft, 'stock-screener')
  assert.deepEqual(draft, ['stock-screener'])
  assert.equal(isValidPinnedSelection(draft), false)
  draft = toggleDraftPin(draft, 'market-heatmap')
  draft = toggleDraftPin(draft, 'risk-calculator')
  assert.equal(isValidPinnedSelection(draft), true)
  draft = toggleDraftPin(draft, 'price-alerts')
  draft = toggleDraftPin(draft, 'research-reports')
  assert.deepEqual(toggleDraftPin(draft, 'feedback'), draft)
  assert.equal(isValidPinnedSelection(draft), true)
  assert.equal(isValidPinnedSelection([]), true)
})

test('recent activity is explicit, de-duplicated and capped at eight tools', () => {
  const recent = recordRecentFeature(['market-heatmap', 'stock-screener', 'option-lab'], 'stock-screener')
  assert.deepEqual(recent, ['stock-screener', 'market-heatmap', 'option-lab'])
  assert.equal(recordRecentFeature(['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h'], 'new').length, 8)
})

test('More page copy is injectable and complete for Simplified and Traditional Chinese', () => {
  assert.equal(MORE_PAGE_COPY['zh-Hant'].searchLabel, '搜尋功能')
  assert.equal(MORE_PAGE_COPY['zh-Hant'].categories.account, '帳戶服務')
  assert.equal(MORE_PAGE_COPY['zh-Hans'].availability.degraded, '服务降级')
  assert.equal(formatMorePageCopy(MORE_PAGE_COPY['zh-Hant'].pinCount, { count: 3 }), '已選擇 3 / 5')
  const source = readFileSync(new URL('../src/pages/MorePage.tsx', import.meta.url), 'utf8')
  assert.match(source, /copy\?: MorePageCopy/)
  assert.match(source, /onSavePins\?: \(pinned: string\[\], recent: string\[\], expectedVersion: number\) => FeatureCatalogPayload/)
  assert.match(source, /\.\.\.catalogSnapshot\.preferences\.recent/)
  assert.match(source, /await onRecordRecent\(item\.key, catalogSnapshot\.preferences\.version\)/)
  assert.match(source, /name="feature-search" autoComplete="off" spellCheck=\{false\}/)
  assert.doesNotMatch(source, /等待主管/)
  const publicProps = source.match(/export interface MorePageProps \{([\s\S]*?)\n\}/)?.[1] ?? ''
  assert.doesNotMatch(publicProps, /onTogglePin\?:/)
  assert.doesNotMatch(source, /[\u3400-\u9fff]/)
})
