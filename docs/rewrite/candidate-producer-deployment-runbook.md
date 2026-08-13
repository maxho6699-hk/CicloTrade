# Candidate producer integration gate

The autonomous candidate producer remains disabled by default. It is a local,
research-only process: it may create bounded candidate requests for the Compute
Gate, but it cannot publish recommendations, notify users, place orders, or
promote a candidate to official or live status.

## Two-key enablement

The producer starts only when both controls are present:

1. `TRADEAI_CANDIDATE_PRODUCER_ENABLED=true` in the root-managed worker
   environment file.
2. `/etc/ciclotrade-worker/enable-candidate-producer.after-integration` exists as
   a regular file owned by root and is not writable by group or other users.

The marker path is fixed in the application and the systemd service. An
environment variable cannot redirect it. If the marker is absent, the process
returns `state=disabled` before parsing runtime paths or constructing the
Compute Gate, queue database, artifact store, producer, or any network-capable
dependency. An unsafe marker is a startup error.

## Integration sequence

Keep the marker absent while installing and testing the release. Leave
`TRADEAI_CANDIDATE_PRODUCER_ENABLED=false` until the candidate-source freezer,
Compute Gate, queue migrations, resource ceilings, and research-only authority
contract have passed integration review.

After that review, an administrator may enable one controlled probe:

```bash
sudo install -m 0644 -o root -g root /dev/null \
  /etc/ciclotrade-worker/enable-candidate-producer.after-integration
sudoedit /etc/ciclotrade-worker/worker.env
```

The candidate producer's first activation and status checks are outside this
release contract. This release surface contains no copyable lifecycle command
for that worker unit.

Set `TRADEAI_CANDIDATE_PRODUCER_ENABLED=true` during the `sudoedit` step. Check
that the run emits at most one bounded request and still reports publication as
disabled before enabling the timer.

## Immediate rollback

Disable the timer and remove the marker. The absent marker is sufficient to
make direct CLI invocation fail closed even if the environment value drifts
back to true.

```bash
sudo rm -f /etc/ciclotrade-worker/enable-candidate-producer.after-integration
```

The candidate producer's timer rollback is outside this release contract. Do
not place a lifecycle command for that unit in this release surface.
