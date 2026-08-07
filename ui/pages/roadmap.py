# -*- coding: utf-8 -*-
"""Transparent product roadmap."""

from __future__ import annotations

import html

import streamlit as st

from core.database import get_database
from ui.components import experience_hero, section_label


STATUS = {"live": "已上线", "in_progress": "开发中", "planning": "规划中", "evaluating": "评估中"}
FALLBACK = (
    ("2026 Q3", "多条件组合预警", "live", "价格、量比、RSI、MACD 与均线条件。"),
    ("2026 Q3", "研究名片与品牌分享", "live", "可复核的信号卡片与水印导出。"),
    ("2026 Q4", "多券商连接", "in_progress", "Tiger、Alpaca、IBKR 的凭证与连接状态。"),
    ("2026 Q4", "A 股 QMT/PTrade", "evaluating", "需要券商授权与部署环境。"),
    ("2027", "定制版期权自动交易", "planning", "即将上线，需单独签约、风控与私有部署。"),
)


def render() -> None:
    experience_hero(
        "PRODUCT / ROADMAP",
        "功能路线图",
        "让已上线、正在接入与依赖外部授权的能力沿同一条交付路线清楚呈现。",
        "交付状态由产品后台同步",
        (
            ("已上线", "核心研究与风控"),
            ("接入中", "券商与支付通道"),
            ("待授权", "商业行情与 A 股终端"),
            ("私有部署", "定制自动化执行"),
        ),
    )
    rows = get_database().fetch_all("SELECT quarter,name,status,description FROM roadmap_items ORDER BY sort_order,quarter,id")
    if not rows:
        rows = [{"quarter": q, "name": n, "status": s, "description": d} for q, n, s, d in FALLBACK]
        st.caption("当前显示产品基线；管理员发布数据库路线图后会自动替换。")
    for quarter in sorted({row["quarter"] for row in rows}):
        section_label(quarter, "状态与交付边界")
        items = []
        for row in (item for item in rows if item["quarter"] == quarter):
            status = str(row["status"])
            items.append(
                f'<article class="roadmap-item {html.escape(status)}" role="listitem">'
                f'<i aria-hidden="true"></i><div><span>{html.escape(STATUS.get(status, status))}</span>'
                f'<h3>{html.escape(str(row["name"]))}</h3><p>{html.escape(str(row["description"]))}</p></div></article>'
            )
        st.html('<section class="roadmap-line" role="list">' + "".join(items) + "</section>")
