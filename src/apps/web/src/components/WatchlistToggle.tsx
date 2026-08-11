import { LoaderCircle, Star } from 'lucide-react'

interface WatchlistToggleProps {
  symbol: string
  saved: boolean
  busy?: boolean
  variant?: 'icon' | 'label'
  className?: string
  onToggle: (remove: boolean) => void | Promise<void>
}

export function WatchlistToggle({
  symbol,
  saved,
  busy = false,
  variant = 'icon',
  className = '',
  onToggle,
}: WatchlistToggleProps) {
  const action = saved ? '从自选移除' : '加入自选'
  const classes = [
    'watchlist-toggle',
    `watchlist-toggle--${variant}`,
    saved ? 'is-saved' : '',
    busy ? 'is-busy' : '',
    className,
  ].filter(Boolean).join(' ')

  return (
    <button
      className={classes}
      type="button"
      aria-label={`${action} ${symbol}`}
      aria-pressed={saved}
      title={`${action} ${symbol}`}
      disabled={busy}
      onClick={() => void onToggle(saved)}
    >
      {busy ? <LoaderCircle size={16} aria-hidden="true" /> : <Star size={16} fill={saved ? 'currentColor' : 'none'} aria-hidden="true" />}
      {variant === 'label' && <span>{saved ? '取消自选' : '加入自选'}</span>}
    </button>
  )
}
