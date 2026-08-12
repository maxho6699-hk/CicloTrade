import {
  Activity,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Expand,
  LoaderCircle,
  Minimize2,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Trash2,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import {
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";
import {
  BrowserApiError,
  fetchOptionCandles,
  fetchOptionChain,
  type OptionCandlePayload,
  type OptionChainPayload,
  type OptionContract,
} from "../api/client";
import {
  buildOptionTemplate,
  summarizeOptionCombination,
  type OptionLegSide,
  type OptionStrategyLeg,
  type OptionTemplateId,
} from "../domain/optionResearch";
import { createVisibilityPolling, deliveryAllowsImmediateAction, displayDataSource, displayDeliveryDelay, displayFreshness, safeDataError } from "../domain/dataSourcePresentation";
import { TimeframeDropdown } from "./ui/TimeframeDropdown";
import { MarketChart, type MarketChartHandle } from "./MarketChart";
import type {
  DrawingHistoryStatus,
  DrawingToolState,
} from "./ChartDrawingLayer";
import { SegmentedControl } from "./ui/SegmentedControl";
import { SelectField } from "./ui/SelectField";

const PAGE_SIZE = 18;
const OPTION_TIMEFRAMES = [
  "1分",
  "5分",
  "15分",
  "30分",
  "1小时",
  "日线",
  "周线",
  "月线",
];
const DRAWING_OFF: DrawingToolState = {
  tool: "cursor",
  continuous: false,
  magnet: "off",
  visible: false,
  crossTimeframe: false,
};
const TEMPLATE_LABELS: Array<[OptionTemplateId, string]> = [
  ["long-straddle", "买入跨式"],
  ["long-strangle", "买入宽跨式"],
  ["bull-call-spread", "牛市看涨价差"],
  ["bear-put-spread", "熊市看跌价差"],
];
type OptionPane = "chain" | "chart" | "combination";

function OptionTaskTabs({
  value,
  onChange,
  legsCount,
  detailOnly = false,
}: {
  value: OptionPane;
  onChange: (value: OptionPane) => void;
  legsCount: number;
  detailOnly?: boolean;
}) {
  const options: Array<{ value: OptionPane; label: string }> = detailOnly
    ? [
        { value: "chart", label: "报价 K 线" },
        {
          value: "combination",
          label: `组合研究${legsCount ? ` · ${legsCount}` : ""}`,
        },
      ]
    : [
        { value: "chain", label: "期权链" },
        { value: "chart", label: "报价 K 线" },
        {
          value: "combination",
          label: `组合研究${legsCount ? ` · ${legsCount}` : ""}`,
        },
      ];
  const activeValue = options.some((option) => option.value === value)
    ? value
    : options[0].value;
  const handleKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) => {
    if (
      event.key !== "ArrowLeft" &&
      event.key !== "ArrowRight" &&
      event.key !== "Home" &&
      event.key !== "End"
    )
      return;
    event.preventDefault();
    const nextIndex =
      event.key === "Home"
        ? 0
        : event.key === "End"
          ? options.length - 1
          : (index + (event.key === "ArrowRight" ? 1 : -1) + options.length) %
            options.length;
    const buttons =
      event.currentTarget.parentElement?.querySelectorAll("button");
    buttons?.[nextIndex]?.focus();
    onChange(options[nextIndex].value);
  };

  return (
    <div
      className={detailOnly ? "option-detail-tabs" : "option-task-tabs"}
      role="tablist"
      aria-label={detailOnly ? "期权右侧研究工具" : "期权研究工具"}
    >
      {options.map((option, index) => (
        <button
          type="button"
          role="tab"
          aria-selected={activeValue === option.value}
          className={activeValue === option.value ? "active" : ""}
          tabIndex={activeValue === option.value ? 0 : -1}
          onClick={() => onChange(option.value)}
          onKeyDown={(event) => handleKeyDown(event, index)}
          key={option.value}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function number(value: number | null, digits = 2) {
  if (value === null) return "—";
  return new Intl.NumberFormat("zh-Hant", {
    maximumFractionDigits: digits,
  }).format(value);
}

function percentage(value: number | null) {
  return value === null
    ? "—"
    : new Intl.NumberFormat("zh-Hant", {
        style: "percent",
        maximumFractionDigits: 1,
      }).format(value);
}

function dollars(value: number | null) {
  return value === null
    ? "—"
    : new Intl.NumberFormat("zh-Hant", {
        style: "currency",
        currency: "USD",
      }).format(value);
}

function quoteTime(value: string | null) {
  if (!value) return "没有报价时间";
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat("zh-Hant", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
        timeZone: "Asia/Hong_Kong",
      }).format(date);
}

function errorMessage(error: unknown) {
  if (error instanceof BrowserApiError || error instanceof Error) return safeDataError();
  return safeDataError();
}

function optionVisibilityStatus(metadata: {
  delivery_delay_minutes?: number;
  freshness: string;
  is_realtime: boolean;
  actionable_quote: boolean;
}) {
  const delay = displayDeliveryDelay(metadata.delivery_delay_minutes);
  const access = deliveryAllowsImmediateAction(metadata)
    ? "可核对即时行动"
    : "仅供研究";
  return [delay || displayFreshness(metadata.freshness), access]
    .filter(Boolean)
    .join(" · ");
}

function noopHistory(_: DrawingHistoryStatus) {}
function noop() {}

interface OptionResearchWorkspaceProps {
  symbol: string;
  onSymbolChange: (symbol: string) => void;
}

export function OptionResearchWorkspace({
  symbol,
  onSymbolChange,
}: OptionResearchWorkspaceProps) {
  const chartRef = useRef<MarketChartHandle | null>(null);
  const expandedChartRef = useRef<HTMLElement | null>(null);
  const [symbolDraft, setSymbolDraft] = useState(symbol);
  const [activeSymbol, setActiveSymbol] = useState(symbol);
  const [expiry, setExpiry] = useState("");
  const [chain, setChain] = useState<OptionChainPayload | null>(null);
  const [chainError, setChainError] = useState("");
  const [chainLoading, setChainLoading] = useState(true);
  const [refreshToken, setRefreshToken] = useState(0);
  const [rightFilter, setRightFilter] = useState<"ALL" | "CALL" | "PUT">("ALL");
  const [strikeFilter, setStrikeFilter] = useState("");
  const [page, setPage] = useState(1);
  const [selectedCode, setSelectedCode] = useState("");
  const [timeframe, setTimeframe] = useState("日线");
  const [candlePayload, setCandlePayload] =
    useState<OptionCandlePayload | null>(null);
  const [candleLoading, setCandleLoading] = useState(false);
  const [candleError, setCandleError] = useState("");
  const [legs, setLegs] = useState<OptionStrategyLeg[]>([]);
  const [templateStatus, setTemplateStatus] = useState("");
  const [chartExpanded, setChartExpanded] = useState(false);
  const [activePane, setActivePane] = useState<OptionPane>("chain");
  const [detailPane, setDetailPane] = useState<OptionPane>("chart");
  const chainRequestSequence = useRef(0);
  const candleRequestSequence = useRef(0);
  const candleKeyRef = useRef("");
  const chainLoadedRef = useRef(false);

  useEffect(() => {
    const normalized = symbol.trim().toUpperCase();
    if (!normalized || normalized === activeSymbol) return;
    setSymbolDraft(normalized);
    setActiveSymbol(normalized);
    setExpiry("");
    setLegs([]);
    chainLoadedRef.current = false;
  }, [activeSymbol, symbol]);

  useEffect(() => {
    let active = true;
    setChainLoading((current) => current || !chainLoadedRef.current);
    setChainError("");
    const stopPolling = createVisibilityPolling(async () => {
      const sequence = ++chainRequestSequence.current;
      try {
        const payload = await fetchOptionChain(activeSymbol, expiry || undefined);
        if (!active || chainRequestSequence.current !== sequence) return;
        chainLoadedRef.current = true;
        setChain(payload);
        if (!expiry) setExpiry(payload.expiry);
        setSelectedCode((current) =>
          payload.items.some((item) => item.contract_code === current)
            ? current
            : (payload.items[0]?.contract_code ?? ""),
        );
      } catch (error) {
        if (!active || chainRequestSequence.current !== sequence) return;
        chainLoadedRef.current = false;
        setChain(null);
        setSelectedCode("");
        setChainError(errorMessage(error));
      } finally {
        if (active && chainRequestSequence.current === sequence) setChainLoading(false);
      }
    }, 15_000);
    return () => {
      active = false;
      stopPolling();
    };
  }, [activeSymbol, expiry, refreshToken]);

  const selectedContract = useMemo(
    () =>
      chain?.items.find((item) => item.contract_code === selectedCode) ?? null,
    [chain, selectedCode],
  );

  useEffect(() => {
    if (!selectedCode) {
      setCandlePayload(null);
      return;
    }
    let active = true;
    const candleKey = `${selectedCode}:${timeframe}`;
    const changedContract = candleKeyRef.current !== candleKey;
    candleKeyRef.current = candleKey;
    setCandleLoading((current) => current || changedContract);
    setCandleError("");
    if (changedContract) setCandlePayload(null);
    const stopPolling = createVisibilityPolling(async () => {
      const sequence = ++candleRequestSequence.current;
      try {
        const payload = await fetchOptionCandles(selectedCode, timeframe);
        if (active && candleRequestSequence.current === sequence) setCandlePayload(payload);
      } catch (error) {
        if (active && candleRequestSequence.current === sequence) setCandleError(errorMessage(error));
      } finally {
        if (active && candleRequestSequence.current === sequence) setCandleLoading(false);
      }
    }, 15_000);
    return () => {
      active = false;
      stopPolling();
    };
  }, [selectedCode, timeframe]);

  useLayoutEffect(() => {
    if (!chartExpanded) return;
    const previousOverflow = document.documentElement.style.overflow;
    const previousFocus =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;
    const panel = expandedChartRef.current;
    document.documentElement.style.overflow = "hidden";
    const close = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        setChartExpanded(false);
        return;
      }
      if (event.key !== "Tab" || !panel) return;
      const focusable = Array.from(
        panel.querySelectorAll<HTMLElement>(
          "button:not([disabled]), select:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex='-1'])",
        ),
      );
      if (!focusable.length) {
        event.preventDefault();
        panel.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", close);
    panel?.focus({ preventScroll: true });
    return () => {
      document.documentElement.style.overflow = previousOverflow;
      window.removeEventListener("keydown", close);
      previousFocus?.focus({ preventScroll: true });
    };
  }, [chartExpanded]);

  const filteredContracts = useMemo(() => {
    const strikeNeedle = strikeFilter.trim();
    return (chain?.items ?? [])
      .filter(
        (item) => rightFilter === "ALL" || item.option_type === rightFilter,
      )
      .filter(
        (item) => !strikeNeedle || String(item.strike).includes(strikeNeedle),
      )
      .sort(
        (a, b) =>
          a.strike - b.strike || a.option_type.localeCompare(b.option_type),
      );
  }, [chain, rightFilter, strikeFilter]);
  const pageCount = Math.max(
    1,
    Math.ceil(filteredContracts.length / PAGE_SIZE),
  );
  const pageRows = filteredContracts.slice(
    (page - 1) * PAGE_SIZE,
    page * PAGE_SIZE,
  );
  const combination = useMemo(() => summarizeOptionCombination(legs), [legs]);
  const candles = candlePayload?.items ?? [];

  useEffect(() => {
    setPage(1);
  }, [rightFilter, strikeFilter, expiry]);
  useEffect(() => {
    if (page > pageCount) setPage(pageCount);
  }, [page, pageCount]);

  const submitSymbol = () => {
    const normalized = symbolDraft.trim().toUpperCase();
    if (!/^[A-Z][A-Z0-9.-]{0,11}$/.test(normalized)) {
      setChainError("请输入有效的美股代码，例如 AAPL。");
      return;
    }
    setExpiry("");
    setActiveSymbol(normalized);
    setLegs([]);
    onSymbolChange(normalized);
  };

  const addLeg = (contract: OptionContract) => {
    setLegs((current) => {
      const existing = current.find(
        (leg) =>
          leg.contract.contract_code === contract.contract_code &&
          leg.side === "BUY",
      );
      if (existing)
        return current.map((leg) =>
          leg.id === existing.id ? { ...leg, quantity: leg.quantity + 1 } : leg,
        );
      return [
        ...current,
        {
          id: `${contract.contract_code}-BUY-${Date.now()}`,
          contract,
          side: "BUY",
          quantity: 1,
        },
      ];
    });
    setTemplateStatus("已加入组合草稿；默认买入，可在组合区改为卖出。");
  };

  const applyTemplate = (template: OptionTemplateId) => {
    if (!selectedContract || !chain) return;
    const next = buildOptionTemplate(template, selectedContract, chain.items);
    if (!next.length) {
      setTemplateStatus("当前到期日与执行价附近没有足够合约，无法建立该模板。");
      return;
    }
    setLegs(next);
    setTemplateStatus(
      "已用真实期权链建立研究草稿；这不是交易建议，也不会自动下单。",
    );
  };

  const updateLeg = (
    id: string,
    patch: Partial<Pick<OptionStrategyLeg, "side" | "quantity">>,
  ) => {
    setLegs((current) =>
      current.map((leg) =>
        leg.id === id
          ? {
              ...leg,
              ...patch,
              quantity: Math.max(
                1,
                Math.min(99, patch.quantity ?? leg.quantity),
              ),
            }
          : leg,
      ),
    );
  };

  return (
    <div className="option-research-workspace">
      <div className="option-source-band" role="status">
        <span>
          <Activity size={15} /> 数据来源：
          {chain ? displayDataSource(chain.source) : "等待可验证数据"}
        </span>
        <strong>
          {chain
            ? `${chain.symbol} · ${chain.expiry} · ${chain.items.length} 张合约 · ${optionVisibilityStatus(chain)}`
            : "等待真实数据"}
        </strong>
      </div>

      {chain && !deliveryAllowsImmediateAction(chain) && (
        <div className="option-research-boundary">
          <CircleAlert size={16} />
          <span>
            <strong>当前期权数据仅供研究</strong>
            {'真实数据来源的实时权限或报价新鲜度尚未满足。'}{" "}
            不用于立即交易；缺失的 Greeks 会明确留空。
          </span>
        </div>
      )}

      <form
        className="option-research-controls"
        onSubmit={(event) => {
          event.preventDefault();
          submitSymbol();
        }}
      >
        <label>
          <span>美股标的</span>
          <span className="option-symbol-field">
            <Search size={15} />
            <input
              name="option-symbol"
              value={symbolDraft}
              onChange={(event) =>
                setSymbolDraft(event.target.value.toUpperCase())
              }
              spellCheck={false}
              autoComplete="off"
              placeholder="例如 AAPL…"
            />
          </span>
        </label>
        <SelectField label="到期日" value={expiry} disabled={!chain?.expiries.length} onValueChange={(value) => { setExpiry(value); setLegs([]); }} options={(chain?.expiries ?? ["读取中"]).map((value) => ({ value, label: value }))} />
        <label className="option-direction-control">
          <span>合约方向</span>
          <SegmentedControl
            ariaLabel="合约方向"
            value={rightFilter}
            options={[
              { value: "ALL", label: "全部" },
              { value: "CALL", label: "Call" },
              { value: "PUT", label: "Put" },
            ]}
            onChange={setRightFilter}
          />
        </label>
        <label>
          <span>执行价筛选</span>
          <input
            name="option-strike-filter"
            inputMode="decimal"
            autoComplete="off"
            value={strikeFilter}
            onChange={(event) => setStrikeFilter(event.target.value)}
            placeholder="例如 210…"
          />
        </label>
        <button className="button primary" type="submit">
          载入期权链
        </button>
        <button
          className="button secondary"
          type="button"
          onClick={() => setRefreshToken((value) => value + 1)}
        >
          <RefreshCw size={15} />
          刷新
        </button>
      </form>

      {chainLoading && (
        <div className="option-data-state" role="status">
          <LoaderCircle className="spin" size={22} />
          <strong>正在读取专业期权链…</strong>
          <span>
            仅使用明确标注的延迟研究数据；不会使用演示数据补位。
          </span>
        </div>
      )}
      {!chainLoading && chainError && (
        <div className="option-data-state error" role="alert">
          <CircleAlert size={22} />
          <strong>期权链暂时不可用</strong>
          <span>{chainError}</span>
          <button
            className="button secondary"
            type="button"
            onClick={() => setRefreshToken((value) => value + 1)}
          >
            重新读取
          </button>
        </div>
      )}

      {!chainLoading && chain && (
        <div className="option-workbench">
          <OptionTaskTabs
            value={activePane}
            onChange={setActivePane}
            legsCount={legs.length}
          />
          <div className="option-research-grid">
            <section
              className="option-chain-panel"
              aria-label="期权链"
              data-active={activePane === "chain"}
            >
              <header>
                <div>
                  <small>OPTION CHAIN</small>
                  <h3>合约报价与 Greeks</h3>
                </div>
                <span>
                  {filteredContracts.length} 项 · 第 {page}/{pageCount} 页
                </span>
              </header>
              <div className="option-chain-scroll">
                <table>
                  <thead>
                    <tr className="option-chain-groups">
                      <th colSpan={2}>合约</th>
                      <th colSpan={4}>报价</th>
                      <th colSpan={3}>流动性与波动</th>
                      <th colSpan={4}>Greeks</th>
                      <th rowSpan={2}>组合</th>
                    </tr>
                    <tr>
                      <th>合约</th>
                      <th>执行价</th>
                      <th>Bid</th>
                      <th>Ask</th>
                      <th>价差</th>
                      <th>最新</th>
                      <th>成交量</th>
                      <th>OI</th>
                      <th>IV</th>
                      <th>Delta</th>
                      <th>Gamma</th>
                      <th>Theta</th>
                      <th>Vega</th>
                      <th>组合</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pageRows.map((contract) => (
                      <tr
                        className={
                          contract.contract_code === selectedCode
                            ? "selected"
                            : ""
                        }
                        key={contract.contract_code}
                      >
                        <td>
                          <button
                            className="option-contract-trigger"
                            type="button"
                            onClick={() =>
                              setSelectedCode(contract.contract_code)
                            }
                          >
                            <b
                              className={
                                contract.option_type === "CALL" ? "call" : "put"
                              }
                            >
                              {contract.option_type}
                            </b>
                            <span>
                              {contract.contract_code.replace("US.", "")}
                            </span>
                          </button>
                          <small>{quoteTime(contract.quote_at)}</small>
                        </td>
                        <td>{number(contract.strike)}</td>
                        <td>{number(contract.bid)}</td>
                        <td>{number(contract.ask)}</td>
                        <td>{number(contract.spread)}</td>
                        <td>{number(contract.last)}</td>
                        <td>{number(contract.volume, 0)}</td>
                        <td>{number(contract.open_interest, 0)}</td>
                        <td>{percentage(contract.implied_volatility)}</td>
                        <td>{number(contract.greeks.delta, 3)}</td>
                        <td>{number(contract.greeks.gamma, 4)}</td>
                        <td>{number(contract.greeks.theta, 3)}</td>
                        <td>{number(contract.greeks.vega, 3)}</td>
                        <td>
                          <button
                            className="option-add-leg"
                            type="button"
                            aria-label={`将 ${contract.contract_code} 加入组合`}
                            onClick={() => addLeg(contract)}
                          >
                            <Plus size={14} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!pageRows.length && (
                  <div className="option-table-empty">
                    当前筛选没有合约，请调整 Call / Put 或执行价。
                  </div>
                )}
              </div>
              <footer className="option-pagination">
                <button
                  type="button"
                  disabled={page <= 1}
                  aria-label="上一页"
                  onClick={() => setPage((value) => value - 1)}
                >
                  <ChevronLeft size={15} />
                </button>
                <span>
                  {page} / {pageCount}
                </span>
                <button
                  type="button"
                  disabled={page >= pageCount}
                  aria-label="下一页"
                  onClick={() => setPage((value) => value + 1)}
                >
                  <ChevronRight size={15} />
                </button>
              </footer>
            </section>

            <section
              ref={expandedChartRef}
              className={`option-chart-panel ${chartExpanded ? "is-expanded" : ""}`}
              aria-label="期权报价 K 线"
              aria-labelledby={chartExpanded ? "option-chart-title" : undefined}
              data-active={activePane === "chart"}
              data-detail-active={detailPane === "chart"}
              role={chartExpanded ? "dialog" : undefined}
              aria-modal={chartExpanded || undefined}
              tabIndex={chartExpanded ? -1 : undefined}
            >
              <header className="option-chart-header">
                <div>
                  <small>OPTION QUOTE CHART</small>
                  <h3 id="option-chart-title">
                    {selectedContract?.contract_code ?? "选择一张合约"}
                  </h3>
                </div>
                <div className="option-chart-controls">
                  <TimeframeDropdown ariaLabel="期权 K 线周期" value={timeframe} options={OPTION_TIMEFRAMES.map((value) => ({ value, label: value, group: value.includes("分") ? "分钟" : value.includes("小时") ? "小时" : "日/周/月" }))} onChange={setTimeframe} />
                  <button
                    type="button"
                    aria-label="缩小 K 线"
                    title="缩小 K 线"
                    onClick={() => chartRef.current?.zoomOut()}
                  >
                    <ZoomOut size={15} />
                  </button>
                  <button
                    type="button"
                    aria-label="放大 K 线"
                    title="放大 K 线"
                    onClick={() => chartRef.current?.zoomIn()}
                  >
                    <ZoomIn size={15} />
                  </button>
                  <button
                    type="button"
                    aria-label="适配全部 K 线"
                    title="适配全部 K 线"
                    onClick={() => chartRef.current?.reset()}
                  >
                    <RotateCcw size={15} />
                  </button>
                  <button
                    type="button"
                    aria-label={
                      chartExpanded ? "恢复期权研究布局" : "全屏查看期权 K 线"
                    }
                    title={chartExpanded ? "恢复布局" : "全屏"}
                    onClick={() => setChartExpanded((value) => !value)}
                  >
                    {chartExpanded ? (
                      <Minimize2 size={15} />
                    ) : (
                      <Expand size={15} />
                    )}
                  </button>
                </div>
              </header>
              {candlePayload && (
                <div
                  className={`option-candle-source ${deliveryAllowsImmediateAction(candlePayload) ? "verified" : "research"}`}
                >
                  <Activity size={14} />
                  <span>{`${displayDataSource(candlePayload.source)} · ${optionVisibilityStatus(candlePayload)}`}</span>
                </div>
              )}
              {selectedContract && (
                <div className="option-quote-strip">
                  <span>
                    Bid <b>{number(selectedContract.bid)}</b>
                  </span>
                  <span>
                    Ask <b>{number(selectedContract.ask)}</b>
                  </span>
                  <span>
                    价差 <b>{number(selectedContract.spread)}</b>
                  </span>
                  <span>
                    IV <b>{percentage(selectedContract.implied_volatility)}</b>
                  </span>
                  <small>{quoteTime(selectedContract.quote_at)}</small>
                </div>
              )}
              <div className="option-chart-canvas">
                {candleLoading && (
                  <div className="option-chart-state">
                    <LoaderCircle className="spin" size={22} />
                    <strong>正在读取期权 K 线…</strong>
                  </div>
                )}
                {!candleLoading && candleError && (
                  <div className="option-chart-state error">
                    <CircleAlert size={22} />
                    <strong>K 线暂时不可用</strong>
                    <span>{candleError}</span>
                  </div>
                )}
                {!candleLoading &&
                  !candleError &&
                  candles.length > 0 &&
                  selectedContract &&
                  candlePayload && (
                    <MarketChart
                      ref={chartRef}
                      candles={candles}
                      market="US"
                      symbol={selectedContract.contract_code}
                      timeframe={timeframe}
                      showGrid
                      showVolume
                      dataStatus={`${displayDataSource(candlePayload.source)} · ${optionVisibilityStatus(candlePayload)}`}
                      officialActivity={null}
                      alertPrices={[]}
                      drawingActive={false}
                      drawingToolState={DRAWING_OFF}
                      drawingCommand={{ id: 0, type: "undo" }}
                      drawingMarkerId="option-research-drawing"
                      onDrawingHistoryChange={noopHistory}
                      onDrawingToolComplete={noop}
                      onViewportChange={noop}
                    />
                  )}
                {!candleLoading && !candleError && !candles.length && (
                  <div className="option-chart-state">
                    <Activity size={22} />
                    <strong>请选择有 K 线数据的合约</strong>
                    <span>
                      数据源没有返回可用蜡烛图时，不会使用模拟数据补位。
                    </span>
                  </div>
                )}
              </div>
            </section>
          </div>

          <OptionTaskTabs
            value={detailPane}
            onChange={setDetailPane}
            legsCount={legs.length}
            detailOnly
          />

          <section
            className="option-combination-panel"
            aria-label="多腿期权组合研究"
            data-active={activePane === "combination"}
            data-detail-active={detailPane === "combination"}
          >
            <header>
              <div>
                <small>MULTI-LEG RESEARCH</small>
                <h3>多腿组合草稿</h3>
              </div>
              <strong>{combination.label}</strong>
            </header>
            <div className="option-template-bar">
              <span>以当前合约为锚点：</span>
              {TEMPLATE_LABELS.map(([id, label]) => (
                <button
                  type="button"
                  disabled={!selectedContract || !chain}
                  onClick={() => applyTemplate(id)}
                  key={id}
                >
                  {label}
                </button>
              ))}
              <button
                className="clear"
                type="button"
                disabled={!legs.length}
                onClick={() => setLegs([])}
              >
                清空组合
              </button>
            </div>
            {templateStatus && (
              <p className="option-template-status" role="status">
                {templateStatus}
              </p>
            )}
            <div className="option-leg-list">
              {legs.map((leg) => (
                <article key={leg.id}>
                  <span>
                    <b
                      className={
                        leg.contract.option_type === "CALL" ? "call" : "put"
                      }
                    >
                      {leg.contract.option_type}
                    </b>
                    <strong>{leg.contract.strike}</strong>
                    <small>{leg.contract.expiry}</small>
                  </span>
                  <SelectField label="方向" value={leg.side} onValueChange={(value) => updateLeg(leg.id, { side: value as OptionLegSide })} options={[{ value: "BUY", label: "买入" }, { value: "SELL", label: "卖出" }]} />
                  <label>
                    数量
                    <input
                      type="number"
                      min="1"
                      max="99"
                      value={leg.quantity}
                      onChange={(event) =>
                        updateLeg(leg.id, {
                          quantity: Number(event.target.value) || 1,
                        })
                      }
                    />
                  </label>
                  <span className="leg-quote">
                    <small>
                      {leg.side === "BUY" ? "按 Ask 估算" : "按 Bid 估算"}
                    </small>
                    <b>
                      {number(
                        leg.side === "BUY"
                          ? leg.contract.ask
                          : leg.contract.bid,
                      )}
                    </b>
                  </span>
                  <button
                    className="option-remove-leg"
                    type="button"
                    aria-label={`删除 ${leg.contract.contract_code}`}
                    onClick={() =>
                      setLegs((current) =>
                        current.filter((item) => item.id !== leg.id),
                      )
                    }
                  >
                    <Trash2 size={15} />
                  </button>
                </article>
              ))}
              {!legs.length && (
                <div className="option-combination-empty">
                  从期权链加入合约，或用当前选中合约建立跨式、宽跨式与价差组合。
                </div>
              )}
            </div>
            <dl className="option-combination-summary">
              <div>
                <dt>{combination.quoteLabel}</dt>
                <dd>
                  {dollars(
                    combination.netCash === null
                      ? null
                      : Math.abs(combination.netCash),
                  )}
                </dd>
                <small>
                  {combination.quoteComplete
                    ? "研究报价完整"
                    : "等待完整 Bid / Ask"}
                </small>
              </div>
              <div>
                <dt>组合 Delta</dt>
                <dd>{number(combination.delta, 2)}</dd>
              </div>
              <div>
                <dt>组合 Gamma</dt>
                <dd>{number(combination.gamma, 3)}</dd>
              </div>
              <div>
                <dt>组合 Theta</dt>
                <dd>{number(combination.theta, 2)}</dd>
              </div>
              <div>
                <dt>组合 Vega</dt>
                <dd>{number(combination.vega, 2)}</dd>
              </div>
            </dl>
            <p className="option-research-disclaimer">
              组合价格按买入取 Ask、卖出取 Bid、每张合约乘数 100
              估算；报价缺失时不计算。这里只构建研究草稿，不代表量化建议，也不会发送订单。
            </p>
          </section>
        </div>
      )}
    </div>
  );
}
