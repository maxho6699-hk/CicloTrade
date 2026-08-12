#!/usr/bin/env python3
"""Perform the fixed-origin Compute Evidence authentication-only probe.

The signed canonical ``{}`` body intentionally fails package schema validation.
An HTTP 400 therefore proves that the receiver authenticated the request without
accepting a candidate package.  The program prints only the HTTP status.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import http.client
import os
from pathlib import Path
import re
import secrets
import ssl
import stat
import sys
from typing import Callable, Mapping, Sequence


RELEASE_ROOT = Path(__file__).resolve().parents[2]
if str(RELEASE_ROOT) not in sys.path:
    sys.path.insert(0, str(RELEASE_ROOT))

from core.compute_evidence_contracts import delivery_signature, sha256_bytes  # noqa: E402


ENV_PATH = Path("/etc/ciclotrade-worker/compute-evidence.env")
HOST = "ciclotrade.com"
PORT = 443
PATH = "/api/rewrite/internal/v1/compute-evidence/equity-shadow"
ENDPOINT = "compute-equity-shadow-package"
SOURCE_WORKER_ID = "auth-probe-worker"
PROBE_KEY = "auth-probe-0001"
MAX_ENV_BYTES = 1024 * 1024


class ProbeError(RuntimeError):
    """A deliberately non-sensitive probe failure."""


def probe(
    env: Mapping[str, str],
    *,
    now: Callable[[], datetime] | None = None,
    connection_factory: Callable[..., http.client.HTTPSConnection] = http.client.HTTPSConnection,
) -> int:
    secret = env.get("TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET", "").encode("utf-8")
    site_id = env.get("TRADEAI_COMPUTE_EVIDENCE_SITE_ID", "")
    publisher_id = env.get("TRADEAI_COMPUTE_EVIDENCE_PUBLISHER_ID", "")
    if len(secret) < 32 or not _identity(site_id) or not _identity(publisher_id):
        raise ProbeError("root-only Compute Evidence environment is invalid")
    instant = (now or (lambda: datetime.now(timezone.utc)))()
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ProbeError("probe clock is invalid")
    expires = (instant.astimezone(timezone.utc) + timedelta(minutes=2)).replace(microsecond=0)
    expires_at = expires.isoformat().replace("+00:00", "Z")
    nonce = secrets.token_urlsafe(32)
    body = b"{}"
    digest = sha256_bytes(body)
    signature = delivery_signature(
        secret,
        site_id=site_id,
        publisher_id=publisher_id,
        source_worker_id=SOURCE_WORKER_ID,
        fencing_epoch=1,
        idempotency_key=PROBE_KEY,
        nonce=nonce,
        expires_at=expires_at,
        package_sha256=digest,
    )
    headers = {
        "content-type": "application/json",
        "x-ciclotrade-site-id": site_id,
        "x-ciclotrade-publisher-id": publisher_id,
        "x-ciclotrade-source-worker-id": SOURCE_WORKER_ID,
        "x-ciclotrade-fencing-epoch": "1",
        "idempotency-key": PROBE_KEY,
        "x-ciclotrade-nonce": nonce,
        "x-ciclotrade-expires-at": expires_at,
        "x-ciclotrade-package-sha256": digest,
        "x-ciclotrade-evidence-signature": signature,
    }
    connection = connection_factory(HOST, PORT, timeout=10, context=ssl.create_default_context())
    try:
        connection.request("POST", PATH, body=body, headers=headers)
        response = connection.getresponse()
        response.read(1024)
        return int(response.status)
    finally:
        connection.close()


def read_root_only_environment(path: Path = ENV_PATH) -> dict[str, str]:
    if os.geteuid() != 0:
        raise ProbeError("authentication probe must be run as root")
    try:
        listed = os.lstat(path)
    except OSError as exc:
        raise ProbeError("root-only Compute Evidence environment is unavailable") from exc
    if (
        stat.S_ISLNK(listed.st_mode)
        or not stat.S_ISREG(listed.st_mode)
        or listed.st_uid != 0
        or listed.st_gid != 0
        or stat.S_IMODE(listed.st_mode) != 0o600
    ):
        raise ProbeError("root-only Compute Evidence environment is unsafe")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise ProbeError("root-only Compute Evidence environment is unavailable") from exc
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (listed.st_dev, listed.st_ino):
            raise ProbeError("root-only Compute Evidence environment changed")
        content = _read_limited(descriptor)
    finally:
        os.close(descriptor)
    return _parse_env(content)


def _read_limited(descriptor: int) -> bytes:
    result = bytearray()
    while len(result) <= MAX_ENV_BYTES:
        chunk = os.read(descriptor, 65_536)
        if not chunk:
            return bytes(result)
        result.extend(chunk)
    raise ProbeError("root-only Compute Evidence environment is too large")


def _parse_env(content: bytes) -> dict[str, str]:
    if b"\x00" in content:
        raise ProbeError("root-only Compute Evidence environment is invalid")
    result: dict[str, str] = {}
    for raw in content.splitlines():
        if not raw or raw.lstrip().startswith(b"#"):
            continue
        match = re.fullmatch(rb"([A-Z_][A-Z0-9_]*)=(.*)", raw)
        if match is None:
            raise ProbeError("root-only Compute Evidence environment is invalid")
        key = match.group(1).decode("ascii")
        if key in result:
            raise ProbeError("root-only Compute Evidence environment contains a duplicate key")
        try:
            result[key] = match.group(2).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProbeError("root-only Compute Evidence environment is invalid") from exc
    return result


def _identity(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value))


def main(_argv: Sequence[str] | None = None) -> int:
    try:
        status = probe(read_root_only_environment())
    except (OSError, ValueError, http.client.HTTPException, ProbeError):
        return 2
    print(status)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
