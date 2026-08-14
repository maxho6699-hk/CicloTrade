# Wave 2 Website Release Runbook

This procedure creates a reproducible website release from one clean Git
commit. It is a packaging and verification gate only: it does not connect to
production, install dependencies, alter a database, or control OpenD.

## Preconditions

- Work from the exact reviewed release commit in a clean checkout. Staged,
  unstaged, and untracked changes all reject the build and verification gate.
- Select the reviewed baseline commit. It must be an ancestor of the release
  commit. Record both SHAs in the release receipt.
- Write the tarball and manifest outside the source checkout.
- Confirm migrations `0032`, `0033`, `0034`, and `0035` are present. Migration
  `0034` is retained as an existing production migration.

## Build and Verify

Use a fixed `SOURCE_DATE_EPOCH` from the reviewed commit or another recorded,
reviewed integer.

```powershell
$env:SOURCE_DATE_EPOCH = (git show -s --format=%ct HEAD)
$baseline = '<reviewed-baseline-commit>'
python ops/scripts/build_web_release.py --baseline $baseline --artifact C:\release-out\tradeai-web.tar.gz --manifest C:\release-out\tradeai-web.manifest.json
python ops/scripts/verify_web_release.py --artifact C:\release-out\tradeai-web.tar.gz --manifest C:\release-out\tradeai-web.manifest.json
```

The first command must report `state: built`; the second must report
`state: accepted`. A rejection stops the release. Never edit the tarball or
manifest by hand.

## What the gate binds

The canonical JSON manifest binds the exact Git commit, tree, reviewed
baseline, artifact SHA-256 and size, Git-blob hashes for requirements, the Web
lockfile, and both tooling files, runtime versions, `SOURCE_DATE_EPOCH`, and every archive
file's path, SHA-256, size, mode, and Git blob identity.

The builder packages only the website runtime allowlist and writes each file
from its Git blob. It verifies the checkout is clean and unchanged again after
the archive closes and after writing the manifest. Verification streams tar members, rejects non-canonical
gzip/tar metadata, archive bombs, unsafe modes, path traversal,
case-insensitive or Unicode-colliding paths, missing or extra entries, secrets,
changed source identity, dirty checkouts, changed inputs, stale Git blobs, and
manifest/artifact replacement during verification.

Only one lifecycle action is permitted: `restart` of
`ciclotrade-rewrite-api.service`. No OpenD action, broker action, database
mutation, payment handling, or publication is authorized.

## Receipt

Attach the accepted manifest unchanged and record the source commit/tree/
baseline, artifact SHA-256 and size, manifest SHA-256, `SOURCE_DATE_EPOCH`,
both command outputs, reviewer identity, and the separately authorized
lifecycle decision. Do not include connection details, customer data,
credentials, payment proofs, or QR material.
