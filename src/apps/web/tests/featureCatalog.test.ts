import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  decodeFeatureCatalog,
  featureOpenRoute,
  featureSearchText,
  filterFeatureCatalog,
  formatMorePageCopy,
  isValidPinnedSelection,
  localizeFeature,
  localizeFeatureReason,
  MORE_PAGE_COPY,
  readFeatureCatalogView,
  recordRecentFeature,
  toggleDraftPin,
  writeFeatureCatalogView,
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
  const strategyResearch = { ...screener, titleKey: 'feature.strategy_research.title', descriptionKey: 'feature.strategy_research.description' }
  assert.equal(localizeFeature(strategyResearch, 'zh-Hant').title, '策略研究覆蓋')
  assert.match(localizeFeature(strategyResearch, 'zh-Hans').description, /97 只股票扩容链/)
})

test('fixed server reasons are localized while unknown operational detail remains untouched', () => {
  const reasons = [
    ['尚未取得可核验的数据新鲜度或服务健康证明。', '尚未取得可核驗的資料新鮮度或服務健康證明。'],
    ['运行状态格式无效，功能已安全停用。', '執行狀態格式無效，功能已安全停用。'],
    ['数据状态或服务健康状态无法验证，功能已安全停用。', '資料狀態或服務健康狀態無法驗證，功能已安全停用。'],
    ['未取得可核验的数据新鲜度证据，功能已安全停用。', '未取得可核驗的資料新鮮度證據，功能已安全停用。'],
    ['运行状态时间晚于当前时钟，功能已安全停用。', '執行狀態時間晚於目前時鐘，功能已安全停用。'],
    ['运行状态证明已超过 5 分钟，请刷新后重试。', '執行狀態證明已超過 5 分鐘，請重新整理後再試。'],
    ['运行状态组合无法验证，功能已安全停用。', '執行狀態組合無法驗證，功能已安全停用。'],
    ['当前数据或服务未达到可用门限。', '目前資料或服務未達到可用門檻。'],
    ['研究证据已过期；可查看只读状态，但不能固定为常用工具。', '研究證據已過期；可檢視只讀狀態，但不能固定為常用工具。'],
    ['研究覆盖尚未完整或服务正在降级；可查看只读状态，但不能固定为常用工具。', '研究覆蓋尚未完整或服務正在降級；可檢視只讀狀態，但不能固定為常用工具。'],
    ['该能力仍在独立开发与验收中，当前会员不包含此功能。', '該功能仍在獨立開發與驗收中，目前會員不包含此功能。'],
    ['当前会员未包含此研究深度；风险与数据状态仍永久免费可见。', '目前會員未包含此研究深度；風險與資料狀態仍可永久免費查看。'],
  ] as const
  for (const [simplified, traditional] of reasons) {
    assert.equal(localizeFeatureReason(simplified, 'zh-Hans'), simplified)
    assert.equal(localizeFeatureReason(simplified, 'zh-Hant'), traditional)
  }
  assert.equal(localizeFeatureReason('上游服务返回自定义维护说明。', 'zh-Hant'), '上游服务返回自定义维护说明。')
  assert.equal(localizeFeatureReason(null, 'zh-Hant'), null)
})

test('planned entries remain visible but never actionable or pinnable', () => {
  const decoded = decodeFeatureCatalog(payload)
  const planned = decoded.items.find((item) => item.key === 'option-live-automation')!
  assert.equal(planned.availability, 'planned')
  assert.equal(planned.access, 'wait')
  assert.equal(planned.pinAllowed, false)
  assert.equal(featureOpenRoute(planned), null)
  assert.throws(() => decodeFeatureCatalog({
    ...payload,
    items: payload.items.map((item) => item.key === 'option-live-automation' ? { ...item, pin_allowed: true } : item),
  }), /planned feature cannot be pinned/)
})

test('legacy locked entries wait without a membership CTA', () => {
  const decoded = decodeFeatureCatalog({
    ...payload,
    items: [...payload.items, {
      key: 'legacy-option', route: '/lab', routes: ['/lab'], category: 'research',
      title_key: 'feature.option_lab.title', description_key: 'feature.option_lab.description',
      icon: 'Gauge', capability: 'option_strategy', availability: 'locked', access: 'wait',
      reason: 'sales_unavailable: 该能力仅保留历史有效权益，当前不公开新购或升级。',
      data_state: 'not_applicable', health: 'not_applicable', placements: ['more'], actions: {},
      pin_allowed: false, primary_nav: false, sort_order: 221, recommendation_rank: null,
    }],
  })
  const item = decoded.items.find((entry) => entry.key === 'legacy-option')!
  assert.equal(featureOpenRoute(item), null)
  assert.equal(MORE_PAGE_COPY['zh-Hans'].availability.locked, '当前未开放')
  assert.equal(MORE_PAGE_COPY['zh-Hant'].availability.locked, '目前未開放')
})

