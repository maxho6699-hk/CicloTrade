# System-cycle research deployment runbook

## Scope and safety boundary

This runbook deploys one signed, research-only path from the isolated strategy
node to the CicloTrade website. It is not an order-routing, Telegram, official
recommendation, broker, OpenD, or option-execution deployment.

The producer evaluates exactly the canonical **13 system stocks** on daily
research data and creates at most one record per New York time slot. The
website stores the result as `research_only` / `shadow`. It must not appear as
an actionable opportunity or cause an order, official simulation, Telegram
message, or OpenD change. The producer currently uses its own research data
adapter; it neither accesses nor relocates the website's OpenD installation.

The website receiver and strategy-node spool use different SQLite databases.
Never mount the strategy filesystem on the website and never reuse the website
product database or the backtest queue database for this feature.

## Prerequisites

1. An approved, tested release commit has been integrated and pushed. Deploy
   that exact commit on both nodes; do not deploy an arbitrary branch tip.
2. The website's rewrite API and Nginx are healthy before receiver activation.
3. The strategy node is Ubuntu 22.04 with Python 3.10, `python3.10-venv`, Git,
   and a `cicloworker` service user. Do not run the worker as root.
4. The approved release has passed focused receiver, producer, publisher, and
   read-model tests, plus the website build and deployment checks.
5. Create one independent random shared secret of at least 32 bytes. It must
   not be a JWT, payment, broker, OpenD, Telegram, or backtest secret.

Do not put secrets in shell history, command arguments, Git, ticket text,
screenshots, or chat. Use `sudoedit` to enter them directly into root-owned
environment files. Never print these files with `cat`, `sed`, `systemctl show`,
or diagnostic commands.

## Website receiver deployment

1. Back up the current release and product database using the existing website
   deployment procedure. Record the approved release commit and backup path in
   the private release receipt, not in this repository.
2. Deploy the approved release, including the rewrite API source, then create
   the dedicated parent directory with the API service account's ownership:

   ```bash
   sudo install -d -o ciclotrade -g ciclotrade -m 0750 /var/lib/ciclotrade
   ```

3. Use `sudoedit /opt/CicloTrade/.env` and add only these receiver settings:

   ```ini
   TRADEAI_SYSTEM_CYCLE_RESEARCH_RECEIVER_ENABLED=true
   TRADEAI_SYSTEM_CYCLE_RESEARCH_DATABASE=/var/lib/ciclotrade/system-cycle-research.db
   TRADEAI_SYSTEM_CYCLE_RESEARCH_SHARED_SECRET=
   ```

   Enter the secret value in the editor only. The database path must be an
   absolute, website-local path and must differ from `DATABASE_URL` and
   `TRADEAI_BACKTEST_DATABASE_URL`.

4. Restart only the rewrite API and verify it came back healthy. A malformed
   receiver configuration prevents the API from starting, which is intentional
   fail-closed behavior.

   ```bash
   sudo systemctl restart ciclotrade-rewrite-api.service
   sudo systemctl is-active --quiet ciclotrade-rewrite-api.service
   sudo nginx -t
   ```

   Nginx already proxies `/api/rewrite/` to this API. Do not expose a new port,
   alter OpenD, or create a public webhook route.

## Strategy-node installation

All commands below use placeholders. Substitute only the already-approved
Git remote and release commit. Neither placeholder is a secret.

```bash
release_sha=<approved-release-sha>
release_dir=/opt/ciclotrade-worker/releases/$release_sha
sudo install -d -o cicloworker -g cicloworker -m 0755 /opt/ciclotrade-worker/releases
sudo install -d -o cicloworker -g cicloworker -m 0750 /var/lib/ciclotrade-worker
sudo install -d -o cicloworker -g cicloworker -m 0750 /var/log/ciclotrade-worker
sudo -u cicloworker git clone <approved-git-remote> "$release_dir"
sudo -u cicloworker git -C "$release_dir" checkout --detach "$release_sha"
sudo -u cicloworker python3.10 -m venv "$release_dir/.venv"
sudo -u cicloworker "$release_dir/.venv/bin/python" -m pip install --upgrade pip
sudo -u cicloworker "$release_dir/.venv/bin/python" -m pip install -r "$release_dir/requirements.txt"
sudo -u cicloworker "$release_dir/.venv/bin/python" -m pip check
```

Run the approved focused test commands from the exact release after dependency
installation. Do not enable any service if `pip check` or a focused test fails.
Only after those checks pass, switch `current` to the immutable release:

```bash
sudo ln -sfnT "$release_dir" /opt/ciclotrade-worker/current
```

Install the two systemd units from that exact release and reload systemd:

