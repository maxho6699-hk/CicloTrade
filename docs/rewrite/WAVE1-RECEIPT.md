# TradeAI Rewrite Wave 1: Read-only Preview Foundation

Date: 2026-08-09
Status: completed as a read-only preview foundation, not a finished product release

## Delivered

- `src/apps/web`: responsive React/Vite terminal shell with Today, Markets, Portfolio, Trade, Reports, Notifications, Account, Membership, and Mystic routes
- `src/apps/web/src/components/MarketChart.tsx`: canvas financial chart with candles, volume, timeframe controls, market-specific direction colors, and accessible chart label
- `src/apps/api`: read-only health and capability facade; no user, payment, Telegram, model promotion, or trading side effects
- `src/apps/worker/quant_learning.py`: explicit stock/option challenger lifecycle and independent promotion gates
- `src/packages/contracts`: recommendation and model-version JSON Schemas
- `docs/rewrite`: architecture, design, and governed quant-learning contracts

## Verification

- `npm run build` in `src/apps/web`: passed
- `npm run lint` in `src/apps/web`: passed
- `python -m pytest -q src/apps/api/tests src/apps/worker/tests`: 11 passed
- `python -m pytest -q`: 249 passed
- `python -m pip check`: passed
- JSON Schema parse checks: passed
- Playwright desktop checks: Today and Markets at 1440px; chart rendered and no console errors after favicon fix
- Playwright mobile checks: Today, Markets, Membership, and Mystic at 390px; document width remained 390px; Markets rendered 7 chart canvas layers at 430px chart height
- Hover check: timeframe control received state feedback without layout shift

## Safety boundary

The legacy Streamlit/ASGI application remains the canonical business system. No existing core, payment, Telegram, admin, or test file was overwritten by this wave. Demo values in the new web preview are labeled `DEMO DATA` and cannot submit orders, send notifications, create payments, or promote a model.

## What "Wave 1 complete" means

Only the visual shell, route structure, design contract, read-only capability declaration, and governed worker prototypes were complete at this point. It did not mean authenticated customer data, protected writes, production integrations, or the full rewrite were complete.

## Next gate

Build authenticated compatibility repositories and contract tests for `/me`, recommendations, quant timeline, memberships, and Telegram status before adding any write path. The next learning wave must add point-in-time feature snapshots, walk-forward evaluation, stress-cost accounting, drift monitoring, and a signed promotion receipt.
