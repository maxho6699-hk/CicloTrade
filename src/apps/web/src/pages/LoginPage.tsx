import {
  ArrowRight,
  CircleAlert,
  Eye,
  EyeOff,
  KeyRound,
  LoaderCircle,
  LockKeyhole,
  MailCheck,
  ShieldCheck,
  UserPlus,
  WalletCards,
} from 'lucide-react'
import { useEffect, useRef, useState, type FormEvent } from 'react'
import { useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import {
  BrowserApiError,
  confirmPasswordReset,
  registerAccount,
  requestEmailVerification,
  requestPasswordReset,
  verifyEmailToken,
} from '../api/client'
import { useWorkspace } from '../api/workspace-context'
import { PublicPageHeader } from '../components/PublicPageHeader'
import { useLocale } from '../i18n/useLocale'
import { applyTheme, readStoredTheme } from '../theme'

const consoleRoutes = ['/today', '/opportunities', '/markets', '/portfolio', '/trade', '/reports', '/lab', '/notifications', '/account', '/help', '/feedback', '/admin', '/membership', '/mystic']
const authModes = ['login', 'register', 'forgot'] as const

type AuthMode = (typeof authModes)[number]

function safeReturnTo(value: string | null) {
  if (!value || value.startsWith('//')) return '/today'
  return consoleRoutes.some((route) => value === route || value.startsWith(`${route}?`) || value.startsWith(`${route}/`)) ? value : '/today'
}

function initialAuthMode(value: string | null): AuthMode {
  return authModes.includes(value as AuthMode) ? (value as AuthMode) : 'login'
}

function maskedEmail(value: string) {
  const [local, domain] = value.trim().split('@')
  if (!local || !domain) return value
  return `${local.slice(0, 2)}${'•'.repeat(Math.max(2, local.length - 2))}@${domain}`
}

export function LoginPage() {
  const navigate = useNavigate()
  const location = useLocation()
  const [searchParams] = useSearchParams()
  const workspace = useWorkspace()
  const { locale, setLocale } = useLocale()
  const traditional = locale === 'zh-Hant'
  const [theme, setTheme] = useState(readStoredTheme)
  const [mode, setMode] = useState<AuthMode>(() => initialAuthMode(searchParams.get('mode')))
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [code, setCode] = useState('')
  const [registrationStage, setRegistrationStage] = useState<'details' | 'verify'>('details')
  const [termsAccepted, setTermsAccepted] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const errorRef = useRef<HTMLParagraphElement>(null)
  const returnTo = safeReturnTo(searchParams.get('returnTo'))
  const loginRequired = Boolean((location.state as { reason?: string } | null)?.reason === 'login-required')

  const copy = traditional
    ? {
        heroTitle: '登入，繼續掌握下一步市場機會',
        heroBody: '今日行動、市場觀察、官方驗證結果與 Telegram 狀態都在同一個帳戶中。未登入訪客不能進入工作台。',
        panelTitles: { login: '登入 CicloTrade', register: '建立 CicloTrade 帳戶', forgot: '重設登入密碼' },
        panelDescriptions: {
          login: loginRequired ? '請先登入，再繼續進入剛才的頁面。' : '使用你的帳戶進入工作台。',
          register: '先填寫帳戶資料；需要驗證時，請在同一張卡片輸入郵件中的一次性驗證碼。',
          forgot: '先要求重設郵件，再輸入郵件中的一次性驗證碼與新密碼。',
        },
        modes: { login: '登入', register: '註冊', forgot: '忘記密碼' },
        email: '電子郵件',
        displayName: '顯示名稱',
        password: '密碼',
        newPassword: '新密碼',
        verificationCode: '一次性驗證碼',
        terms: '我已閱讀並同意使用條款、私隱政策與風險披露。',
        submit: { login: '進入決策工作台', register: '建立帳戶', forgot: '重設密碼' },
        requestCode: { verify: '重新發送驗證郵件', forgot: '發送重設郵件' },
        genericError: '認證服務暫時不可用，請稍後再試。',
      }
    : {
        heroTitle: '登录，继续掌握下一步市场机会',
        heroBody: '今日行动、市场观察、官方验证结果与 Telegram 状态都在同一个账户中。未登录访客不能进入工作台。',
        panelTitles: { login: '登录 CicloTrade', register: '建立 CicloTrade 账户', forgot: '重设登录密码' },
        panelDescriptions: {
          login: loginRequired ? '请先登录，再继续进入刚才的页面。' : '使用你的账户进入工作台。',
          register: '先填写账户资料；需要验证时，请在同一张卡片输入邮件中的一次性验证码。',
          forgot: '先请求重设邮件，再输入邮件中的一次性验证码与新密码。',
        },
        modes: { login: '登录', register: '注册', forgot: '忘记密码' },
        email: '电子邮件',
        displayName: '显示名称',
        password: '密码',
        newPassword: '新密码',
        verificationCode: '一次性验证码',
        terms: '我已阅读并同意使用条款、隐私政策与风险披露。',
        submit: { login: '进入决策工作台', register: '建立账户', forgot: '重设密码' },
        requestCode: { verify: '重新发送验证邮件', forgot: '发送重设邮件' },
        genericError: '认证服务暂时不可用，请稍后再试。',
      }

  useEffect(() => {
    if (workspace.mode === 'authenticated') navigate(returnTo, { replace: true })
  }, [navigate, returnTo, workspace.mode])

  useEffect(() => {
    if (error) errorRef.current?.focus()
  }, [error])

  function toggleTheme() {
    const next = theme === 'dark' ? 'light' : 'dark'
    applyTheme(next, true)
    setTheme(next)
  }

  function changeMode(next: AuthMode, nextNotice = '') {
    setMode(next)
    setPassword('')
    setConfirmPassword('')
    setCode('')
    setRegistrationStage('details')
    setShowPassword(false)
    setError('')
    setNotice(nextNotice)
  }

  function clearFeedback() {
    if (error) setError('')
    if (notice) setNotice('')
  }

  async function requestCode() {
    if (!email.trim()) {
      setError(traditional ? '請先輸入電子郵件。' : '请先输入电子邮件。')
      return
    }
    setSubmitting(true)
    setError('')
    setNotice('')
    try {
      if (mode === 'register') {
        await requestEmailVerification(email.trim())
        setCode('')
        setNotice(traditional ? '如帳戶需要驗證，新的驗證郵件已經發送。' : '如果账户需要验证，新的验证邮件已经发送。')
      } else {
        await requestPasswordReset(email.trim())
        setNotice(traditional ? '如帳戶存在，密碼重設郵件已經發送。' : '如果账户存在，密码重设邮件已经发送。')
      }
    } catch (caught) {
      setError(caught instanceof BrowserApiError ? caught.message : copy.genericError)
    } finally {
      setSubmitting(false)
    }
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (submitting) return
    const submittedForm = event.currentTarget
    const submittedData = new FormData(submittedForm)
    const submittedEmail = String(submittedData.get('email') ?? email).trim()
    const submittedPassword = String(submittedData.get('password') ?? password)
    const submittedConfirmPassword = String(submittedData.get('confirm_password') ?? confirmPassword)
    setSubmitting(true)
    setError('')
    setNotice('')
    try {
      if (mode === 'login') {
        // Password managers may fill the DOM without dispatching a React input
        // event.  Read the submitted form value so login never depends on the
        // user toggling password visibility first.
        await workspace.login(submittedEmail, submittedPassword)
        navigate(returnTo, { replace: true })
        return
      }
      if (mode === 'register') {
        if (registrationStage === 'verify') {
          if (!code.trim()) {
            setError(traditional ? '請輸入郵件中的一次性驗證碼。' : '请输入邮件中的一次性验证码。')
            return
          }
          await verifyEmailToken(code.trim())
          changeMode('login', traditional ? '電郵驗證完成，現在可以登入。' : '邮箱验证完成，现在可以登录。')
          return
        }
        if (submittedPassword !== submittedConfirmPassword) {
          setError(traditional ? '兩次輸入的密碼不一致。' : '两次输入的密码不一致。')
          return
        }
        const response = await registerAccount({
          email: submittedEmail,
          password: submittedPassword,
          display_name: displayName.trim(),
          terms_accepted: termsAccepted,
        })
        if (response.verification_required) {
          setRegistrationStage('verify')
          setPassword('')
          setConfirmPassword('')
          setCode('')
          setNotice(traditional ? '註冊資料已接收。請檢查電郵並在此輸入驗證碼。' : '注册资料已接收。请检查邮箱并在此输入验证码。')
        } else {
          changeMode('login', traditional ? '帳戶已建立，現在可以登入。' : '账户已建立，现在可以登录。')
        }
        return
      }
      if (!code.trim()) {
        await requestCode()
        return
      }
      await confirmPasswordReset(code.trim(), submittedPassword)
      changeMode('login', traditional ? '密碼已重設，所有舊工作階段已失效。' : '密码已重设，所有旧会话已失效。')
    } catch (caught) {
      setError(caught instanceof BrowserApiError ? caught.message : copy.genericError)
    } finally {
      setSubmitting(false)
    }
  }

  if (workspace.mode === 'authenticated') {
    return <div className="login-redirect" role="status"><LoaderCircle size={22} /><strong>{traditional ? '正在進入 CicloTrade 工作台' : '正在进入 CicloTrade 工作台'}</strong></div>
  }

  const modeIcon = mode === 'register' ? (registrationStage === 'verify' ? <MailCheck size={21} /> : <UserPlus size={21} />) : <KeyRound size={21} />
  const codeMode = mode === 'forgot' || (mode === 'register' && registrationStage === 'verify')
  const requestCodeLabel = mode === 'register' ? copy.requestCode.verify : copy.requestCode.forgot
  const primaryLabel = mode === 'forgot' && !code.trim() ? requestCodeLabel : mode === 'register' && registrationStage === 'verify' ? (traditional ? '驗證電郵' : '验证邮箱') : copy.submit[mode]

  return (
    <div className="login-page">
      <a className="skip-link" href="#login-form">{traditional ? '跳到認證表單' : '跳到认证表单'}</a>
      <img className="login-background-photo login-background-dark" src="/media/landing/login-glass-office-dark-v1.webp" width="1672" height="941" alt="" aria-hidden="true" fetchPriority="high" />
      <img className="login-background-photo login-background-light" src="/media/landing/login-glass-office-light-v1.webp" width="1672" height="941" alt="" aria-hidden="true" fetchPriority="high" />
      <div className="login-background-overlay" aria-hidden="true" />

      <PublicPageHeader
        variant="login"
        locale={locale}
        theme={theme}
        onLocaleToggle={() => void setLocale(traditional ? 'zh-Hans' : 'zh-Hant')}
        onThemeToggle={toggleTheme}
      />

      <main className="login-stage">
        <section className="login-context">
          <span className="public-kicker"><span className="status-light" />CicloTrade {traditional ? '決策終端' : '决策终端'}</span>
          <h1>{copy.heroTitle}</h1>
          <p>{copy.heroBody}</p>
          <div className="login-context-points">
            <span><ShieldCheck size={17} /><b>{traditional ? '資料狀態說清楚' : '数据状态说清楚'}</b><small>{traditional ? '即時、延遲、演示與不可用分別標註' : '实时、延迟、演示与不可用分别标注'}</small></span>
            <span><LockKeyhole size={17} /><b>{traditional ? '會員權限按帳戶讀取' : '会员权限按账户读取'}</b><small>{traditional ? '正股與期權功能不會混在一起' : '正股与期权功能不会混在一起'}</small></span>
            <span><WalletCards size={17} /><b>{traditional ? '實盤必須主動授權' : '实盘必须主动授权'}</b><small>{traditional ? '登入不會自動連接你的券商帳戶' : '登录不会自动连接你的券商账户'}</small></span>
          </div>
        </section>

        <form className={`login-panel login-panel-${mode}`} id="login-form" onSubmit={submit} aria-busy={submitting}>
          <header><span className="login-key">{modeIcon}</span><div><small>ACCOUNT ACCESS</small><h2>{copy.panelTitles[mode]}</h2><p>{copy.panelDescriptions[mode]}</p></div></header>
          <nav className="login-mode-tabs" aria-label={traditional ? '帳戶操作' : '账户操作'}>
            {authModes.map((item) => (
              <button key={item} type="button" className={mode === item ? 'active' : ''} aria-current={mode === item ? 'page' : undefined} onClick={() => changeMode(item)}>
                {copy.modes[item]}
              </button>
            ))}
          </nav>
          <div className="login-fields">
            {mode === 'register' && registrationStage === 'details' && (
              <label htmlFor="register-name"><span>{copy.displayName}</span><input id="register-name" autoComplete="name" name="display_name" required value={displayName} onChange={(event) => { setDisplayName(event.target.value); clearFeedback() }} placeholder={traditional ? '你的稱呼…' : '你的称呼…'} /></label>
            )}
            <label htmlFor="login-email"><span>{copy.email}</span><input id="login-email" autoComplete="username" autoCapitalize="none" inputMode="email" name="email" required readOnly={mode === 'register' && registrationStage === 'verify'} spellCheck={false} type="email" value={email} onChange={(event) => { setEmail(event.target.value); clearFeedback() }} placeholder="name@example.com" aria-invalid={Boolean(error)} aria-describedby={error ? 'login-error' : undefined} /></label>
            {mode === 'register' && registrationStage === 'verify' && <p className="login-notice login-field-full" role="status"><MailCheck size={16} />{traditional ? `驗證碼已發送至 ${maskedEmail(email)}` : `验证码已发送至 ${maskedEmail(email)}`}</p>}
            {mode === 'register' && registrationStage === 'verify' && <button className="button secondary login-field-full" type="button" disabled={submitting} onClick={() => { setRegistrationStage('details'); setCode(''); setPassword(''); setConfirmPassword(''); clearFeedback() }}>{traditional ? '改用其他電子郵件' : '改用其他电子邮件'}</button>}
            {(mode === 'login' || (mode === 'register' && registrationStage === 'details') || mode === 'forgot') && (
              <label className={mode === 'forgot' ? 'login-field-full' : undefined} htmlFor="login-password"><span>{mode === 'forgot' ? copy.newPassword : copy.password}</span><div className="password-field"><input id="login-password" autoComplete={mode === 'login' ? 'current-password' : 'new-password'} minLength={mode === 'login' ? undefined : 8} name="password" required={mode !== 'forgot' || Boolean(code.trim())} type={showPassword ? 'text' : 'password'} value={password} onChange={(event) => { setPassword(event.target.value); clearFeedback() }} placeholder={mode === 'login' ? (traditional ? '輸入你的密碼…' : '输入你的密码…') : (traditional ? '至少 8 個字元，包含字母和數字…' : '至少 8 个字符，包含字母和数字…')} aria-invalid={Boolean(error)} aria-describedby={error ? 'login-error' : undefined} /><button className="icon-button" type="button" aria-controls="login-password" aria-label={showPassword ? (traditional ? '隱藏密碼' : '隐藏密码') : (traditional ? '顯示密碼' : '显示密码')} aria-pressed={showPassword} onClick={() => setShowPassword(!showPassword)}>{showPassword ? <EyeOff size={18} /> : <Eye size={18} />}</button></div></label>
            )}
            {mode === 'register' && registrationStage === 'details' && (
              <label htmlFor="register-confirm-password"><span>{traditional ? '確認密碼' : '确认密码'}</span><div className="password-field"><input id="register-confirm-password" autoComplete="new-password" minLength={8} name="confirm_password" required type={showPassword ? 'text' : 'password'} value={confirmPassword} onChange={(event) => { setConfirmPassword(event.target.value); clearFeedback() }} placeholder={traditional ? '再次輸入密碼…' : '再次输入密码…'} aria-invalid={Boolean(error)} aria-describedby={error ? 'login-error' : undefined} /></div></label>
            )}
            {codeMode && (
              <label htmlFor="auth-code"><span>{copy.verificationCode}</span><input id="auth-code" autoComplete="one-time-code" autoCapitalize="characters" inputMode="text" name="code" value={code} onChange={(event) => { setCode(event.target.value); clearFeedback() }} placeholder={traditional ? '輸入郵件中的驗證碼…' : '输入邮件中的验证码…'} /></label>
            )}
            {mode === 'register' && registrationStage === 'details' && (
              <label className="login-terms login-field-full"><input type="checkbox" checked={termsAccepted} onChange={(event) => { setTermsAccepted(event.target.checked); clearFeedback() }} /><span>{copy.terms}</span></label>
            )}
            {error && <p className="form-error login-field-full" id="login-error" role="alert" tabIndex={-1} ref={errorRef}><CircleAlert size={16} />{error}</p>}
            {notice && <p className="login-notice login-field-full" role="status"><MailCheck size={16} />{notice}</p>}
            <button className="button primary wide login-submit" type="submit" disabled={submitting || (mode === 'register' && registrationStage === 'details' && !termsAccepted)}>{submitting ? <><LoaderCircle className="login-spinner" size={17} />{traditional ? '處理中…' : '处理中…'}</> : <>{primaryLabel} <ArrowRight size={17} /></>}</button>
            {codeMode && (mode === 'register' || code.trim()) && (
              <button className="button secondary wide login-resend" type="button" disabled={submitting} onClick={() => void requestCode()}>{requestCodeLabel}</button>
            )}
            <p className="login-recovery login-field-full"><strong>{traditional ? '需要協助？' : '需要帮助？'}</strong><span>{traditional ? '你可以自行註冊、重新發送驗證郵件或重設密碼；系統不會透露某個電子郵件是否已存在。' : '你可以自行注册、重新发送验证邮件或重设密码；系统不会透露某个电子邮件是否已存在。'}</span></p>
          </div>
          <footer><span><ShieldCheck size={14} />{traditional ? '登入資料不會寫進網頁程式碼' : '登录资料不会写进网页代码'}</span><span><LockKeyhole size={14} />{traditional ? '登入後按真實會員權限載入' : '登录后按真实会员权限加载'}</span></footer>
        </form>
      </main>
    </div>
  )
}
