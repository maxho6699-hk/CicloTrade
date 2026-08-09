import { AlertTriangle, BellRing, Bot, CheckCircle2, Clock3, Link2, RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useWorkspace } from '../api/workspace-context'
import { BrowserApiError, createPriceAlert, saveTelegramEvents } from '../api/client'
import { PageHeader } from '../components/PageHeader'
import { WorkspaceState } from '../components/WorkspaceState'
import { deliveryRecords } from '../data/workspace'
import { getFormatLocale } from '../i18n/runtime'

const defaultEvents = [
  { key: 'stock_signal', label: '正股买卖建议', note: '买入、加仓、持有、减仓与退出', enabled: true, level: '高级会员' },
  { key: 'option_signal', label: '期权策略建议', note: '合约、到期日、执行价与最大风险', enabled: true, level: '专业会员' },
  { key: 'risk_rejected', label: '风险与止损提醒', note: '仓位、回撤、冷却期与系统暂停', enabled: true, level: '标准会员' },
  { key: 'order_filled', label: '订单与成交状态', note: '提交、成交、拒绝与异常恢复', enabled: true, level: '标准会员' },
  { key: 'membership_update', label: '会员与账单', note: '到期提醒、付款结果与权益变更', enabled: false, level: '全部会员' },
]

export function NotificationsPage() {
  const [searchParams] = useSearchParams()
  const workspace = useWorkspace()
  const [events, setEvents] = useState(defaultEvents)
  const [eventStatus, setEventStatus] = useState('')
  const [alertSymbol, setAlertSymbol] = useState(searchParams.get('symbol') ?? 'AAPL')
  const [alertPrice, setAlertPrice] = useState(Number(searchParams.get('price') ?? 220))
  const [alertStatus, setAlertStatus] = useState('')
  const [connectionStatus, setConnectionStatus] = useState('')
  const [showAuditNote, setShowAuditNote] = useState(false)
  const telegram = workspace.data?.telegram
  const shownDeliveries = workspace.mode === 'authenticated' ? [] : deliveryRecords

  useEffect(() => {
    const stored = workspace.data?.telegram.events
    if (!stored) return
    setEvents((items) => items.map((item) => item.key in stored ? { ...item, enabled: stored[item.key] } : item))
  }, [workspace.data])

  return (
    <div className="page operations-page">
      <PageHeader kicker="DELIVERY / TELEGRAM" title="通知中心" description="每种事件独立控制，并显示最后一次发送结果、失败原因和恢复状态。" />
      <WorkspaceState />
      <section className="telegram-hero data-panel">
        <div className="telegram-identity"><span className="channel-icon"><Bot size={25} /></span><div><span>TELEGRAM PRIVATE DESK</span><h2>{telegram?.bound ? '个人服务台已绑定' : '尚未绑定个人服务台'}</h2><p>{telegram?.verified ? '账户已验证' : '账户未验证'} · {telegram?.consented ? '已同意接收通知' : '尚未授权通知'} · Chat ID {telegram?.chat_id_masked || '未登记'}</p></div></div>
        <div className="telegram-health"><span className={`status-chip ${telegram?.consented ? 'official' : 'research'}`}>{telegram?.consented ? <CheckCircle2 size={14} /> : <AlertTriangle size={14} />} {telegram?.consented ? '连接配置正常' : '通知未启用'}</span><small><Clock3 size={14} /> {telegram?.updated_at ? `状态更新 ${new Date(telegram.updated_at).toLocaleString(getFormatLocale(), { hour12: false })}` : '暂无真实投递时间'}</small><button className="button secondary" type="button" onClick={() => setConnectionStatus(telegram?.bound && telegram?.verified && telegram?.consented ? '账户绑定、验证和授权状态均正常；本次检查没有发送 Telegram 消息。' : '连接检查未通过，请先在原服务台完成绑定、验证和授权。')}><RefreshCw size={15} /> 检查状态</button><span className="form-status" role="status">{connectionStatus}</span></div>
      </section>

      <section className="alert-composer data-panel">
        <header className="panel-heading"><div><span>PRICE ALERT</span><h2>建立价格预警</h2></div><BellRing size={20} /></header>
        <div className="alert-fields"><label><span>标的</span><input value={alertSymbol} onChange={(event) => setAlertSymbol(event.target.value.toUpperCase())} /></label><label><span>价格达到或高于</span><input min="0.01" step="0.01" type="number" value={alertPrice} onChange={(event) => setAlertPrice(Number(event.target.value))} /></label><button className="button primary" type="button" onClick={async () => {
          if (workspace.mode !== 'authenticated') { setAlertStatus('请先登录后建立真实预警'); return }
          try { const result = await createPriceAlert(alertSymbol, alertPrice); setAlertStatus(`${alertSymbol} 预警已建立 · 当前共 ${result.items.length} 条`) } catch (caught) { setAlertStatus(caught instanceof BrowserApiError ? caught.message : '预警建立失败') }
        }}>建立预警</button><span className="form-status" role="status">{alertStatus}</span></div>
      </section>

      <div className="notification-layout">
        <section className="data-panel">
          <header className="panel-heading"><div><span>EVENT MATRIX</span><h2>推送事件</h2></div><BellRing size={20} /></header>
          <div className="setting-list">{events.map((event) => <article key={event.key}><div><strong>{event.label}</strong><small>{event.note}</small><em>{event.level}</em></div><button className={`toggle ${event.enabled ? 'on' : ''}`} type="button" role="switch" aria-checked={event.enabled} aria-label={`${event.label}推送`} onClick={async () => {
            const next = !event.enabled
            if (workspace.mode !== 'authenticated') { setEventStatus('请先登录后保存真实推送设置'); return }
            try {
              await saveTelegramEvents({ [event.key]: next })
              setEvents((items) => items.map((item) => item.key === event.key ? { ...item, enabled: next } : item))
              setEventStatus(`${event.label}已${next ? '开启' : '关闭'}`)
            } catch (caught) { setEventStatus(caught instanceof BrowserApiError ? caught.message : '设置保存失败') }
          }}><i /></button></article>)}</div><p className="setting-status" role="status">{eventStatus}</p>
        </section>
        <aside className="data-panel delivery-health">
          <header className="panel-heading"><div><span>DELIVERY HEALTH</span><h2>投递健康</h2></div><Link2 size={20} /></header>
          <strong>{telegram?.consented ? '已就绪' : '未启用'}</strong><p>新界面只读取绑定与偏好状态，不主动发送测试消息。</p><dl><div><dt>已绑定</dt><dd>{telegram?.bound ? '是' : '否'}</dd></div><div><dt>已验证</dt><dd>{telegram?.verified ? '是' : '否'}</dd></div><div><dt>已授权</dt><dd>{telegram?.consented ? '是' : '否'}</dd></div></dl>
          <div className="inline-warning"><AlertTriangle size={17} /><span>投递成功率和消息正文仍由旧 Telegram 服务台保管，新界面不会伪造统计。</span></div>
        </aside>
      </div>

      <section className="data-panel">
        <header className="panel-heading"><div><span>RECENT DELIVERIES</span><h2>{workspace.mode === 'authenticated' ? '真实投递记录' : '演示推送记录'}</h2></div><button className="button tertiary" type="button" onClick={() => setShowAuditNote(!showAuditNote)}>{showAuditNote ? '收起说明' : '查看记录范围'}</button></header>
        {showAuditNote && <div className="inline-warning"><AlertTriangle size={17} /><span>为避免泄露 Telegram 消息正文和内部错误，新 API 尚未开放投递日志；这里只显示后端明确返回的状态。</span></div>}
        <div className="compact-list delivery-list">{shownDeliveries.map((record) => <article key={record.id}><span className={`delivery-dot ${record.status}`} /><div><strong>{record.event}</strong><small>{record.id} · {record.channel}</small></div><div className="list-value"><strong>{record.status === 'sent' ? '已送达' : '等待重试'}</strong><small>{record.deliveredAt}</small></div></article>)}</div>
        {!shownDeliveries.length && <div className="inline-empty">真实投递日志尚未开放到新界面。</div>}
      </section>
    </div>
  )
}
