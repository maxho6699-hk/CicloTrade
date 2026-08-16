import {
  AlertTriangle,
  BellRing,
  Bot,
  CheckCircle2,
  Circle,
  Clock3,
  LoaderCircle,
  Link2,
  RefreshCw,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useWorkspace } from "../api/workspace-context";
import { BrowserApiError, saveTelegramEvents } from "../api/client";
import { notificationsApi, type NotificationItem } from "../api/notifications";
import { PageHeader } from "../components/PageHeader";
import { WorkspaceState } from "../components/WorkspaceState";
import { getFormatLocale } from "../i18n/runtime";
import "../styles/secondary-pages.css";
import "../styles/account-center.css";

const defaultEvents = [
  {
    key: "stock_signal",
    label: "股票买卖建议",
    note: "买入、加仓、持有、减仓与退出",
    enabled: false,
  },
  {
    key: "option_signal",
    label: "期权策略建议",
    note: "合约、到期日、执行价与最大风险",
    enabled: false,
  },
  {
    key: "risk_rejected",
    label: "风险与止损提醒",
    note: "仓位、回撤、冷却期与系统暂停",
    enabled: false,
  },
  {
    key: "order_filled",
    label: "订单与成交状态",
    note: "提交、成交、拒绝与异常恢复",
    enabled: false,
  },
  {
    key: "membership_update",
    label: "会员与账单",
    note: "到期提醒、付款结果与权益变更",
    enabled: false,
  },
];

const EVENT_CAPABILITIES: Partial<Record<(typeof defaultEvents)[number]["key"], string>> = {
  stock_signal: "tg_stock_signal",
  option_signal: "tg_option_signal",
};

