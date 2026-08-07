# -*- coding: utf-8 -*-
"""Shared CicloTrade interface primitives."""

from __future__ import annotations

import html
import os
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import urlsplit

import streamlit as st

from notification.telegram_bot import telegram_community_url


STYLE_PATH = Path(__file__).with_name("styles.css")


def _telegram_community_url() -> str | None:
    value = os.getenv("TRADEAI_TELEGRAM_COMMUNITY_URL", "").strip() or telegram_community_url() or ""
    if not value:
        return None
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname in {"t.me", "telegram.me", "www.telegram.me"}
            and parsed.port in {None, 443}
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        return None
    return value if valid else None


def load_styles() -> None:
    st.html(STYLE_PATH)
    st.html(
        """
<script>
(() => {
  if (window.__tradeaiAmbientStarted) return;
  window.__tradeaiAmbientStarted = true;
  const canvas = document.createElement('canvas');
  canvas.className = 'tradeai-ambient-canvas';
  canvas.setAttribute('aria-hidden', 'true');
  canvas.setAttribute('role', 'presentation');
  document.body.prepend(canvas);
  const ctx = canvas.getContext('2d', { alpha: true });
  if (!ctx) return;
  const reduced = window.matchMedia('(prefers-reduced-motion: reduce)');
  let width = 0;
  let height = 0;
  let points = [];
  let frame = 0;
  let running = false;
  let compact = false;
  const isActive = () => !document.hidden && Boolean(document.querySelector('.auth-shell, .experience-hero'));
  const color = (tone, alpha) => {
    const values = { cyan: [99, 189, 245], mint: [126, 225, 181], gold: [231, 185, 106] }[tone];
    return `rgba(${values[0]},${values[1]},${values[2]},${alpha})`;
  };
  const resize = () => {
    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    width = window.innerWidth;
    height = window.innerHeight;
    compact = width <= 620;
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    const count = Math.max(compact ? 14 : 18, Math.min(28, Math.round(width / 72)));
    points = Array.from({ length: count }, (_, index) => ({
      x: (index / Math.max(count - 1, 1)) * width,
      y: height * ((compact ? 0.37 : 0.57) + Math.sin(index * 0.78) * 0.065 + Math.sin(index * 1.83) * 0.025),
      tone: index % 7 === 0 ? 'gold' : index % 3 === 0 ? 'mint' : 'cyan',
    }));
  };
  const signalY = (point, index, time, offset = 0) => (
    point.y + offset + Math.sin(time * 0.00038 + index * 0.72) * (compact ? 8 : 11)
  );
  const draw = (time, animate) => {
    ctx.clearRect(0, 0, width, height);
    ctx.lineWidth = 1;
    ctx.strokeStyle = 'rgba(255,255,255,0.018)';
    for (let x = 0; x < width; x += 32) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.stroke();
    }
    for (let y = 0; y < height; y += 32) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }

    ctx.strokeStyle = 'rgba(255,255,255,0.052)';
    for (let x = 0; x < width; x += 96) {
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, height);
      ctx.stroke();
    }
    for (let y = 0; y < height; y += 96) {
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }

    ctx.save();
    ctx.setLineDash([5, 9]);
    for (const [ratio, tone] of [[0.33, 'cyan'], [0.82, 'gold']]) {
      ctx.beginPath();
      ctx.strokeStyle = color(tone, 0.075);
      ctx.moveTo(0, height * ratio);
      ctx.lineTo(width, height * ratio);
      ctx.stroke();
    }
    ctx.restore();

    const fill = ctx.createLinearGradient(0, height * 0.5, 0, height);
    fill.addColorStop(0, 'rgba(99,189,245,0.11)');
    fill.addColorStop(1, 'rgba(99,189,245,0)');
    ctx.beginPath();
    points.forEach((point, index) => {
      const y = signalY(point, index, animate ? time : 0);
      if (index === 0) ctx.moveTo(point.x, y);
      else ctx.lineTo(point.x, y);
    });
    ctx.lineTo(width, height);
    ctx.lineTo(0, height);
    ctx.closePath();
    ctx.fillStyle = fill;
    ctx.fill();

    const volumeBase = height * (compact ? 0.74 : 0.86);
    const volumeScale = compact ? 18 : 34;
    points.forEach((point, index) => {
      const phase = Math.sin((animate ? time : 0) * 0.00055 + index * 1.37);
      const barHeight = 7 + ((index * 13) % 9) + (phase + 1) * volumeScale * 0.32;
      const barWidth = Math.max(2, Math.min(compact ? 5 : 8, width / points.length * 0.18));
      ctx.fillStyle = color(index % 5 === 0 ? 'gold' : index % 3 === 0 ? 'mint' : 'cyan', 0.1 + (phase + 1) * 0.025);
      ctx.fillRect(point.x - barWidth / 2, volumeBase - barHeight, barWidth, barHeight);
    });

    for (const [offset, alpha, tone] of [[-38, 0.15, 'gold'], [30, 0.17, 'mint'], [0, 0.56, 'cyan']]) {
      ctx.beginPath();
      points.forEach((point, index) => {
        const y = signalY(point, index, animate ? time : 0, offset);
        if (index === 0) ctx.moveTo(point.x, y);
        else ctx.lineTo(point.x, y);
      });
      ctx.strokeStyle = color(tone, alpha);
      ctx.lineWidth = offset === 0 ? 1.55 : 0.85;
      ctx.stroke();
    }

    points.forEach((point, index) => {
      if (index === 0 || index === points.length - 1 || index % 2 !== 0) return;
      const y = signalY(point, index, animate ? time : 0);
      const nextY = signalY(points[index + 1], index + 1, animate ? time : 0);
      const rising = nextY < y;
      ctx.beginPath();
      ctx.strokeStyle = color(rising ? 'mint' : 'gold', 0.22);
      ctx.moveTo(point.x, y - 10);
      ctx.lineTo(point.x, y + 10);
      ctx.stroke();
      ctx.fillStyle = color(rising ? 'mint' : 'gold', 0.18);
      ctx.fillRect(point.x - 2, y - 3, 4, 7);
    });

    points.forEach((point, index) => {
      if (index % 3 !== 0) return;
      const pulse = 0.55 + Math.sin((animate ? time : 0) * 0.0014 + index) * 0.2;
      const y = signalY(point, index, animate ? time : 0);
      ctx.beginPath();
      ctx.fillStyle = color(point.tone, pulse);
      ctx.arc(point.x, y, index % 6 === 0 ? 2.6 : 1.8, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.strokeStyle = color(point.tone, 0.11);
      ctx.arc(point.x, y, 7 + pulse * 3, 0, Math.PI * 2);
      ctx.stroke();
    });

    if (points.length > 1) {
      const cursor = ((animate ? time : 0) / 10500) % 1 * (points.length - 1);
      const index = Math.min(points.length - 2, Math.floor(cursor));
      const progress = cursor - index;
      const start = points[index];
      const end = points[index + 1];
      const x = start.x + (end.x - start.x) * progress;
      const startY = signalY(start, index, animate ? time : 0);
      const endY = signalY(end, index + 1, animate ? time : 0);
      const y = startY + (endY - startY) * progress;
      const beam = ctx.createLinearGradient(x - 34, 0, x + 34, 0);
      beam.addColorStop(0, 'rgba(126,225,181,0)');
      beam.addColorStop(0.5, 'rgba(126,225,181,0.055)');
      beam.addColorStop(1, 'rgba(126,225,181,0)');
      ctx.fillStyle = beam;
      ctx.fillRect(x - 34, Math.max(0, y - 150), 68, 300);
      ctx.beginPath();
      ctx.strokeStyle = 'rgba(126,225,181,0.2)';
      ctx.moveTo(x, Math.max(0, y - 140));
      ctx.lineTo(x, Math.min(height, y + 140));
      ctx.stroke();
      ctx.beginPath();
      ctx.fillStyle = 'rgba(126,225,181,0.95)';
      ctx.arc(x, y, 3, 0, Math.PI * 2);
      ctx.fill();
    }
  };
  const tick = (time) => {
    if (!isActive() || reduced.matches) {
      running = false;
      frame = 0;
      if (!isActive()) ctx.clearRect(0, 0, width, height);
      else draw(0, false);
      return;
    }
    draw(time, true);
    frame = window.requestAnimationFrame(tick);
  };
  const sync = () => {
    const active = isActive();
    if (!active && frame) window.cancelAnimationFrame(frame);
    if (!active) {
      running = false;
      frame = 0;
      ctx.clearRect(0, 0, width, height);
      return;
    }
    if (reduced.matches) {
      if (frame) window.cancelAnimationFrame(frame);
      running = false;
      frame = 0;
      draw(0, false);
      return;
    }
    if (!running) {
      running = true;
      frame = window.requestAnimationFrame(tick);
    }
  };
  resize();
  window.addEventListener('resize', () => {
    resize();
    if (isActive()) draw(0, false);
  }, { passive: true });
  document.addEventListener('visibilitychange', sync);
  reduced.addEventListener('change', sync);
  new MutationObserver(sync).observe(document.body, { childList: true, subtree: true });
  sync();
})();
</script>
""",
        unsafe_allow_javascript=True,
    )


