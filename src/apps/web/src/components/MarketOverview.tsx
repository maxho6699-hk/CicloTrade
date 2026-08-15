import {
  ArrowDownRight,
  ArrowUpRight,
  ChevronLeft,
  ChevronRight,
  CandlestickChart,
  LayoutGrid,
  List,
  Search,
  Star,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { fetchMarketCandles } from "../api/client";
import {
  candles as demoCandles,
  instruments as demoInstruments,
} from "../data/demo";
import type { Candle, Instrument, Market } from "../types";
import { CalendarDays, Grid3X3 } from "lucide-react";
import { MarketEventCalendar } from "./MarketEventCalendar";
import { MarketHeatmap } from "./MarketHeatmap";
import { WatchlistToggle } from "./WatchlistToggle";
import { SegmentedControl } from "./ui/SegmentedControl";
import { displayDataSource, displayDeliveryDelay, displayFreshness } from "../domain/dataSourcePresentation";

interface OverviewQuote extends Instrument {
  candles: Candle[];
  volatility: number;
  status: string;
}

interface MarketQuoteCacheEntry {
  candidatesKey: string;
  quotes: OverviewQuote[];
  status: string;
}

interface MarketOverviewProps {
  market: Market;
  watchlist: string[];
  marketDataEnabled: boolean;
  demoMode: boolean;
  authenticated: boolean;
  busySymbol: string;
  onMarketChange: (market: Market) => void;
  onOpen: (instrument: Instrument) => void;
  onWatchlist: (instrument: Instrument, remove: boolean) => Promise<void>;
}

const TABS = [
  "我的自选",
  "热门关注",
  "涨幅榜",
  "跌幅榜",
  "波幅榜",
  "热力图",
  "事件日历",
] as const;

const DESKTOP_CARD_PAGE_SIZE = 8;
const MOBILE_CARD_LIMIT = 4;
const marketQuoteCache = new Map<Market, MarketQuoteCacheEntry>();
const POPULAR: Record<Market, Array<{ symbol: string; name: string }>> = {
  US: [
    { symbol: "AAPL", name: "Apple" },
    { symbol: "NVDA", name: "NVIDIA" },
    { symbol: "MSFT", name: "Microsoft" },
    { symbol: "TSLA", name: "Tesla" },
    { symbol: "PLTR", name: "Palantir" },
    { symbol: "AMZN", name: "Amazon" },
    { symbol: "META", name: "Meta" },
    { symbol: "SPY", name: "S&P 500 ETF" },
  ],
  CN: [
    { symbol: "600519", name: "贵州茅台" },
    { symbol: "000001", name: "平安银行" },
    { symbol: "300750", name: "宁德时代" },
    { symbol: "601318", name: "中国平安" },
    { symbol: "600036", name: "招商银行" },
    { symbol: "000858", name: "五粮液" },
  ],
};

function quoteFromCandles(
  symbol: string,
  name: string,
  market: Market,
  series: Candle[],
  status: string,
): OverviewQuote {
  const latest = series.at(-1);
  const previous = series.at(-2);
  const closes = series.slice(-20);
  const high = closes.length ? Math.max(...closes.map((item) => item.high)) : 0;
  const low = closes.length ? Math.min(...closes.map((item) => item.low)) : 0;
  const price = latest?.close ?? 0;
  return {
    symbol,
    name,
    market,
    currency: market === "CN" ? "CNY" : "USD",
    price,
    changePct: previous?.close
      ? ((price - previous.close) / previous.close) * 100
      : 0,
    candles: series,
    volatility: price ? ((high - low) / price) * 100 : 0,
    status,
  };
}

function MiniChart({ item }: { item: OverviewQuote }) {
  const values = item.candles.slice(-32).map((candle) => candle.close);
  const low = Math.min(...values);
  const high = Math.max(...values);
  const range = Math.max(high - low, 1e-9);
  const points = values
    .map(
      (value, index) =>
        `${values.length <= 1 ? 0 : (index / (values.length - 1)) * 180},${58 - ((value - low) / range) * 52}`,
    )
    .join(" ");
  return (
    <svg
      className="overview-mini-chart"
      viewBox="0 0 180 64"
      preserveAspectRatio="none"
      role="img"
      aria-label={`${item.symbol} 近期价格走势`}
    >
      <polyline
        points={points}
        fill="none"
        stroke={item.changePct >= 0 ? "var(--positive)" : "var(--negative)"}
        strokeWidth="2"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  );
}

function demoQuote(symbol: string, name: string, market: Market) {
  const base = demoInstruments.find((item) => item.symbol === symbol);
  const price =
    base?.price ??
    (market === "CN" ? 42 + symbol.charCodeAt(0) : 80 + symbol.charCodeAt(0));
  const scale = price / demoCandles.at(-1)!.close;
  return quoteFromCandles(
    symbol,
    name,
    market,
    demoCandles.map((item) => ({
      ...item,
      open: item.open * scale,
      high: item.high * scale,
      low: item.low * scale,
      close: item.close * scale,
    })),
    "界面演示",
  );
}

function candidatesKey(candidates: Array<{ symbol: string; name: string }>) {
  return candidates.map((item) => `${item.symbol}:${item.name}`).join("|");
}

async function fetchOverviewQuotes(market: Market, candidates: Array<{ symbol: string; name: string }>) {
  const results = await Promise.allSettled(
    candidates.map(async (item) => {
      const payload = await fetchMarketCandles(item.symbol, "日线");
      return quoteFromCandles(
        item.symbol,
        item.name,
        market,
        payload.items,
        `${displayDataSource(payload.status.display_source)} · ${displayDeliveryDelay(payload.status.delivery_delay_minutes) || displayFreshness(payload.status.freshness)}`,
      );
    }),
  );
  const quotes = results.flatMap((result) => result.status === "fulfilled" ? [result.value] : []);
  const status = quotes.length === candidates.length
    ? (quotes[0]?.status ?? "行情已读取")
    : `已读取 ${quotes.length}/${candidates.length} 只股票；失败项目未用演示数据替代。`;
  const entry = { candidatesKey: candidatesKey(candidates), quotes, status };
  marketQuoteCache.set(market, entry);
  return entry;
}

export function MarketOverview({
  market,
  watchlist,
  marketDataEnabled,
  demoMode,
  authenticated,
  busySymbol,
  onMarketChange,
  onOpen,
  onWatchlist,
}: MarketOverviewProps) {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get("board");
  const tab: (typeof TABS)[number] = TABS.includes(
    requestedTab as (typeof TABS)[number],
  )
    ? (requestedTab as (typeof TABS)[number])
    : "我的自选";
  const view: "cards" | "list" =
    searchParams.get("display") === "list" ? "list" : "cards";
  const [quotes, setQuotes] = useState<OverviewQuote[]>([]);
  const [status, setStatus] = useState("");
  const [cardsPage, setCardsPage] = useState(1);
  const [narrowCards, setNarrowCards] = useState(() => window.matchMedia("(max-width: 760px)").matches);
  const requestSequence = useRef(0);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 760px)");
    const update = () => setNarrowCards(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const setOverviewParam = (
    key: "board" | "display",
    value: string,
    defaultValue: string,
  ) => {
    const next = new URLSearchParams(searchParams);
    if (value === defaultValue) next.delete(key);
    else next.set(key, value);
    setSearchParams(next);
  };
  const candidates = useMemo(() => {
    const names = new Map(
      POPULAR[market].map((item) => [item.symbol, item.name]),
    );
    return [
      ...new Set([...watchlist, ...POPULAR[market].map((item) => item.symbol)]),
    ]
      .slice(0, 12)
      .map((symbol) => ({ symbol, name: names.get(symbol) ?? symbol }));
  }, [market, watchlist]);

  useEffect(() => {
    if (!marketDataEnabled) return;
    const otherMarket: Market = market === "US" ? "CN" : "US";
    const preloadCandidates = POPULAR[otherMarket].slice(0, 12);
    const cached = marketQuoteCache.get(otherMarket);
    if (cached?.candidatesKey === candidatesKey(preloadCandidates)) return;
    void fetchOverviewQuotes(otherMarket, preloadCandidates).catch(() => undefined);
  }, [market, marketDataEnabled]);

  useEffect(() => {
    let active = true;
    const sequence = ++requestSequence.current;
    if (!marketDataEnabled) {
      if (demoMode) {
      setQuotes(
        candidates.map((item) => demoQuote(item.symbol, item.name, market)),
      );
      setStatus("当前为界面演示；登录后读取可验证行情。");
      } else {
        setQuotes([]);
        setStatus("行情连接未启用，不显示演示行情。");
      }
      return () => {
        active = false;
      };
    }
    const key = candidatesKey(candidates);
    const cached = marketQuoteCache.get(market);
    if (cached?.candidatesKey === key) {
      setQuotes(cached.quotes);
      setStatus(cached.status);
    } else {
      setQuotes([]);
      setStatus("正在读取榜单行情…");
    }
    void fetchOverviewQuotes(market, candidates).then((entry) => {
      if (!active || requestSequence.current !== sequence) return;
      setQuotes(entry.quotes);
      setStatus(entry.status);
    }).catch(() => {
      if (!active || requestSequence.current !== sequence) return;
      setQuotes([]);
      setStatus("行情暂时不可用，不显示演示行情。");
    });
    return () => {
      active = false;
    };
  }, [candidates, demoMode, market, marketDataEnabled]);

  const shown = useMemo(() => {
    const watch = new Set(watchlist);
    const base =
      tab === "我的自选"
        ? quotes.filter((item) => watch.has(item.symbol))
        : quotes;
    if (tab === "涨幅榜")
      return [...base].sort((a, b) => b.changePct - a.changePct);
    if (tab === "跌幅榜")
      return [...base].sort((a, b) => a.changePct - b.changePct);
    if (tab === "波幅榜")
      return [...base].sort((a, b) => b.volatility - a.volatility);
    return base;
  }, [quotes, tab, watchlist]);
  const cardPageCount = narrowCards
    ? 1
    : Math.max(1, Math.ceil(shown.length / DESKTOP_CARD_PAGE_SIZE));
  useEffect(() => {
    setCardsPage(1);
  }, [market, tab, quotes, watchlist, narrowCards]);
  const visibleCards = narrowCards
    ? shown.slice(0, MOBILE_CARD_LIMIT)
    : shown.slice(
        (cardsPage - 1) * DESKTOP_CARD_PAGE_SIZE,
        cardsPage * DESKTOP_CARD_PAGE_SIZE,
      );

  return (
    <div className="market-overview" data-market={market}>
      <header className="market-overview-header">
        <div>
          <span>MARKET DISCOVERY</span>
          <h1>发现值得进一步查看的股票</h1>
          <p>先从自选、热门、涨跌幅和波幅中发现机会，再进入完整 K线工作图。</p>
        </div>
        <div className="market-overview-actions">
          <SegmentedControl
            ariaLabel="市场"
            value={market}
            options={[
              { value: "US", label: "美股" },
              { value: "CN", label: "A股" },
            ]}
            onChange={onMarketChange}
            className="market-selector"
          />
          <button
            className="button secondary"
            type="button"
            onClick={() =>
              document
                .querySelector<HTMLButtonElement>(".command-search")
                ?.click()
            }
          >
            <Search size={16} /> 搜索股票
          </button>
        </div>
      </header>
      <div className="market-overview-toolbar">
        <nav aria-label="市场榜单">
          {TABS.slice(0, 5).map((item) => (
            <button
              className={tab === item ? "active" : ""}
              type="button"
              onClick={() => setOverviewParam("board", item, "我的自选")}
              key={item}
            >
              {item}
            </button>
          ))}
          <button
            className={tab === "热力图" ? "active" : ""}
            type="button"
            onClick={() => setOverviewParam("board", "热力图", "我的自选")}
          >
            <Grid3X3 size={15} />
            热力图
          </button>
          <button
            className={tab === "事件日历" ? "active" : ""}
            type="button"
            onClick={() => setOverviewParam("board", "事件日历", "我的自选")}
          >
            <CalendarDays size={15} />
            事件日历
          </button>
        </nav>
        <div>
          <span className="market-data-note">{status}</span>
          <button
            className={view === "cards" ? "active" : ""}
            type="button"
            aria-label="迷你 K 线模式"
            title="迷你 K 线模式"
            onClick={() => setOverviewParam("display", "cards", "cards")}
          >
            <LayoutGrid size={17} />
          </button>
          <button
            className={view === "list" ? "active" : ""}
            type="button"
            aria-label="列表模式"
            title="列表模式"
            onClick={() => setOverviewParam("display", "list", "cards")}
          >
            <List size={17} />
          </button>
        </div>
      </div>
      {tab !== "我的自选" && (
        <div className="ranking-risk-note" role="note">
          <strong>这不是买入榜</strong>
          <span>
            这里只按过去表现排序，不等于现在应该买；涨得太多时更不适合追价。
          </span>
        </div>
      )}
      {tab === ("热力图" as typeof tab) ? (
        <MarketHeatmap
          market={market}
          authenticated={authenticated}
        />
      ) : tab === ("事件日历" as typeof tab) ? (
        <MarketEventCalendar market={market} />
      ) : !shown.length ? (
        <section className="market-overview-empty">
          <Star size={24} />
          <h2>
            {tab === "我的自选" ? "还没有自选股票" : "暂时没有可验证行情"}
          </h2>
          <p>
            {tab === "我的自选"
              ? "从热门关注或股票搜索开始建立你的关注清单。"
              : "数据读取失败时不会用演示价格冒充真实行情。"}
          </p>
          {tab === "我的自选" && (
            <div className="market-overview-empty-actions">
              <button
                className="button secondary"
                type="button"
                onClick={() =>
                  document
                    .querySelector<HTMLButtonElement>(".command-search")
                    ?.click()
                }
              >
                <Search size={16} /> 搜索股票
              </button>
              <button
                className="button tertiary"
                type="button"
                onClick={() =>
                  setOverviewParam("board", "热门关注", "我的自选")
                }
              >
                <Star size={16} /> 查看热门关注
              </button>
            </div>
          )}
        </section>
      ) : view === "cards" ? (
        <>
        <section className="overview-card-grid" aria-label={tab}>
          {visibleCards.map((item) => {
            const index = shown.findIndex((candidate) => candidate.symbol === item.symbol);
            const saved = watchlist.includes(item.symbol);
            return (
              <article className="overview-quote-card" key={item.symbol}>
                <header className="overview-card-header">
                  <button
                    className="overview-card-identity"
                    type="button"
                    aria-label={`打开 ${item.symbol} K 线`}
                    onClick={() => onOpen(item)}
                  >
                    <strong>{item.symbol}</strong>
                    <small>{item.name}</small>
                  </button>
                  <button
                    className={`overview-card-price ${item.changePct >= 0 ? "positive-text" : "negative-text"}`}
                    type="button"
                    aria-label={`打开 ${item.symbol} K 线，当前价格 ${item.price.toFixed(2)}`}
                    onClick={() => onOpen(item)}
                  >
                    <strong>{item.price.toFixed(2)}</strong>
                    <small>
                      {item.changePct >= 0 ? (
                        <ArrowUpRight size={13} />
                      ) : (
                        <ArrowDownRight size={13} />
                      )}
                      {Math.abs(item.changePct).toFixed(2)}%
                    </small>
                  </button>
                </header>
                <button
                  className="overview-card-main"
                  type="button"
                  onClick={() => onOpen(item)}
                >
                  <MiniChart item={item} />
                </button>
                <footer className="overview-card-footer">
                  <div className="overview-card-actions">
                    <button type="button" onClick={() => onOpen(item)}>
                      <CandlestickChart size={14} /> K线工作图
                    </button>
                    {authenticated && (
                      <WatchlistToggle
                        variant="label"
                        className="overview-watch-action"
                        symbol={item.symbol}
                        saved={saved}
                        busy={busySymbol === item.symbol}
                        onToggle={(remove) => onWatchlist(item, remove)}
                      />
                    )}
                  </div>
                  <span>#{index + 1}</span>
                  <span>近20日波幅 {item.volatility.toFixed(2)}%</span>
                </footer>
              </article>
            );
          })}
        </section>
        {cardPageCount > 1 && <nav className="overview-card-pagination" aria-label="榜单分页">
          <button type="button" disabled={cardsPage <= 1} aria-label="上一页" onClick={() => setCardsPage((page) => Math.max(1, page - 1))}><ChevronLeft size={15} /></button>
          <span>第 {cardsPage} / {cardPageCount} 页</span>
          <button type="button" disabled={cardsPage >= cardPageCount} aria-label="下一页" onClick={() => setCardsPage((page) => Math.min(cardPageCount, page + 1))}><ChevronRight size={15} /></button>
        </nav>}
        </>
      ) : (
        <section className="overview-list" aria-label={tab}>
          {shown.map((item, index) => {
            const saved = watchlist.includes(item.symbol);
            return (
              <article className="overview-list-row" key={item.symbol}>
                <button
                  className="overview-list-main"
                  type="button"
                  onClick={() => onOpen(item)}
                >
                  <span>{index + 1}</span>
                  <span>
                    <strong>{item.symbol}</strong>
                    <small>{item.name}</small>
                  </span>
                  <MiniChart item={item} />
                  <strong>{item.price.toFixed(2)}</strong>
                  <span
                    className={
                      item.changePct >= 0 ? "positive-text" : "negative-text"
                    }
                  >
                    {item.changePct >= 0 ? "+" : ""}
                    {item.changePct.toFixed(2)}%
                  </span>
                  <span>波幅 {item.volatility.toFixed(2)}%</span>
                  <CandlestickChart size={17} />
                </button>
                {authenticated && (
                  <WatchlistToggle
                    className="overview-list-watch"
                    symbol={item.symbol}
                    saved={saved}
                    busy={busySymbol === item.symbol}
                    onToggle={(remove) => onWatchlist(item, remove)}
                  />
                )}
              </article>
            );
          })}
        </section>
      )}
    </div>
  );
}
