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
import { useWorkspace } from "../api/workspace-context";
import { BrowserApiError, saveTelegramEvents } from "../api/client";
import { PageHeader } from "../components/PageHeader";
import { WorkspaceState } from "../components/WorkspaceState";
import { getFormatLocale } from "../i18n/runtime";
import "../styles/secondary-pages.css";

const defaultEvents = [
  {
    key: "stock_signal",
    label: "正股买卖建议",
    note: "买入、加仓、持有、减仓与退出",
    enabled: false,
    capability: "tg_stock_signal",
  },
  {
    key: "option_signal",
    label: "期权策略建议",
    note: "合约、到期日、执行价与最大风险",
    enabled: false,
    capability: "tg_option_signal",
  },
  {
    key: "risk_rejected",
    label: "风险与止损提醒",
    note: "仓位、回撤、冷却期与系统暂停",
    enabled: false,
    capability: "tg_risk_alert",
  },
  {
    key: "order_filled",
    label: "订单与成交状态",
    note: "提交、成交、拒绝与异常恢复",
    enabled: false,
    capability: "tg_order_status",
  },
  {
    key: "membership_update",
    label: "会员与账单",
    note: "到期提醒、付款结果与权益变更",
    enabled: false,
    capability: "tg_membership_update",
  },
];

export function NotificationsPage() {
  const workspace = useWorkspace();
  const [events, setEvents] = useState(defaultEvents);
  const [eventStatus, setEventStatus] = useState("");
  const [connectionStatus, setConnectionStatus] = useState("");
  const [pendingEventKey, setPendingEventKey] = useState<string | null>(null);
  const telegram = workspace.data?.telegram;
  const telegramReady = Boolean(
    telegram?.bound && telegram?.verified && telegram?.consented,
  );
  const capabilities = workspace.data?.membership.capabilities ?? [];
  const capabilityListKnown = Array.isArray(workspace.data?.membership.capabilities);

  useEffect(() => {
    const stored = workspace.data?.telegram.events;
    if (!stored) return;
    setEvents((items) =>
      items.map((item) => {
        if (!(item.key in stored)) return item;
        const allowed = capabilityListKnown && capabilities.includes(item.capability);
        return { ...item, enabled: allowed && stored[item.key] };
      }),
    );
  }, [capabilityListKnown, capabilities, workspace.data]);

  return (
    <div className="page operations-page notification-dashboard">
      <PageHeader
        kicker="DELIVERY / TELEGRAM"
        title="消息通知"
        description="查看 Telegram 绑定与通知偏好；真实投递日志接口开放后，才会显示发送结果与失败原因。"
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
              const locked = !capabilityListKnown || !capabilities.includes(event.capability);
              const entitlementLabel = !capabilityListKnown
                ? "服务端能力未返回 · 已锁定"
                : locked
                  ? "当前账户未授予此服务端能力 · 已锁定"
                  : "服务端能力已授予";
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

        <section className="data-panel notification-deliveries">
          <header className="panel-heading">
            <div>
              <span>RECENT DELIVERIES</span>
              <h2>投递记录 · 尚未接入</h2>
            </div>
            <AlertTriangle size={20} />
          </header>
          <div className="inline-warning">
            <AlertTriangle size={17} />
            <span>
              当前 API
              只提供绑定、验证、授权和通知偏好，没有返回任何真实投递结果。接口接入前不会展示演示送达记录，也不会宣称消息已经发送成功。
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
