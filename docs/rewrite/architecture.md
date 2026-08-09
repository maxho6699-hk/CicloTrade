# TradeAI Full Rewrite Architecture

Status: accepted direction, implementation contract

## Product boundary

The rewrite replaces the Streamlit presentation and application delivery layers while preserving existing business behavior and data. The initial supported markets remain US equities and A-shares. Hong Kong equities and crypto are extension points, not visible empty products.

The following capabilities remain first-class:

- official buy, add, hold, reduce, exit, correction, and reversal events
- research candidates that are visually distinct from official actions
- paper trading by default and separately gated live trading
- Telegram stock, option, alert, order, risk, and membership notifications
- five membership levels and non-recurring purchases
- strategy generation, backtesting, reports, audit, risk controls, and broker gates
- the entertainment-only Mystic area, including future X and Threads editorial ingestion
- versioned stock and option recommendation models with governed iterative learning

## Target shape

```text
apps/web       React application and responsive trading workspace
apps/api       Versioned browser API and compatibility facade
apps/worker    Scheduler, outbox, evaluation, and model-training jobs
packages       Typed contracts, shared domain types, and design primitives
domains        Identity, membership, billing, quant, research, risk, trading,
               notifications, mystic, and audit application modules
adapters       Legacy SQLite, market data, brokers, payment providers, Telegram
infra          Migrations, observability, deployment, backup, and reconciliation
legacy         Streamlit compatibility entry and old-route redirects
```

The target begins as an API-first modular monolith with a separate worker. It does not start as a microservice fleet.

## Canonical data during migration

SQLite remains canonical until each write domain passes reconciliation. Compatibility reads stay read-only against legacy repositories; the small approved write set continues through legacy authorization and risk services. The rewrite must not change existing primary keys, password hashes, order numbers, provider event IDs, Telegram identities, quant event IDs, or payload hashes.

The migration baseline is an immutable source-and-database snapshot, not Git HEAD alone. The current worktree contains user-owned uncommitted changes.

## Non-breakable contracts

- JWT/session validation, refresh rotation, logout, email verification, rate limits, and IP rules
- exact plan names, capability aliases, expiry downgrade, pricing, rewards, and referral behavior
- payment order state, terms version, provider verification, callback idempotency, and reversal rollback
- quant append-only events, legs, corrections, reversals, snapshots, hashes, and deterministic replay
- paper/live order status, risk decision codes, pause controls, broker gates, and audit records
- Telegram consent, verification, event toggles, plan delays, quotas, receipts, outboxes, and retries
- Mystic observations and their user ownership; Mystic never contributes to a trading score
- all user, system, risk, membership, strategy, and administrative audit trails

## Migration waves and current state

1. `Completed - preview foundation`: freeze the rewrite contract and build the responsive shell without production side effects.
2. `Completed - compatibility`: authenticate through the legacy rules and expose customer-owned membership, Telegram, quant, portfolio, settings, alert, and paper-order contracts.
3. `Completed - interface integration`: bind authenticated data to the new pages, complete safe interactions, add governed market-data adapters, and pass four-viewport acceptance.
4. `Pending independent release gate`: move scheduler and Telegram outboxes to a single leased worker without duplicate external sends.
5. `Pending independent release gate`: move payment capture and callbacks behind parity, replay, and reconciliation tests. The new UI currently creates pending one-time orders only.
6. `Pending independent release gate`: cut over paper trading and risk after production decision-code reconciliation. Live trading remains legacy-gated and disabled in the new UI.
7. `Future infrastructure decision`: migrate to PostgreSQL with dual-read/write reconciliation, then retire Streamlit routes.

Each wave must have a route flag and rollback path. No live-trade, payment, model-promotion, or external-message cutover is implicit.

## Current interface acceptance

- the old application continues to run unchanged
- new web routes cover Today, Markets, Portfolio, Trade, Reports, Notifications, Membership, and Mystic
- 390px, 768px, 1440px, and 1920px layouts do not overlap or overflow
- recommendation cards expose official/research status, provenance, version, timestamp, and data freshness
- contract tests prove new read APIs preserve authorization and response semantics
- authenticated pages distinguish canonical records, historical snapshots, stale data, offline state, empty state, and interface demonstrations
- the learning worker can train and evaluate in shadow mode but cannot publish or trade
- no live trade, Telegram message, payment capture, model promotion, X/Threads collection, or production cutover occurs implicitly
