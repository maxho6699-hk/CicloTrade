# First-wave Nginx route cutover

This one-time release step is limited to serving `/paper` and `/more` from the
already reviewed React build. It does not authorize any other route, service,
OpenD, broker, market-data, Telegram, order, or strategy lifecycle change.

Required order:

1. Verify the candidate against the downloaded active baseline and its expected
   SHA-256 with `verify_nginx_route_cutover.py`. The only accepted text change is
   adding `/paper` and `/more` to the existing React route expression.
2. Back up the active CicloTrade Nginx configuration with owner, mode, and hash.
3. Install only the reviewed `ops/nginx-ciclotrade.conf` candidate using a
   same-filesystem temporary file and atomic rename; never partially overwrite it.
4. Run the Nginx configuration test. A failure restores the backup immediately.
5. Perform the single approved Nginx configuration reload. This exception does
   not grant a reusable or general reload permission.
6. Verify `/paper` and `/more` return the React document, both APIs remain healthy,
   an existing legacy route remains healthy, and the pre/post OpenD read-only
   receipt is unchanged. Record only hashes, exit codes, and health results.

Rollback restores the exact backed-up Nginx configuration, tests it, performs
one final Nginx configuration reload, and verifies the previous routes and
services. The rollback reload is the only permitted second reload. If restore,
configuration testing, or rollback reload fails, stop immediately. No Nginx
restart and no other service lifecycle action is part of this exception.
