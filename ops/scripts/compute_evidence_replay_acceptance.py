#!/usr/bin/env python3
"""Run one fail-closed Compute Evidence 201 -> same-nonce 409 acceptance."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from types import MappingProxyType
from typing import Any, Mapping, Sequence


RELEASE_ROOT = Path(__file__).resolve().parents[2]
if str(RELEASE_ROOT) not in sys.path:
    sys.path.insert(0, str(RELEASE_ROOT))

from compute_evidence_auth_probe import ProbeError, read_root_only_environment  # noqa: E402
from core.compute_evidence_contracts import ComputeEvidenceError  # noqa: E402
from src.apps.worker.compute_evidence_publisher import (  # noqa: E402
    ComputeEvidencePublisherError,
    HttpsPublisherTransport,
    PublisherTransportError,
    PublisherUncertainTransportError,
    run_compute_evidence_publisher,
)
from src.apps.worker.compute_evidence_spool import ComputeEvidenceSpoolError  # noqa: E402


SERVICE_USER = "cicloworker"


class ReplayAcceptanceTransport:
    """Require one created response followed by same-request nonce rejection."""

    def __init__(self, transport: Any) -> None:
        if not callable(getattr(transport, "post", None)):
            raise TypeError("acceptance transport must provide post")
        self.transport = transport
        self.first_http_status: int | None = None
        self.replay_http_status: int | None = None

    def post(self, path: str, headers: Mapping[str, str], body: bytes, **limits: Any) -> Any:
        self.first_http_status = None
        self.replay_http_status = None
        protected_headers = MappingProxyType(dict(headers))
        protected_body = bytes(body)
        try:
            first = self.transport.post(path, protected_headers, protected_body, **limits)
        except PublisherTransportError as exc:
            raise PublisherUncertainTransportError("replay acceptance first delivery did not complete", status=exc.status) from exc
        self.first_http_status = int(first.status)
        if self.first_http_status != 201:
            raise PublisherUncertainTransportError("replay acceptance expected HTTP 201", status=self.first_http_status)
        try:
            replay = self.transport.post(path, protected_headers, protected_body, **limits)
        except PublisherTransportError as exc:
            raise PublisherUncertainTransportError("replay acceptance second delivery did not complete", status=exc.status) from exc
        self.replay_http_status = int(replay.status)
        if self.replay_http_status != 409:
            raise PublisherUncertainTransportError("replay acceptance expected HTTP 409", status=self.replay_http_status)
        return first


def run_acceptance(*, env: Mapping[str, str] | None = None, transport: Any | None = None, clock: Any | None = None) -> dict[str, Any]:
    if env is None:
        values = read_root_only_environment()
        _drop_service_privileges()
    else:
        values = dict(env)
    guarded = ReplayAcceptanceTransport(transport or HttpsPublisherTransport())
    result = run_compute_evidence_publisher(env=values, transport=guarded, clock=clock)
    return {
        "state": result.get("state"),
        "origin": result.get("origin"),
        "spool_id": result.get("spool_id"),
        "attempts": result.get("attempts"),
        "first_http_status": guarded.first_http_status,
        "replay_http_status": guarded.replay_http_status,
    }


def _drop_service_privileges(*, platform: str | None = None, geteuid: Any = None, getegid: Any = None, account_lookup: Any = None, initgroups: Any = None, setgid: Any = None, setuid: Any = None) -> None:
    if (platform or os.name) != "posix":
        return
    effective_uid = geteuid or os.geteuid
    if effective_uid() != 0:
        raise ProbeError("replay acceptance must begin as root")
    effective_gid = getegid or os.getegid
    if account_lookup is None:
        import pwd

        lookup = pwd.getpwnam
    else:
        lookup = account_lookup
    try:
        account = lookup(SERVICE_USER)
        (initgroups or os.initgroups)(SERVICE_USER, account.pw_gid)
        (setgid or os.setgid)(account.pw_gid)
        (setuid or os.setuid)(account.pw_uid)
    except (KeyError, OSError) as exc:
        raise ProbeError("replay acceptance could not enter the publisher service account") from exc
    if effective_uid() != account.pw_uid or effective_gid() != account.pw_gid:
        raise ProbeError("replay acceptance service account transition failed")


def main(_argv: Sequence[str] | None = None) -> int:
    try:
        result = run_acceptance()
    except (OSError, TypeError, ValueError, ComputeEvidenceError, ComputeEvidencePublisherError, ComputeEvidenceSpoolError, ProbeError):
        print('{"state":"error"}', file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
    return 0 if result.get("state") == "delivered" and result.get("first_http_status") == 201 and result.get("replay_http_status") == 409 else 2


if __name__ == "__main__":
    raise SystemExit(main())
