import { ArrowLeft, ArrowRight, Languages, Moon, Sun } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { UiLocale } from '../i18n/runtime'

export type PublicTheme = 'dark' | 'light'

interface PublicPageHeaderProps {
  variant: 'welcome' | 'login'
  locale: UiLocale
  theme: PublicTheme
  onLocaleToggle: () => void
  onThemeToggle: () => void
}

export function PublicPageHeader({ variant, locale, theme, onLocaleToggle, onThemeToggle }: PublicPageHeaderProps) {
  const isWelcome = variant === 'welcome'

  return (
    <header className={`public-nav-shell ${isWelcome ? 'public-header' : 'login-public-header'}`}>
      <div className="public-nav-inner">
        <Link className={isWelcome ? 'public-brand' : 'login-header-brand'} to="/" aria-label={isWelcome ? 'CicloTrade 首页' : '返回 CicloTrade 欢迎页'}>
          <img className={isWelcome ? 'public-brand-logo' : undefined} src="/brand/ciclotrade-logo.webp" width="512" height="512" alt="CicloTrade" fetchPriority="high" />
          <span><strong translate="no">CicloTrade</strong><small translate="no">{isWelcome ? 'DECISION INTELLIGENCE' : 'SECURE ACCOUNT ACCESS'}</small></span>
        </Link>

        {isWelcome && (
          <nav aria-label="欢迎页导航">
            <a href="#paths">适合谁用</a>
            <a href="#telegram">Telegram 推送</a>
            <a href="#membership">会员方案</a>
          </nav>
        )}

        <div className={isWelcome ? 'public-header-actions' : 'login-header-actions'}>
          <button className="public-control" type="button" aria-label={locale === 'zh-Hant' ? '切换为简体中文' : '切换为繁体中文'} onClick={onLocaleToggle}>
            <Languages size={16} aria-hidden="true" />
            <span>{locale === 'zh-Hant' ? '繁' : '简'}</span>
          </button>
          <button className="public-control icon-only" type="button" aria-label={theme === 'dark' ? '切换浅色模式' : '切换深色模式'} aria-pressed={theme === 'light'} onClick={onThemeToggle}>
            {theme === 'dark' ? <Sun size={16} aria-hidden="true" /> : <Moon size={16} aria-hidden="true" />}
          </button>
          {isWelcome ? (
            <Link className="button primary public-login" to="/login">登录 <ArrowRight size={16} aria-hidden="true" /></Link>
          ) : (
            <Link className="login-back-link" to="/"><ArrowLeft size={16} aria-hidden="true" /> 返回欢迎页</Link>
          )}
        </div>
      </div>
    </header>
  )
}
