# -*- coding: utf-8 -*-
"""推荐、签到和社交分享奖励。"""

from __future__ import annotations

from datetime import date, datetime
from core.compat import UTC
import ipaddress
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

from core.admin_service import AdminService
from core.database import get_database
from core.plans import referral_code
from payment.order_service import grant_subscription_days
from ui.components import metric_grid, page_heading, section_label


REWARD_LABELS = {
    "CHECKIN": "每日签到",
    "STREAK_7": "连续签到奖励",
    "REFERRAL_30": "有效推荐奖励",
    "SOCIAL_PENDING": "分享审核中",
    "SOCIAL_APPROVED": "分享奖励已发放",
    "SOCIAL_REJECTED": "分享申请未通过",
}


def _tier(count: int) -> str:
    if count >= 50:
        return "钻石"
    if count >= 15:
        return "黄金"
    if count >= 5:
        return "白银"
    return "青铜"


def _claim_daily_checkin(db, user_id: int, day: str) -> tuple[bool, bool]:
    claimed_day = date.fromisoformat(day)
    now = datetime.now(UTC)
    with db.transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        inserted = conn.execute(
            """INSERT OR IGNORE INTO rewards
               (user_id,reward_type,days,reference,created_at) VALUES (?,?,?,?,?)""",
            (user_id, "CHECKIN", 0, day, now.isoformat(timespec="seconds")),
        )
        if not inserted.rowcount:
            return False, False
        rows = conn.execute(
            """SELECT reference FROM rewards
               WHERE user_id=? AND reward_type='CHECKIN'
               ORDER BY reference DESC LIMIT 7""",
            (user_id,),
        ).fetchall()
        if len(rows) != 7:
            return True, False
        dates = sorted(date.fromisoformat(row["reference"]) for row in rows)
        if not all((dates[index] - dates[index - 1]).days == 1 for index in range(1, 7)):
            return True, False
        previous = conn.execute(
            """SELECT reference FROM rewards
               WHERE user_id=? AND reward_type='STREAK_7'
               ORDER BY reference DESC LIMIT 1""",
            (user_id,),
        ).fetchone()
        if previous and (claimed_day - date.fromisoformat(previous["reference"])).days < 7:
            return True, False
        streak = conn.execute(
            """INSERT OR IGNORE INTO rewards
               (user_id,reward_type,days,reference,created_at) VALUES (?,?,?,?,?)""",
            (user_id, "STREAK_7", 1, day, now.isoformat(timespec="seconds")),
        )
        if not streak.rowcount:
            return True, False
        grant_subscription_days(
            conn,
            user_id,
            1,
            "标准版",
            now,
            source_kind="streak_reward",
            source_ref=f"reward:{streak.lastrowid}",
        )
        return True, True


def _canonical_social_url(value: str) -> str | None:
    parsed = urlparse(value.strip())
    if parsed.scheme.lower() != "https" or not parsed.hostname or parsed.username or parsed.password:
        return None
    host = parsed.hostname.lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        if "." not in host or host == "localhost":
            return None
    try:
        port_value = parsed.port
    except ValueError:
        return None
    port = f":{port_value}" if port_value and port_value != 443 else ""
    path = parsed.path or "/"
    return parsed._replace(scheme="https", netloc=f"{host}{port}", path=path, fragment="").geturl()


