import {
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Download,
  FlaskConical,
  LockKeyhole,
  RefreshCw,
  ShieldCheck,
  Upload,
} from "lucide-react";
import { useEffect, useRef, useState, type ChangeEvent, type KeyboardEvent } from "react";
import { useNavigate } from "react-router-dom";
import { PageHeader } from "../components/PageHeader";
import { BacktestWorkspace } from "../components/lab/BacktestWorkspace";
import { OptionResearchWorkspace } from "../components/OptionResearchWorkspace";
import { MetricRing } from "../components/ui/MetricRing";
import { useWorkspace } from "../api/workspace-context";
import { displayDataSource } from "../domain/dataSourcePresentation";
import type { BacktestPrepareRequest } from "../api/backtests";
import { fetchLabStressCatalog, runLabStress, type LabStressCatalog, type LabStressResult } from "../api/labStress";
import {
  downloadLabCsvImportCsv,
  fetchLabCsvImport,
  fetchLabCsvImportReadiness,
  fetchLabCsvImportSignals,
  listLabCsvImports,
  uploadLabCsvImport,
  type LabCsvImportJob,
  type LabCsvImportReadiness,
  type LabCsvImportSignal,
} from "../api/labCsvImports";
import "../styles/lab-stress.css";
import "../styles/lab-csv-imports.css";

type LabTab = "strategy" | "backtest" | "risk" | "options";

const tabs: Array<{ key: LabTab; label: string }> = [
  { key: "strategy", label: "策略模板" },
  { key: "backtest", label: "回测与参数" },
  { key: "risk", label: "压力测试" },
  { key: "options", label: "期权与 Greeks" },
];

type CsvReadinessState = "loading" | "ready" | "forbidden" | "error";
type CsvHistoryState = "idle" | "loading" | "ready" | "error";
type CsvUploadState = "idle" | "loading" | "success" | "replay" | "forbidden" | "quota" | "error";

