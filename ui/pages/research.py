# -*- coding: utf-8 -*-
"""Public-facing research card and explainable signal summary."""

from __future__ import annotations

from datetime import datetime
from core.compat import UTC
from functools import lru_cache
from io import BytesIO
from pathlib import Path
from textwrap import wrap

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from PIL import Image, ImageDraw, ImageFont

from core.database import get_database
from core.plans import can, effective_plan
from core.strategy_tracking import StrategyPerformanceTracker
from ui.components import experience_hero, metric_grid, section_label


DISCLAIMER = "研究内容来自历史账本与已配置数据源，不构成投资建议、收益承诺或自动下单指令。"
PROJECT_ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=12)
def _card_font(size: int, *, bold: bool = False):
    candidates = (
        Path("C:/Windows/Fonts/msjhbd.ttc" if bold else "C:/Windows/Fonts/msjh.ttc"),
        Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
        Path("/System/Library/Fonts/PingFang.ttc"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
        PROJECT_ROOT / "static" / "fonts" / ("IBMPlexMono-SemiBold.ttf" if bold else "IBMPlexMono-Regular.ttf"),
    )
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _card_png(symbol: str, strategy: str, action: str, reason: str, size: tuple[int, int]) -> bytes:
    image = Image.new("RGB", size, "#0c0f13")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((32, 32, size[0] - 32, size[1] - 32), radius=24, fill="#131820", outline="#37d996", width=3)
    draw.text((64, 68), "CICLOTRADE / RESEARCH", font=_card_font(28, bold=True), fill="#67d9ad")
    draw.line((64, 120, size[0] - 64, 120), fill="#2b3541", width=2)
    draw.text((64, 154), str(symbol)[:24], font=_card_font(72, bold=True), fill="#f3f5f7")
    strategy_text = "\n".join(wrap(str(strategy), width=28, break_long_words=True)[:2])
    draw.multiline_text((64, 252), strategy_text, font=_card_font(36), fill="#b8c2c8", spacing=8)
    draw.text((64, 378), str(action)[:28], font=_card_font(42, bold=True), fill="#e9b96b")
    reason_text = "\n".join(wrap(str(reason), width=32, break_long_words=True)[:3])
    draw.multiline_text((64, 464), reason_text, font=_card_font(28), fill="#aab4bf", spacing=10)
    draw.text((64, size[1] - 92), "FOR RESEARCH ONLY · CICLOTRADE", font=_card_font(24, bold=True), fill="#7f8b98")
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()


def render() -> None:
    user = st.session_state.user
    plan = effective_plan(user)
    db = get_database()
    experience_hero(
        "RESEARCH / SIGNALS",
        "研究名片",
        "把市场数据、策略验证、风险条件与行动信号放在同一条可复核路线中。",
        "可追溯账本 · 不承诺收益",
        (
            ("数据输入", "行情与事件"),
            ("因子评分", "方向与强度"),
            ("交叉验证", "策略一致性"),
            ("风险闸门", "仓位与止损"),
            ("研究信号", "可解释行动"),
        ),
    )
    events = db.fetch_all(
        """SELECT e.id,e.strategy_name,e.occurred_at,e.metadata_json,l.symbol,l.instrument_type,
                  l.quantity_delta,l.price
           FROM quant_events e JOIN quant_event_legs l ON l.event_id=e.id
           WHERE e.event_type='signal' ORDER BY e.occurred_at DESC,e.id DESC LIMIT 12"""
    )
    backtests = db.fetch_all(
        "SELECT strategy_name,symbol,return_rate,max_drawdown,win_rate,created_at FROM backtest_records WHERE user_id=? ORDER BY created_at DESC LIMIT 6",
        (user["id"],),
    )
    metric_grid(
        (
            ("公开研究事件", str(len(events)), "来自不可变策略账本", "positive" if events else ""),
            ("个人回测记录", str(len(backtests)), "只显示当前账户", ""),
            ("当前方案", plan, "分享配额按方案计算", "positive" if plan != "免费版" else ""),
        )
    )
    section_label("最新策略信号", "每条信号保留时间、策略与标的")
    if events:
        frame = pd.DataFrame(events)
        frame["方向"] = frame["quantity_delta"].map(lambda value: "买入/增持" if float(value) > 0 else "卖出/减持" if float(value) < 0 else "持有")
        frame = frame[["symbol", "strategy_name", "方向", "occurred_at"]]
        frame.columns = ["标的", "策略", "动作", "时间"]
        selection = st.dataframe(
            frame,
            hide_index=True,
            width="stretch",
            on_select="rerun",
            selection_mode="single-row",
            key="research_signal_table",
        )
        selected_rows = selection.selection.rows
        selected = events[selected_rows[0]] if selected_rows and selected_rows[0] < len(events) else events[0]
        with st.container(border=True):
            st.markdown(f"### {selected['symbol']} · {selected['strategy_name']}")
            action = "买入 / 增持" if float(selected["quantity_delta"]) > 0 else "卖出 / 减持" if float(selected["quantity_delta"]) < 0 else "持有"
            st.write(f"动作：{action} · 发生时间：{selected['occurred_at']}")
            st.caption(f"账本事件 #{selected['id']} · {selected['instrument_type']} · {DISCLAIMER}")
            if plan == "免费版":
                st.info("研究卡片分享从标准版开放。", icon=":material/lock:")
            else:
                sizes = {"牛牛圈": (1080, 1440), "Threads": (1080, 1350), "X": (1600, 900)}
                fmt = st.selectbox("分享尺寸", list(sizes), key="research_share_format")
                share_limit = {"标准版": 3, "高级版": 10}.get(plan)
                today = datetime.now(UTC).date().isoformat()
                used = int((db.fetch_one("SELECT COUNT(*) count FROM share_events WHERE user_id=? AND created_at>=?", (user["id"], today)) or {"count": 0})["count"])
                exhausted = share_limit is not None and used >= share_limit
                st.caption(f"今日分享 {used} / {'不限' if share_limit is None else share_limit}")
                if exhausted:
                    st.warning("今日分享配额已用完，UTC 00:00 后自动重置。", icon=":material/schedule:")
                image = _card_png(selected["symbol"], selected["strategy_name"], action, DISCLAIMER, sizes[fmt])
                safe_symbol = "".join(character for character in str(selected["symbol"]) if character.isalnum() or character in "._-")[:24] or "signal"
                st.download_button(
                    "下载品牌水印卡片",
                    data=image,
                    file_name=f"ciclotrade-{safe_symbol}-{fmt}.png",
                    mime="image/png",
                    icon=":material/download:",
                    disabled=exhausted,
                    width="stretch",
                    on_click=lambda: db.execute("INSERT INTO share_events (user_id,format,created_at) VALUES (?,?,?)", (user["id"], fmt, datetime.now(UTC).isoformat(timespec="seconds"))),
                )
    else:
        st.info("暂时没有公开策略事件；系统接收到外部量化事件后会按时间线展示。", icon=":material/hourglass_empty:")

    section_label("策略履歷", "保存後按交易日追加，歷史記錄不覆蓋")
    if can(plan, "strategy_tracking"):
        tracker = StrategyPerformanceTracker(db)
        saved_strategies = tracker.list(int(user["id"]))
        if saved_strategies:
            labels = {item["id"]: item["name"] for item in saved_strategies}
            saved_id = st.selectbox(
                "選擇已保存策略", list(labels), format_func=labels.get, key="research_saved_strategy"
            )
            history = tracker.history(int(user["id"]), int(saved_id))
            if history:
                latest = history[0]
                metric_grid(
                    (
                        ("近 30 日", f"{latest['return_30d']:+.2%}", latest["eval_date"], "positive" if latest["return_30d"] >= 0 else "negative"),
                        ("歷史最大回撤", f"{latest['max_drawdown']:.2%}", "策略履歷", "negative"),
                        ("年化收益", f"{latest['annual_return']:+.2%}", "歷史年化", ""),
                        ("夏普 / 勝率", f"{latest['sharpe_ratio']:.2f} / {latest['win_rate']:.1%}", "歷史統計", ""),
                    )
                )
                curve = latest.get("equity_curve") or []
                if curve:
                    figure = go.Figure(go.Scatter(y=curve, mode="lines", line={"color": "#37d996", "width": 2.5}))
                    figure.update_layout(
                        height=320, margin={"l": 8, "r": 8, "t": 16, "b": 8},
                        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="#090b0d",
                        font={"color": "#8d999b", "family": "IBM Plex Mono"},
                        xaxis={"title": "最近 90 個交易日", "gridcolor": "#1b2327"},
                        yaxis={"title": "策略淨值", "gridcolor": "#1b2327"},
                    )
                    st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
            else:
                st.info("策略已保存；完成第一個收盤後更新才會出現真實履歷。", icon=":material/schedule:")
        else:
            st.info("尚未保存策略。可從策略模板或一句話策略頁保存並開始追蹤。", icon=":material/bookmark:")
    else:
        st.info("策略績效追蹤從高級版開放。", icon=":material/lock:")

    section_label("回测摘要", "历史结果不代表未来表现")
    if backtests:
        frame = pd.DataFrame(backtests)
        frame["return_rate"] = frame["return_rate"].map(lambda value: f"{float(value):+.2%}")
        frame["max_drawdown"] = frame["max_drawdown"].map(lambda value: f"{float(value):.2%}")
        frame["win_rate"] = frame["win_rate"].map(lambda value: f"{float(value):.2%}")
        frame.columns = ["策略", "标的", "收益率", "最大回撤", "胜率", "时间"]
        st.dataframe(frame, hide_index=True, width="stretch")
    else:
        st.caption("完成第一次回测后，这里会出现可复核的摘要。")
    st.markdown(f'<div class="disclaimer"><strong>研究边界</strong><p>{DISCLAIMER}</p></div>', unsafe_allow_html=True)
