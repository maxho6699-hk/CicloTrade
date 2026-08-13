#!/usr/bin/env python3
"""Verify the one-time CicloTrade SPA route cutover without changing services."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


REQUIRED_ROUTES = frozenset({"paper", "more"})
BEFORE_ROUTES = "|opportunities|lab|earnings)/?$"
AFTER_ROUTES = "|opportunities|lab|earnings|paper|more)/?$"
FORBIDDEN = re.compile(
    r"\b(?:systemctl|service|nginx|futu[ -]?opend|opend)\b[^\n#;]*\b(?:start|stop|restart|reload|try-reload|reload-or-restart|enable|disable|daemon-reload|login|relogin|migrate|kill)\b",
    re.IGNORECASE,
)
LOCATION = re.compile(r"location\s+~\s+\^/\(([^)]+)\)/\?\$\s*\{")


def verify(
    config: Path,
    *,
    baseline: Path | None = None,
    expected_sha256: str | None = None,
) -> list[str]:
    text = config.read_text(encoding="utf-8")
    violations: list[str] = []
    if expected_sha256 and hashlib.sha256(config.read_bytes()).hexdigest() != expected_sha256:
        violations.append("candidate SHA-256 mismatch")
    if baseline is not None:
        original = baseline.read_text(encoding="utf-8")
        if original.count(BEFORE_ROUTES) != 1:
            violations.append("baseline route contract mismatch")
        elif text != original.replace(BEFORE_ROUTES, AFTER_ROUTES, 1):
            violations.append("candidate changes more than paper/more routes")
    matched = LOCATION.search(text)
    routes = set(matched.group(1).split("|")) if matched else set()
    missing = sorted(REQUIRED_ROUTES - routes)
    if missing:
        violations.append(f"missing SPA routes: {','.join(missing)}")
    if "location ^~ /api/rewrite/" not in text:
        violations.append("rewrite API route missing")
    if text.index("location ^~ /api/rewrite/") > text.index("\n    location ^~ /api/ {"):
        violations.append("rewrite API precedence changed")
    if FORBIDDEN.search(text):
        violations.append("config contains a lifecycle command")
    return violations


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--expected-sha256")
    args = parser.parse_args()
    violations = verify(
        args.config,
        baseline=args.baseline,
        expected_sha256=args.expected_sha256,
    )
    print(json.dumps({"state": "rejected" if violations else "accepted", "violations": violations}))
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
