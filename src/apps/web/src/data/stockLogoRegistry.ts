export type StockLogoMarket = 'US' | 'CN'

const STOCK_LOGO_FILES: Record<string, string> = {
  'US:AAPL': 'aapl.us.png',
  'US:NVDA': 'nvda.us.png',
  'US:MSFT': 'msft.us.png',
  'US:AMZN': 'amzn.us.png',
  'US:GOOGL': 'googl.us.png',
  'US:META': 'meta.us.png',
  'US:AVGO': 'avgo.us.png',
  'US:JPM': 'jpm.us.png',
  'US:TSLA': 'tsla.us.png',
  'US:ADBE': 'adbe.us.png',
  'US:NFLX': 'nflx.us.png',
  'US:COST': 'cost.us.png',
  'US:PLTR': 'pltr.us.png',
  'US:SPY': 'spy.us.png',
  'US:BABA': 'baba.us.png',
  'CN:600519': '600519.cn.png',
  'CN:300750': '300750.cn.png',
  'CN:601318': '601318.cn.png',
  'CN:000001': '000001.cn.png',
  'CN:600036': '600036.cn.png',
  'CN:000858': '000858.cn.png',
}

const STOCK_LOGO_ALIASES: Record<string, string> = {
  'US:GOOG': 'US:GOOGL',
}

export function normalizeStockLogoMarket(market: string | undefined, symbol: string): StockLogoMarket {
  if (/^\d{6}$/.test(symbol)) return 'CN'
  if (/^[A-Z][A-Z0-9.=-]*$/.test(symbol)) return 'US'
  const normalizedMarket = market?.trim().toUpperCase() ?? ''
  return normalizedMarket === 'CN' || normalizedMarket === 'A股' || normalizedMarket.includes('中国') ? 'CN' : 'US'
}

export function resolveStockLogo(market: string | undefined, symbol: string | undefined): string | null {
  const normalizedSymbol = symbol?.trim().toUpperCase().replace(/\.(SS|SZ)$/, '') ?? ''
  if (!normalizedSymbol) return null
  const key = `${normalizeStockLogoMarket(market, normalizedSymbol)}:${normalizedSymbol}`
  const resolvedKey = STOCK_LOGO_ALIASES[key] ?? key
  const file = STOCK_LOGO_FILES[resolvedKey]
  return file ? `/stock-logos/${file}` : null
}

export function stockLogoRegistryEntries() {
  return Object.entries(STOCK_LOGO_FILES).map(([key, file]) => ({ key, path: `/stock-logos/${file}` }))
}
