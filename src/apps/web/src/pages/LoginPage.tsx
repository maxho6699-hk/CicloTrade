import { Eye, EyeOff, KeyRound, LockKeyhole, ShieldCheck } from 'lucide-react'
import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useWorkspace } from '../api/workspace-context'
import { BrowserApiError } from '../api/client'
import { PageHeader } from '../components/PageHeader'

export function LoginPage() {
  const navigate = useNavigate()
  const workspace = useWorkspace()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    setSubmitting(true)
    setError('')
    try {
      await workspace.login(email, password)
      navigate('/today')
    } catch (caught) {
      setError(caught instanceof BrowserApiError ? caught.message : '登录服务暂时不可用。')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="page operations-page login-page">
      <PageHeader kicker="SECURE SESSION" title="登录 CicloTrade" description="登录后读取你的真实会员、量化时间线、模拟持仓和 Telegram 状态。" />
      <div className="login-layout">
        <form className="login-panel data-panel" onSubmit={submit}>
          <header className="panel-heading"><div><span>ACCOUNT ACCESS</span><h2>账户登录</h2></div><KeyRound size={20} /></header>
          <div className="login-fields"><label><span>邮箱</span><input autoComplete="username" inputMode="email" required type="email" value={email} onChange={(event) => setEmail(event.target.value)} /></label><label><span>密码</span><div className="password-field"><input autoComplete="current-password" required type={showPassword ? 'text' : 'password'} value={password} onChange={(event) => setPassword(event.target.value)} /><button className="icon-button" type="button" aria-label={showPassword ? '隐藏密码' : '显示密码'} onClick={() => setShowPassword(!showPassword)}>{showPassword ? <EyeOff size={17} /> : <Eye size={17} />}</button></div></label>{error && <p className="form-error" role="alert">{error}</p>}<button className="button primary wide" type="submit" disabled={submitting}>{submitting ? '正在验证…' : '安全登录'}</button></div>
        </form>
        <aside className="session-safety data-panel"><img className="login-brand-logo" src="/brand/ciclotrade-logo.webp" alt="CicloTrade" /><LockKeyhole size={25} /><h2>会话保护</h2><ul><li><ShieldCheck size={16} />刷新凭证仅保存在 HttpOnly Cookie</li><li><ShieldCheck size={16} />单账户只保留一个有效登录会话</li><li><ShieldCheck size={16} />登录、IP 和失败次数沿用原有安全规则</li></ul><p>当前新界面接入真实数据时仍保持只读。模拟下单与付款会在独立确认流程中开放。</p></aside>
      </div>
    </div>
  )
}
