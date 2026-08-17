import { CircleAlert } from 'lucide-react'
import { useEffect, useState } from 'react'
import { resolveStockLogo } from '../data/stockLogoRegistry'

interface StockLogoProps {
  symbol?: string
  market?: string
  size?: 'sm' | 'md' | 'lg'
  className?: string
}

export function StockLogo({ symbol, market, size = 'md', className = '' }: StockLogoProps) {
  const normalized = symbol?.trim().toUpperCase().replace(/\.(SS|SZ)$/, '') ?? ''
  const src = resolveStockLogo(market, normalized)
  const [failed, setFailed] = useState(false)

  useEffect(() => setFailed(false), [src])

  const classes = ['stock-company-logo', `is-${size}`, failed || !src ? 'is-missing' : 'is-ready', className].filter(Boolean).join(' ')
  if (!src || failed) {
    return <span className={classes} role="img" aria-label={`${normalized || '股票'} 公司 Logo 暂不可用`} title={`${normalized || '股票'} Logo 暂不可用`} data-logo-status="missing"><CircleAlert aria-hidden="true" /></span>
  }

  return <span className={classes} title={normalized} data-logo-status="ready"><img src={src} alt={`${normalized} 公司 Logo`} width={48} height={48} loading="lazy" decoding="async" onError={() => setFailed(true)} /></span>
}
