export type UiLocale = 'zh-Hans' | 'zh-Hant'

export type FeatureCategory = 'discover' | 'research' | 'simulate' | 'review' | 'automation' | 'account'
export type FeatureAvailability = 'available' | 'locked' | 'planned' | 'degraded' | 'unavailable'
export type FeatureAccess = 'open' | 'upgrade' | 'wait' | 'retry'
export type FeatureDataState = 'ready' | 'delayed' | 'stale' | 'missing' | 'not_applicable'
export type FeatureHealth = 'healthy' | 'degraded' | 'unavailable' | 'not_applicable'
export type FeaturePlacement = 'more' | 'secondary_nav' | 'dashboard_card' | 'inspector' | 'drawer' | 'dialog' | 'overlay'
export type FeatureCatalogView = 'list' | 'icon'
export const FEATURE_CATALOG_VIEW_STORAGE_KEY = 'ciclotrade.feature-catalog.view.v1'
export interface FeatureActions {
  researchUrl?: string
  alertPrefill?: Record<string, string | number>
  paperPrefill?: Record<string, string | number>
}
export type FeatureIconName =
  | 'BellRing' | 'BookOpenCheck' | 'CalendarClock' | 'ChartCandlestick' | 'ClipboardCheck'
  | 'Gauge' | 'Grid2X2' | 'LifeBuoy' | 'ListFilter' | 'RadioTower' | 'ShieldCheck'
  | 'Sparkles' | 'Target' | 'WalletCards'

export interface FeatureCatalogItem {
  key: string
  route: string
  routes: string[]
  category: FeatureCategory
  titleKey: string
  descriptionKey: string
  icon: FeatureIconName
  capability: string | null
  availability: FeatureAvailability
  access: FeatureAccess
  reason: string | null
  dataState: FeatureDataState
  health: FeatureHealth
  placements: FeaturePlacement[]
  actions: FeatureActions
  pinAllowed: boolean
  primaryNav: boolean
  sortOrder: number
  recommendationRank: number | null
}

export interface FeaturePreferences {
  pinned: string[]
  recent: string[]
  version: number
}

export interface FeatureCatalogPayload {
  catalogVersion: string
  items: FeatureCatalogItem[]
  preferences: FeaturePreferences
}

export function featureOpenRoute(item: FeatureCatalogItem): string | null {
  if (item.availability === 'available') return item.route
  if (item.availability === 'locked' && item.access === 'upgrade') return '/membership'
  if ((item.availability === 'degraded' || item.availability === 'unavailable') && item.actions.researchUrl) {
    return item.actions.researchUrl
  }
  return null
}

export interface MorePageCopy {
  kicker: string
  title: string
  description: string
  hubEyebrow: string
  hubTitle: string
  hubDescription: string
  availableMetric: string
  categoryMetric: string
  pinnedMetric: string
  tierMetric: string
  deliberationStatus: string
  safetyNote: string
  searchLabel: string
  searchPlaceholder: string
  loadingTitle: string
  loadingDescription: string
  errorTitle: string
  retry: string
  disconnectedTitle: string
  disconnectedDescription: string
  pinnedSection: string
  recentSection: string
  recommendedSection: string
  noResultsTitle: string
  noResultsDescription: string
  pin: string
  unpin: string
  pinManagerTitle: string
  pinManagerDescription: string
  pinCount: string
  pinInvalid: string
  pinLimit: string
  pinSave: string
  pinSaving: string
  pinSaved: string
  pinSaveError: string
  recentSaveError: string
  viewLabel: string
  listView: string
  iconView: string
  categories: Record<FeatureCategory, string>
  availability: Record<FeatureAvailability, string>
}

