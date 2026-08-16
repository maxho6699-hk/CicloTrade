from __future__ import annotations

import csv
from io import StringIO

import pytest

from core.database import DatabaseManager
from core.signal_import_portal import (
    SignalImportConflict,
    SignalImportForbidden,
    SignalImportPortalService,
)


def _csv(strategy: str = "趋势研究") -> bytes:
    return (
        "标的,日期,操作,数量,价格,策略\n"
        f"AAPL,2026-08-15T09:30:00+08:00,買入,2,100,{strategy}\n"
    ).encode("utf-8")


@pytest.fixture()
def database(tmp_path):
    db = DatabaseManager(str(tmp_path / "signals.db"))
    db.execute("INSERT INTO users(email,password_hash,created_at) VALUES (?,?,?)", ("signal@example.com", "x", "2026-01-01"))
    return db


def test_import_requires_verified_csv_capability(database):
    service = SignalImportPortalService(database, authorize=lambda _owner, _cap: {"allowed": True, "verified": False})
    with pytest.raises(SignalImportForbidden):
        service.readiness(1)


def test_import_is_owner_scoped_idempotent_and_quota_free_on_replay(database):
    service = SignalImportPortalService(database, authorize=lambda _owner, cap: cap == "csv_import", quota_resolver=lambda _owner: 1)
    content = _csv()
    first = service.import_csv(1, content, "signals.csv", "idem-0001")
    assert first["public_id"].startswith("sigjob_")
    assert "id" not in first
    replay = service.import_csv(1, content, "renamed.csv", "idem-0001")
    assert replay["replayed"] is True and replay["public_id"] == first["public_id"]
    # Same source with a different key is also a replay and does not consume quota.
    duplicate = service.import_csv(1, content, "another.csv", "idem-0002")
    assert duplicate["replayed"] is True
    assert len(service.list_jobs(1)) == 1
    assert service.list_jobs(2) == []


def test_same_idempotency_key_cannot_change_content(database):
    service = SignalImportPortalService(database, authorize=lambda _owner, _cap: True)
    service.import_csv(1, _csv(), "signals.csv", "idem-0003")
    with pytest.raises(SignalImportConflict):
        service.import_csv(1, _csv("different"), "signals.csv", "idem-0003")


def test_export_escapes_formula_cells_and_contains_safety_boundary(database):
    service = SignalImportPortalService(database, authorize=lambda _owner, _cap: True)
    result = service.import_csv(1, _csv("=HYPERLINK(\"http://evil\")"), "signals.csv", "idem-0004")
    detail = service.get_job(1, result["public_id"])
    assert detail["safety_boundary"]["creates_orders"] is False
    exported = service.export_csv(1, result["public_id"]).decode("utf-8-sig")
    assert "'=HYPERLINK" in exported
    assert "user_id" not in exported and "job_id" not in exported
    rows = list(csv.reader(StringIO(exported)))
    assert rows[1][5].startswith("'=HYPERLINK")
