# TradeAI Interface Design Contract

## Direction

TradeAI is a dense professional market workspace, not a form dashboard and not a decorative finance landing page. Real quotes, charts, recommendations, risk, positions, and delivery state provide the visual proof.

## Visual grammar

| Token | Value |
| --- | --- |
| canvas | `#0b0e12` |
| workspace | `#12161c` |
| raised surface | `#181d24` |
| border | `#29313b` |
| primary text | `#f2f4f7` |
| muted text | `#98a2ae` |
| brand accent | `#d6a36a` |
| positive | `#27b487` |
| negative | `#e4606b` |
| warning | `#e3aa48` |
| information | `#4c8dff` |

Use 3-6px radii, one-pixel structural borders, restrained shadows, and no decorative gradient/orb layer. Use a neutral sans face for language and tabular/monospace figures for prices, timestamps, event IDs, and quantities.

US and A-share price colors follow the selected market convention by default, but every direction also includes a sign, arrow, or word. Color is never the only signal.

## Navigation

Desktop primary navigation:

1. Today
2. Markets
3. Portfolio
4. Trade
5. Reports
6. Notifications
7. Account

Mobile bottom navigation is Today, Markets, Trade, Portfolio, and More. More contains Reports, Notifications, Membership, Settings, Help, and Mystic. Admin routes are separate.

## Desktop market workspace

```text
top bar: global search | market/data status | Telegram | membership/account
left 216px: primary navigation + watchlist
center: instrument summary + financial chart + evidence tabs
right 336px: official action, order ticket, alert, or inspector
bottom: positions | orders | fills | signal timeline | connection state
```

The right inspector changes with the user's current task. It is not a permanent form. Market, symbol, and timeframe are shared URL state across Today, Markets, and Trade.

## Core surfaces

- Today: official next action first, then owned-position actions, research candidates, and history.
- Markets: watchlist, full chart, volume, signal markers, news/events, alerts, and options evidence.
- Portfolio: explicit account source, equity summary, positions, options, orders, and freshness.
- Trade: paper by default; stable ticket, risk checklist, confirmation, and order lifecycle.
- Reports: portfolio, strategy, backtest, and export views with coverage and sample-size states.
- Notifications: personal Telegram binding, event matrix, last delivery, errors, and recovery.
- Membership: current plan first; card-local period, one-time price, expiry, rights, and purchase.
- Mystic: editorial X/Threads source feed and AI summaries, visibly entertainment-only and isolated from scoring.

## Interaction rules

- high-frequency controls use tabs, segmented buttons, toolbars, tables, lists, sliders, and steppers
- selects are reserved for long or low-frequency option sets
- changing symbol, timeframe, or indicator does not rerender the whole page
- hover/focus feedback is 120-180ms; press feedback is 80-140ms
- transitions name exact properties; never use `transition: all`
- loading preserves geometry; stale data retains the last chart with a visible timestamp
- every surface defines loading, empty, stale, offline, locked, failed, pending, success, and unknown states
- icon-only buttons have an accessible name and tooltip
- controls are at least 44px on touch devices and all motion respects reduced-motion settings

## Recommendation presentation

Official and research recommendations must never share the same visual status. An action card shows action, instrument, entry context, stop/invalidation, target/scenario, maximum modeled loss, holding horizon, evidence, strategy/model version, event ID, source, and freshness.

Primary actions are View chart, View evidence, Set alert, Historical validation, and Paper verify. Live trade remains a separate gated path.
