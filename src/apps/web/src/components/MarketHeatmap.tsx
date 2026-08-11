import { AlertTriangle, Clock3, Info, Tags } from 'lucide-react'
import type { Market } from '../types'

interface MarketHeatmapProps {
  market: Market
  authenticated: boolean
}

const REQUIRED_INDUSTRY_FIELDS = [
  'taxonomy_source',
  'taxonomy_code',
  'taxonomy_level',
  'label',
  'return_pct',
  'constituent_count',
  'as_of',
] as const

export function MarketHeatmap({ market, authenticated }: MarketHeatmapProps) {
  const marketLabel = market === 'US' ? '美股' : 'A股'

  return <section className="market-preview-module" aria-label={`${marketLabel}行业与主题数据状态`}>
    <header className="heatmap-heading">
      <div><span><Info size={15} /> MARKET HEATMAP</span><strong>{marketLabel}行业与主题热力图</strong></div>
      <small><Clock3 size={13} /> 聚合行情合同未接入</small>
    </header>

    <div className="market-preview-note" role="note">
      <AlertTriangle size={16} />
      <span><strong>{authenticated ? '当前 API 没有返回可验证的行业聚合数据' : '登录前不展示行业行情'}</strong> 为避免把静态分类目录伪装成实时热力图，当前不绘制涨跌方块、不排序，也不提供代表股票。</span>
    </div>

    <div className="heatmap-contract-fields" aria-label="行业热力图所需数据字段">
      <strong>待接入合同</strong>
      <span>{REQUIRED_INDUSTRY_FIELDS.join(' · ')}</span>
    </div>

    <div className="market-heatmap-empty" data-state="unavailable">
      <Tags />
      <strong>行业热力图暂不可用</strong>
      <span>只有 API 返回统一层级的权威分类、成分数、聚合收益与统计时间后才会显示。一级行业只作分组标题；行业组与行业不会在同一层级混放。</span>
      <small>来源状态：未接入 · 更新时间：暂无</small>
    </div>

    <section className="market-theme-taxonomy" aria-label="市场主题与概念数据状态">
      <header><strong>市场主题 / 概念</strong><small>独立合同待接入</small></header>
      <div><Tags /><span><strong>主题数据暂不可用</strong> “光通信”等市场主题只会进入独立主题合同，不会写入官方行业分类树；缺少主题编号、定义版本、成分规则与统计口径时不创建方块。</span></div>
    </section>
  </section>
}