def page_heading(kicker: str, title: str, description: str, meta: str = "") -> None:
    meta_html = f'<span class="page-meta">{html.escape(meta)}</span>' if meta else ""
    st.html(
        '<header class="page-heading"><div class="page-title-block">'
        f'<span class="page-kicker">{html.escape(kicker)}</span><h1>{html.escape(title)}</h1>'
        f'<p>{html.escape(description)}</p></div>{meta_html}</header>'
    )


def experience_hero(
    kicker: str,
    title: str,
    description: str,
    meta: str,
    steps: Iterable[tuple[str, str]],
) -> None:
    """Shared product scene for research, roadmap, and help surfaces."""
    nodes = "".join(
        '<article class="experience-node" role="listitem">'
        f'<span>{index:02d}</span><strong>{html.escape(label)}</strong>'
        f'<small>{html.escape(detail)}</small></article>'
        for index, (label, detail) in enumerate(steps, start=1)
    )
    st.html(
        '<header class="experience-hero"><div class="experience-copy">'
        f'<span class="page-kicker">{html.escape(kicker)}</span><h1>{html.escape(title)}</h1>'
        f'<p>{html.escape(description)}</p><b>{html.escape(meta)}</b></div>'
        f'<div class="experience-map" role="list" aria-label="{html.escape(title)}流程">{nodes}</div></header>'
    )