def _submit_social_share(db, user_id: int, platform: str, url: str) -> bool:
    reference = f"{platform}:{url}"
    with db.transaction() as conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            """SELECT reference FROM rewards
               WHERE user_id=? AND reward_type LIKE 'SOCIAL_%'""",
            (user_id,),
        ).fetchall()
        if any(str(row["reference"] or "").partition(":")[2] == url for row in existing):
            return False
        conn.execute(
            "INSERT INTO rewards (user_id,reward_type,days,reference,created_at) VALUES (?,?,?,?,?)",
            (
                user_id,
                "SOCIAL_PENDING",
                0,
                reference,
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
    return True


def _render_social_review(user: dict, db) -> None:
    if not user.get("is_admin"):
        return
    service = AdminService(db)
    try:
        role = service.role_for(int(user["id"]))
        if not service.has_permission(role, "research"):
            return
        requests = service.list_social_share_requests(int(user["id"]))
    except PermissionError:
        return

    section_label("分享奖励审核", "核对公开内容和互动数据后批准 1–15 天，或直接驳回")
    if not requests:
        st.success("没有等待审核的分享申请。", icon=":material/check_circle:")
        return

    rows = []
    for request in requests:
        platform, separator, url = str(request["reference"] or "").partition(":")
        rows.append(
            {
                "申请 ID": int(request["id"]),
                "用户": request["email"],
                "平台": platform if separator else "其他",
                "公开链接": url if separator else str(request["reference"] or ""),
                "提交时间": request["created_at"],
            }
        )
    st.dataframe(
        pd.DataFrame(rows),
        hide_index=True,
        width="stretch",
        column_config={
            "公开链接": st.column_config.LinkColumn(display_text=":material/open_in_new:"),
        },
    )
    labels = {
        int(request["id"]): f"#{request['id']} · {request['email']}"
        for request in requests
    }
    with st.form("social_share_review"):
        reward_id = st.selectbox(
            "选择申请",
            list(labels),
            format_func=labels.__getitem__,
        )
        decision = st.segmented_control("审核结果", ["批准", "驳回"], default="批准", required=True)
        days = st.number_input(
            "批准奖励天数",
            min_value=1,
            max_value=15,
            value=1,
            step=1,
            help="按已核对的点赞与转发量选择档位；驳回时不会发放。",
        )
        reviewed = st.checkbox("我已打开公开链接并核对内容及互动数据")
        submitted = st.form_submit_button(
            "提交审核结果", type="primary", icon=":material/fact_check:"
        )
    if submitted:
        if not reviewed:
            st.error("请先确认已完成公开链接核验。")
            return
        approved = decision == "批准"
        try:
            service.review_social_share(
                int(user["id"]), int(reward_id), approved, int(days) if approved else 0
            )
        except (PermissionError, ValueError) as exc:
            st.error(str(exc))
        else:
            st.session_state.growth_flash = "分享奖励已发放。" if approved else "分享申请已驳回。"
            st.rerun()


def render() -> None:
    user = st.session_state.user
    db = get_database()
    code = referral_code(user["id"])
    referrals = db.fetch_all("SELECT * FROM referrals WHERE referrer_id=? ORDER BY created_at DESC", (user["id"],))
    qualified = len([row for row in referrals if row["status"] == "qualified"])
    rewards = db.fetch_all("SELECT * FROM rewards WHERE user_id=? ORDER BY created_at DESC", (user["id"],))
    page_heading(
        "GROWTH / REWARDS",
        "推荐与奖励",
        "邀请、签到与公开分享均需留下可审核记录。奖励只以订阅时长发放，不触发现金承诺。",
        f"ALLIANCE · {_tier(qualified)}",
    )
    if flash := st.session_state.pop("growth_flash", None):
        st.success(flash, icon=":material/check_circle:")
    metric_grid(
        (
            ("联盟等级", _tier(qualified), f"{qualified} 位有效推荐", "positive"),
            ("推荐总数", str(len(referrals)), "完成注册", ""),
            ("有效推荐", str(qualified), "首笔订单已支付", ""),
            ("奖励天数", str(sum(int(row["days"]) for row in rewards)), "已发放订阅时长", ""),
        )
    )
    section_label("专属推荐码", "被推荐人注册时填写")
    st.code(code, language="text")
    st.caption("有效推荐的首笔已支付订阅按时长 30% 自动发放，同一位被推荐人只奖励一次。")

    section_label("连续签到", "连续登录 7 天奖励 1 天订阅时长")
    today = date.today().isoformat()
    checked = db.fetch_one("SELECT 1 FROM rewards WHERE user_id=? AND reward_type='CHECKIN' AND reference=?", (user["id"], today))
    if st.button("今日签到", type="primary", icon=":material/calendar_today:", disabled=bool(checked)):
        _, streak_granted = _claim_daily_checkin(db, int(user["id"]), today)
        if streak_granted:
            st.session_state.growth_flash = "连续签到 7 天，已增加 1 天订阅时长。"
        st.rerun()
    st.caption("今日已签到" if checked else "今日尚未签到")

    section_label("社交媒体分享", "提交公开链接后由管理员核验点赞与转发量")
    with st.form("share_reward"):
        platform = st.selectbox(
            "平台",
            ["YouTube", "Instagram", "Facebook", "X / Twitter", "Threads", "小红书", "抖音", "其他"],
        )
        url = st.text_input(
            "公开内容链接", placeholder="https://…", autocomplete="off", max_chars=450
        )
        submitted = st.form_submit_button("提交审核", icon=":material/share:")
    if submitted:
        url = url.strip()
        canonical_url = _canonical_social_url(url)
        if not canonical_url:
            st.error("请输入可公开访问的 HTTPS 链接，不能使用本机或内网地址。")
        else:
            inserted = _submit_social_share(db, int(user["id"]), platform, canonical_url)
            st.session_state.growth_flash = (
                "分享记录已提交，审核后按 1–15 天档位发放。"
                if inserted
                else "这条分享已在等待审核，请勿重复提交。"
            )
            st.rerun()
    if rewards:
        section_label("奖励记录", f"{len(rewards)} 条")
        frame = pd.DataFrame(rewards)[["reward_type", "days", "reference", "created_at"]]
        frame["reward_type"] = frame["reward_type"].map(REWARD_LABELS).fillna(frame["reward_type"])
        frame.columns = ["类型", "天数", "依据", "建立时间"]
        st.dataframe(frame, hide_index=True, width="stretch")

    _render_social_review(user, db)