test('catalog entries use real hosts and retain no retired fake query routes', () => {
  const source = readFileSync(new URL('../src/domain/featureCatalog.ts', import.meta.url), 'utf8')
  assert.match(source, /account\|admin\|ai\|deliberation\|discover\|earnings\|feedback\|help\|lab\|legal\|membership/)
  for (const retired of ['tool=heatmap', 'tool=calendar', 'workspace=options', 'tool=risk', 'section=data', 'tool=alerts']) {
    assert.doesNotMatch(source, new RegExp(retired.replace(/[?=]/g, '\\$&')))
  }
})

test('secondary operational routes keep complete bilingual directory copy', () => {
  const decoded = decodeFeatureCatalog({
    ...payload,
    items: [...payload.items, ...[
      ['workflow-tasks', '/workflow', 'feature.workflow_tasks.title', 'feature.workflow_tasks.description', 'ClipboardCheck'],
      ['notifications', '/notifications', 'feature.notifications.title', 'feature.notifications.description', 'BellRing'],
      ['trade-control', '/trade', 'feature.trade_control.title', 'feature.trade_control.description', 'ShieldCheck'],
      ['membership', '/membership', 'feature.membership.title', 'feature.membership.description', 'WalletCards'],
      ['promotion', '/promotion', 'feature.promotion.title', 'feature.promotion.description', 'WalletCards'],
      ['help', '/help', 'feature.help.title', 'feature.help.description', 'LifeBuoy'],
      ['legal', '/legal', 'feature.legal.title', 'feature.legal.description', 'ShieldCheck'],
      ['admin', '/admin', 'feature.admin.title', 'feature.admin.description', 'ShieldCheck'],
    ].map(([key, route, title_key, description_key, icon], index) => ({
      key, route, routes: [route], category: key === 'trade-control' ? 'automation' : 'account', title_key, description_key, icon,
      capability: null, availability: 'available', access: 'open', reason: null,
      data_state: 'not_applicable', health: 'not_applicable', placements: ['more'], actions: {},
      pin_allowed: key !== 'admin', primary_nav: false, sort_order: 500 + index * 10, recommendation_rank: null,
    }))],
  })
  for (const key of ['workflow-tasks', 'notifications', 'trade-control', 'membership', 'promotion', 'help', 'legal', 'admin']) {
    const item = decoded.items.find((entry) => entry.key === key)!
    assert.notEqual(localizeFeature(item, 'zh-Hans').title, item.titleKey)
    assert.notEqual(localizeFeature(item, 'zh-Hant').description, item.descriptionKey)
    assert.equal(featureOpenRoute(item), item.route)
  }
})

test('account center keeps the historical data-status key without duplicating the /account entry', () => {
  const decoded = decodeFeatureCatalog(payload)
  const source = readFileSync(new URL('../src/domain/featureCatalog.ts', import.meta.url), 'utf8')
  const accountEntries = decoded.items.filter((item) => item.route === '/account')
  assert.equal(accountEntries.length, 0)
  assert.match(source, /feature\.account_center\.title/)
  assert.match(source, /个人资料与账户中心/)
  assert.match(source, /個人資料與帳戶中心/)
})

test('new bounded contexts have safe routes and complete Simplified and Traditional copy', () => {
  const decoded = decodeFeatureCatalog({
    ...payload,
    items: [...payload.items, ...[
      ['ai-workspace', '/ai', 'feature.ai_workspace.title', 'feature.ai_workspace.description', 'Sparkles'],
      ['multi-agent-deliberation', '/deliberation', 'feature.multi_agent_deliberation.title', 'feature.multi_agent_deliberation.description', 'ShieldCheck'],
      ['csv-signal-import', '/lab?tab=csv-import', 'feature.csv_signal_import.title', 'feature.csv_signal_import.description', 'ClipboardCheck'],
    ].map(([key, route, title_key, description_key, icon], index) => ({
      key, route, routes: [route], category: 'research', title_key, description_key, icon,
      capability: key, availability: 'available', access: 'open', reason: null,
      data_state: 'ready', health: 'healthy', placements: ['more', 'secondary_nav'], actions: {},
      pin_allowed: true, primary_nav: false, sort_order: 250 + index * 10, recommendation_rank: index + 1,
    }))],
  })
  for (const key of ['ai-workspace', 'multi-agent-deliberation', 'csv-signal-import']) {
    const item = decoded.items.find((entry) => entry.key === key)!
    assert.notEqual(localizeFeature(item, 'zh-Hans').title, item.titleKey)
    assert.notEqual(localizeFeature(item, 'zh-Hant').description, item.descriptionKey)
    assert.equal(featureOpenRoute(item), item.route)
  }
})

