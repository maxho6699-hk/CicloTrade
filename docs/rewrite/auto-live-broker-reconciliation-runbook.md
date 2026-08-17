# Auto-live Broker Reconciliation Runbook

## Purpose

Recover an auto-live order whose latest submission receipt is `submission_unknown` by querying the broker **without resending the order**. The runtime writes immutable evidence and only clears the opening gate after a matching reconciliation receipt exists.

## Safety invariants

- Reconciliation is read-only at the broker boundary. `TigerOrderObservationSource` calls `orders()` only and has no send, cancel, replace, or retry operation.
- `TIGER_REAL_TRADING_ENABLED` is not set or changed by the runtime, service, timer, migration, or runbook.
- The runtime is disabled unless both gates are present:
  1. `CICLO_AUTO_LIVE_RECONCILIATION_ENABLED=true` in the protected environment file.
  2. `/etc/ciclotrade/enable-auto-live-reconciliation.after-integration` exists.
- The systemd unit is `Type=oneshot`; the timer is non-persistent and processes at most `CICLO_AUTO_LIVE_RECONCILIATION_LIMIT` items per run (1–500, default 100).
- An unresolved or unrecognized broker status remains `submission_unknown`; opening stays blocked while cancellation, exposure reduction, and closing remain available.
- A known projection cannot clear a historical unknown unless receipt ID, mandate, client order, state, broker order ID, observed time and payload hash all match an append-only broker reconciliation receipt.
- Broker lookup is bound to a SHA-256 fingerprint of the mandate's configured external account. Tiger verifies the configured account fingerprint before calling `get_orders`.
- The current runtime advertises only the `tiger` read source; non-Tiger intents are excluded instead of being polled forever.
- Receipt history is never rewritten or deleted.

## Data model

Migration `0048_auto_live_broker_reconciliation.sql` adds:

- `auto_live_broker_reconciliation_receipts`: immutable normalized broker observations, including a non-reversible broker-account fingerprint.
- Unique evidence binding on `(intent_public_id, evidence_sha256)` for idempotent polling.
- Owner/intent consistency trigger binding receipt mandate and client order to the original intent.

Each accepted observation appends:

1. A broker reconciliation receipt with canonical payload hash.
2. A public order receipt projection (broker order ID excluded from owner snapshot).
3. An intent event: `submission_unknown`, `accepted`, `rejected`, `cancelled`, or `reconciled`.

The safety gate and owner snapshot use the latest receipt per `client_order_id`; older receipts remain in the audit ledger.

## Local verification

```bash
PYTHONPATH=. uv run --with-requirements requirements.txt pytest -q \
  tests/test_auto_live_broker_reconciliation.py \
  src/apps/worker/tests/test_auto_live_reconciliation_runtime.py

uv run --with-requirements requirements.txt ruff check \
  core/auto_live_broker_reconciliation.py \
  core/auto_live_runtime_integrity.py \
  core/auto_live_control_snapshot.py \
  trading/tiger_reconciliation.py \
  src/apps/worker/auto_live_reconciliation_runtime.py
```

## One bounded manual run

Only after broker read access, database backup, migration verification, and an approved unknown receipt fixture:

```bash
CICLO_AUTO_LIVE_RECONCILIATION_ENABLED=true \
CICLO_AUTO_LIVE_RECONCILIATION_LIMIT=25 \
/opt/CicloTrade/.venv/bin/python -m src.apps.worker.auto_live_reconciliation_runtime --once
```

The command prints counts only:

```json
{"failed":0,"resolved":1,"status":"completed","total":1,"unresolved":0}
```

It does not print account IDs, broker order IDs, credentials, tokens, or raw broker payloads.

## Systemd templates

- `ops/ciclotrade-auto-live-reconciliation.service`
- `ops/ciclotrade-auto-live-reconciliation.timer`

The timer intentionally has no `[Install]` section, so ordinary `systemctl enable` cannot activate it. Activation requires an explicit reviewed timer link/drop-in, the protected environment flag, and the marker file.

## Rollback

1. Disable and stop the timer/service.
2. Remove the marker file.
3. Set `CICLO_AUTO_LIVE_RECONCILIATION_ENABLED=false`.
4. Leave receipt and event ledgers intact for audit and incident review.
5. Keep all unknown submissions blocked; never retry an order to “repair” reconciliation.