export const MORE_PAGE_COPY: Record<UiLocale, MorePageCopy> = {
  'zh-Hans': {
    kicker: 'FEATURE DIRECTORY',
    title: '更多功能',
    description: '按任务找到全部工具。固定 3–5 个常用入口后，它们只会出现在桌面次级导航区。',
    hubEyebrow: '综合功能中心',
    hubTitle: '研究、提醒与账户服务',
    hubDescription: '主导航已有页面不重复，只保留独立工具与综合服务。',
    availableMetric: '可用工具',
    categoryMetric: '服务分类',
    pinnedMetric: '固定工具',
    tierMetric: '会员机器人',
    deliberationStatus: '牛熊页入口已恢复',
    safetyNote: 'AI 只提供研究观点，不执行交易',
    searchLabel: '搜索功能',
    searchPlaceholder: '搜索筛选器、期权、风险或复盘',
    loadingTitle: '正在加载功能目录',
    loadingDescription: '正在核对会员权限与服务状态。',
    errorTitle: '功能目录暂时无法加载',
    retry: '重新加载',
    disconnectedTitle: '功能目录暂不可用',
    disconnectedDescription: '暂时无法取得功能与权限状态，请稍后重新加载。',
    pinnedSection: '已固定（仅桌面次级导航显示）',
    recentSection: '最近使用',
    recommendedSection: '为你推荐',
    noResultsTitle: '没有找到相关工具',
    noResultsDescription: '试试“筛选器”“期权”“风险”或“复盘”。',
    pin: '固定工具',
    unpin: '取消固定',
    pinManagerTitle: '桌面次级导航',
    pinManagerDescription: '先在本页选择，再一次保存 3–5 个工具；也可以清空全部固定项。',
    pinCount: '已选择 {count} / 5',
    pinInvalid: '请选择 3–5 个工具，或清空全部固定项。',
    pinLimit: '最多只能固定 5 个工具。',
    pinSave: '保存固定项',
    pinSaving: '正在保存…',
    pinSaved: '已提交，正在等待目录刷新。',
    pinSaveError: '固定设置保存失败，请重试。',
    recentSaveError: '最近使用记录保存失败，功能仍可正常打开。',
    viewLabel: '功能显示方式',
    listView: '列表',
    iconView: '图标',
    categories: { discover: '发现机会', research: '研究工具', simulate: '模拟与风险', review: '组合与复盘', automation: '自动化', account: '账户服务' },
    availability: { available: '可用', locked: '当前未开放', planned: '开发中', degraded: '服务降级', unavailable: '暂不可用' },
  },
  'zh-Hant': {
    kicker: 'FEATURE DIRECTORY',
    title: '更多功能',
    description: '按任務找到全部工具。固定 3–5 個常用入口後，它們只會出現在桌面次級導覽區。',
    hubEyebrow: '綜合功能中心',
    hubTitle: '研究、提醒與帳戶服務',
    hubDescription: '主導覽已有頁面不重複，只保留獨立工具與綜合服務。',
    availableMetric: '可用工具',
    categoryMetric: '服務分類',
    pinnedMetric: '固定工具',
    tierMetric: '會員機器人',
    deliberationStatus: '牛熊頁入口已恢復',
    safetyNote: 'AI 只提供研究觀點，不執行交易',
    searchLabel: '搜尋功能',
    searchPlaceholder: '搜尋篩選器、期權、風險或複盤',
    loadingTitle: '正在載入功能目錄',
    loadingDescription: '正在核對會員權限與服務狀態。',
    errorTitle: '功能目錄暫時無法載入',
    retry: '重新載入',
    disconnectedTitle: '功能目錄暫不可用',
    disconnectedDescription: '暫時無法取得功能與權限狀態，請稍後重新載入。',
    pinnedSection: '已固定（僅桌面次級導覽顯示）',
    recentSection: '最近使用',
    recommendedSection: '為你推薦',
    noResultsTitle: '沒有找到相關工具',
    noResultsDescription: '試試「篩選器」「期權」「風險」或「複盤」。',
    pin: '固定工具',
    unpin: '取消固定',
    pinManagerTitle: '桌面次級導覽',
    pinManagerDescription: '先在本頁選擇，再一次儲存 3–5 個工具；也可以清空全部固定項。',
    pinCount: '已選擇 {count} / 5',
    pinInvalid: '請選擇 3–5 個工具，或清空全部固定項。',
    pinLimit: '最多只能固定 5 個工具。',
    pinSave: '儲存固定項',
    pinSaving: '正在儲存…',
    pinSaved: '已提交，正在等待目錄重新整理。',
    pinSaveError: '固定設定儲存失敗，請重試。',
    recentSaveError: '最近使用記錄儲存失敗，功能仍可正常開啟。',
    viewLabel: '功能顯示方式',
    listView: '列表',
    iconView: '圖示',
    categories: { discover: '發現機會', research: '研究工具', simulate: '模擬與風險', review: '組合與複盤', automation: '自動化', account: '帳戶服務' },
    availability: { available: '可用', locked: '目前未開放', planned: '開發中', degraded: '服務降級', unavailable: '暫不可用' },
  },
}

