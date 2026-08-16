import {
  CheckCircle2,
  Clock3,
  Crown,
  FileCheck2,
  LockKeyhole,
  ShieldCheck,
  Upload,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  BrowserApiError,
  createMembershipOrder,
  fetchMembershipPaymentQr,
  quoteMembershipOrder,
  submitMembershipProof,
  type MembershipBillingCycle,
  type MembershipPlan,
  type MembershipPlanKey,
  type MembershipQuote,
} from "../api/client";
import { useWorkspace } from "../api/workspace-context";
import { accountCenterApi, type AppearancePayload } from "../api/accountCenter";
import { PageHeader } from "../components/PageHeader";
import { SegmentedControl } from "../components/ui/SegmentedControl";
import { WorkspaceState } from "../components/WorkspaceState";
import { getFormatLocale, localizeText } from "../i18n/runtime";
import { useLocale } from "../i18n/useLocale";

const goalGuides: Array<{
  key: string;
  title: string;
  detail: string;
  plan: MembershipPlanKey;
}> = [
  {
    key: "understand",
    title: "我只想看懂",
    detail: "标准版适合查看网页正式建议与更完整的策略解释。",
    plan: "标准版",
  },
  {
    key: "alerts",
    title: "我想收到提醒",
    detail: "高级版开放正股 Telegram，并提供 1 个股票账号的受控自动实盘产品资格；仍需独立授权与安全门。",
    plan: "高级版",
  },
  {
    key: "research",
    title: "我想研究期权或写策略",
    detail: "高级版提供股票深度研究与 1 个股票账号的受控自动实盘产品资格；不包含期权自动实盘申请。",
    plan: "高级版",
  },
];

type MembershipQuoteRequest = Parameters<typeof quoteMembershipOrder>[0];

function normalizedCouponCode(value: string) {
  return value.trim().toUpperCase();
}

function membershipQuoteFingerprint(input: MembershipQuoteRequest) {
  return [input.plan, input.cycle, input.coupon_code ?? ""].join("\u001f");
}

function quoteMatchesRequest(
  value: MembershipQuote,
  input: MembershipQuoteRequest,
) {
  return (
    value.plan === input.plan &&
    value.cycle === input.cycle &&
    (value.coupon_code ?? "") === (input.coupon_code ?? "")
  );
}

function quoteMatchesDisplayedQuote(
  displayed: MembershipQuote,
  verified: MembershipQuote,
) {
  return (
    displayed.plan === verified.plan &&
    displayed.cycle === verified.cycle &&
    displayed.currency === verified.currency &&
    displayed.list_price_minor === verified.list_price_minor &&
    displayed.coupon_discount_minor === verified.coupon_discount_minor &&
    displayed.referral_discount_minor === verified.referral_discount_minor &&
    displayed.final_amount_minor === verified.final_amount_minor &&
    displayed.coupon_code === verified.coupon_code &&
    displayed.referral_eligible === verified.referral_eligible &&
    displayed.discount_order[0] === verified.discount_order[0] &&
    displayed.discount_order[1] === verified.discount_order[1] &&
    displayed.server_reprices_on_order === verified.server_reprices_on_order
  );
}

function membershipOrderFingerprint(
  input: MembershipQuoteRequest,
  method: PaymentMethod,
  termsAccepted: boolean,
) {
  return [membershipQuoteFingerprint(input), method, termsAccepted ? "accepted" : "not-accepted"].join("\u001f");
}

function isMembershipBillingCycle(
  value: string,
): value is MembershipBillingCycle {
  return (
    value === "monthly" ||
    value === "quarterly" ||
    value === "yearly" ||
    value === "project"
  );
}

function isMembershipPlan(value: unknown): value is MembershipPlan {
  if (!value || typeof value !== "object") return false;
  const key = (value as { key?: unknown }).key;
  return isMembershipPlanKey(key);
}

function isMembershipPlanKey(value: unknown): value is MembershipPlanKey {
  return (
    value === "免费版" ||
    value === "标准版" ||
    value === "高级版" ||
    value === "专业版" ||
    value === "定制版"
  );
}

function planBillingCycles(plan: MembershipPlan): MembershipBillingCycle[] {
  return Object.keys(plan.prices ?? {}).filter(isMembershipBillingCycle);
}

function availableBillingCycles(
  plans: MembershipPlan[],
): MembershipBillingCycle[] {
  return [...new Set(plans.flatMap(planBillingCycles))];
}

function selectedBillingCycle(
  plan: MembershipPlan | undefined,
  preferred: MembershipBillingCycle,
): MembershipBillingCycle | undefined {
  if (!plan) return undefined;
  const cycles = planBillingCycles(plan);
  return cycles.includes(preferred) ? preferred : cycles[0];
}

