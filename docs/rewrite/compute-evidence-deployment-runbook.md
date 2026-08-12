# Compute Evidence safe deployment runbook

## Safety boundary

This runbook is for the already-approved Compute Evidence release only. It
does not create remote access, add a host or IP address, expose a port, move a
database, enable a service before acceptance, or handle any unrelated
credential. The receiver is fixed in code to HTTPS `ciclotrade.com:443` and
`/api/rewrite/internal/v1/compute-evidence/equity-shadow`; do not substitute a
hostname, URL, path, or proxy.

Each accepted package is quarantined, research-only, non-actionable, and not
user-visible. It must not feed orders, official recommendations, Telegram,
OpenD, or a live strategy.

Start with the integration marker **absent** and both Compute Evidence timers
disabled. When the queue has zero completed eligible candidates, keep that
state: do not create the marker, start either service, or enable either timer.
`queue=0` is a safe no-op, not an activation reason.

## Prerequisites and permissions

Use the exact reviewed release commit and keep an offline/private rollback
reference. Do not put a secret in a command line, shell history, repository,
ticket, terminal capture, service environment output, or log. Create one new,
independent shared secret of at least 32 bytes.

The website environment file is `/opt/CicloTrade/.env`, owned by
`root:ciclotrade` with mode `0640`. The worker strategy environment is
`/etc/ciclotrade-worker/compute-evidence.env`, owned by `root:root` with mode
`0600`. The two files need the same value only for
`TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET`; their databases remain distinct and
absolute paths.

Install scripts from the release with ordinary executable permissions. They
are local tools; copying them does not activate Compute Evidence.

```bash
cd /opt/ciclotrade-worker/current
sudo install -d -o root -g root -m 0755 /opt/ciclotrade-worker/current/ops/scripts
sudo install -m 0755 ops/scripts/atomic_env_secret.py /opt/ciclotrade-worker/current/ops/scripts/
sudo install -m 0755 ops/scripts/compute_evidence_auth_probe.py /opt/ciclotrade-worker/current/ops/scripts/
sudo install -d -o root -g root -m 0750 /etc/ciclotrade-worker
sudo test -e /etc/ciclotrade-worker/compute-evidence.env || \
  sudo install -m 0600 -o root -g root config/compute-evidence.env.example /etc/ciclotrade-worker/compute-evidence.env
```

The worker file must already be edited for its non-secret flags before secret
installation. The conditional install only bootstraps a missing file; it never
overwrites an existing environment file or its prior configuration.

Use a root-controlled stdin source, not a terminal argument, to update the
secret. The updater only permits the two named targets and the fixed Compute
Evidence secret key, rejects symlinks and duplicate keys, makes a protected
backup, fsyncs, then atomically replaces the file. It does not print the
secret or backup content.

```bash
sudo /opt/ciclotrade-worker/current/ops/scripts/atomic_env_secret.py \
  --target /opt/CicloTrade/.env --owner root --group ciclotrade --mode 0640 \
  --key TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET
sudo /opt/ciclotrade-worker/current/ops/scripts/atomic_env_secret.py \
  --target /etc/ciclotrade-worker/compute-evidence.env --owner root --group root --mode 0600 \
  --key TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET
```

Provide the secret bytes once to each command through an approved protected
stdin mechanism. Do not use `echo`, shell substitution, or a command argument.
Afterwards, check only metadata (never file contents):

```bash
sudo stat -c '%U:%G %a %n' /opt/CicloTrade/.env /etc/ciclotrade-worker/compute-evidence.env
```

The expected result is `root:ciclotrade 640 /opt/CicloTrade/.env` and
`root:root 600 /etc/ciclotrade-worker/compute-evidence.env`.

## Receiver and authentication checks

Configure the website receiver in its protected `.env` with an isolated,
absolute `TRADEAI_COMPUTE_EVIDENCE_RECEIVER_DATABASE`, the fixed site and
publisher IDs, and `TRADEAI_COMPUTE_EVIDENCE_RECEIVER_ENABLED=true`. Restart
only the already-existing rewrite API after validating its unit configuration.
Do not alter Nginx, add a route, open a port, or display the environment file.

The following outcomes are intentional and must be recorded only as HTTP
status codes in the private release receipt:

| Request | Expected status | Meaning |
| --- | --- | --- |
| Empty request body | 400 | Body is rejected before package processing. |
| Unsigned canonical `{}` | 401 | Authentication headers/signature are required. |
| Correctly signed canonical `{}` | 400 | Authentication succeeded, then strict package schema rejected the probe. |

Run the authentication-only probe as root on the worker only after the website
receiver is healthy. It reads the root-only worker environment, uses the fixed
HTTPS contract and canonical `{}`, and prints exactly one HTTP status. It never
prints a header, signature, environment value, or secret. A status of `400` is
the required success result for this probe.

```bash
sudo /opt/ciclotrade-worker/current/ops/scripts/compute_evidence_auth_probe.py
```

If it returns any other status or exits nonzero, stop. Do not retry by changing
the path, host, secret, headers, package, or service activation state.

## One real candidate acceptance

Proceed only when the local queue has exactly one completed, eligible shadow
candidate ready for export. Confirm the marker remains absent before this
check. Create the marker only for this controlled single candidate, then start
the two oneshot services manually; do not enable timers yet.

```bash
sudo test ! -e /etc/ciclotrade-worker/enable-compute-evidence.after-integration
sudo install -m 0644 /dev/null /etc/ciclotrade-worker/enable-compute-evidence.after-integration
sudo systemctl start ciclotrade-compute-evidence-exporter.service
sudo systemctl start ciclotrade-compute-evidence-publisher.service
```

Confirm one new quarantined, research-only, non-actionable and hidden receipt
on the website. It must be a real shadow candidate—not `{}`, a fixture, or a
replayed request. Confirm no order, official, Telegram, OpenD, or live state
changed.

For replay protection, the controlled validation must show that re-sending the
same signed candidate delivery with the same nonce returns `409`. Do not make
an alternate package or mutate a payload to obtain this result.

Only after the single-candidate acceptance and replay check both pass may the
operator enable timers:

```bash
sudo systemctl enable --now ciclotrade-compute-evidence-exporter.timer
sudo systemctl enable --now ciclotrade-compute-evidence-publisher.timer
sudo systemctl list-timers --all 'ciclotrade-compute-evidence-*'
```

## Rollback

On any unexpected status, signature problem, schema result other than the
expected probe `400`, replay result other than `409`, receiver error, or any
non-research side effect, stop immediately. Disable both timers, stop either
oneshot service if active, and remove the integration marker. Restore the
previous reviewed release and environment files only through the existing
protected backup/release process; do not inspect or print a secret while doing
so.

```bash
sudo systemctl disable --now ciclotrade-compute-evidence-exporter.timer
sudo systemctl disable --now ciclotrade-compute-evidence-publisher.timer
sudo systemctl stop ciclotrade-compute-evidence-exporter.service ciclotrade-compute-evidence-publisher.service
sudo rm -f /etc/ciclotrade-worker/enable-compute-evidence.after-integration
```

Finish the private receipt with the exact release commit, status-only probe
evidence, one candidate receipt identity, the `409` replay result, timer state,
and confirmation that the rollback marker is absent when rollback occurred.