test('status-bearing research entries remain readable while degraded but never pinnable', () => {
  const decoded = decodeFeatureCatalog(payload)
  const available = decoded.items.find((item) => item.key === 'stock-screener')!
  const degraded = { ...available, availability: 'degraded' as const, pinAllowed: false }
  const unavailable = { ...available, availability: 'unavailable' as const, pinAllowed: false }
  assert.equal(featureOpenRoute(degraded), '/discover?tool=screener')
  assert.equal(featureOpenRoute(unavailable), '/discover?tool=screener')
  assert.equal(featureOpenRoute({ ...degraded, actions: {} }), null)
  assert.equal(featureOpenRoute({ ...available, availability: 'planned', pinAllowed: false }), null)
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
  assert.match(source, /Promise\.resolve\(\)\s*\.then\(\(\) => onRecordRecent\(item\.key, catalogSnapshot\.preferences\.version\)\)/)
  assert.match(source, /<Link className="feature-card-main"/)
  assert.match(source, /<button className="feature-card-main" type="button" disabled/)
  assert.doesNotMatch(source, /preventDefault\(/)
  assert.match(source, /name="feature-search" autoComplete="off" spellCheck=\{false\}/)
  assert.match(source, /localizeFeatureReason\(item\.reason, locale\)/)
  assert.doesNotMatch(source, /等待主管/)
  const publicProps = source.match(/export interface MorePageProps \{([\s\S]*?)\n\}/)?.[1] ?? ''
  assert.doesNotMatch(publicProps, /onTogglePin\?:/)
  assert.doesNotMatch(source, /[\u3400-\u9fff]/)
})

test('More page remembers an explicit display choice and uses a responsive first-visit default', () => {
  assert.equal(readFeatureCatalogView(null, false), 'list')
  assert.equal(readFeatureCatalogView(null, true), 'icon')
  assert.equal(readFeatureCatalogView('icon', false), 'icon')
  assert.equal(readFeatureCatalogView('other', true), 'icon')
  const writes: Array<[string, string]> = []
  writeFeatureCatalogView({ setItem: (key, value) => writes.push([key, value]) }, 'list')
  assert.deepEqual(writes, [['ciclotrade.feature-catalog.view.v1', 'list']])
  assert.equal(MORE_PAGE_COPY['zh-Hans'].iconView, '图标')
  assert.equal(MORE_PAGE_COPY['zh-Hant'].iconView, '圖示')
  const source = readFileSync(new URL('../src/pages/MorePage.tsx', import.meta.url), 'utf8')
  assert.match(source, /window\.matchMedia\('\(max-width: 760px\)'\)\.matches/)
  assert.match(source, /window\.localStorage\.getItem\(FEATURE_CATALOG_VIEW_STORAGE_KEY\)/)
  assert.match(source, /aria-pressed=\{view === 'list'\}/)
  assert.match(source, /aria-pressed=\{view === 'icon'\}/)
  const styles = readFileSync(new URL('../src/styles/more.css', import.meta.url), 'utf8')
  assert.match(styles, /\.feature-view-toggle button \{[^}]*min-height: 44px/)
})

test('icon view pin actions keep an accessible name while list view retains visible copy', () => {
  const source = readFileSync(new URL('../src/pages/MorePage.tsx', import.meta.url), 'utf8')
  assert.match(source, /className="feature-pin" aria-label=\{pinLabel\} title=\{pinLabel\} aria-pressed=\{pinned\}/)
  assert.match(source, /<span className="feature-pin-label">\{pinned \? copy\.unpin : copy\.pin\}<\/span>/)
  assert.match(source, /const pinLabel = pinned \? copy\.unpin : copy\.pin/)
})
