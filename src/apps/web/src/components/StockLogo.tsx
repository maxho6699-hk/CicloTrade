import { Apple, Boxes, Cpu, PackageOpen, PanelsTopLeft, Search, ShoppingBag, Zap, type LucideIcon } from 'lucide-react'

const STOCK_MARKS: Record<string, { label: string; tone: string; icon: LucideIcon }> = {
  AAPL: { label: 'Apple', tone: 'apple', icon: Apple },
  TSLA: { label: 'Tesla', tone: 'tesla', icon: Zap },
  BABA: { label: 'Alibaba', tone: 'alibaba', icon: ShoppingBag },
  MSFT: { label: 'Microsoft', tone: 'microsoft', icon: PanelsTopLeft },
  GOOGL: { label: 'Google', tone: 'google', icon: Search },
  GOOG: { label: 'Google', tone: 'google', icon: Search },
  NVDA: { label: 'NVIDIA', tone: 'nvidia', icon: Cpu },
  AMZN: { label: 'Amazon', tone: 'amazon', icon: PackageOpen },
}

export function StockLogo({ symbol, size = 'md' }: { symbol?: string; size?: 'sm' | 'md' | 'lg' }) {
  const normalized = symbol?.trim().toUpperCase() ?? ''
  const mark = STOCK_MARKS[normalized]
  const Icon = mark?.icon ?? Boxes
  return <span className={`stock-company-logo is-${mark?.tone ?? 'generic'} is-${size}`} role="img" aria-label={mark ? `${mark.label} 公司标志` : `${normalized || '股票'} 标志`} title={mark?.label ?? (normalized || '股票')}><Icon aria-hidden="true" /><b aria-hidden="true">{normalized.slice(0, 2) || '—'}</b></span>
}