function membershipText(
  value: string | undefined,
  locale: "zh-Hant" | "zh-Hans",
) {
  if (!value) return locale === "zh-Hant" ? "資料未提供" : "资料未提供";
  return locale === "zh-Hant" ? localizeText(value) : value;
}

function billingCycleText(
  cycle: MembershipBillingCycle | undefined,
  annualBonusEnabled: boolean,
  locale: "zh-Hant" | "zh-Hans",
) {
  if (!cycle) return locale === "zh-Hant" ? "週期未選擇" : "周期未选择";
  const labels: Record<MembershipBillingCycle, string> =
    locale === "zh-Hant"
      ? {
          monthly: "月付",
          quarterly: "季付",
          yearly: "年付",
          project: "專案制",
        }
      : {
          monthly: "月付",
          quarterly: "季付",
          yearly: "年付",
          project: "项目制",
        };
  if (cycle === "yearly" && annualBonusEnabled) {
    return `${labels.yearly}（${locale === "zh-Hant" ? "贈送" : "赠送"} 90 天）`;
  }
  return labels[cycle];
}

function priceText(value: number | undefined) {
  return typeof value === "number" && Number.isFinite(value)
    ? `HKD ${value.toLocaleString(getFormatLocale())}`
    : "价格以订单确认页为准";
}

function formatMembershipDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.valueOf())
    ? value
    : new Intl.DateTimeFormat(getFormatLocale(), {
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
      }).format(date);
}
const paymentMethodLabels = {
  fps: "FPS",
  alipay: "支付宝",
  wechat: "微信支付",
  paypal: "PayPal（历史）",
  paddle: "Paddle（历史）",
} as const;
type PaymentMethod = "fps" | "alipay" | "wechat";
type ProofOrder = {
  orderNo: string;
  method: PaymentMethod;
  instructions: string;
  hasQr: boolean;
};
type OrderNotice =
  | { kind: "created"; orderNo: string; currency: string; amount: string }
  | { kind: "refresh-failed"; orderNo: string }
  | { kind: "proof-submitted"; orderNo: string }
  | { kind: "plain"; text: string };
const manualPaymentMethods = new Set<PaymentMethod>([
  "fps",
  "alipay",
  "wechat",
]);

function orderNoticeText(
  notice: OrderNotice | null,
  locale: "zh-Hant" | "zh-Hans",
) {
  if (!notice) return "";
  if (notice.kind === "plain")
    return locale === "zh-Hant" ? localizeText(notice.text) : notice.text;
  if (notice.kind === "created")
    return locale === "zh-Hant"
      ? `訂單 ${notice.orderNo} 已建立 · ${notice.currency} ${notice.amount} · 請在本頁上傳付款截圖，財務核對到帳後開通`
      : `订单 ${notice.orderNo} 已建立 · ${notice.currency} ${notice.amount} · 请在本页上传付款截图，财务核对到账后开通`;
  if (notice.kind === "refresh-failed")
    return locale === "zh-Hant"
      ? `訂單 ${notice.orderNo} 已建立；列表重新整理失敗，請稍後重試。`
      : `订单 ${notice.orderNo} 已建立；列表刷新失败，请稍后重试。`;
  return locale === "zh-Hant"
    ? `訂單 ${notice.orderNo} 的付款憑證已提交，等待財務人工核對。`
    : `订单 ${notice.orderNo} 的付款凭证已提交，等待财务人工核对。`;
}

