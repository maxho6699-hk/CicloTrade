from __future__ import annotations

from datetime import datetime, timezone
import importlib.util
import os
from pathlib import Path
import stat
import sys
from types import SimpleNamespace

import pytest

from core.compute_evidence_contracts import delivery_signature
from core.backtest_queue_database import BacktestQueueDatabase
from core.compute_evidence_contracts import canonical_json, sha256_bytes
from src.apps.worker.compute_evidence_publisher import (
    ComputeEvidencePublisher,
    ComputeEvidencePublisherSettings,
    PublisherResponse,
    PublisherUncertainTransportError,
)
from src.apps.worker.compute_evidence_spool import PersistentComputeEvidenceSpool
from tests.test_compute_evidence_acceptance import (
    Clock,
    PUBLISHER_ID,
    SECRET,
    package_fixture,
)


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


atomic_env_secret = _load("atomic_env_secret", "ops/scripts/atomic_env_secret.py")
auth_probe = _load("compute_evidence_auth_probe", "ops/scripts/compute_evidence_auth_probe.py")
replay_acceptance = _load(
    "compute_evidence_replay_acceptance",
    "ops/scripts/compute_evidence_replay_acceptance.py",
)


def _policy(path: Path):
    return atomic_env_secret.TargetPolicy(path, "test-owner", "test-group", 0o600)


def _portable_metadata(monkeypatch):
    monkeypatch.setattr(atomic_env_secret.os, "fchown", lambda *_args: None, raising=False)
    monkeypatch.setattr(atomic_env_secret.os, "fchmod", lambda *_args: None, raising=False)
    monkeypatch.setattr(atomic_env_secret, "_fsync_directory", lambda *_args: None)


def test_atomic_env_update_replaces_one_key_writes_backup_and_has_safe_permissions(tmp_path, monkeypatch):
    target = tmp_path / "compute-evidence.env"
    target.write_bytes(b"OTHER=value\nTRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET=old-value\n")
    monkeypatch.setattr(atomic_env_secret, "_uid", lambda _value: 1000)
    monkeypatch.setattr(atomic_env_secret, "_gid", lambda _value: 1000)
    _portable_metadata(monkeypatch)
    backup = atomic_env_secret.update_secret(
        policy=_policy(target),
        owner="test-owner",
        group="test-group",
        mode="0600",
        key="TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET",
        secret=b"s" * 32,
        require_root=False,
    )
    assert backup is not None
    assert backup.read_bytes().endswith(b"old-value\n")
    assert target.read_bytes() == b"OTHER=value\nTRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET=" + b"s" * 32 + b"\n"
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.parametrize(
    ("content", "key", "secret", "message"),
    [
        (b"TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET=a\nTRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET=b\n", "TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET", b"s" * 32, "duplicate"),
        (b"", "UNRELATED_SECRET", b"s" * 32, "outside"),
        (b"", "TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET", b"short", "length"),
    ],
)
def test_atomic_env_update_fails_closed_for_duplicate_key_or_invalid_request(tmp_path, monkeypatch, content, key, secret, message):
    target = tmp_path / "compute-evidence.env"
    target.write_bytes(content)
    monkeypatch.setattr(atomic_env_secret, "_uid", lambda _value: 1000)
    monkeypatch.setattr(atomic_env_secret, "_gid", lambda _value: 1000)
    _portable_metadata(monkeypatch)
    with pytest.raises(atomic_env_secret.SecretUpdateError, match=message):
        atomic_env_secret.update_secret(
            policy=_policy(target),
            owner="test-owner",
            group="test-group",
            mode="0600",
            key=key,
            secret=secret,
            require_root=False,
        )
    assert target.read_bytes() == content


@pytest.mark.parametrize(
    "secret",
    [
        b"s" * 31 + b" ",
        b"s" * 31 + b"#",
        b"s" * 31 + b'\"',
        b"s" * 31 + b"'",
        b"s" * 31 + b"\\",
        b"s" * 31 + b"=",
        b"s" * 31 + b"\t",
        b"s" * 31 + b"\xff",
    ],
)
def test_atomic_env_update_rejects_values_with_ambiguous_environment_syntax(
    tmp_path, monkeypatch, secret
):
    target = tmp_path / "compute-evidence.env"
    original = b"TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET=old-value\n"
    target.write_bytes(original)
    monkeypatch.setattr(atomic_env_secret, "_uid", lambda _value: 1000)
    monkeypatch.setattr(atomic_env_secret, "_gid", lambda _value: 1000)
    _portable_metadata(monkeypatch)

    with pytest.raises(atomic_env_secret.SecretUpdateError, match="base64url"):
        atomic_env_secret.update_secret(
            policy=_policy(target),
            owner="test-owner",
            group="test-group",
            mode="0600",
            key="TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET",
            secret=secret,
            require_root=False,
        )

    assert target.read_bytes() == original