export function formatMorePageCopy(template: string, values: Record<string, string | number>): string {
  return Object.entries(values).reduce((result, [key, value]) => result.replaceAll(`{${key}}`, String(value)), template)
}

export function isValidPinnedSelection(keys: string[]): boolean {
  return keys.length === 0 || (keys.length >= 3 && keys.length <= 5)
}

export function toggleDraftPin(keys: string[], key: string): string[] {
  if (keys.includes(key)) return keys.filter((candidate) => candidate !== key)
  return keys.length < 5 ? [...keys, key] : keys
}

export function recordRecentFeature(keys: string[], key: string): string[] {
  return [key, ...keys.filter((candidate) => candidate !== key)].slice(0, 8)
}

export function readFeatureCatalogView(raw: string | null | undefined, isPhone: boolean): FeatureCatalogView {
  return raw === 'list' || raw === 'icon' ? raw : isPhone ? 'icon' : 'list'
}

export function writeFeatureCatalogView(storage: Pick<Storage, 'setItem'> | null | undefined, view: FeatureCatalogView): void {
  try { storage?.setItem(FEATURE_CATALOG_VIEW_STORAGE_KEY, view) } catch { /* storage can be unavailable */ }
}

export const FEATURE_ICON_NAMES = new Set<FeatureIconName>([
  'BellRing', 'BookOpenCheck', 'CalendarClock', 'ChartCandlestick', 'ClipboardCheck',
  'Gauge', 'Grid2X2', 'LifeBuoy', 'ListFilter', 'RadioTower', 'ShieldCheck', 'Sparkles', 'Target', 'WalletCards',
])

