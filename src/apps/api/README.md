# CicloTrade Rewrite API

The browser compatibility API preserves the legacy database identity, membership, quant, risk, alert, and billing-order contracts while the CicloTrade React application is introduced.

Run from the repository root:

```powershell
python -m uvicorn src.apps.api.app:app --host 127.0.0.1 --port 8001
```

Core browser routes use `/api/rewrite/v1`:

- session login, refresh, and logout
- authenticated bootstrap, identity, membership, Telegram status, recommendations, timeline, performance, and the read-only CicloTrade official validation portfolio
- risk settings, Telegram preferences, price alerts, watchlists, and pending one-time membership orders
- governed market search and K-line reads

Canonical compatibility reads use SQLite read-only mode. Market access additionally requires `MARKET_DATA_ENABLED`; a provider is never labeled real-time unless its adapter and deployment both declare real-time authorization.

This API does not accept personal simulated orders, send Telegram messages, capture payments, submit live orders, publish models, or switch production routes.