def test_atomic_env_update_emits_the_exact_value_read_by_the_probe_parser(tmp_path, monkeypatch):
    target = tmp_path / "compute-evidence.env"
    target.write_bytes(
        b"TRADEAI_COMPUTE_EVIDENCE_SITE_ID=hk-strategy-worker\n"
        b"TRADEAI_COMPUTE_EVIDENCE_PUBLISHER_ID=compute-evidence-publisher\n"
        b"TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET=old-value\n"
    )
    secret = b"AbCdEfGhIjKlMnOpQrStUvWxYz0123456789_-ab"
    monkeypatch.setattr(atomic_env_secret, "_uid", lambda _value: 1000)
    monkeypatch.setattr(atomic_env_secret, "_gid", lambda _value: 1000)
    _portable_metadata(monkeypatch)

    atomic_env_secret.update_secret(
        policy=_policy(target),
        owner="test-owner",
        group="test-group",
        mode="0600",
        key="TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET",
        secret=secret,
        require_root=False,
    )

    parsed = auth_probe._parse_env(target.read_bytes())
    assert parsed["TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET"].encode("ascii") == secret


def test_atomic_env_update_rejects_symlink_target(tmp_path, monkeypatch):
    real = tmp_path / "real.env"
    real.write_bytes(b"OTHER=value\n")
    target = tmp_path / "compute-evidence.env"
    try:
        target.symlink_to(real)
    except OSError:
        pytest.skip("symlink creation requires an unavailable Windows privilege")
    monkeypatch.setattr(atomic_env_secret, "_uid", lambda _value: 1000)
    monkeypatch.setattr(atomic_env_secret, "_gid", lambda _value: 1000)
    _portable_metadata(monkeypatch)
    with pytest.raises(atomic_env_secret.SecretUpdateError, match="safe regular file"):
        atomic_env_secret.update_secret(
            policy=_policy(target),
            owner="test-owner",
            group="test-group",
            mode="0600",
            key="TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET",
            secret=b"s" * 32,
            require_root=False,
        )
    assert real.read_bytes() == b"OTHER=value\n"


class _Response:
    status = 400

    @staticmethod
    def read(_limit: int) -> bytes:
        return b'{"error":"invalid schema"}'


class _Connection:
    recorded: dict[str, object] = {}

    def __init__(self, host, port, timeout, context):
        self.recorded["connection"] = (host, port, timeout, context)

    def request(self, method, path, body, headers):
        self.recorded["request"] = (method, path, body, headers)

    @staticmethod
    def getresponse():
        return _Response()

    @staticmethod
    def close():
        return None


def test_auth_probe_uses_fixed_https_contract_and_returns_only_fake_status():
    status = auth_probe.probe(
        {
            "TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET": "s" * 32,
            "TRADEAI_COMPUTE_EVIDENCE_SITE_ID": "hk-strategy-worker",
            "TRADEAI_COMPUTE_EVIDENCE_PUBLISHER_ID": "compute-evidence-publisher",
        },
        now=lambda: datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc),
        connection_factory=_Connection,
    )
    method, path, body, headers = _Connection.recorded["request"]
    assert status == 400
    assert _Connection.recorded["connection"][0:2] == ("ciclotrade.com", 443)
    assert method == "POST" and path == auth_probe.PATH and body == b"{}"
    assert headers["content-type"] == "application/json"
    assert headers["x-ciclotrade-package-sha256"]
    assert headers["x-ciclotrade-evidence-signature"] == delivery_signature(
        b"s" * 32,
        site_id="hk-strategy-worker",
        publisher_id="compute-evidence-publisher",
        source_worker_id=auth_probe.SOURCE_WORKER_ID,
        fencing_epoch=1,
        idempotency_key=auth_probe.PROBE_KEY,
        nonce=headers["x-ciclotrade-nonce"],
        expires_at=headers["x-ciclotrade-expires-at"],
        package_sha256=headers["x-ciclotrade-package-sha256"],
    )