def section_label(title: str, meta: str = "") -> None:
    st.html(
        f'<section class="section-label"><h2>{html.escape(title)}</h2>'
        f'<span>{html.escape(meta)}</span></section>'
    )


def brand_bar(paused: bool, market_live: bool | None = None) -> None:
    control = "暂停开仓" if paused else "风险控制正常"
    control_class = "danger" if paused else "success"
    market = "行情在线" if market_live is True else "行情连接中" if market_live is None else "行情离线"
    market_class = "success" if market_live is True else "warning" if market_live is None else "danger"
    community_url = _telegram_community_url()
    st.html(
        '<header class="brand-bar" role="status" aria-live="polite" aria-atomic="true">'
        '<div class="brand-lockup"><b class="brand-mark" aria-hidden="true">C<i>T</i></b>'
        '<div><strong>CicloTrade</strong><small>QUANT RESEARCH TERMINAL</small></div></div>'
        '<div class="brand-status">'
        + (f'<a class="chip" href="{html.escape(community_url, quote=True)}" target="_blank" rel="noopener noreferrer">Telegram 客服</a>' if community_url else '<span class="brand-link-disabled">Telegram 未配置</span>')
        + f'<span class="chip {market_class}">{market}</span>'
        '<span class="chip warning">模拟组合</span>'
        f'<span class="chip {control_class}">{control}</span></div></header>'
    )


def market_tape(items: Iterable[dict], updated_at: datetime, source: str = "数据源未指定") -> None:
    cells = []
    for item in items:
        change = float(item["change"])
        tone = "positive" if change >= 0 else "negative"
        sign = "+" if change >= 0 else ""
        cells.append(
            '<div class="tape-cell">'
            f'<span>{html.escape(str(item["symbol"]))}</span><strong>{float(item["price"]):,.2f}</strong>'
            f'<b class="{tone}">{sign}{change:.2%}</b></div>'
        )
    cells.append(
        f'<div class="tape-cell tape-source"><span>数据源</span><strong>{html.escape(source.upper())}</strong>'
        f'<b>{html.escape(updated_at.strftime("%H:%M %Z"))}</b></div>'
    )
    st.html('<section class="market-tape" aria-label="市场行情摘要">' + "".join(cells) + "</section>")


def metric_grid(items: Iterable[tuple[str, str, str, str]]) -> None:
    cells = []
    for label, value, detail, tone in items:
        tone_class = f" {tone}" if tone else ""
        cells.append(
            f'<article class="metric-card"><span>{html.escape(label)}</span>'
            f'<strong class="{tone_class.strip()}">{html.escape(value)}</strong>'
            f'<small>{html.escape(detail)}</small></article>'
        )
    st.html('<section class="metric-grid">' + "".join(cells) + "</section>")


def gauge(label: str, value: float, sub: str, color: str) -> None:
    clamped = max(0.0, min(100.0, float(value)))
    st.html(
        f'<article class="gauge" role="img" aria-label="{html.escape(label)} {clamped:.0f}%，{html.escape(sub)}">'
        f'<div class="gauge-ring" style="--value:{clamped * 3.6:.1f}deg;--accent:{html.escape(color)}">'
        f'<strong>{clamped:.0f}<small>%</small></strong></div>'
        f'<div><b>{html.escape(label)}</b><span>{html.escape(sub)}</span></div></article>'
    )


def terminal(logs: Iterable[Sequence[str]]) -> None:
    rows = []
    for time, level, component, message in logs:
        rows.append(
            f'<div class="terminal-row"><time>[{html.escape(time)}]</time>'
            f'<b class="level-{level.lower()}">{html.escape(level)}</b>'
            f'<span>[{html.escape(component)}]</span><p>{html.escape(message)}</p></div>'
        )
    if not rows:
        rows.append('<p class="terminal-empty">没有符合条件的日志。</p>')
    st.html('<section class="terminal" role="log" aria-live="polite" aria-label="系统日志">' + "".join(rows) + "</section>")


def disclaimer() -> None:
    st.html(
        '<aside class="disclaimer" aria-label="风险提示"><strong>风险披露</strong>'
        '<p>行情新鲜度以页面数据源状态为准。策略损益仅为到期教学模型，不包含波动率、滑点、佣金与税费，不构成投资建议。'
        '期权可能损失全部权利金；玄学参考仅供娱乐。</p></aside>'
    )