```bash
sudo install -m 0644 /opt/ciclotrade-worker/current/ops/ciclotrade-system-cycle-producer.service /etc/systemd/system/
sudo install -m 0644 /opt/ciclotrade-worker/current/ops/ciclotrade-system-cycle-producer.timer /etc/systemd/system/
sudo install -m 0644 /opt/ciclotrade-worker/current/ops/ciclotrade-system-cycle-publisher.service /etc/systemd/system/
sudo install -m 0644 /opt/ciclotrade-worker/current/ops/ciclotrade-system-cycle-publisher.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemd-analyze verify /etc/systemd/system/ciclotrade-system-cycle-producer.service /etc/systemd/system/ciclotrade-system-cycle-publisher.service
```

Both units intentionally stay disabled until the end-to-end acceptance below.

## Root-only worker configuration

Create the directory and copy templates without placing a secret on a command
line. Then edit the two files with `sudoedit`.

```bash
sudo install -d -o root -g root -m 0750 /etc/ciclotrade-worker
sudo install -m 0600 -o root -g root /opt/ciclotrade-worker/current/config/system-cycle-producer.env.example /etc/ciclotrade-worker/producer.env
sudo install -m 0600 -o root -g root /opt/ciclotrade-worker/current/config/system-cycle-publisher.env.example /etc/ciclotrade-worker/publisher.env
sudoedit /etc/ciclotrade-worker/producer.env
sudoedit /etc/ciclotrade-worker/publisher.env
```

Set `TRADEAI_SYSTEM_CYCLE_PRODUCER_ENABLED=true` and
`MARKET_DATA_ENABLED=true` in `producer.env`. Set
`TRADEAI_SYSTEM_CYCLE_PUBLISHER_ENABLED=true` in `publisher.env`, and enter the
same shared secret as the website receiver through the editor. The publisher
database must remain the local spool path; it must not point at the website
database. Reassert the at-rest permissions after editing:

```bash
sudo chown root:root /etc/ciclotrade-worker/producer.env /etc/ciclotrade-worker/publisher.env
sudo chmod 0600 /etc/ciclotrade-worker/producer.env /etc/ciclotrade-worker/publisher.env
```

## One-time acceptance before timers

The marker files are deliberate integration gates. Do not create them before
the website receiver, exact release, Python environment, and root-only files
have all passed the preceding checks.

```bash
sudo install -m 0644 /dev/null /etc/ciclotrade-worker/enable-system-cycle-producer.after-integration
sudo install -m 0644 /dev/null /etc/ciclotrade-worker/enable-system-cycle-publisher.after-integration
sudo systemctl start ciclotrade-system-cycle-producer.service
sudo systemctl start ciclotrade-system-cycle-publisher.service
sudo systemctl is-active --quiet ciclotrade-rewrite-api.service
sudo systemctl status --no-pager ciclotrade-system-cycle-producer.service
sudo systemctl status --no-pager ciclotrade-system-cycle-publisher.service
```

Inspect only service state, bounded journal output, and the authenticated
website research view. Confirm one accepted shadow receipt or an explicit,
non-actionable `no_data` outcome; confirm the website shows exactly 13 system
stock rows when coverage is available. Do not inspect payload JSON, environment
contents, signatures, or secrets. Confirm no entries were created in order,
official-simulation, Telegram, or standard quant-event paths.

After this manual run has a valid website receipt, enable the timers:

```bash
sudo systemctl enable --now ciclotrade-system-cycle-producer.timer
sudo systemctl enable --now ciclotrade-system-cycle-publisher.timer
sudo systemctl list-timers --all 'ciclotrade-system-cycle-*'
```

The producer timer offers a run every 15 minutes but stable New York slot
idempotency means it cannot create more than one result for a slot. The
publisher timer runs once per minute and uses fenced, signed delivery.

## Monitoring and rollback

Monitor timer state, service exit status, website receiver health, accepted
receipt count, and heartbeat freshness. Treat a disabled receiver, signature
error, stale fence, uncertain delivery, persistent data failure, or API restart
failure as a stop condition; do not bypass a gate or retry by changing payloads.

To stop further compute and delivery immediately:

```bash
sudo systemctl disable --now ciclotrade-system-cycle-producer.timer ciclotrade-system-cycle-publisher.timer
sudo rm -f /etc/ciclotrade-worker/enable-system-cycle-producer.after-integration
sudo rm -f /etc/ciclotrade-worker/enable-system-cycle-publisher.after-integration
```

Keep the local spool database for reconciliation; do not delete it as part of
rollback. If the website API fails after receiver activation, restore the prior
website release and private environment backup through the existing deployment
procedure, restart only `ciclotrade-rewrite-api.service`, and leave the worker
timers disabled. OpenD remains untouched throughout this rollback.