def test_auth_probe_fails_before_transport_when_environment_is_invalid():
    with pytest.raises(auth_probe.ProbeError, match="invalid"):
        auth_probe.probe(
            {
                "TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET": "short",
                "TRADEAI_COMPUTE_EVIDENCE_SITE_ID": "hk-strategy-worker",
                "TRADEAI_COMPUTE_EVIDENCE_PUBLISHER_ID": "compute-evidence-publisher",
            },
            connection_factory=lambda *_args, **_kwargs: pytest.fail("transport must not be created"),
        )


def test_auth_probe_rejects_duplicate_or_malformed_environment_data():
    with pytest.raises(auth_probe.ProbeError, match="duplicate"):
        auth_probe._parse_env(
            b"TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET=a\nTRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET=b\n"
        )
    with pytest.raises(auth_probe.ProbeError, match="invalid"):
        auth_probe._parse_env(b"not an env assignment\n")


class _ReplayTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def post(self, path, headers, body, **limits):
        self.calls.append((path, headers, body, limits))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


def _receipt(package: dict) -> dict:
    return {
        "accepted": True,
        "created": True,
        "receipt_key": package["package_id"],
        "package_id": package["package_id"],
        "package_sha256": sha256_bytes(canonical_json(package)),
        "publication_state": "quarantine",
        "research_only": True,
        "actionable": False,
        "user_visible": False,
    }


def _publisher_settings(path: Path) -> ComputeEvidencePublisherSettings:
    return ComputeEvidencePublisherSettings(
        enabled=True,
        database_path=path,
        shared_secret=SECRET,
        publisher_id=PUBLISHER_ID,
        connect_timeout_seconds=2,
        total_timeout_seconds=5,
        max_response_bytes=64 * 1024,
        lease_seconds=30,
        delivery_expiry_seconds=120,
        max_retry_after_seconds=3_600,
    )


def test_replay_acceptance_completes_spool_only_after_exact_same_request_returns_201_then_409(tmp_path):
    clock = Clock()
    package = package_fixture()
    spool = PersistentComputeEvidenceSpool(BacktestQueueDatabase(tmp_path / "spool.db"), clock=clock)
    row, _ = spool.enqueue(package)
    first = PublisherResponse(
        201,
        {"content-type": "application/json"},
        canonical_json(_receipt(package)),
    )
    raw = _ReplayTransport([first, PublisherResponse(409, {}, b"")])
    guarded = replay_acceptance.ReplayAcceptanceTransport(raw)

    result = ComputeEvidencePublisher(
        spool,
        _publisher_settings(tmp_path / "spool.db"),
        guarded,
        clock=clock,
    ).run_once()

    assert result["state"] == "delivered" and result["http_status"] == 201
    assert (guarded.first_http_status, guarded.replay_http_status) == (201, 409)
    assert len(raw.calls) == 2
    assert raw.calls[0][0] == raw.calls[1][0]
    assert raw.calls[0][1] is raw.calls[1][1]
    assert raw.calls[0][2] is raw.calls[1][2]
    assert raw.calls[0][3] == raw.calls[1][3]
    assert raw.calls[0][1]["x-ciclotrade-nonce"] == raw.calls[1][1]["x-ciclotrade-nonce"]
    stored = spool.database.fetch_one(
        "SELECT state,last_http_status,delivery_receipt_json FROM compute_evidence_spool WHERE id=?",
        (row["id"],),
    )
    assert stored["state"] == "delivered" and stored["last_http_status"] == 201
    assert stored["delivery_receipt_json"]


