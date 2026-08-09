# CicloTrade Release Candidate: Web + Telegram Billing Concurrency

Date: 2026-08-09
Status: ready for authorized deployment; not deployed

## Scope

This candidate covers the website and Telegram membership purchase paths. Both paths use the same order service and database, so a user cannot obtain two pending orders or two entitlements by opening both channels at once. Administrator receiving configuration is intentionally Telegram-only; the website has no admin upload form.

## Security contract

1. A pending order fingerprint is derived from plan, billing cycle, manual payment method, amount, entitlement days, and terms version. A repeated request returns the existing pending order.
2. `BEGIN IMMEDIATE` plus the partial unique index in migration `0010_manual_payment_concurrency.sql` closes the multi-process race.
3. An order accepts at most one submitted manual-payment claim. Repeated web/TG submits return the existing claim.
4. Web and Telegram screenshots are cleaned and re-encoded before storage. The SHA-256 digest is checked across both sources, and an active digest cannot be used for a different order.
5. Telegram downloads are bounded and use Telegram's official file endpoint. Proof files stay outside static/public/dist paths.
6. Finance approval checks the private file and digest again, canonicalizes the settlement reference (Unicode normalization, case, spaces, and separators), rejects reuse, and activates the order in one transaction.
7. FPS, Alipay, and WeChat orders reject provider callbacks. PayPal/Paddle callbacks require an explicitly `legacy` order source.
8. A billing administrator can configure FPS, Alipay, and WeChat text/ID, QR, or both only through `/payconfig` or the Telegram desk. Every write re-checks the `billing` permission inside the transaction.
9. Migration `0011_manual_payment_receivers.sql` stores the current profile and an immutable profile snapshot for each order, so later administrator changes affect new orders only.
10. Receiver QR images are cleaned, re-encoded, stored outside public web roots, and SHA-256 checked before delivery. The website endpoint requires authentication, ownership, pending status, and a non-expired order.

## Verification evidence

- full repository regression: `338 passed`
- focused payment/API/Telegram security regression: `111 passed`
- focused Telegram receiving/API regression: `64 passed`
- Telegram regression: `27 passed`
- frontend unit suite: `6 passed`
- frontend lint and production build: passed
- Ruff, pip check, and npm production audit: passed; `npm audit --omit=dev` reports 0 vulnerabilities
- migrations through 0011 validated through the repository suite; schema, indexes, order snapshots, and integrity checks passed
- API smoke on `127.0.0.1:8002`: health 200, `Cache-Control: no-store`, `X-Content-Type-Options: nosniff`, and proof request above 5 MB returns 413
- Playwright desktop/mobile proof upload: no horizontal overflow, no console errors, real image submission accepted
- Telegram receiving configuration: non-admin denial, text-only/QR-only/both states, cancellation, permission revocation, order snapshots, and `sendPhoto` validation passed in the focused suite
- website receiving QR: authenticated owner access passed; other-user and completed-order access were rejected

## Release contents

See `output/deploy/ciclotrade-20260809-telegram-receivers-final.MANIFEST.txt`. The package includes migration `0011_manual_payment_receivers.sql`, Telegram-only receiving configuration, private QR storage, authenticated website QR delivery, and the current React `dist`. It intentionally excludes `.env`, credentials, databases, private proof files, and build caches.

## Deployment gate

Do not activate this release until the operator verifies a database backup, valid SSH deploy permission, receiving instructions/QR assets for FPS/Alipay/WeChat, and a finance reviewer account. No production upload or service reload has been performed in this workspace.

## Rollback

1. Stop the new API service and restore the previous application bundle.
2. Restore the pre-migration database backup if the migration has to be reverted; do not delete migration rows from a live database without a backup and an explicit operator decision.
3. Confirm `/api/health`, login, membership order history, and the legacy payment/webhook routes before reopening traffic.
