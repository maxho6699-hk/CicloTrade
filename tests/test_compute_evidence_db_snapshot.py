from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sqlite3
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("compute_evidence_db_snapshot", ROOT / "ops/scripts/compute_evidence_db_snapshot.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


snapshot_tool = _load()


def _database(path: Path, script: str) -> Path:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(script)
        connection.commit()
    finally:
        connection.close()
    return path


def _receiver(path: Path) -> Path:
    return _database(path, """
        CREATE TABLE compute_evidence_receiver_fences (site_id TEXT, publisher_id TEXT, highest_epoch INTEGER, updated_at TEXT);
        CREATE TABLE compute_evidence_receiver_nonces (nonce TEXT, receipt_key TEXT, package_sha256 TEXT, expires_at TEXT, received_at TEXT);
        CREATE TABLE compute_evidence_receipts (receipt_key TEXT, package_id TEXT, payload_json TEXT, publication_state TEXT);
    """)


def test_receiver_profile_reports_only_counts_and_hashes(tmp_path):
    database = _receiver(tmp_path / "receiver.db")
    sensitive = "private-package-material"
    package = {"secret": sensitive, "authority": {"research_only": True, "actionable": False, "user_visible": False, "official": False, "live": False}}
    with sqlite3.connect(database) as connection:
        connection.execute("INSERT INTO compute_evidence_receiver_fences VALUES(?,?,?,?)", ("site-private", "publisher-private", 1, "now"))
        connection.execute("INSERT INTO compute_evidence_receiver_nonces VALUES(?,?,?,?,?)", ("nonce-private", "receipt-private", "a" * 64, "soon", "now"))
        connection.execute("INSERT INTO compute_evidence_receipts VALUES(?,?,?,?)", ("receipt-private", "package-private", json.dumps(package), "quarantine"))
    result = snapshot_tool.snapshot(database.resolve(), "receiver")
    encoded = json.dumps(result, sort_keys=True)
    receipt = result["tables"]["compute_evidence_receipts"]
    assert receipt["rows"] == 1
    assert receipt["counts"]["safe_quarantine"] == 1
    assert sensitive not in encoded and "site-private" not in encoded and str(database) not in encoded


def test_stable_hash_is_independent_of_insert_order(tmp_path):
    first, second = _receiver(tmp_path / "first.db"), _receiver(tmp_path / "second.db")
    rows = [("a", "p2", "{}", "shadow"), ("b", "p1", "{}", "quarantine")]
    with sqlite3.connect(first) as connection:
        connection.executemany("INSERT INTO compute_evidence_receipts VALUES(?,?,?,?)", rows)
    with sqlite3.connect(second) as connection:
        connection.executemany("INSERT INTO compute_evidence_receipts VALUES(?,?,?,?)", reversed(rows))
    assert snapshot_tool.snapshot(first.resolve(), "receiver")["snapshot_sha256"] == snapshot_tool.snapshot(second.resolve(), "receiver")["snapshot_sha256"]


def test_main_profile_is_fixed_allowlist(tmp_path):
    database = _database(tmp_path / "main.db", "CREATE TABLE orders(id INTEGER,account_mode TEXT,private_note TEXT); INSERT INTO orders VALUES(1,'paper','secret'),(2,'live','secret'); CREATE TABLE unrelated(secret TEXT);")
    result = snapshot_tool.snapshot(database.resolve(), "main")
    encoded = json.dumps(result, sort_keys=True)
    assert result["tables"]["orders"]["counts"] == {"paper": 1, "live": 1}
    assert "unrelated" not in encoded and "secret" not in encoded


def test_connection_is_read_only(tmp_path):
    connection = snapshot_tool._connect_read_only(_receiver(tmp_path / "receiver.db").resolve())
    try:
        assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("CREATE TABLE forbidden(value TEXT)")
    finally:
        connection.close()


def test_cli_failure_does_not_echo_path(tmp_path, capsys):
    missing = (tmp_path / "sensitive.db").resolve()
    assert snapshot_tool.main(["--profile", "receiver", "--database", str(missing)]) == 2
    captured = capsys.readouterr()
    assert captured.err == "snapshot failed\n" and str(missing) not in captured.err
