# CicloTrade Rewrite Wave 3: Interface and Billing Completion

Date: 2026-08-09
Status: release candidate validated locally; production cutover is pending authorized server access

## Delivered

- global typography baseline increased to 16px with no visible operational copy below 12px
- 24px mobile and 28px desktop page titles, 19-22px action headings, and larger tabular prices
- 44px touch targets at tablet and mobile breakpoints
- authenticated data mapping for Today, Portfolio, Reports, Notifications, Account, and Membership
- explicit loading, demo, real-record, empty, stale-history, and offline states
- keyboard command search, local watchlist search, mobile US/A-share switching, and URL-persisted symbol/timeframe/evidence state
- chart volume/grid settings, dynamic accessibility labels, market-specific colors, and nonblank Canvas rendering
- paper-trade `Buy` and `Sell` markers, weighted-average commission-inclusive trade intervals, explicit realized/estimated P/L, desktop hover/lock, and mobile same-marker toggle behavior
- responsive chart reflow after a 700px-to-1440px resize without page overflow or right-side blank space
- governed market search and K-line API adapters for US equities and A-shares; external access stays disabled until platform configuration enables it
- two-step paper-order confirmation, real risk-limit display, post-write refresh, membership order history, Telegram status checks, and honest unavailable states for password/broker migration
- Mystic editorial preview is explicitly non-live and remains isolated from recommendation scoring
- website and Telegram membership checkout share one order fingerprint and the same pending-order state machine
- pending manual-payment orders have a database uniqueness guard across web and Telegram processes
- one order can have only one submitted claim; web and Telegram proof uploads share a cleaned-image SHA-256 duplicate guard
- Telegram files are downloaded through the official file endpoint with bounded size and the same private proof storage/cleaning pipeline as web uploads
- finance approval re-checks proof existence and SHA-256, normalizes settlement references, and grants entitlements in the same transaction
- manual FPS/Alipay/WeChat orders reject provider callbacks; legacy PayPal/Paddle callbacks are isolated to explicitly legacy orders

## Acceptance evidence

- `npm run build`: passed
- `npm run lint`: passed
- `python -m ruff check src/apps/api src/apps/worker`: passed
- focused API and worker suite: 43 passed
- chart projection unit suite: 2 passed
- complete repository suite: 338 passed
- focused Telegram receiving/API suite: 64 passed
- `python -m pip check`: passed
- `npm audit --omit=dev`: zero vulnerabilities
- Ruflo deep scan of `src/apps`: zero findings
- Playwright at 390, 768, 1440, and 1920px across all nine routes: no horizontal overflow, no console errors, and no visible text below 12px
- authenticated response simulation: identity, recommendation, position, performance, Telegram, and membership data appeared on the correct pages
- A-share mobile flow: market tab visible, URL state correct, accessible chart label updated, and Canvas contained colored pixels
- local browser acceptance at 390px and 1440px: no page-level horizontal overflow and no console warnings or errors
- local resize acceptance at 700px then 1440px: document and workspace returned to the full viewport width and the chart Canvas resized with its host
- local API smoke acceptance on port 8002: health 200, no-store responses, nosniff header, and oversized proof request rejected with 413
- migration validation from 0001 through 0011: registration, schema/index checks, and `PRAGMA integrity_check=ok`

## Previous production receipt (historical)

- public origin: `https://ciclotrade.com`
- release artifact: `ciclotrade-rewrite-20260809-1445.tar.gz`
- SHA-256: `043c0b6c4ad338d75b53fed7fa54e347754d7b0a93ad03a61793ed00ed23aca5`
- React entry assets: `index-BBqJ7GEe.js` and `index-Bo3Ne36Q.css`
- React compatibility API: `ciclotrade-rewrite-api.service`, loopback port 8001
- legacy Streamlit/API service remains active on loopback port 8501 for existing API, payment, webhook, and operations-console routes
- Nginx only assigns the explicit React route allowlist and `/assets/` to the new application; unclaimed routes continue to the legacy service
- pre-cutover backup: `/opt/CicloTrade/.deploy-backups/20260809-1425`
- public smoke checks: React root and `/today` 200, rewrite health 200 JSON, legacy health 200 JSON, JavaScript and CSS 200 with correct MIME types

The historical artifact above predates the current billing-concurrency release and must not be used for the next cutover.

## Current release candidate

- artifact: `output/deploy/ciclotrade-20260809-telegram-receivers-final.tar.gz`
- manifest: `output/deploy/ciclotrade-20260809-telegram-receivers-final.MANIFEST.txt`
- SHA-256: recorded beside the artifact after packaging
- status: built and locally validated; not uploaded or activated on the production server
- required production inputs: valid SSH account/key with deploy rights, FPS receiving details, Alipay receiving instructions/QR, WeChat receiving instructions/QR, and a production database backup
- deployment order: run `python ops/scripts/verify_release_safety.py --artifact <candidate.tar.gz> --manifest <candidate.MANIFEST.txt>` locally; record the read-only receipt below; back up database and current release; apply migrations 0009 through 0011; install Python dependencies and React dist; then perform the one approved Rewrite API restart and run the manifest smoke checklist. OpenD, FutuOpenD, `futu-opend.service`, reload variants, and every other service lifecycle action are prohibited.

## Zero-touch OpenD release gate (P0)

The static gate reads only tracked release-surface files (`ops`, `config`, and every `docs/rewrite` file) plus an optional candidate tar/zip. It never connects to production, starts/stops/reloads a service, or prints IPs, accounts, or secrets. Repository-owned `ops/opend/` source is intentionally excluded from static source scanning because it contains legitimate control implementation; it is always forbidden in a release artifact.

Before packaging and after unpacking, retain this read-only receipt contract for the single permitted website service. Collection is performed by the authorized deployment operator with existing read-only tooling; the gate only defines the comparison and does not collect it.

```json
{"schema":"ciclotrade.release-readonly-receipt.v1","service":"ciclotrade-rewrite-api.service","allowed_action":"restart","read_only_fields":["MainPID","ActiveEnterTimestamp","QOTRIGHT"],"forbidden_fields":["host","ip","account","secret","token"]}
```

Record pre/post values verbatim, compare them as part of the release receipt, and do not attach connection information or credentials. A gate rejection blocks the cutover; it is never overridden by a reload, restart, migration, relogin, kill, or lifecycle action for OpenD/FutuOpenD or any unlisted unit.

## External gates still closed

- real X/Threads ingestion needs official platform authorization and credentials
- live market data needs `MARKET_DATA_ENABLED` plus an approved provider configuration
- Telegram delivery, live brokerage, and password migration remain on their existing governed paths and require separate feature authority
- manual FPS/Alipay/WeChat approval is implemented but remains closed until receiving instructions and the finance approval operator are configured
- administrator receiving configuration is Telegram-only: `/payconfig` (or the Telegram desk's `🏦 收款资料管理`) controls FPS, Alipay, and WeChat text instructions/IDs and private QR images; the website intentionally has no administrator upload form
- each receiving method may have text only, QR only, or both; changing the current profile affects new orders only because each order stores an immutable receiving-profile snapshot
- customer checkout on the website and Telegram displays only configured methods, sends the Telegram QR when present, and keeps website QR delivery behind an authenticated, owner-checked, pending-order API
- automatic model cycles may reach Shadow only; independent approval cannot activate or deploy them automatically