function csvErrorMessage(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

function formatCsvDate(value: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString("zh-TW", { dateStyle: "medium", timeStyle: "short" });
}

function CsvImportHistoryPanel() {
  const [readiness, setReadiness] = useState<LabCsvImportReadiness | null>(null);
  const [readinessState, setReadinessState] = useState<CsvReadinessState>("loading");
  const [readinessError, setReadinessError] = useState<string | null>(null);
  const [historyState, setHistoryState] = useState<CsvHistoryState>("idle");
  const [jobs, setJobs] = useState<LabCsvImportJob[]>([]);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [uploadState, setUploadState] = useState<CsvUploadState>("idle");
  const [uploadMessage, setUploadMessage] = useState<string | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<LabCsvImportJob | null>(null);
  const [signals, setSignals] = useState<LabCsvImportSignal[]>([]);
  const [detailState, setDetailState] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [detailError, setDetailError] = useState<string | null>(null);
  const [exportingId, setExportingId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement | null>(null);

  const loadHistory = async (nextReadiness = readiness) => {
    if (!nextReadiness) return;
    setHistoryState("loading");
    setHistoryError(null);
    try {
      setJobs(await listLabCsvImports());
      setHistoryState("ready");
    } catch (error) {
      setHistoryState("error");
      setHistoryError(csvErrorMessage(error, "CSV 导入历史暂时不可用。"));
    }
  };

  const load = async () => {
    setReadinessState("loading");
    setReadinessError(null);
    try {
      const nextReadiness = await fetchLabCsvImportReadiness();
      setReadiness(nextReadiness);
      setReadinessState("ready");
      await loadHistory(nextReadiness);
    } catch (error) {
      const status = typeof error === "object" && error && "status" in error ? Number(error.status) : 0;
      setReadiness(null);
      setReadinessState(status === 403 ? "forbidden" : "error");
      setReadinessError(csvErrorMessage(error, status === 403 ? "当前账户未开放 CSV 股票记录导入。" : "CSV 导入准备状态暂时不可用。"));
      setHistoryState("idle");
      setJobs([]);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const handleFileChange = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!readiness || readiness.quota.remaining === 0) {
      setUploadState("quota");
      setUploadMessage("今日 CSV 导入额度已用完。");
      return;
    }
    setUploadState("loading");
    setUploadMessage(null);
    try {
      const job = await uploadLabCsvImport(file);
      setJobs((current) => [job, ...current.filter((item) => item.public_id !== job.public_id)]);
      setReadiness((current) => current ? { ...current, quota: { ...current.quota, used: current.quota.used + (job.replayed ? 0 : 1), remaining: current.quota.remaining === null ? null : Math.max(0, current.quota.remaining - (job.replayed ? 0 : 1)) } } : current);
      setUploadState(job.replayed ? "replay" : "success");
      setUploadMessage(job.replayed ? "检测到相同 CSV，已复用历史记录。" : `已导入 ${job.row_count} 条股票记录。`);
    } catch (error) {
      const status = typeof error === "object" && error && "status" in error ? Number(error.status) : 0;
      const message = csvErrorMessage(error, "CSV 上传失败，请检查文件后重试。");
      setUploadState(status === 403 ? (readiness?.quota.remaining === 0 || /额度|上限/.test(message) ? "quota" : "forbidden") : "error");
      setUploadMessage(message);
    }
  };

  const toggleDetail = async (job: LabCsvImportJob) => {
    if (selectedId === job.public_id) {
      setSelectedId(null);
      return;
    }
    setSelectedId(job.public_id);
    setDetail(null);
    setSignals([]);
    setDetailError(null);
    setDetailState("loading");
    try {
      const [nextDetail, nextSignals] = await Promise.all([fetchLabCsvImport(job.public_id), fetchLabCsvImportSignals(job.public_id)]);
      setDetail(nextDetail);
      setSignals(nextSignals);
      setDetailState("ready");
    } catch (error) {
      setDetailState("error");
      setDetailError(csvErrorMessage(error, "CSV 股票记录详情暂时不可用。"));
    }
  };

  const exportCsv = async (publicId?: string) => {
    setExportingId(publicId ?? "all");
    try {
      const blob = await downloadLabCsvImportCsv(publicId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = publicId ? `ciclo-signal-import-${publicId}.csv` : "ciclo-signal-import-all.csv";
      anchor.click();
      URL.revokeObjectURL(url);
    } catch (error) {
      setHistoryError(csvErrorMessage(error, "CSV 导出失败，请稍后重试。"));
    } finally {
      setExportingId(null);
    }
  };

  const quotaRemaining = readiness?.quota.remaining ?? null;

  return (
    <section className="data-panel lab-csv-import-panel" aria-labelledby="lab-csv-import-title">
      <header className="panel-heading lab-csv-import-heading">
        <div>
          <span>CSV RECORD IMPORT</span>
          <h2 id="lab-csv-import-title">CSV 导入 / 历史</h2>
          <p>上传不超过 256 KB 的 CSV，保存为可追溯的股票研究记录。</p>
        </div>
        <div className="lab-csv-import-heading-actions">
          {readinessState === "ready" && <span className="status-chip research">今日额度 {quotaRemaining === null ? "不限" : `${quotaRemaining} 次`}</span>}
          <button className="icon-button" type="button" onClick={() => void load()} disabled={readinessState === "loading"} aria-label="刷新 CSV 导入状态" title="刷新">
            <RefreshCw size={15} className={readinessState === "loading" ? "lab-csv-spin" : ""} />
          </button>
        </div>
      </header>

      {readinessState === "loading" && <p className="lab-csv-state" role="status">正在读取 CSV 导入权限…</p>}
      {readinessState === "forbidden" && <p className="lab-csv-state is-forbidden" role="alert"><LockKeyhole size={17} />{readinessError ?? "当前账户未开放 CSV 股票记录导入。"}</p>}
      {readinessState === "error" && <div className="lab-csv-state is-error" role="alert"><span>{readinessError ?? "CSV 导入暂时不可用。"}</span><button className="button secondary" type="button" onClick={() => void load()}>重试</button></div>}

      {readinessState === "ready" && (
        <>
          <div className="lab-csv-upload-row">
            <div className="lab-csv-upload-copy">
              <Upload size={19} />
              <span><strong>选择 CSV 文件</strong><small>仅接受 CSV；单文件上限 256 KB。</small></span>
            </div>
            <input ref={fileInputRef} className="lab-csv-file-input" type="file" accept=".csv,text/csv" onChange={handleFileChange} disabled={uploadState === "loading" || quotaRemaining === 0} />
            <button className="button primary" type="button" onClick={() => fileInputRef.current?.click()} disabled={uploadState === "loading" || quotaRemaining === 0}>
              {uploadState === "loading" ? "上传中…" : quotaRemaining === 0 ? "额度已用完" : "选择文件"}
            </button>
            <button className="button secondary" type="button" onClick={() => void exportCsv()} disabled={jobs.length === 0 || exportingId !== null}>{exportingId === "all" ? "导出中…" : "导出全部"}</button>
          </div>
          {uploadMessage && <p className={`lab-csv-feedback is-${uploadState}`} role={uploadState === "error" || uploadState === "forbidden" || uploadState === "quota" ? "alert" : "status"}>{uploadMessage}</p>}
          {historyState === "loading" && <p className="lab-csv-state" role="status">正在读取导入历史…</p>}
          {historyState === "error" && <div className="lab-csv-state is-error" role="alert"><span>{historyError ?? "CSV 导入历史暂时不可用。"}</span><button className="button secondary" type="button" onClick={() => void loadHistory()}>重试</button></div>}
          {historyState === "ready" && jobs.length === 0 && <p className="lab-csv-state">还没有 CSV 导入记录。选择文件后，这里会显示股票记录历史。</p>}
          {historyState === "ready" && jobs.length > 0 && (
            <div className="lab-csv-history" aria-label="CSV 导入历史">
              {jobs.map((job) => {
                const expanded = selectedId === job.public_id;
                return (
                  <article className={`lab-csv-job ${expanded ? "is-expanded" : ""}`} key={job.public_id}>
                    <div className="lab-csv-job-row">
                      <button className="lab-csv-job-toggle" type="button" onClick={() => void toggleDetail(job)} aria-expanded={expanded}>
                        {expanded ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
                        <span><strong>{job.filename ?? "CSV 文件"}</strong><small>{formatCsvDate(job.created_at)} · {job.row_count} 条股票记录</small></span>
                      </button>
                      <span className="status-chip research">{job.replayed ? "历史复用" : job.status === "validated" ? "已验证" : job.status}</span>
                      <button className="icon-button" type="button" onClick={() => void exportCsv(job.public_id)} disabled={exportingId !== null} aria-label={`导出 ${job.filename ?? "CSV 文件"}`} title="导出 CSV"><Download size={15} /></button>
                    </div>
                    {expanded && <div className="lab-csv-detail">
                      {detailState === "loading" && <p className="lab-csv-state">正在读取股票记录…</p>}
                      {detailState === "error" && <p className="lab-csv-state is-error" role="alert">{detailError}</p>}
                      {detailState === "ready" && detail?.public_id === job.public_id && <>
                        <div className="lab-csv-provenance"><span>记录编号 <code>{detail.public_id}</code></span><span>完成时间 {formatCsvDate(detail.completed_at)}</span><span>来源校验 <code>{detail.source_sha256?.slice(0, 12) ?? "—"}…</code></span></div>
                        {signals.length === 0 ? <p className="lab-csv-state">这条导入记录没有股票行。</p> : <div className="lab-csv-signal-table"><table><thead><tr><th>股票</th><th>操作</th><th>数量</th><th>价格</th><th>时间</th></tr></thead><tbody>{signals.map((signal) => <tr key={signal.signal_id}><td data-label="股票">{signal.symbol}</td><td data-label="操作">{signal.action}</td><td data-label="数量">{signal.quantity ?? "—"}</td><td data-label="价格">{signal.price ?? "—"}</td><td data-label="时间">{formatCsvDate(signal.timestamp)}</td></tr>)}</tbody></table></div>}
                      </>}
                    </div>}
                  </article>
                );
              })}
            </div>
          )}
        </>
      )}
    </section>
  );
}

export function ProfessionalLabPage() {
  const workspace = useWorkspace();
  const navigate = useNavigate();
  const [tab, setTab] = useState<LabTab>("strategy");
  const [symbol, setSymbol] = useState("AAPL");
  const [templateKey, setTemplateKey] = useState<BacktestPrepareRequest["template_key"]>("equity.trend.long_flat.v1");
  const [sampleYears, setSampleYears] = useState(1);
  const [lookback, setLookback] = useState("20");
  const [scenarioKey, setScenarioKey] = useState("");
  const [stressCatalog, setStressCatalog] = useState<LabStressCatalog | null>(null);
  const [catalogState, setCatalogState] = useState<"loading" | "ready" | "error">("loading");
  const [stressState, setStressState] = useState<"idle" | "loading" | "success" | "error">("idle");
  const [stressResult, setStressResult] = useState<LabStressResult | null>(null);
  const [stressError, setStressError] = useState<string | null>(null);
  const tabRefs = useRef<Array<HTMLButtonElement | null>>([]);
  useEffect(() => {
    const controller = new AbortController();
    setCatalogState("loading");
    void fetchLabStressCatalog().then((catalog) => {
      if (controller.signal.aborted) return;
      setStressCatalog(catalog);
      setScenarioKey((current) => catalog.scenarios.some((item) => item.key === current) ? current : catalog.scenarios[0]?.key ?? "");
      setCatalogState("ready");
    }).catch(() => {
      if (!controller.signal.aborted) {
        setStressCatalog(null);
        setScenarioKey("");
        setCatalogState("error");
      }
    });
    return () => controller.abort();
  }, []);
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
  const portfolio = workspace.data?.portfolio;
  const snapshot = portfolio?.accounts.US?.captured_at && portfolio.accounts.US.status === "recorded"
    ? {
        account_mode: portfolio.account_mode,
        currency: "USD" as const,
        as_of: portfolio.accounts.US.captured_at,
        data_status: "recorded" as const,
        positions: portfolio.positions.filter((item) => item.market === "US" && item.currency === "USD" && item.instrument_type === "stock").map((item) => ({ symbol: item.symbol, instrument_type: "stock" as const, currency: "USD" as const, quantity: item.quantity, last_trade_price: item.last_trade_price })),
      }
    : null;
  const runStress = async () => {
    if (!stressCatalog || !scenarioKey) {
      setStressState("error");
      setStressError(catalogState === "error" ? "压力场景目录不可用，压力测试已锁定。" : "压力场景目录正在读取，压力测试已锁定。");
      return;
    }
    if (!snapshot || !snapshot.positions.length) {
      setStressState("error");
      setStressError("没有可用的新鲜 USD 股票持仓快照，压力测试已拒绝执行。");
      return;
    }
    setStressState("loading"); setStressError(null); setStressResult(null);
    try {
      const result = await runLabStress(scenarioKey);
      setStressResult(result); setStressState("success");
    } catch (error) {
      setStressState("error"); setStressError(error instanceof Error ? error.message : "压力测试暂时不可用。");
    }
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
        description="写策略并配置回测、参数与压力场景。任务、取消状态与制品来自真实队列；模型不得自行发布或开启实盘。"
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
          <small>真实队列已接线 · 服务端先冻结输入再入队</small>
        </span>
        <span className="is-blocked">
          <LockKeyhole size={16} />
          <strong>自动发布</strong>
          <small>禁止</small>
        </span>
      </section>
      <CsvImportHistoryPanel />
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
              <h2>策略模板</h2>
            </div>
            <span className="status-chip research">服务端 allowlist</span>
          </header>
          <div className="strategy-toolbar">
            <label>
              股票
              <input value={symbol} onChange={(event) => setSymbol(event.target.value.toUpperCase())} />
            </label>
            <label>
              服务端模板
              <select value={templateKey} onChange={(event) => setTemplateKey(event.target.value as BacktestPrepareRequest["template_key"])}>
                <option value="equity.trend.long_flat.v1">趋势跟随</option>
                <option value="equity.mean_reversion.long_flat.v1">均值回归</option>
                <option value="equity.breakout.long_flat.v1">突破确认</option>
              </select>
            </label>
            <label>
              周期
              <input value="日线" readOnly aria-label="回测周期" />
            </label>
          </div>
          <div className="strategy-template-grid" aria-label="可用策略模板">
            <button className={templateKey === "equity.trend.long_flat.v1" ? "active" : ""} type="button" onClick={() => setTemplateKey("equity.trend.long_flat.v1")}><strong>趋势跟随</strong><small>美股 · 日线 · long / flat</small></button>
            <button className={templateKey === "equity.mean_reversion.long_flat.v1" ? "active" : ""} type="button" onClick={() => setTemplateKey("equity.mean_reversion.long_flat.v1")}><strong>均值回归</strong><small>美股 · 日线 · long / flat</small></button>
            <button className={templateKey === "equity.breakout.long_flat.v1" ? "active" : ""} type="button" onClick={() => setTemplateKey("equity.breakout.long_flat.v1")}><strong>突破确认</strong><small>美股 · 日线 · long / flat</small></button>
          </div>
          <p className="lab-template-note"><ShieldCheck size={15} /> 策略逻辑、代码版本、数据快照和费用口径由服务端执行器维护；浏览器只选择允许的模板和股票参数。</p>
          <footer className="lab-actions">
            <button
              className="button secondary"
              type="button"
              onClick={() => setTab("backtest")}
            >
              进入回测参数
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
                <strong>选择服务端策略模板</strong>
                <small>
                  当前只开放三种美股日线 long / flat 模板；浏览器不接收任意代码或执行材料。
                </small>
              </span>
            </li>
            <li>
              <CheckCircle2 />
              <span>
                <strong>可以配置股票与回测参数</strong>
                <small>
                  服务端会记录样本期、数据截止、模板版本和结果制品证明。
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
            <ShieldCheck size={14} /> 真实队列
          </span>
        </header>
        <BacktestWorkspace
          authenticated={workspace.mode === "authenticated"}
          maxBacktestYears={maxBacktestYears}
          symbol={symbol}
          templateKey={templateKey}
          sampleYears={sampleYears}
          lookback={lookback}
          onSymbolChange={setSymbol}
          onTemplateChange={setTemplateKey}
          onSampleYearsChange={(value) => setSampleYears(Number(value))}
          onLookbackChange={setLookback}
        />
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
          <span className={`status-chip ${stressState === "success" ? "official" : "research"}`}>
            <ShieldCheck size={14} /> {stressState === "success" ? "固定情景已完成" : catalogState === "ready" ? "服务端固定情景目录" : "压力场景目录锁定"}
          </span>
        </header>
        <div className="lab-risk-workbench">
          <div className="lab-config-zone">
            <div className="stress-grid">
              <label>固定压力情景<select value={scenarioKey} disabled={catalogState !== "ready" || !stressCatalog?.scenarios.length} onChange={(event) => { setScenarioKey(event.target.value); setStressResult(null); setStressState("idle"); }}>{stressCatalog?.scenarios.map((scenario) => <option value={scenario.key} key={scenario.key}>{scenario.label}</option>)}</select></label>
              <p className="lab-field-help">{catalogState === "loading" ? "压力场景目录正在读取，未收到服务端目录前保持锁定。" : catalogState === "error" ? "压力场景目录不可用，未显示本地场景标签或冲击数值。" : "价格冲击、波动率、跳空、费用和滑点均由服务端目录固定，不可由浏览器编辑。"}</p>
            </div>
            <div className="stress-result">
              <strong>{stressState === "loading" ? "正在冻结快照并重估…" : stressState === "success" ? `压力重估：${stressResult?.pnl_change.toFixed(2)} USD` : stressState === "error" ? "压力测试未完成" : "尚未生成压力测试结论"}</strong>
              <span>{stressError ?? (stressResult ? `基准 ${stressResult.baseline_value.toFixed(2)} → 情景 ${stressResult.stressed_value.toFixed(2)}；费用 ${stressResult.scenario.fee_bps}bps + 滑点 ${stressResult.scenario.slippage_bps}bps。` : catalogState === "ready" ? "只对服务端记录的真实持仓做机械重估，不是预测、胜率或交易建议。" : "压力场景目录正在读取或不可用，测试保持锁定。")}</span>
              <div className="lab-stress-actions">
                <small>{snapshot ? `数据：${snapshot.as_of} · ${snapshot.positions.length} 个 USD 股票持仓` : "数据快照为空或已过期"}</small>
                <button className="button secondary" type="button" onClick={runStress} disabled={stressState === "loading" || catalogState !== "ready" || !scenarioKey}>{stressState === "loading" ? "计算中…" : "运行固定情景"}</button>
              </div>
              {stressResult && <small>method {stressResult.method_version} · input {stressResult.input_sha256} · result {stressResult.result_sha256}</small>}
            </div>
          </div>
          <aside className="lab-risk-inspector" aria-label="压力场景强度">
            <header>
              <span>SCENARIO INTENSITY</span>
              <h3>压力参数概览</h3>
              <small>圆盘仅显示当前输入强度，不代表模型结论。</small>
            </header>
            {stressResult ? <div className="lab-risk-rings">
              <MetricRing label="价格冲击" value={Math.abs(stressResult.scenario.price_shock_pct) / 40 * 100} displayValue={`${stressResult.scenario.price_shock_pct}%`} caption="服务端返回场景" tone="negative" />
              <MetricRing label="波动率" value={stressResult.scenario.volatility_shock_pct} displayValue={`+${stressResult.scenario.volatility_shock_pct}%`} caption="服务端返回场景" tone="positive" />
            </div> : <p className="lab-field-help">{catalogState === "ready" ? "运行服务端固定情景后显示参数。" : "压力场景目录正在读取或不可用，参数概览保持锁定。"}</p>}
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
