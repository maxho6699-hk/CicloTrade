import { CheckCircle2, CircleAlert, Lightbulb, LoaderCircle, MessageSquareText, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import { useLocation } from 'react-router-dom'
import { BrowserApiError, fetchFeedback, submitFeedback, type FeedbackCategory, type FeedbackReceipt } from '../api/client'
import { PageHeader } from '../components/PageHeader'
import { useLocale } from '../i18n/useLocale'
import '../styles/secondary-pages.css'

const kinds: Array<{ value: FeedbackCategory; label: string; note: string }> = [
  { value: 'bug', label: '问题报告', note: '功能、显示或操作异常' },
  { value: 'suggestion', label: '产品建议', note: '改善工作流或信息呈现' },
  { value: 'data', label: '数据反馈', note: '行情、状态或口径问题' },
  { value: 'experience', label: '使用体验', note: '流程、层级或可访问性问题' },
  { value: 'other', label: '其他', note: '不属于以上分类' },
]

function newIdempotencyKey() {
  return globalThis.crypto?.randomUUID?.() ?? `feedback-${Date.now()}-${Math.random().toString(36).slice(2)}`
}

const STATUS_LABELS: Record<string, { 'zh-Hans': string; 'zh-Hant': string }> = {
  received: { 'zh-Hans': '已收到', 'zh-Hant': '已收到' },
  pending: { 'zh-Hans': '处理中', 'zh-Hant': '處理中' },
  acknowledged: { 'zh-Hans': '已确认', 'zh-Hant': '已確認' },
  resolved: { 'zh-Hans': '已解决', 'zh-Hant': '已解決' },
  closed: { 'zh-Hans': '已关闭', 'zh-Hant': '已關閉' },
  rejected: { 'zh-Hans': '已拒绝', 'zh-Hant': '已拒絕' },
  '已收到': { 'zh-Hans': '已收到', 'zh-Hant': '已收到' },
  '处理中': { 'zh-Hans': '处理中', 'zh-Hant': '處理中' },
  '處理中': { 'zh-Hans': '处理中', 'zh-Hant': '處理中' },
  '已确认': { 'zh-Hans': '已确认', 'zh-Hant': '已確認' },
  '已確認': { 'zh-Hans': '已确认', 'zh-Hant': '已確認' },
  '已解决': { 'zh-Hans': '已解决', 'zh-Hant': '已解決' },
  '已解決': { 'zh-Hans': '已解决', 'zh-Hant': '已解決' },
  '已关闭': { 'zh-Hans': '已关闭', 'zh-Hant': '已關閉' },
  '已關閉': { 'zh-Hans': '已关闭', 'zh-Hant': '已關閉' },
  '已拒绝': { 'zh-Hans': '已拒绝', 'zh-Hant': '已拒絕' },
  '已拒絕': { 'zh-Hans': '已拒绝', 'zh-Hant': '已拒絕' },
}

function statusLabel(status: string, locale: 'zh-Hans' | 'zh-Hant') {
  return STATUS_LABELS[status.toLowerCase()]?.[locale] ?? status
}

export function FeedbackPage() {
  const location = useLocation()
  const { locale } = useLocale()
  const [kind, setKind] = useState<FeedbackCategory>('suggestion')
  const [page, setPage] = useState(location.state && typeof location.state === 'object' && typeof (location.state as { sourcePage?: unknown }).sourcePage === 'string' ? (location.state as { sourcePage: string }).sourcePage : '/research')
  const [content, setContent] = useState('')
  const [contactPreference, setContactPreference] = useState<'none' | 'telegram' | 'email'>('none')
  const [receipts, setReceipts] = useState<FeedbackReceipt[]>([])
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [notice, setNotice] = useState('')
  const [error, setError] = useState('')
  const [historyError, setHistoryError] = useState('')
  const mounted = useRef(true)
  const receiptRequest = useRef(0)
  const sourcePage = useMemo(() => page.trim().startsWith('/') ? page.trim() : '/research', [page])

  const loadReceipts = useCallback(async () => {
    const request = receiptRequest.current + 1
    receiptRequest.current = request
    setLoading(true)
    setHistoryError('')
    try {
      const items = await fetchFeedback()
      if (mounted.current && receiptRequest.current === request) setReceipts(items)
    } catch (caught) {
      if (mounted.current && receiptRequest.current === request) setHistoryError(caught instanceof BrowserApiError ? caught.message : '反馈记录暂时不可用。')
    } finally {
      if (mounted.current && receiptRequest.current === request) setLoading(false)
    }
  }, [])

  useEffect(() => {
    mounted.current = true
    void loadReceipts()
    return () => { mounted.current = false; receiptRequest.current += 1 }
  }, [loadReceipts])

  async function submit(event: FormEvent) {
    event.preventDefault()
    if (!content.trim() || submitting) return
    setSubmitting(true); setError(''); setNotice('')
    try {
      const receipt = await submitFeedback({ category: kind, context_path: sourcePage, message: content.trim(), contact_preference: contactPreference }, newIdempotencyKey())
      setReceipts((items) => [receipt, ...items.filter((item) => item.id !== receipt.id)])
      setContent('')
      setNotice(`已收到反馈 · 回执 ${receipt.id}`)
    } catch (caught) {
      setError(caught instanceof BrowserApiError ? caught.message : '反馈暂时无法提交，请稍后再试。')
    } finally { setSubmitting(false) }
  }

  return <div className="page operations-page feedback-page">
    <PageHeader kicker="PRODUCT / FEEDBACK" title="反馈建议" description="提交结构化产品反馈；不会上传附件、富文本或账户敏感资料。" />
    <div className="feedback-layout">
      <form className="data-panel feedback-form" onSubmit={submit} aria-busy={submitting}>
        <header className="panel-heading"><div><span>NEW RECEIPT</span><h2>告诉我们需要修正什么</h2></div><MessageSquareText size={20} /></header>
        <fieldset><legend>反馈类型</legend><div className="feedback-kind-grid">{kinds.map((item) => <button className={kind === item.value ? 'active' : ''} type="button" aria-pressed={kind === item.value} onClick={() => setKind(item.value)} key={item.value}><strong>{item.label}</strong><small>{item.note}</small></button>)}</div></fieldset>
        <label><span>问题页面</span><input value={page} onChange={(event) => setPage(event.target.value)} name="page" autoComplete="off" placeholder="/research" /><small>仅记录页面路径，不收集搜索内容或账户资料。</small></label>
        <label><span>反馈内容</span><textarea value={content} onChange={(event) => setContent(event.target.value)} name="content" required maxLength={2000} placeholder="说明你看到的情况、期望结果和发生步骤…" /><small>{content.length}/2000 · 请勿填写密码、验证码、券商凭据或付款资料。</small></label>
        <fieldset><legend>联系偏好</legend><label className="feedback-choice"><input type="radio" name="contact" checked={contactPreference === 'none'} onChange={() => setContactPreference('none')} />不需要联系</label><label className="feedback-choice"><input type="radio" name="contact" checked={contactPreference === 'telegram'} onChange={() => setContactPreference('telegram')} />可通过 Telegram 联系</label><label className="feedback-choice"><input type="radio" name="contact" checked={contactPreference === 'email'} onChange={() => setContactPreference('email')} />可通过账户邮箱联系</label></fieldset>
        {error && <p className="form-error" role="alert"><CircleAlert size={16} />{error}</p>}
        {notice && <p className="feedback-notice" role="status"><CheckCircle2 size={16} />{notice}</p>}
        <button className="button primary" type="submit" disabled={!content.trim() || submitting}>{submitting ? <><LoaderCircle className="spin" size={16} />正在提交…</> : '提交反馈'}</button>
      </form>
      <section className="data-panel feedback-history" aria-busy={loading}>
        <header className="panel-heading"><div><span>YOUR RECEIPTS</span><h2>历史回执</h2></div><Lightbulb size={20} /></header>
        {loading ? <div className="feedback-state"><LoaderCircle className="spin" size={20} />正在读取回执…</div> : historyError ? <div className="feedback-state feedback-state-error" role="alert"><CircleAlert size={20} /><strong>暂时无法读取历史回执</strong><span>{historyError}</span><button className="button secondary" type="button" onClick={() => void loadReceipts()}><RefreshCw size={15} />重新读取</button></div> : receipts.length ? <ol>{receipts.map((item) => <li key={item.id}><header><strong>{kinds.find((kindItem) => kindItem.value === item.category)?.label ?? '反馈'}</strong><span>{statusLabel(item.status, locale)}</span></header><p>{item.summary}</p><footer><code>{item.id}</code><time>{new Date(item.created_at).toLocaleString('zh-HK', { hour12: false, timeZone: 'Asia/Hong_Kong' })}</time></footer></li>)}</ol> : <div className="feedback-state"><RefreshCw size={20} /><strong>尚无反馈回执</strong><span>提交后将在这里显示处理状态。</span></div>}
      </section>
    </div>
  </div>
}
