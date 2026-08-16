import { ArrowUpRight, FlaskConical, LoaderCircle, LockKeyhole, ShieldAlert } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { EarningsOptionDetail, EarningsOptionReferenceItem } from '../domain/earningsForecast.ts'

function money(value: number, currency: 'USD' | 'CNY') {
  return new Intl.NumberFormat('zh-CN', { style: 'currency', currency, maximumFractionDigits: 2 }).format(value)
}

function percent(value: number) {
  return `${value.toFixed(1)}%`
}

function structureLabel(value: string) {
  return ({ LONG_CALL: '买入看涨', LONG_PUT: '买入看跌', LONG_STRADDLE: '买入跨式', LONG_STRANGLE: '买入宽跨式' } as Record<string, string>)[value] ?? value
}

export function EarningsOptionStructure({
  option,
  currency,
  references = [],
  selectedOptionId = null,
  onSelectReference,
  loading = false,
}: {
  option: EarningsOptionDetail | null
  currency: 'USD' | 'CNY'
  references?: EarningsOptionReferenceItem[]
  selectedOptionId?: string | null
  onSelectReference?: (optionId: string) => void
  loading?: boolean
}) {
  if (loading) {
    return (
      <section className="earnings-option earnings-option-empty" aria-labelledby="earnings-option-title" aria-busy="true">
        <header className="earnings-section-heading">
          <div><span>DEFINED RISK / OPTIONS</span><h2 id="earnings-option-title">有限风险期权结构</h2></div>
          <span className="earnings-boundary-chip"><FlaskConical size={14} /> 研究模块</span>
        </header>
        <div className="earnings-empty-state"><LoaderCircle className="is-spinning" /><strong>正在读取封存结构</strong><span>只会显示与当前 D-day 快照绑定、且当前账户有权限读取的结构。</span></div>
      </section>
    )
  }
  if (!option) {
    return (
      <section className="earnings-option earnings-option-empty" aria-labelledby="earnings-option-title">
        <header className="earnings-section-heading">
          <div><span>DEFINED RISK / OPTIONS</span><h2 id="earnings-option-title">有限风险期权结构</h2></div>
          <span className="earnings-boundary-chip"><FlaskConical size={14} /> 研究模块</span>
        </header>
        <div className="earnings-empty-state">
          <FlaskConical />
          <strong>当前事件没有可读取的关联结构</strong>
          <span>只有后端返回已封存的期权研究编号后才会显示合约腿、成本、最大亏损与 IV crush；页面不会自行推算或拼凑结构。</span>
        </div>
      </section>
    )
  }

  if (option.state === 'locked') {
    return (
      <section className="earnings-option earnings-option-locked" aria-labelledby="earnings-option-title">
        <header className="earnings-section-heading">
          <div><span>DEFINED RISK / LOCKED</span><h2 id="earnings-option-title">有限风险期权结构</h2></div>
          <span className="earnings-boundary-chip is-locked"><LockKeyhole size={14} /> 权限锁定</span>
        </header>
        <div className="earnings-empty-state">
          <LockKeyhole />
          <strong>该研究需要期权专业权限</strong>
          <span>锁定状态不会读取合约腿、报价、成本或模型细节。</span>
          {option.upgrade_path ? (
            <Link className="button primary" to={option.upgrade_path}>查看会员方案 <ArrowUpRight size={15} /></Link>
          ) : (
            <button className="button secondary" type="button" disabled>当前不开放新购</button>
          )}
        </div>
      </section>
    )
  }

  return (
    <section className="earnings-option" aria-labelledby="earnings-option-title">
      <header className="earnings-section-heading">
        <div><span>DEFINED RISK / OPTIONS</span><h2 id="earnings-option-title">{structureLabel(option.structure_type)}</h2></div>
        <span className="earnings-boundary-chip"><ShieldAlert size={14} /> 最大亏损已定义</span>
      </header>

      {references.length > 1 && <div className="earnings-option-tabs" role="tablist" aria-label="有限风险期权结构">
        {references.map((reference) => <button
          className={reference.option_id === selectedOptionId ? 'is-selected' : ''}
          type="button"
          role="tab"
          aria-selected={reference.option_id === selectedOptionId}
          onClick={() => onSelectReference?.(reference.option_id)}
          key={reference.option_id}
        >{structureLabel(reference.structure_type)}</button>)}
      </div>}

      <dl className="earnings-option-metrics">
        <div><dt>总权利金</dt><dd>{money(option.total_premium, currency)}</dd></div>
        <div className="is-risk"><dt>最大亏损</dt><dd>{money(option.max_loss, currency)}</dd></div>
        <div><dt>下方盈亏平衡</dt><dd>{option.lower_breakeven === null ? '不适用' : option.lower_breakeven.toFixed(2)}</dd></div>
        <div><dt>上方盈亏平衡</dt><dd>{option.upper_breakeven === null ? '不适用' : option.upper_breakeven.toFixed(2)}</dd></div>
        <div><dt>模型预期波幅</dt><dd>{percent(option.model_expected_move_pct)}</dd></div>
        <div><dt>盈亏平衡外概率</dt><dd>{percent(option.probability_outside_breakeven * 100)}</dd></div>
      </dl>

      <div className="earnings-option-grid">
        <div className="earnings-option-legs">
          <h3>合约腿</h3>
          {option.legs.map((leg) => (
            <article key={leg.contract_id}>
              <span className={`earnings-option-right is-${leg.right.toLowerCase()}`}>{leg.right}</span>
              <strong>{leg.strike.toFixed(2)} · {leg.expiry}</strong>
              <small>{leg.quantity} × {leg.multiplier} · Ask {leg.ask.toFixed(2)} · IV {percent(leg.implied_volatility * 100)}</small>
            </article>
          ))}
          <dl className="earnings-cost-ledger">
            <div><dt>手续费</dt><dd>{money(option.commission_cost, currency)}</dd></div>
            <div><dt>点差成本</dt><dd>{money(option.spread_cost, currency)}</dd></div>
            <div><dt>滑点成本</dt><dd>{money(option.slippage_cost, currency)}</dd></div>
          </dl>
        </div>

        <div className="earnings-iv-table">
          <h3>IV crush 情景</h3>
          <div role="table" aria-label="隐含波动率下降情景">
            <div role="row" className="earnings-table-head"><span role="columnheader">IV 变化</span><span role="columnheader">结构估值</span><span role="columnheader">成本后损益</span></div>
            {option.iv_crush_scenarios.map((scenario) => (
              <div role="row" key={scenario.relative_iv_change_pct}>
                <span role="cell">{percent(scenario.relative_iv_change_pct)}</span>
                <span role="cell">{money(scenario.estimated_structure_value, currency)}</span>
                <span role="cell" className={scenario.estimated_pnl_after_costs >= 0 ? 'is-positive' : 'is-negative'}>{money(scenario.estimated_pnl_after_costs, currency)}</span>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="earnings-action-contract">
        <span><strong>失效条件</strong>{option.action_contract.invalidation}</span>
        <span><strong>退出条件</strong>{option.action_contract.exit}</span>
        <span><strong>执行边界</strong>research-only · no automatic order</span>
      </div>
    </section>
  )
}