const ROUTE_ALLOWLIST = /^\/(?:account|admin|ai|deliberation|discover|earnings|feedback|help|lab|legal|membership|more|notifications|paper|portfolio|promotion|reports|research|today|trade|workflow)(?:[/?#].*)?$/
const CATEGORIES = new Set<FeatureCategory>(['discover', 'research', 'simulate', 'review', 'automation', 'account'])
const AVAILABILITIES = new Set<FeatureAvailability>(['available', 'locked', 'planned', 'degraded', 'unavailable'])
const ACCESS = new Set<FeatureAccess>(['open', 'upgrade', 'wait', 'retry'])
const DATA_STATES = new Set<FeatureDataState>(['ready', 'delayed', 'stale', 'missing', 'not_applicable'])
const HEALTH_STATES = new Set<FeatureHealth>(['healthy', 'degraded', 'unavailable', 'not_applicable'])
const PLACEMENTS = new Set<FeaturePlacement>(['more', 'secondary_nav', 'dashboard_card', 'inspector', 'drawer', 'dialog', 'overlay'])
const PRIMARY_NAV_ROUTES = new Set(['/today', '/discover', '/research', '/paper', '/portfolio', '/more'])
const DRAFT_ACTION_FIELDS = new Set(['market', 'symbol', 'price', 'side', 'reference_id'])

const COPY: Record<string, { hans: string; hant: string }> = {
  'feature.today.title': { hans: '今日', hant: '今日' },
  'feature.today.description': { hans: '查看今天的行动、等待项、风险和数据状态。', hant: '查看今天的行動、等待項、風險和資料狀態。' },
  'feature.discover.title': { hans: '发现', hant: '發現' },
  'feature.discover.description': { hans: '从筛选器、榜单和事件中发现研究机会。', hant: '從篩選器、榜單和事件中發現研究機會。' },
  'feature.research.title': { hans: '行情与研究', hant: '行情與研究' },
  'feature.research.description': { hans: '在图表、证据和基本面之间完成研究。', hant: '在圖表、證據和基本面之間完成研究。' },
  'feature.portfolio.title': { hans: '组合与复盘', hant: '組合與複盤' },
  'feature.portfolio.description': { hans: '跟踪持仓、风险、成绩和计划偏差。', hant: '追蹤持倉、風險、成績和計畫偏差。' },
  'feature.more.title': { hans: '更多功能', hant: '更多功能' },
  'feature.more.description': { hans: '搜索、固定和管理全部工具。', hant: '搜尋、固定和管理全部工具。' },
  'feature.stock_screener.title': { hans: '股票筛选器', hant: '股票篩選器' },
  'feature.stock_screener.description': { hans: '按市场、行业、财务与技术条件寻找候选股票。', hant: '按市場、行業、財務與技術條件尋找候選股票。' },
  'feature.market_heatmap.title': { hans: '市场热力图', hant: '市場熱力圖' },
  'feature.market_heatmap.description': { hans: '按官方行业分类观察涨跌、权重与成分股。', hant: '按官方行業分類觀察漲跌、權重與成分股。' },
  'feature.earnings_calendar.title': { hans: '财报与事件日历', hant: '財報與事件日曆' },
  'feature.earnings_calendar.description': { hans: '集中查看美股与 A 股财报和关键市场事件。', hant: '集中查看美股與 A 股財報和關鍵市場事件。' },
  'feature.chart_workspace.title': { hans: 'K线研究工作台', hant: 'K線研究工作台' },
  'feature.chart_workspace.description': { hans: '在完整图表空间研究价格、成交和证据。', hant: '在完整圖表空間研究價格、成交和證據。' },
  'feature.price_alerts.title': { hans: '价格与条件预警', hant: '價格與條件預警' },
  'feature.price_alerts.description': { hans: '创建可隐藏、可删除并可追踪的市场预警。', hant: '建立可隱藏、可刪除並可追蹤的市場預警。' },
  'feature.option_lab.title': { hans: '期权研究', hant: '期權研究' },
  'feature.option_lab.description': { hans: '查看期权链、Greeks、IV 与有限风险结构。', hant: '查看期權鏈、Greeks、IV 與有限風險結構。' },
  'feature.earnings_forecast.title': { hans: '业绩预测', hant: '業績預測' },
  'feature.earnings_forecast.description': { hans: '追踪七日观点变化、区间预测与事后复盘。', hant: '追蹤七日觀點變化、區間預測與事後復盤。' },
  'feature.strategy_research.title': { hans: '策略研究覆盖', hant: '策略研究覆蓋' },
  'feature.strategy_research.description': { hans: '查看 13 股稳定链与 97 只股票扩容链的研究证据和覆盖状态。', hant: '查看 13 股穩定鏈與 97 隻股票擴容鏈的研究證據和覆蓋狀態。' },
  'feature.ai_workspace.title': { hans: 'AI 研究工作台', hant: 'AI 研究工作台' },
  'feature.ai_workspace.description': { hans: '在服务端证据、可控记忆与公开任务轨迹上继续结构化研究。', hant: '在服務端證據、可控記憶與公開任務軌跡上繼續結構化研究。' },
  'feature.multi_agent_deliberation.title': { hans: '多智能体审议', hant: '多智能體審議' },
  'feature.multi_agent_deliberation.description': { hans: '由四个研究席核对支持、反向、风险与未知信息，并保留证据版本。', hant: '由四個研究席核對支持、反向、風險與未知資訊，並保留證據版本。' },
  'feature.csv_signal_import.title': { hans: 'CSV 股票记录导入', hant: 'CSV 股票記錄匯入' },
  'feature.csv_signal_import.description': { hans: '把受控 CSV 股票记录导入专业实验室，校验后保留来源与审计。', hant: '把受控 CSV 股票記錄匯入專業實驗室，校驗後保留來源與稽核。' },
  'feature.workflow_tasks.title': { hans: 'Workflow 任务', hant: 'Workflow 任務' },
  'feature.workflow_tasks.description': { hans: '查看真实任务状态、公开事件、来源版本与结果收据。', hant: '查看真實任務狀態、公開事件、來源版本與結果收據。' },
  'feature.risk_calculator.title': { hans: '风险与仓位计算器', hant: '風險與倉位計算器' },
  'feature.risk_calculator.description': { hans: '在行动前计算仓位、最大亏损与购买力影响。', hant: '在行動前計算倉位、最大虧損與購買力影響。' },
  'feature.personal_paper.title': { hans: '个人模拟交易', hant: '個人模擬交易' },
  'feature.personal_paper.description': { hans: '使用独立 USD 10,000 赛季练习订单与纪律。', hant: '使用獨立 USD 10,000 賽季練習訂單與紀律。' },
  'feature.portfolio_review.title': { hans: '组合与复盘', hant: '組合與復盤' },
  'feature.portfolio_review.description': { hans: '检查持仓、集中度、计划偏差和交易结果。', hant: '檢查持倉、集中度、計劃偏差和交易結果。' },
  'feature.research_reports.title': { hans: '研究报告', hant: '研究報告' },
  'feature.research_reports.description': { hans: '查看可核验的研究记录、解释和绩效摘要。', hant: '查看可核驗的研究記錄、解釋和績效摘要。' },
  'feature.account_center.title': { hans: '个人资料与账户中心', hant: '個人資料與帳戶中心' },
  'feature.account_center.description': { hans: '管理个人资料、外观、授权、会话、安全与数据状态；这里不会重复提供交易入口。', hant: '管理個人資料、外觀、授權、工作階段、安全與資料狀態；這裡不會重複提供交易入口。' },
  'feature.feedback.title': { hans: '反馈与建议', hant: '反饋與建議' },
  'feature.feedback.description': { hans: '提交产品反馈，并在账户内查看处理状态。', hant: '提交產品反饋，並在帳戶內查看處理狀態。' },
  'feature.notifications.title': { hans: '通知中心', hant: '通知中心' },
  'feature.notifications.description': { hans: '查看真实站内通知、Telegram 状态与已验证投递结果。', hant: '查看真實站內通知、Telegram 狀態與已驗證投遞結果。' },
  'feature.trade_control.title': { hans: '券商与自动实盘控制', hant: '券商與自動實盤控制' },
  'feature.trade_control.description': { hans: '管理资格申请、mandate、独立门控、暂停与不可变控制收据。', hant: '管理資格申請、mandate、獨立門控、暫停與不可變控制收據。' },
  'feature.membership.title': { hans: '会员方案', hant: '會員方案' },
  'feature.membership.description': { hans: '查看公开三档方案、当前权益、历史订单与权威价格拆分。', hant: '查看公開三檔方案、目前權益、歷史訂單與權威價格拆分。' },
  'feature.promotion.title': { hans: '推广与资金账本', hant: '推廣與資金帳本' },
  'feature.promotion.description': { hans: '查看推荐归因、佣金、奖金、债务、提现资格与审计记录。', hant: '查看推薦歸因、佣金、獎金、債務、提現資格與稽核記錄。' },
  'feature.help.title': { hans: '帮助中心', hant: '幫助中心' },
  'feature.help.description': { hans: '按业务链路查找账户域、研究、模拟、券商和数据状态说明。', hant: '按業務鏈路查找帳戶域、研究、模擬、券商和資料狀態說明。' },
  'feature.legal.title': { hans: '法律与政策', hant: '法律與政策' },
  'feature.legal.description': { hans: '查看隐私、风险、账户隔离与版本化同意政策入口。', hant: '查看隱私、風險、帳戶隔離與版本化同意政策入口。' },
  'feature.admin.title': { hans: '超级管理员后台', hant: '超級管理員後台' },
  'feature.admin.description': { hans: '仅超级管理员查看审核、政策、运行状态和审计控制。', hant: '僅超級管理員查看審核、政策、執行狀態和稽核控制。' },
  'feature.option_live_automation.title': { hans: '受控期权自动交易', hant: '受控期權自動交易' },
  'feature.option_live_automation.description': { hans: '申请制、逐策略确认的有限风险实盘路线。', hant: '申請制、逐策略確認的有限風險實盤路線。' },
}

const FEATURE_REASON_COPY: Readonly<Record<string, string>> = {
  '尚未取得可核验的数据新鲜度或服务健康证明。': '尚未取得可核驗的資料新鮮度或服務健康證明。',
  '运行状态格式无效，功能已安全停用。': '執行狀態格式無效，功能已安全停用。',
  '数据状态或服务健康状态无法验证，功能已安全停用。': '資料狀態或服務健康狀態無法驗證，功能已安全停用。',
  '未取得可核验的数据新鲜度证据，功能已安全停用。': '未取得可核驗的資料新鮮度證據，功能已安全停用。',
  '运行状态时间晚于当前时钟，功能已安全停用。': '執行狀態時間晚於目前時鐘，功能已安全停用。',
  '运行状态证明已超过 5 分钟，请刷新后重试。': '執行狀態證明已超過 5 分鐘，請重新整理後再試。',
  '运行状态组合无法验证，功能已安全停用。': '執行狀態組合無法驗證，功能已安全停用。',
  '当前数据或服务未达到可用门限。': '目前資料或服務未達到可用門檻。',
  '研究证据已过期；可查看只读状态，但不能固定为常用工具。': '研究證據已過期；可檢視只讀狀態，但不能固定為常用工具。',
  '研究覆盖尚未完整或服务正在降级；可查看只读状态，但不能固定为常用工具。': '研究覆蓋尚未完整或服務正在降級；可檢視只讀狀態，但不能固定為常用工具。',
  '该能力仍在独立开发与验收中，当前会员不包含此功能。': '該功能仍在獨立開發與驗收中，目前會員不包含此功能。',
  '当前会员未包含此研究深度；风险与数据状态仍永久免费可见。': '目前會員未包含此研究深度；風險與資料狀態仍可永久免費查看。',
  'sales_unavailable: 该能力仅保留历史有效权益，当前不公开新购或升级。': 'sales_unavailable：該功能僅保留歷史有效權益，目前不公開新購或升級。',
}

function record(value: unknown, message: string): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(message)
  return value as Record<string, unknown>
}

function stringValue(value: unknown, name: string): string {
  if (typeof value !== 'string' || !value.trim()) throw new Error(`invalid feature ${name}`)
  return value
}

function nullableString(value: unknown, name: string): string | null {
  if (value === null) return null
  return stringValue(value, name)
}

function routeValue(value: unknown, name: string): string {
  const route = stringValue(value, name)
  if (!ROUTE_ALLOWLIST.test(route)) throw new Error('invalid feature route')
  return route
}

function decodeActions(value: unknown): FeatureActions {
  const raw = record(value, 'invalid feature actions')
  const unexpected = Object.keys(raw).filter((key) => !['research_url', 'alert_prefill', 'paper_prefill'].includes(key))
  if (unexpected.length) throw new Error('invalid feature action')
  const prefill = (item: unknown, name: string): Record<string, string | number> => {
    const draft = record(item, `invalid ${name}`)
    for (const [key, field] of Object.entries(draft)) {
      if (!DRAFT_ACTION_FIELDS.has(key) || (typeof field !== 'string' && typeof field !== 'number')) throw new Error('invalid feature draft action')
    }
    return draft as Record<string, string | number>
  }
  const result: FeatureActions = {}
  if ('research_url' in raw) result.researchUrl = routeValue(raw.research_url, 'research action route')
  if ('alert_prefill' in raw) result.alertPrefill = prefill(raw.alert_prefill, 'alert prefill')
  if ('paper_prefill' in raw) result.paperPrefill = prefill(raw.paper_prefill, 'paper prefill')
  return result
}

export function decodeFeatureCatalog(value: unknown): FeatureCatalogPayload {
  const payload = record(value, 'invalid feature catalog payload')
  const catalogVersion = stringValue(payload.catalog_version, 'catalog version')
  if (!Array.isArray(payload.items)) throw new Error('invalid feature items')
  const items = payload.items.map((raw): FeatureCatalogItem => {
    const item = record(raw, 'invalid feature item')
    const route = routeValue(item.route, 'route')
    if (!Array.isArray(item.routes) || !item.routes.length) throw new Error('invalid feature routes')
    const routes = item.routes.map((candidate) => routeValue(candidate, 'route'))
    if (route !== routes[0]) throw new Error('feature route compatibility mismatch')
    const icon = stringValue(item.icon, 'icon')
    const category = stringValue(item.category, 'category') as FeatureCategory
    const availability = stringValue(item.availability, 'availability') as FeatureAvailability
    const access = stringValue(item.access, 'access') as FeatureAccess
    const dataState = stringValue(item.data_state, 'data state') as FeatureDataState
    const health = stringValue(item.health, 'health') as FeatureHealth
    if (!FEATURE_ICON_NAMES.has(icon as FeatureIconName)) throw new Error('invalid feature icon')
    if (!CATEGORIES.has(category) || !AVAILABILITIES.has(availability) || !ACCESS.has(access) || !DATA_STATES.has(dataState) || !HEALTH_STATES.has(health)) {
      throw new Error('invalid feature state')
    }
    if (['locked', 'planned', 'degraded', 'unavailable'].includes(availability) && (typeof item.reason !== 'string' || !item.reason.trim())) {
      throw new Error('feature state requires a reason')
    }
    if (typeof item.pin_allowed !== 'boolean' || typeof item.primary_nav !== 'boolean') throw new Error('invalid feature flags')
    if (availability === 'planned' && item.pin_allowed) throw new Error('planned feature cannot be pinned')
    if (!Array.isArray(item.placements) || !item.placements.length || item.placements.some((placement) => typeof placement !== 'string' || !PLACEMENTS.has(placement as FeaturePlacement))) throw new Error('invalid feature placements')
    if (!Number.isSafeInteger(item.sort_order)) throw new Error('invalid feature sort order')
    if (item.recommendation_rank !== null && !Number.isSafeInteger(item.recommendation_rank)) throw new Error('invalid recommendation rank')
    const key = stringValue(item.key, 'key')
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(key)) throw new Error('invalid feature key')
    return {
      key,
      route,
      routes,
      category,
      titleKey: stringValue(item.title_key, 'title key'),
      descriptionKey: stringValue(item.description_key, 'description key'),
      icon: icon as FeatureIconName,
      capability: nullableString(item.capability, 'capability'),
      availability,
      access,
      reason: nullableString(item.reason, 'reason'),
      dataState,
      health,
      placements: [...new Set(item.placements as FeaturePlacement[])],
      actions: decodeActions(item.actions),
      pinAllowed: item.pin_allowed,
      primaryNav: item.primary_nav,
      sortOrder: Number(item.sort_order),
      recommendationRank: item.recommendation_rank === null ? null : Number(item.recommendation_rank),
    }
  })
  const known = new Set(items.map((item) => item.key))
  if (known.size !== items.length) throw new Error('duplicate feature key')
  const primaryRoutes = items.filter((item) => item.primaryNav).map((item) => item.route)
  if (primaryRoutes.length !== 6 || primaryRoutes.some((route) => !PRIMARY_NAV_ROUTES.has(route)) || new Set(primaryRoutes).size !== 6) throw new Error('invalid primary navigation')
  if (items.some((item) => item.primaryNav && item.pinAllowed)) throw new Error('primary navigation cannot be pinned')
  const preferences = record(payload.preferences, 'invalid feature preferences')
  if (!Array.isArray(preferences.pinned) || !Array.isArray(preferences.recent) || !Number.isSafeInteger(preferences.version) || Number(preferences.version) < 0) {
    throw new Error('invalid feature preferences')
  }
  const cleanKeys = (raw: unknown[], maximum: number) => Array.from(new Set(raw.filter((key): key is string => typeof key === 'string' && known.has(key)))).slice(0, maximum)
  const pinned = cleanKeys(preferences.pinned, 5)
  if (![0, 3, 4, 5].includes(pinned.length)) throw new Error('invalid pinned feature count')
  if (pinned.some((key) => !items.find((item) => item.key === key)?.pinAllowed)) throw new Error('invalid pinned feature')
  return {
    catalogVersion,
    items: items.sort((left, right) => left.sortOrder - right.sortOrder || left.key.localeCompare(right.key)),
    preferences: { pinned, recent: cleanKeys(preferences.recent, 8), version: Number(preferences.version) },
  }
}

export function localizeFeature(item: FeatureCatalogItem, locale: UiLocale) {
  const title = COPY[item.titleKey]
  const description = COPY[item.descriptionKey]
  return {
    title: locale === 'zh-Hant' ? title?.hant ?? item.titleKey : title?.hans ?? item.titleKey,
    description: locale === 'zh-Hant' ? description?.hant ?? item.descriptionKey : description?.hans ?? item.descriptionKey,
  }
}

export function localizeFeatureReason(reason: string | null, locale: UiLocale): string | null {
  if (reason === null || locale === 'zh-Hans') return reason
  return FEATURE_REASON_COPY[reason] ?? reason
}

export function featureSearchText(item: FeatureCatalogItem): string {
  const copy = localizeFeature(item, 'zh-Hans')
  const traditional = localizeFeature(item, 'zh-Hant')
  return `${copy.title} ${copy.description} ${traditional.title} ${traditional.description} ${item.key}`.toLocaleLowerCase()
}

export function filterFeatureCatalog(items: FeatureCatalogItem[], query: string, _locale: UiLocale): FeatureCatalogItem[] {
  const normalized = query.trim().toLocaleLowerCase()
  return normalized ? items.filter((item) => featureSearchText(item).includes(normalized)) : items
}