function PaymentProofPanel({
  order,
  onSubmitted,
}: {
  order: ProofOrder;
  onSubmitted: () => void;
}) {
  const { locale } = useLocale();
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState("");
  const [claimId, setClaimId] = useState<number | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [qrUrl, setQrUrl] = useState("");
  const [qrError, setQrError] = useState("");

  useEffect(() => {
    let active = true;
    let objectUrl = "";
    if (!order.hasQr) {
      setQrUrl("");
      setQrError("");
      return () => undefined;
    }
    void fetchMembershipPaymentQr(order.orderNo)
      .then((blob) => {
        if (!active) return;
        objectUrl = URL.createObjectURL(blob);
        setQrUrl(objectUrl);
        setQrError("");
      })
      .catch((caught) => {
        if (active)
          setQrError(
            caught instanceof BrowserApiError
              ? caught.message
              : "收款二维码暂时不可用。",
          );
      });
    return () => {
      active = false;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [order.hasQr, order.orderNo]);

  async function uploadProof() {
    if (!file || submitted) return;
    try {
      const claim = await submitMembershipProof(order.orderNo, file);
      setSubmitted(true);
      setClaimId(claim.claim_id);
      setError("");
      onSubmitted();
    } catch (caught) {
      setError(
        caught instanceof BrowserApiError ? caught.message : "付款凭证提交失败",
      );
    }
  }

  return (
    <section className="data-panel payment-proof-panel">
      <header className="panel-heading">
        <div>
          <span>PAYMENT PROOF</span>
          <h2>提交付款凭证</h2>
        </div>
        <FileCheck2 size={20} />
      </header>
      <div className="payment-proof-body">
        <div className="payment-proof-order">
          <strong>{order.orderNo}</strong>
          <span>{paymentMethodLabels[order.method]} · 全部人工对账</span>
        </div>
        <div className="payment-instructions">
          <strong>收款资料</strong>
          {order.instructions && <p data-no-localize>{order.instructions}</p>}
          {order.hasQr && (
            <div className="payment-qr-frame">
              {qrUrl ? (
                <img
                  src={qrUrl}
                  width={280}
                  height={280}
                  alt={`${paymentMethodLabels[order.method]} 收款二维码`}
                />
              ) : (
                <span>{qrError || "正在读取收款二维码…"}</span>
              )}
            </div>
          )}
          {!order.instructions && !order.hasQr && (
            <p>收款资料尚未配置，请联系客服。</p>
          )}
          <small>
            请使用与订单金额一致的付款凭证；凭证只用于财务人工核对，不会自动开通会员。
          </small>
        </div>
        <label className="proof-upload-field">
          <span>
            <Upload size={16} /> 选择付款截图
          </span>
          <input
            type="file"
            accept="image/jpeg,image/png,image/webp"
            disabled={submitted}
            onChange={(event) => {
              setFile(event.target.files?.[0] ?? null);
              setError("");
            }}
          />
          <small>{file?.name ?? "支持 JPG、PNG、WebP，最大 4 MB"}</small>
        </label>
        <button
          className="button primary wide"
          type="button"
          disabled={!file || submitted}
          onClick={uploadProof}
        >
          {submitted ? "已提交，等待核对" : "上传并提交人工审核"}
        </button>
        <p className="form-status" role="status">
          {claimId !== null
            ? locale === "zh-Hant"
              ? `付款憑證已提交，申報 #${claimId} 等待財務人工核對。`
              : `付款凭证已提交，申报 #${claimId} 等待财务人工核对。`
            : locale === "zh-Hant"
              ? localizeText(error)
              : error}
        </p>
      </div>
    </section>
  );
}

export function MembershipPage() {
  const { locale } = useLocale();
  const workspace = useWorkspace();
  const navigate = useNavigate();
  const [cycle, setCycle] = useState<MembershipBillingCycle>("yearly");
  const [selectedPlan, setSelectedPlan] = useState<MembershipPlanKey | "">("");
  const [selectedGoal, setSelectedGoal] = useState("");
  const [paymentMethod, setPaymentMethod] = useState<PaymentMethod>("fps");
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [orderStatus, setOrderStatus] = useState<OrderNotice | null>(null);
  const [showOrders, setShowOrders] = useState(false);
  const [proofOrder, setProofOrder] = useState<ProofOrder | null>(null);
  const [couponCode, setCouponCode] = useState("");
  const [quote, setQuote] = useState<MembershipQuote | null>(null);
  const [quotedInputFingerprint, setQuotedInputFingerprint] = useState("");
  const [quoteBusy, setQuoteBusy] = useState(false);
  const [quoteError, setQuoteError] = useState("");
  const [appearance, setAppearance] = useState<AppearancePayload | null>(null);
  const [appearanceState, setAppearanceState] = useState("正在读取外观权益…");
  const membershipOrderIdempotency = useRef<{
    fingerprint: string;
    key: string;
  } | null>(null);
  const paymentAvailability = workspace.data?.membership.payment_methods;
  const plans = useMemo(
    () =>
      Array.isArray(workspace.data?.membership.plans)
        ? workspace.data.membership.plans.filter(isMembershipPlan)
        : [],
    [workspace.data?.membership.plans],
  );
  const cycles = availableBillingCycles(plans);
  const annualBonusEnabled =
    workspace.data?.membership.annual_bonus_enabled === true;
  const brokerage = workspace.data?.membership.brokerage;
  const subscriptionAutoConnectsBroker =
    brokerage?.subscription_auto_connects_broker ?? false;
  const noCicloTradeShortApproval =
    brokerage?.us_short?.requires_ciclotrade_manual_approval === false;

  useEffect(() => {
    if (workspace.mode !== "authenticated") return;
    let active = true;
    void accountCenterApi.appearance().then((payload) => {
      if (!active) return;
      setAppearance(payload);
      setAppearanceState(payload.items.length ? "外观权益由服务端 manifest 验证。" : "外观 manifest 尚未发布。");
    }).catch(() => {
      if (active) setAppearanceState("外观权益接口暂不可用；不会按方案名称猜测解锁状态。");
    });
    return () => { active = false; };
  }, [workspace.mode]);

  function resetOrderStatus() {
    setOrderStatus(null);
  }

  const selectedPlanDetails = plans.find((plan) => plan.key === selectedPlan);
  const currentPlanKey = isMembershipPlanKey(workspace.user?.plan)
    ? workspace.user.plan
    : "免费版";
  useEffect(() => {
    if (!selectedPlan) return;
    const selectedPlanState = plans.find((plan) => plan.key === selectedPlan);
    if (!selectedPlanState?.can_purchase) {
      setSelectedPlan("");
      setOrderStatus(null);
    }
  }, [plans, selectedPlan]);
  const recommendedPlan = goalGuides.find(
    (guide) => guide.key === selectedGoal,
  )?.plan;
  const checkoutCycle = selectedBillingCycle(selectedPlanDetails, cycle);
  const quoteRequest: MembershipQuoteRequest | null =
    selectedPlanDetails && checkoutCycle
      ? {
          plan: selectedPlanDetails.key,
          cycle: checkoutCycle,
          ...(normalizedCouponCode(couponCode)
            ? { coupon_code: normalizedCouponCode(couponCode) }
            : {}),
        }
      : null;
  const quoteInputFingerprint = quoteRequest
    ? membershipQuoteFingerprint(quoteRequest)
    : "";
  const currentQuoteFingerprint = useRef(quoteInputFingerprint);
  currentQuoteFingerprint.current = quoteInputFingerprint;
  const orderRequestFingerprint = quoteRequest
    ? membershipOrderFingerprint(quoteRequest, paymentMethod, termsAccepted)
    : "";
  const hasCurrentQuote = Boolean(
    quote &&
      quoteRequest &&
      quotedInputFingerprint === quoteInputFingerprint &&
      quoteMatchesRequest(quote, quoteRequest),
  );

  useEffect(() => {
    setQuote(null);
    setQuotedInputFingerprint("");
    setQuoteBusy(false);
    setQuoteError("");
  }, [quoteInputFingerprint]);

  useEffect(() => {
    if (
      membershipOrderIdempotency.current?.fingerprint !==
      orderRequestFingerprint
    ) {
      membershipOrderIdempotency.current = null;
    }
  }, [orderRequestFingerprint]);

  function orderIdempotencyKey(fingerprint: string) {
    const previous = membershipOrderIdempotency.current;
    if (previous?.fingerprint === fingerprint) return previous.key;
    const key = crypto.randomUUID();
    membershipOrderIdempotency.current = { fingerprint, key };
    return key;
  }

  function clearOrderIdempotency(fingerprint: string) {
    if (membershipOrderIdempotency.current?.fingerprint === fingerprint) {
      membershipOrderIdempotency.current = null;
    }
  }

  async function refreshQuote(input = quoteRequest) {
    if (!input || workspace.mode !== "authenticated") return null;
    const requestedFingerprint = membershipQuoteFingerprint(input);
    setQuoteBusy(true);
    setQuoteError("");
    try {
      const result = await quoteMembershipOrder(input);
      if (currentQuoteFingerprint.current !== requestedFingerprint) return null;
      setQuote(result);
      setQuotedInputFingerprint(requestedFingerprint);
      return result;
    } catch (caught) {
      if (currentQuoteFingerprint.current !== requestedFingerprint) return null;
      setQuote(null);
      setQuotedInputFingerprint("");
      setQuoteError(caught instanceof BrowserApiError ? caught.message : "会员报价暂时不可用。");
      return null;
    } finally {
      if (currentQuoteFingerprint.current === requestedFingerprint) {
        setQuoteBusy(false);
      }
    }
  }

  return (
    <div className="page operations-page membership-page">
      <PageHeader
        kicker="MEMBERSHIP / ONE-TIME"
        title="会员与账单"
        description="一次付款获得固定有效期，到期不会自动扣款，也不会绑定自动续费。"
      />
      <WorkspaceState />
      <section className="membership-goal-guide data-panel">
        <header className="panel-heading">
          <div>
            <span>按目标选择</span>
            <h2>你想先解决哪件事？</h2>
          </div>
          <Crown size={20} />
        </header>
        <div>
          {goalGuides.map((guide) => (
            <button
              className={selectedGoal === guide.key ? "active" : ""}
              type="button"
              aria-pressed={selectedGoal === guide.key}
              key={guide.title}
              onClick={() => setSelectedGoal(guide.key)}
            >
              <strong>{guide.title}</strong>
              <span>{guide.detail}</span>
              <small>
                {selectedGoal === guide.key
                  ? "已按此目标标出适合方案"
                  : "选择这个目标"}
              </small>
            </button>
          ))}
        </div>
        <section className="membership-skin-preview" aria-label="Ciclo 外观进化">
          <header><div><strong>Ciclo 机器人外观进化</strong><p>会员负责解锁，账户页负责选择；外观不代表 AI 等级、收益能力或自动实盘权限。</p></div><span>{appearanceState}</span></header>
          {appearance?.items.length ? <div className="membership-skin-grid">{appearance.items.map((item) => { const preview = typeof item.assets.preview === 'string' && /^\/media\/ciclo\/[a-z0-9-]+\.svg$/.test(item.assets.preview) ? item.assets.preview : null; const alt = typeof item.assets.alt === 'string' ? item.assets.alt : item.skin_id; const plan = typeof item.assets.unlock_plan === 'string' ? item.assets.unlock_plan : '对应会员'; const current = appearance.current.public_id === item.public_id; return <article className={`${item.entitled ? 'is-entitled' : 'is-locked'} ${current ? 'is-current' : ''}`} key={item.public_id}>{preview ? <img src={preview} width={320} height={320} alt={alt} /> : <span className="membership-skin-missing"><LockKeyhole size={22} /></span>}<div><strong>{alt}</strong><small>{plan} · {item.asset_version}</small><span>{current ? '当前使用' : item.entitled ? '已解锁，可在账户页选择' : plan === '专业版' ? '专业版恢复销售前仅锁定预览' : '升级后解锁'}</span></div>{!item.entitled && <LockKeyhole aria-label="锁定" size={16} />}</article> })}</div> : <div className="inline-empty">服务端没有返回外观 manifest；不会显示虚构等级素材。</div>}
        </section>
        <p>
          会员付款只开通研究、提醒、数据、历史样本范围与回测参数权限。回测任务已接入真实队列，结果仍依赖服务端计算引擎与数据，不承诺收益、胜率或回撤成绩。订阅
          {subscriptionAutoConnectsBroker ? "会" : "不会"}
          自动连接券商，实盘服务由用户主动连接个人券商。
          {noCicloTradeShortApproval
            ? "美股做空无需 CicloTrade 额外的做空审核，"
            : "美股做空会依当前券商与平台条件核对，"}
          但仍取决于用户主动授权券商、保证金与可借券；A
          股不支持做空。任何实盘下单仍须经过适用的通用安全、授权及管理员门禁。
        </p>
        <div className="inline-warning membership-live-trade-note">
          <ShieldCheck size={17} />
          <span>
            需要实盘连接？打开交易控制台查看券商资格、mandate 与独立门控状态。这里不会把实盘或做空伪装成会员自动权益，也不要求先购买套餐才能查看条件。
          </span>
          <button
            className="button tertiary"
            type="button"
            onClick={() => navigate("/trade")}
          >
            查看券商状态
          </button>
        </div>
      </section>
      {workspace.data?.membership.legacy_plans?.length ? (
        <section className="membership-legacy-notice data-panel" aria-label="历史会员方案">
          <div><ShieldCheck size={18} /><span><strong>专业版与定制版当前暂停公开销售</strong><small>历史订单和未到期权益继续保留；不能新购、续费或由普通管理员赠送。功能完成并通过独立验收后，可通过新策略版本恢复，无需重写订单系统。</small></span></div>
        </section>
      ) : null}
      <section className="current-plan-band data-panel">
        <span className="membership-emblem">
          <Crown size={25} />
        </span>
        <div>
          <span>CURRENT PLAN</span>
          <h2>{workspace.user?.plan_display_name ?? "未登录"}</h2>
          <p>
            <Clock3 size={15} />{" "}
            {workspace.user?.subscription_expire
              ? `有效期至 ${formatMembershipDate(workspace.user.subscription_expire)}`
              : workspace.user
                ? "长期有效或未设置到期日"
                : "登录后查看真实有效期"}
          </p>
        </div>
        <span
          className={`status-chip ${workspace.user ? "official" : "research"}`}
        >
          <ShieldCheck size={14} /> {workspace.user ? "权益正常" : "安全只读"}
        </span>
        <button
          className="button secondary"
          type="button"
          aria-expanded={showOrders}
          onClick={() => setShowOrders(!showOrders)}
        >
          {showOrders ? "收起订单" : "查看订单"}
        </button>
      </section>
      {showOrders && (
        <section className="data-panel membership-orders">
          <header className="panel-heading">
            <div>
              <span>ORDER HISTORY</span>
              <h2>一次性购买记录</h2>
            </div>
            <span className="status-chip official">绝不自动续费</span>
          </header>
          {workspace.data?.membership.orders.length ? (
            <div className="responsive-table">
              <table>
                <thead>
                  <tr>
                    <th>订单</th>
                    <th>方案</th>
                    <th>周期</th>
                    <th>金额</th>
                    <th>方式</th>
                    <th>凭证</th>
                    <th>状态</th>
                    <th>建立时间</th>
                    <th>操作</th>
                  </tr>
                </thead>
                <tbody>
                  {workspace.data.membership.orders.map((order) => {
                    const method = manualPaymentMethods.has(
                      order.pay_method as PaymentMethod,
                    )
                      ? (order.pay_method as PaymentMethod)
                      : null;
                    const canSubmit = order.can_submit_proof && method;
                    return (
                      <tr key={order.order_no}>
                        <td>
                          <strong>{order.order_no}</strong>
                        </td>
                        <td>{order.plan_type}</td>
                        <td>
                          {billingCycleText(
                            order.billing_cycle,
                            annualBonusEnabled,
                            locale,
                          )}
                        </td>
                        <td>
                          {order.currency}{" "}
                          {Number(order.amount).toLocaleString(
                            getFormatLocale(),
                          )}
                        </td>
                        <td>{paymentMethodLabels[order.pay_method]}</td>
                        <td>
                          {order.proof_status === "submitted"
                            ? "审核中"
                            : order.proof_status === "approved"
                              ? "已核对"
                              : order.proof_status === "rejected"
                                ? "需重新提交"
                                : "未提交"}
                        </td>
                        <td>
                          <span
                            className={`model-state ${order.status.toLowerCase() === "paid" ? "active" : "shadow"}`}
                          >
                            {order.status}
                          </span>
                        </td>
                        <td>
                          {new Date(order.created_at).toLocaleString(
                            getFormatLocale(),
                            { hour12: false },
                          )}
                        </td>
                        <td>
                          {canSubmit && method ? (
                            <button
                              className="button tertiary"
                              type="button"
                              onClick={() =>
                                setProofOrder({
                                  orderNo: order.order_no,
                                  method,
                                  instructions:
                                    order.payment_instructions ?? "",
                                  hasQr: order.payment_qr_available === true,
                                })
                              }
                            >
                              提交凭证
                            </button>
                          ) : order.proof_status === "submitted" ? (
                            <span className="status-chip research">
                              等待审核
                            </span>
                          ) : order.blocked_reason ? (
                            <span className="table-muted">
                              {membershipText(order.blocked_reason, locale)}
                            </span>
                          ) : (
                            <span className="table-muted">--</span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="inline-empty">当前账户还没有会员订单。</div>
          )}
        </section>
      )}
      <div className="billing-cycle-bar">
        <span>购买时长</span>
        {cycles.length ? (
          <SegmentedControl
            ariaLabel="购买时长"
            className="membership-cycle-control"
            value={cycle}
            options={cycles.map((item) => ({
              value: item,
              label: billingCycleText(item, annualBonusEnabled, locale),
            }))}
            onChange={(item) => {
              setCycle(item);
              resetOrderStatus();
            }}
          />
        ) : (
          <small>会员方案资料正在读取。</small>
        )}
        <small>公开方案均为一次性付款，到期不会自动扣款。</small>
      </div>
      {plans.length ? (
        <div className="membership-grid">
          {plans.map((plan) => {
            const planCycle = selectedBillingCycle(plan, cycle);
            const price = planCycle ? plan.prices?.[planCycle] : undefined;
            const freePlan = plan.key === "免费版";
            const isCurrentPlan =
              workspace.mode === "authenticated" && plan.key === currentPlanKey;
            const isCoveredPlan = plan.purchase_action === "covered";
            const canPurchase = plan.can_purchase && !freePlan;
            const features = Array.isArray(plan.features) ? plan.features : [];
            return (
              <article
                className={`membership-card ${plan.key === recommendedPlan && canPurchase ? "recommended" : ""} ${isCurrentPlan ? "current" : ""} ${isCoveredPlan ? "covered" : ""}`}
                key={plan.key}
              >
                {isCurrentPlan ? (
                  <span className="recommended-label current-label">
                    当前方案
                  </span>
                ) : plan.key === recommendedPlan && canPurchase ? (
                  <span className="recommended-label">适合你当前目标</span>
                ) : null}
                <header>
                  <h3>
                    {membershipText(plan.display_name || plan.key, locale)}
                  </h3>
                  <strong>{freePlan ? "免费" : priceText(price)}</strong>
                  <small>
                    {billingCycleText(planCycle, annualBonusEnabled, locale)}
                  </small>
                  <p>{membershipText(plan.summary, locale)}</p>
                </header>
                <ul>
                  {features.map((feature) => (
                    <li key={feature}>
                      <CheckCircle2 size={16} />{" "}
                      {membershipText(feature, locale)}
                    </li>
                  ))}
                </ul>
                <button
                  className={
                    selectedPlan === plan.key || isCoveredPlan
                      ? "button secondary wide"
                      : plan.key === recommendedPlan || isCurrentPlan
                        ? "button primary wide"
                        : "button secondary wide"
                  }
                  title={
                    !canPurchase && plan.blocked_reason
                      ? membershipText(plan.blocked_reason, locale)
                      : undefined
                  }
                  type="button"
                  disabled={!canPurchase}
                  onClick={() => {
                    if (!canPurchase) return;
                    setSelectedPlan(plan.key);
                    if (planCycle) setCycle(planCycle);
                    resetOrderStatus();
                  }}
                >
                  {freePlan
                    ? currentPlanKey === "免费版"
                      ? "当前免费权益"
                      : "免费基础权益"
                    : isCoveredPlan
                      ? "当前会员已覆盖"
                      : plan.purchase_action === "renew"
                        ? "续费当前方案"
                        : plan.purchase_action === "upgrade"
                          ? `${membershipText('升级至', locale)}${membershipText(plan.display_name || plan.key, locale)}`
                          : plan.purchase_action === "unavailable"
                            ? "暂不可购买"
                            : selectedPlan === plan.key
                              ? "已选择"
                              : isCurrentPlan
                                  ? "续费当前方案"
                                  : "选择并查看付款方式"}
                </button>
                {!canPurchase && plan.blocked_reason ? (
                  <small className="membership-blocked-reason">
                    {membershipText(plan.blocked_reason, locale)}
                  </small>
                ) : null}
                <footer>
                  不会自动续费 · 到期需主动购买
                </footer>
              </article>
            );
          })}
        </div>
      ) : (
        <section className="data-panel">
          <div className="inline-empty">
            会员方案资料正在读取；资料为空时不能建立订单。
          </div>
        </section>
      )}
      {selectedPlanDetails && checkoutCycle && (
        <section className="checkout-panel data-panel">
          <header className="panel-heading">
            <div>
              <span>ORDER CONFIRMATION</span>
              <h2>确认一次性会员订单</h2>
            </div>
            <ShieldCheck size={20} />
          </header>
          <div className="checkout-body">
            <dl>
              <div>
                <dt>方案</dt>
                <dd>
                  {membershipText(selectedPlanDetails.display_name, locale)}
                </dd>
              </div>
              <div>
                <dt>时长</dt>
                <dd>
                  {billingCycleText(checkoutCycle, annualBonusEnabled, locale)}
                </dd>
              </div>
              <div>
                <dt>金额</dt>
                <dd>
                  {hasCurrentQuote && quote
                    ? `HKD ${(quote.final_amount_minor / 100).toLocaleString(getFormatLocale())}`
                    : quoteBusy
                      ? "服务端核价中…"
                      : "请先取得服务端最终报价"}
                </dd>
              </div>
              <div>
                <dt>续费方式</dt>
                <dd>
                  到期停止，不自动扣款
                </dd>
              </div>
            </dl>
            {workspace.mode !== "authenticated" ? (
              <div className="inline-warning checkout-login-gate">
                <ShieldCheck size={17} />
                <span>
                  <strong>登录后继续购买</strong>
                  <small>
                    登录后会读取可用付款方式，并保留你正在查看的会员方案。付款仍是一次性购买，不会自动续费。
                  </small>
                </span>
                <button
                  className="button primary"
                  type="button"
                  onClick={() => navigate("/login?returnTo=%2Fmembership")}
                >
                  登录后购买
                </button>
              </div>
            ) : (
              <>
                <div className="membership-coupon-row">
                  <label htmlFor="membership-coupon"><span>优惠码</span><input id="membership-coupon" name="membership-coupon" autoComplete="off" spellCheck={false} maxLength={64} placeholder="输入活动优惠码" value={couponCode} onChange={(event) => setCouponCode(event.target.value.toUpperCase())} /></label>
                  <button className="button secondary" type="button" disabled={quoteBusy || !selectedPlanDetails || !checkoutCycle} onClick={() => void refreshQuote()}>{quoteBusy ? "核价中…" : couponCode.trim() ? "应用并核价" : "查看最终价格"}</button>
                </div>
                {hasCurrentQuote && quote && <dl className="membership-price-breakdown" aria-label="价格明细">
                  <div><dt>方案列表价</dt><dd>HKD {(quote.list_price_minor / 100).toLocaleString(getFormatLocale())}</dd></div>
                  <div><dt>优惠码</dt><dd>− HKD {(quote.coupon_discount_minor / 100).toLocaleString(getFormatLocale())}</dd></div>
                  <div><dt>推荐新客 95 折</dt><dd>− HKD {(quote.referral_discount_minor / 100).toLocaleString(getFormatLocale())}</dd></div>
                  <div><dt>最终实付</dt><dd>HKD {(quote.final_amount_minor / 100).toLocaleString(getFormatLocale())}</dd></div>
                </dl>}
                {quoteError && <p className="form-error" role="alert">{membershipText(quoteError, locale)}</p>}
                <small className="checkout-policy-note">计算顺序固定为先优惠码、再对符合条件的推荐新客计算 95 折；每单仅可使用一张优惠码，多张优惠码不可叠加。建立订单时服务端会重新核价。</small>
                <div>
                  <span>付款方式 · 全部人工对账</span>
                  <SegmentedControl
                    ariaLabel="付款方式"
                    className="membership-payment-control"
                    value={paymentMethod}
                    options={(
                      [
                        ["fps", "FPS"],
                        ["alipay", "支付宝"],
                        ["wechat", "微信支付"],
                      ] as const
                    ).map(([key, label]) => ({
                      value: key,
                      label,
                      disabled: !paymentAvailability?.[key]?.available,
                    }))}
                    onChange={(method) => {
                      setPaymentMethod(method);
                      resetOrderStatus();
                    }}
                  />
                </div>
                <label className="terms-check">
                  <input
                    name="membership-terms-accepted"
                    type="checkbox"
                    checked={termsAccepted}
                    onChange={(event) => setTermsAccepted(event.target.checked)}
                  />
                  <span>
                    我已阅读并同意用户协议、风险披露与不退款政策，确认这是一次性购买。
                  </span>
                </label>
                <button
                  className="button primary wide"
                  type="button"
                  disabled={
                    !termsAccepted ||
                    !hasCurrentQuote ||
                    quoteBusy ||
                    !paymentAvailability?.[paymentMethod]?.available
                  }
                  onClick={async () => {
                    if (workspace.mode !== "authenticated") {
                      setOrderStatus({
                        kind: "plain",
                        text: "请先登录后建立会员订单",
                      });
                      return;
                    }
                    const displayedQuote = quote;
                    if (!quoteRequest || !hasCurrentQuote || !displayedQuote) return;
                    const requestFingerprint = quoteInputFingerprint;
                    const orderFingerprint = membershipOrderFingerprint(
                      quoteRequest,
                      paymentMethod,
                      termsAccepted,
                    );
                    const verifiedQuote = await refreshQuote(quoteRequest);
                    if (
                      !verifiedQuote ||
                      currentQuoteFingerprint.current !== requestFingerprint ||
                      !quoteMatchesRequest(verifiedQuote, quoteRequest)
                    ) {
                      return;
                    }
                    if (!quoteMatchesDisplayedQuote(displayedQuote, verifiedQuote)) {
                      setOrderStatus({
                        kind: "plain",
                        text: "最终报价已更新，请核对后再次建立订单。",
                      });
                      return;
                    }
                    try {
                      const order = await createMembershipOrder(
                        {
                          plan: quoteRequest.plan,
                          cycle: quoteRequest.cycle,
                          method: paymentMethod,
                          terms_accepted: termsAccepted,
                          ...(quoteRequest.coupon_code
                            ? { coupon_code: quoteRequest.coupon_code }
                            : {}),
                        },
                        orderIdempotencyKey(orderFingerprint),
                      );
                      clearOrderIdempotency(orderFingerprint);
                      setProofOrder({
                        orderNo: order.order_no,
                        method: paymentMethod,
                        instructions: order.payment_instructions,
                        hasQr: order.payment_qr_available,
                      });
                      setOrderStatus({
                        kind: "created",
                        orderNo: order.order_no,
                        currency: order.currency,
                        amount: order.amount.toLocaleString(getFormatLocale()),
                      });
                      setShowOrders(true);
                      try {
                        await workspace.refresh();
                      } catch {
                        setOrderStatus({
                          kind: "refresh-failed",
                          orderNo: order.order_no,
                        });
                      }
                    } catch (caught) {
                      const safelyRejected =
                        caught instanceof BrowserApiError &&
                        caught.status >= 400 &&
                        caught.status < 500;
                      if (safelyRejected) clearOrderIdempotency(orderFingerprint);
                      setOrderStatus({
                        kind: "plain",
                        text:
                          !safelyRejected
                            ? "订单结果暂时无法确认。请保持当前方案、时长、优惠码和付款方式后重试，系统会安全复用本次请求。"
                            : caught instanceof BrowserApiError
                            ? caught.message
                            : "会员订单建立失败",
                      });
                    }
                  }}
                >
                  建立待付款订单
                </button>
                <p className="form-status" role="status">
                  {orderNoticeText(orderStatus, locale)}
                </p>
                {annualBonusEnabled && checkoutCycle === "yearly" && (
                  <small className="checkout-policy-note">
                    年度周期的 90 天赠送以当前平台条款开关与订单有效期为准。
                  </small>
                )}
                <small className="checkout-policy-note">
                  会员订单只开通研究订阅，不会自动连接个人券商。
                </small>
              </>
            )}
          </div>
        </section>
      )}
      {proofOrder && (
        <PaymentProofPanel
          order={proofOrder}
          onSubmitted={() => {
            setOrderStatus({
              kind: "proof-submitted",
              orderNo: proofOrder.orderNo,
            });
            void workspace.refresh().catch(() => undefined);
          }}
        />
      )}
    </div>
  );
}