@pytest.mark.parametrize(
    "responses",
    [
        [PublisherResponse(200, {"content-type": "application/json"}, b"{}")],
        [
            PublisherResponse(201, {"content-type": "application/json"}, b"{}"),
            PublisherResponse(200, {"content-type": "application/json"}, b"{}"),
        ],
        [
            PublisherResponse(201, {"content-type": "application/json"}, b"{}"),
            PublisherUncertainTransportError("response unavailable"),
        ],
    ],
)
def test_replay_acceptance_fails_closed_without_persisting_first_receipt(tmp_path, responses):
    clock = Clock()
    package = package_fixture("candidate-job-replay-failure")
    spool = PersistentComputeEvidenceSpool(BacktestQueueDatabase(tmp_path / "spool.db"), clock=clock)
    row, _ = spool.enqueue(package)
    guarded = replay_acceptance.ReplayAcceptanceTransport(_ReplayTransport(responses))

    result = ComputeEvidencePublisher(
        spool,
        _publisher_settings(tmp_path / "spool.db"),
        guarded,
        clock=clock,
    ).run_once()

    assert result["state"] == "uncertain"
    stored = spool.database.fetch_one(
        "SELECT state,delivery_receipt_json FROM compute_evidence_spool WHERE id=?",
        (row["id"],),
    )
    assert stored == {"state": "uncertain", "delivery_receipt_json": None}


def test_replay_acceptance_cli_emits_only_sanitized_status(monkeypatch, capsys):
    monkeypatch.setattr(
        replay_acceptance,
        "run_acceptance",
        lambda: {
            "state": "delivered",
            "origin": "https://ciclotrade.com",
            "spool_id": 7,
            "attempts": 1,
            "first_http_status": 201,
            "replay_http_status": 409,
        },
    )
    assert replay_acceptance.main() == 0
    output = capsys.readouterr()
    assert output.err == ""
    assert output.out == (
        '{"attempts":1,"first_http_status":201,"origin":"https://ciclotrade.com",'
        '"replay_http_status":409,"spool_id":7,"state":"delivered"}\n'
    )
    assert "secret" not in output.out.lower()


def test_replay_acceptance_cli_fails_without_echoing_exception(monkeypatch, capsys):
    monkeypatch.setattr(
        replay_acceptance,
        "run_acceptance",
        lambda: (_ for _ in ()).throw(ValueError("signature=do-not-print")),
    )
    assert replay_acceptance.main() == 2
    output = capsys.readouterr()
    assert output.out == ""
    assert output.err == '{"state":"error"}\n'
    assert "signature" not in output.err


def test_replay_acceptance_drops_to_existing_publisher_service_account():
    identity = {"uid": 0, "gid": 0}
    calls = []

    def setgid(value):
        calls.append(("gid", value))
        identity["gid"] = value

    def setuid(value):
        calls.append(("uid", value))
        identity["uid"] = value

    replay_acceptance._drop_service_privileges(
        platform="posix",
        geteuid=lambda: identity["uid"],
        getegid=lambda: identity["gid"],
        account_lookup=lambda user: SimpleNamespace(pw_uid=1201, pw_gid=1202),
        initgroups=lambda user, gid: calls.append(("groups", user, gid)),
        setgid=setgid,
        setuid=setuid,
    )

    assert calls == [
        ("groups", "cicloworker", 1202),
        ("gid", 1202),
        ("uid", 1201),
    ]


def test_replay_acceptance_refuses_non_root_or_failed_service_account_transition():
    with pytest.raises(replay_acceptance.ProbeError, match="begin as root"):
        replay_acceptance._drop_service_privileges(
            platform="posix",
            geteuid=lambda: 1000,
        )

    with pytest.raises(replay_acceptance.ProbeError, match="transition failed"):
        replay_acceptance._drop_service_privileges(
            platform="posix",
            geteuid=lambda: 0,
            getegid=lambda: 0,
            account_lookup=lambda user: SimpleNamespace(pw_uid=1201, pw_gid=1202),
            initgroups=lambda user, gid: None,
            setgid=lambda gid: None,
            setuid=lambda uid: None,
        )


def test_atomic_env_update_rejects_missing_target(tmp_path, monkeypatch):
    target = tmp_path / "missing.env"
    monkeypatch.setattr(atomic_env_secret, "_uid", lambda _value: 1000)
    monkeypatch.setattr(atomic_env_secret, "_gid", lambda _value: 1000)
    _portable_metadata(monkeypatch)
    with pytest.raises(atomic_env_secret.SecretUpdateError, match="does not exist"):
        atomic_env_secret.update_secret(
            policy=_policy(target),
            owner="test-owner",
            group="test-group",
            mode="0600",
            key="TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET",
            secret=b"s" * 32,
            require_root=False,
        )
