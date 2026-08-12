import {
  BarChart3,
  CheckCircle2,
  Code2,
  FlaskConical,
  LockKeyhole,
  Play,
  RotateCcw,
  ShieldCheck,
  Sparkles,
  WandSparkles,
} from "lucide-react";
import { useRef, useState, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { PageHeader } from "../components/PageHeader";
import { OptionResearchWorkspace } from "../components/OptionResearchWorkspace";
import { MetricRing } from "../components/ui/MetricRing";
import { SelectField } from "../components/ui/SelectField";
import { useWorkspace } from "../api/workspace-context";
import { displayDataSource } from "../domain/dataSourcePresentation";
import { generateStrategyDraft } from "../domain/strategyDraft";

type LabTab = "strategy" | "backtest" | "risk" | "options";

const tabs: Array<{ key: LabTab; label: string }> = [
  { key: "strategy", label: "策略编辑器" },
  { key: "backtest", label: "回测与参数" },
  { key: "risk", label: "压力测试" },
  { key: "options", label: "期权与 Greeks" },
];

export function ProfessionalLabPage() {
  const workspace = useWorkspace();
  const navigate = useNavigate();
  const [tab, setTab] = useState<LabTab>("strategy");
  const [symbol, setSymbol] = useState("AAPL");
  const [timeframe, setTimeframe] = useState("日线");
  const [lookback, setLookback] = useState("1 年");
  const [commission, setCommission] = useState("0.03");
  const [slippage, setSlippage] = useState("0.05");
  const [status, setStatus] = useState("");
  const [priceShock, setPriceShock] = useState(-15);
  const [volatilityShock, setVolatilityShock] = useState(35);
  const [slippageShock, setSlippageShock] = useState(100);
  const [profitTarget, setProfitTarget] = useState("2R");
  const [gapRisk, setGapRisk] = useState("财报周");
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  const [code, setCode] = useState(`
// 示例策略：只用于研究，不会自动下单
when rsi(14) < 30 and close > sma(50)
  action = BUY
  risk = 1%
  stop = atr(14) * 1.5
  target = 2R
`);
  const [naturalLanguage, setNaturalLanguage] = useState(
    "当 RSI 低于 30，价格重新站上 50 日均线时买入；每笔最多亏账户的 1%，跌破 ATR 止损，达到 2 倍风险分批止盈。",
  );
  const capabilities = workspace.data?.membership.capabilities ?? [];
  const currentPlan = workspace.user?.plan_display_name ?? "演示模式";
  const maxBacktestYears = capabilities.includes("backtest_10y")
    ? 10
    : capabilities.includes("backtest_3y")
      ? 3
      : capabilities.includes("backtest_1y")
        ? 1
        : 0;
  const isProfessional = capabilities.some((item) =>
    ["api_access", "code_import", "strategy_generate_complex"].includes(item),
  );
  const canUseOptionResearch = [
    "option_chain",
    "option_quote_chart",
    "option_greeks",
    "option_iv",
    "option_strategy",
    "option_strategy_multi_leg",
  ].every((capability) => capabilities.includes(capability));
  const lookbackYears = Number.parseInt(lookback, 10);
  const runBacktest = () => {
    setStatus(
      "回测引擎尚未接入；参数只保留在当前页面，离开后不会保存，也不会生成虚假成绩。",
    );
  };
  const generateStrategy = () => {
    const prompt = naturalLanguage.trim();
    if (!prompt) return;
    const draft = generateStrategyDraft(prompt, symbol, timeframe);
    setCode(draft.code);
    setSymbol(draft.symbol);
    setTimeframe(draft.timeframe);
    setStatus(draft.summary);
    setTab("strategy");
  };
  const handleTabKeyDown = (
    event: KeyboardEvent<HTMLButtonElement>,
    index: number,
  ) => {
    let target = index;
    if (event.key === "ArrowRight") target = (index + 1) % tabs.length;
    else if (event.key === "ArrowLeft")
      target = (index - 1 + tabs.length) % tabs.length;
    else if (event.key === "Home") target = 0;
    else if (event.key === "End") target = tabs.length - 1;
    else return;
    event.preventDefault();
    const next = tabs[target];
    setTab(next.key);
    tabRefs.current[target]?.focus();
  };

  return (
    <div
      className={`page operations-page professional-lab-page lab-tab-${tab}`}
    >
      <PageHeader
        kicker="PROFESSIONAL / RESEARCH LAB"
        title="专业研究工作台"
        description="写策略并配置回测、参数与压力场景。真实结果必须由已接入的数据和计算引擎返回，模型不得自行发布或开启实盘。"
      />
      <section className="lab-context-band" aria-label="研究工作台状态">
        <span>
          <LockKeyhole size={16} />
          <strong>当前方案：{currentPlan}</strong>
          <small>
            {maxBacktestYears
              ? `近 ${maxBacktestYears} 年样本范围`
              : "回测参数未开放"}{" "}
            · {isProfessional ? "代码与 API 已开放" : "复杂研究需专业会员"}
          </small>
        </span>
        <span>
          <ShieldCheck size={16} />
          <strong>数据合同</strong>
          <small>
            {displayDataSource(workspace.data?.market_data.display_source, "界面演示数据")}
          </small>
        </span>
        <span>
          <FlaskConical size={16} />
          <strong>计算状态</strong>
          <small>参数配置中 · 引擎未接入</small>
        </span>
        <span className="is-blocked">
          <LockKeyhole size={16} />
          <strong>自动发布</strong>
          <small>禁止</small>
        </span>
      </section>
      <nav className="lab-tabs" role="tablist" aria-label="专业研究工具">
        {tabs.map((item, index) => (
          <button
            id={`lab-tab-${item.key}`}
            className={tab === item.key ? "active" : ""}
            key={item.key}
            type="button"
            role="tab"
            aria-selected={tab === item.key}
            aria-controls={`lab-panel-${item.key}`}
            tabIndex={tab === item.key ? 0 : -1}
            ref={(element) => {
              tabRefs.current[index] = element;
            }}
            onClick={() => setTab(item.key)}
            onKeyDown={(event) => handleTabKeyDown(event, index)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      <section
        id="lab-panel-strategy"
        className="lab-grid lab-module-card"
        data-lab-tab="strategy"
        role="tabpanel"
        aria-labelledby="lab-tab-strategy"
        hidden={tab !== "strategy"}
      >
        <article className="data-panel code-editor-panel">
          <header className="panel-heading">
            <div>
              <span>STRATEGY BUILDER</span>
              <h2>策略编辑器</h2>
            </div>
            <span className="status-chip research">
              <Code2 size={14} /> 研究草稿
            </span>
          </header>
          <div className="strategy-toolbar">
            <label>
              策略名称
              <input defaultValue="RSI · 趋势确认" />
            </label>
            <label>
              标的
              <input
                value={symbol}
                onChange={(event) =>
                  setSymbol(event.target.value.toUpperCase())
                }
              />
            </label>
            <SelectField label="周期" value={timeframe} onValueChange={setTimeframe} options={[{ value: "日线", label: "日线" }, { value: "1小时", label: "1小时" }, { value: "15分钟", label: "15分钟" }]} />
          </div>
          <div className="natural-language-builder">
            <label>
              <span>
                <WandSparkles size={15} /> 自然语言生成策略
              </span>
              <textarea
                value={naturalLanguage}
                onChange={(event) => setNaturalLanguage(event.target.value)}
                aria-label="自然语言策略描述"
              />
            </label>
            <button
              className="button secondary"
              type="button"
              onClick={generateStrategy}
            >
              <Sparkles size={15} />
              生成规则草稿
            </button>
            <small>
              当前解析标的、周期、方向、常见
              RSI/均线/价格触发与风控；未识别内容会保留为人工补充，不会自动下单或发布。
            </small>
          </div>
          <textarea
            className="strategy-code"
            value={code}
            onChange={(event) => setCode(event.target.value)}
            spellCheck={false}
            aria-label="策略代码编辑器"
          />
          <footer className="lab-actions">
            <span>
              语法检查：{code.includes("action") ? "通过" : "需要 action"}
            </span>
            <button
              className="button secondary"
              type="button"
              onClick={() => setCode(code.trim())}
            >
              <RotateCcw size={15} /> 格式化
            </button>
            <button
              className="button primary"
              type="button"
              onClick={() => {
                setTab("backtest");
                setStatus(
                  "策略已载入当前页面的参数区；引擎未接入，不会生成成绩。",
                );
              }}
            >
              <Play size={15} /> 载入回测参数
            </button>
          </footer>
        </article>
        <aside className="data-panel lab-explainer">
          <header className="panel-heading">
            <div>
              <span>GOVERNANCE</span>
              <h2>研究边界</h2>
            </div>
          </header>
          <ul>
            <li>
              <CheckCircle2 />
              <span>
                <strong>可以写和改策略</strong>
                <small>
                  代码、指标、入场、止损和目标可在本页编辑为草稿；离开页面前请自行保存内容。
                </small>
              </span>
            </li>
            <li>
              <CheckCircle2 />
              <span>
                <strong>可以配置回测与压力参数</strong>
                <small>
                  接入引擎后，结果必须记录样本期、手续费、滑点和数据来源。
                </small>
              </span>
            </li>
            <li>
              <LockKeyhole />
              <span>
                <strong>不能自动发布</strong>
                <small>
                  正式模型需要独立审核，实盘还需要用户授权个人券商。
                </small>
              </span>
            </li>
          </ul>
        </aside>
      </section>

      <section
        id="lab-panel-backtest"
        className="data-panel lab-panel lab-module-card"
        data-lab-tab="backtest"
        role="tabpanel"
        aria-labelledby="lab-tab-backtest"
        hidden={tab !== "backtest"}
      >
        <header className="panel-heading">
          <div>
            <span>BACKTEST CONFIGURATION</span>
            <h2>回测与参数测试</h2>
          </div>
          <span className="status-chip research">
            <BarChart3 size={14} /> 引擎未接入
          </span>
        </header>
        <div className="lab-backtest-workbench">
          <div className="lab-config-zone">
            <div className="backtest-form">
              <label>
                标的
                <input
                  value={symbol}
                  onChange={(event) =>
                    setSymbol(event.target.value.toUpperCase())
                  }
                />
              </label>
              <SelectField label="样本期" value={lookback} onValueChange={setLookback} options={[1, 3, 5, 10].map((years) => ({ value: `${years} 年`, label: `${years} 年`, disabled: maxBacktestYears < years }))} />
              <label>
                手续费（%）
                <input
                  inputMode="decimal"
                  value={commission}
                  onChange={(event) => setCommission(event.target.value)}
                />
              </label>
              <label>
                滑点（%）
                <input
                  inputMode="decimal"
                  value={slippage}
                  onChange={(event) => setSlippage(event.target.value)}
                />
              </label>
            </div>
            <div className="parameter-grid">
              <label>
                RSI 周期
                <input type="number" defaultValue="14" min="2" max="100" />
              </label>
              <label>
                均线周期
                <input type="number" defaultValue="50" min="5" max="300" />
              </label>
              <label>
                单笔风险（%）
                <input
                  type="number"
                  defaultValue="1"
                  min="0.1"
                  max="5"
                  step="0.1"
                />
              </label>
              <SelectField label="分批止盈" value={profitTarget} onValueChange={setProfitTarget} options={["1.5R", "2R", "3R"].map((value) => ({ value, label: value }))} />
            </div>
            <div className="lab-run-row">
              <button
                className="button primary"
                type="button"
                onClick={runBacktest}
                disabled={!maxBacktestYears || lookbackYears > maxBacktestYears}
              >
                <Play size={15} /> 保留本页参数
              </button>
              <span>
                {status ||
                  (maxBacktestYears
                    ? `当前页面参数：${lookback} ${timeframe}，手续费 ${commission}%、滑点 ${slippage}%；离开后不会保存。`
                    : "升级会员后可配置回测参数。")}
              </span>
            </div>
          </div>
          <aside className="lab-result-zone" aria-label="回测结果状态">
            <div className="backtest-unavailable">
              <BarChart3 size={24} />
              <strong>尚未生成成绩</strong>
              <span>
                当前环境没有连接回测引擎。接入后才会显示交易数、收益、回撤、稳定性、运行
                ID 和数据来源。
              </span>
            </div>
            <p className="lab-disclaimer">
              没有真实引擎返回的数据时，不展示任何看起来像历史成绩的数字。
            </p>
          </aside>
        </div>
      </section>

      <section
        id="lab-panel-risk"
        className="data-panel lab-panel lab-module-card"
        data-lab-tab="risk"
        role="tabpanel"
        aria-labelledby="lab-tab-risk"
        hidden={tab !== "risk"}
      >
        <header className="panel-heading">
          <div>
            <span>STRESS SCENARIO</span>
            <h2>压力场景参数</h2>
          </div>
          <span className="status-chip research">
            <ShieldCheck size={14} /> 计算引擎未接入
          </span>
        </header>
        <div className="lab-risk-workbench">
          <div className="lab-config-zone">
            <div className="stress-grid">
              <label>
                价格冲击
                <input
                  type="range"
                  min="-40"
                  max="10"
                  value={priceShock}
                  onChange={(event) =>
                    setPriceShock(Number(event.target.value))
                  }
                />
                <output>
                  {priceShock > 0 ? "+" : ""}
                  {priceShock}%
                </output>
              </label>
              <label>
                波动率变化
                <input
                  type="range"
                  min="-20"
                  max="100"
                  value={volatilityShock}
                  onChange={(event) =>
                    setVolatilityShock(Number(event.target.value))
                  }
                />
                <output>
                  {volatilityShock > 0 ? "+" : ""}
                  {volatilityShock}%
                </output>
              </label>
              <label>
                滑点放大
                <input
                  type="range"
                  min="0"
                  max="300"
                  value={slippageShock}
                  onChange={(event) =>
                    setSlippageShock(Number(event.target.value))
                  }
                />
                <output>{(1 + slippageShock / 100).toFixed(1)}×</output>
              </label>
              <SelectField label="跳空风险" value={gapRisk} onValueChange={setGapRisk} options={["正常", "财报周", "极端事件"].map((value) => ({ value, label: value }))} />
            </div>
            <div className="stress-result">
              <strong>尚未生成压力测试结论</strong>
              <span>
                这里只配置待计算的场景。接入真实策略、行情样本和计算引擎后才会显示损益、回撤与通过/不通过结论。
              </span>
            </div>
          </div>
          <aside className="lab-risk-inspector" aria-label="压力场景强度">
            <header>
              <span>SCENARIO INTENSITY</span>
              <h3>压力参数概览</h3>
              <small>圆盘仅显示当前输入强度，不代表模型结论。</small>
            </header>
            <div className="lab-risk-rings">
              <MetricRing
                label="价格冲击"
                value={(Math.abs(priceShock) / 40) * 100}
                displayValue={`${priceShock > 0 ? "+" : ""}${priceShock}%`}
                caption="相对极端跌幅区间"
                tone={priceShock <= -20 ? "negative" : "warning"}
              />
              <MetricRing
                label="波动率"
                value={Math.abs(volatilityShock)}
                displayValue={`${volatilityShock > 0 ? "+" : ""}${volatilityShock}%`}
                caption="当前波动率变化假设"
                tone="positive"
              />
              <MetricRing
                label="滑点放大"
                value={slippageShock / 3}
                displayValue={`${(1 + slippageShock / 100).toFixed(1)}×`}
                caption="相对基础滑点倍数"
                tone="accent"
              />
            </div>
          </aside>
        </div>
      </section>

      <section
        id="lab-panel-options"
        className="data-panel lab-panel lab-module-card"
        data-lab-tab="options"
        role="tabpanel"
        aria-labelledby="lab-tab-options"
        hidden={tab !== "options"}
      >
        <header className="panel-heading">
          <div>
            <span>OPTIONS RESEARCH</span>
            <h2>期权链、报价 K 线与组合研究</h2>
          </div>
          <span
            className={`status-chip ${canUseOptionResearch ? "official" : "research"}`}
          >
            <LockKeyhole size={14} />{" "}
            {canUseOptionResearch ? "专业研究权限已开放" : "需要专业会员"}
          </span>
        </header>
        {canUseOptionResearch ? (
          <OptionResearchWorkspace symbol={symbol} onSymbolChange={setSymbol} />
        ) : (
          <div className="options-gate">
            <LockKeyhole size={27} />
            <h2>期权研究只对专业会员开放</h2>
            <p>
              期权链、报价 K 线、Greeks、IV、Call / Put
              合约与单腿、多腿组合内容保持隐藏。升级前不会请求或泄露任何合约字段。
            </p>
            <button
              className="button secondary"
              type="button"
              onClick={() => navigate("/membership")}
            >
              查看专业会员权限
            </button>
          </div>
        )}
      </section>
    </div>
  );
}