export function NotificationsPage() {
  const workspace = useWorkspace();
  const navigate = useNavigate();
  const [events, setEvents] = useState(defaultEvents);
  const [eventStatus, setEventStatus] = useState("");
  const [connectionStatus, setConnectionStatus] = useState("");
  const [pendingEventKey, setPendingEventKey] = useState<string | null>(null);
  const [inbox, setInbox] = useState<NotificationItem[]>([]);
  const [inboxState, setInboxState] = useState("");
  const telegram = workspace.data?.telegram;
  const telegramReady = Boolean(
    telegram?.bound && telegram?.verified && telegram?.consented,
  );
  const capabilities = workspace.data?.membership.capabilities;
  const capabilityListKnown = Array.isArray(capabilities);

  useEffect(() => {
    if (workspace.mode !== "authenticated") return;
    let active = true;
    setInboxState("正在读取真实通知…");
    void notificationsApi.list().then((payload) => {
      if (!active) return;
      setInbox(payload.items);
      setInboxState(payload.items.length ? "通知来自服务端真实收件箱。" : "当前没有服务端返回的通知。")
    }).catch(() => {
      if (active) setInboxState("通知收件箱接口尚未配置；不会展示演示消息。")
    });
    return () => { active = false };
  }, [workspace.mode]);

  const openNotification = async (item: NotificationItem) => {
    if (!item.read) {
      try {
        await notificationsApi.markRead(item.public_id);
        setInbox((items) => items.map((entry) => entry.public_id === item.public_id ? { ...entry, read: true } : entry));
      } catch {
        setInboxState("通知已打开，但服务端尚未确认已读。")
      }
    }
    if (!item.target) return;
    try {
      const resolved = await notificationsApi.resolve(item.public_id);
      if (resolved.stale || !resolved.locator) {
        setInboxState("这条通知的行动链接已失效，已保留在通知收件箱。")
        return
      }
      const allowedRoutes = new Set(["/account", "/membership", "/notifications", "/today", "/discover", "/research", "/paper", "/portfolio", "/reports", "/trade"])
      if (!allowedRoutes.has(resolved.route)) {
        setInboxState("通知行动链接返回了不受支持的页面；为安全起见没有跳转。")
        return
      }
      const params = new URLSearchParams({ notification_kind: resolved.locator.kind, notification_public_id: resolved.locator.public_id, notification_version: String(resolved.locator.version) })
      navigate(`${resolved.route}?${params.toString()}`);
    } catch {
      setInboxState("通知行动链接暂时无法验证；为安全起见没有跳转。")
    }
  };

  useEffect(() => {
    const stored = workspace.data?.telegram.events;
    if (!stored) return;
    setEvents((items) =>
      items.map((item) => {
        if (!(item.key in stored)) return item;
        const requiredCapability = EVENT_CAPABILITIES[item.key];
        const allowed = telegramReady
          && (!requiredCapability || (capabilityListKnown && capabilities?.includes(requiredCapability) === true));
        return { ...item, enabled: allowed && stored[item.key] };
      }),
    );
  }, [capabilityListKnown, capabilities, telegramReady, workspace.data]);

  return (
    <div className="page operations-page notification-dashboard">
      <PageHeader
        kicker="DELIVERY / TELEGRAM"
        title="消息通知"
        description="查看真实站内收件箱、网站投递回执与 Telegram 绑定偏好；未接入的外部投递不会用演示记录补位。"
      />
      <WorkspaceState />
      <div className="notification-dashboard-grid">
        <section className="telegram-hero data-panel notification-connection">
          <header className="panel-heading">
            <div>
              <span>TELEGRAM CONNECTION</span>
              <h2>通知连接状态</h2>
            </div>
            <Link2 size={20} />
          </header>
          <div className="notification-connection-body">
            <div className="telegram-identity">
              <span className="channel-icon">
                <Bot size={25} />
              </span>
              <div>
                <span>TELEGRAM 通知连接</span>
                <strong>
                  {telegram?.bound ? "个人通知已绑定" : "尚未绑定个人通知"}
                </strong>
                <p>
                  {telegram?.verified ? "已验证" : "未验证"} ·{" "}
                  {telegram?.consented ? "已授权接收通知" : "未授权接收通知"} ·
                  识别码 {telegram?.chat_id_masked || "未登记"}
                </p>
              </div>
            </div>
            <div className="telegram-health">
              <span
                className={`status-chip ${telegramReady ? "official" : "research"}`}
              >
                {telegramReady ? (
                  <CheckCircle2 size={14} />
                ) : (
                  <AlertTriangle size={14} />
                )}{" "}
                {telegramReady ? "绑定资料齐全" : "绑定资料未完成"}
              </span>
              <small>
                <Clock3 size={14} />{" "}
                {telegram?.updated_at
                  ? `资料更新 ${new Date(telegram.updated_at).toLocaleString(getFormatLocale(), { hour12: false })}`
                  : "暂无绑定资料更新时间"}
              </small>
              <button
                className="button secondary"
                type="button"
                onClick={() =>
                  setConnectionStatus(
                    telegramReady
                      ? "账户绑定、验证和授权资料均已登记；这不代表 Bot 网络或消息投递已经验证。"
                      : "绑定资料尚未齐全，请先完成绑定、验证和授权。",
                  )
                }
              >
                <RefreshCw size={15} /> 检查绑定状态
              </button>
            </div>
            <span
              className="form-status notification-connection-status"
              role="status"
            >
              {connectionStatus}
            </span>
            <ol className="notification-connection-progress" aria-label="Telegram 连接进度">
              {[
                {
                  key: "bound",
                  label: "建立连接",
                  detail: "通知目标已绑定",
                  complete: Boolean(telegram?.bound),
                },
                {
                  key: "verified",
                  label: "确认身份",
                  detail: "账户身份已验证",
                  complete: Boolean(telegram?.verified),
                },
                {
                  key: "consented",
                  label: "接收授权",
                  detail: "通知接收已授权",
                  complete: Boolean(telegram?.consented),
                },
              ].map((stage, index) => (
                <li
                  className={`notification-connection-stage ${stage.complete ? "is-complete" : "is-pending"}`}
                  key={stage.key}
                >
                  <span className="notification-stage-icon" aria-hidden="true">
                    {stage.complete ? <CheckCircle2 size={17} /> : <Circle size={17} />}
                  </span>
                  <span className="notification-stage-copy">
                    <small>{`0${index + 1} · ${stage.label}`}</small>
                    <strong>{stage.complete ? "已完成" : "等待完成"}</strong>
                    <span>{stage.detail}</span>
                  </span>
                </li>
              ))}
            </ol>
            <div className="inline-warning">
              <AlertTriangle size={17} />
              <span>新界面只读取绑定与偏好状态，不主动发送测试消息。</span>
            </div>
          </div>
        </section>

        <section className="data-panel notification-preferences">
          <header className="panel-heading">
            <div>
              <span>通知类型</span>
              <h2>你要接收什么</h2>
            </div>
            <BellRing size={20} />
          </header>
          <div className="setting-list">
            {events.map((event) => {
              const requiredCapability = EVENT_CAPABILITIES[event.key];
              const capabilityLocked = Boolean(requiredCapability)
                && (!capabilityListKnown || (requiredCapability ? !capabilities?.includes(requiredCapability) : false));
              const locked = !telegramReady || capabilityLocked;
              const entitlementLabel = !telegramReady
                ? "Telegram 授权未完成 · 已锁定"
                : requiredCapability && !capabilityListKnown
                  ? "服务端能力未返回 · 已锁定"
                  : capabilityLocked
                    ? "当前账户未授予服务端能力 · 已锁定"
                    : "Telegram 授权已完成";
              return (
                <article key={event.key}>
                  <div>
                    <strong>{event.label}</strong>
                    <small>{event.note}</small>
                    <em>{entitlementLabel}</em>
                  </div>
                  <button
                    className={`toggle ${event.enabled ? "on" : ""} ${pendingEventKey === event.key ? "is-loading" : ""}`}
                    type="button"
                    role="switch"
                    aria-checked={event.enabled}
                    aria-busy={pendingEventKey === event.key}
                    aria-disabled={locked || pendingEventKey === event.key}
                    data-state={event.enabled ? "checked" : "unchecked"}
                    data-disabled={locked || pendingEventKey === event.key || undefined}
                    aria-label={`${event.label}推送`}
                    disabled={locked || pendingEventKey === event.key}
                    title={locked ? entitlementLabel : undefined}
                    onClick={async () => {
                      const next = !event.enabled;
                      if (workspace.mode !== "authenticated") {
                        setEventStatus("请先登录后保存真实推送设置");
                        return;
                      }
                      setPendingEventKey(event.key);
                      try {
                        await saveTelegramEvents({ [event.key]: next });
                        setEvents((items) =>
                          items.map((item) =>
                            item.key === event.key
                              ? { ...item, enabled: next }
                              : item,
                          ),
                        );
                        setEventStatus(
                          `${event.label}已${next ? "开启" : "关闭"}`,
                        );
                      } catch (caught) {
                        setEventStatus(
                          caught instanceof BrowserApiError
                            ? caught.message
                            : "设置保存失败",
                        );
                      } finally {
                        setPendingEventKey(null);
                      }
                    }}
                  >
                    {pendingEventKey === event.key ? (
                      <LoaderCircle aria-hidden="true" size={16} />
                    ) : (
                      <i />
                    )}
                  </button>
                </article>
              );
            })}
          </div>
          <p className="setting-status" role="status">
            {eventStatus}
          </p>
        </section>

        <section className="data-panel notification-inbox">
          <header className="panel-heading">
            <div>
              <span>CANONICAL INBOX</span>
              <h2>通知收件箱</h2>
            </div>
            <BellRing size={20} />
          </header>
          <p className="setting-status" role="status">{inboxState}</p>
          {inbox.length ? <div className="notification-inbox-list">{inbox.map((item) => <article className={item.read ? "is-read" : "is-unread"} key={item.public_id}><div className="notification-inbox-copy"><span className={`status-chip ${item.severity === "error" || item.severity === "warning" ? "research" : "official"}`}>{item.kind}</span><strong>{item.title}</strong><p>{item.body}</p><small>{new Date(item.created_at).toLocaleString(getFormatLocale(), { hour12: false })} · {item.delivery.length ? item.delivery.map((delivery) => `${delivery.channel} ${delivery.status}`).join(" · ") : "尚无投递记录"}</small></div><button className="button tertiary" type="button" onClick={() => void openNotification(item)}>{item.target ? "查看行动" : item.read ? "已读" : "标记已读"}</button></article>)}</div> : <div className="inline-empty notification-inbox-empty">服务端没有返回任何真实投递结果；不会展示演示送达记录、占位消息或伪造投递状态。</div>}
        </section>

        <section className="data-panel notification-deliveries">
          <header className="panel-heading">
            <div>
              <span>RECENT DELIVERIES</span>
              <h2>外部投递历史 · 尚未接入</h2>
            </div>
            <AlertTriangle size={20} />
          </header>
          <div className="inline-warning">
            <AlertTriangle size={17} />
            <span>
              当前 canonical inbox 已提供网站渠道投递回执；Telegram 独立 outbox 尚未投影到本页面。接入前不会把站内 delivered 冒充 Telegram 已送达。
            </span>
          </div>
          <div className="inline-empty">
            后续真实记录将显示事件时间、尝试时间、投递状态、脱敏失败原因，以及返回同一行动详情的链接。
          </div>
        </section>
      </div>
    </div>
  );
}
