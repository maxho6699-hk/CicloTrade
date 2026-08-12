#!/usr/bin/env python3
"""Emit a content-free SQLite receipt or side-effect snapshot."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import sys
from typing import Any, Mapping, Sequence


HASH_DOMAIN = b"ciclotrade-sqlite-table-snapshot-v1\0"


class SnapshotError(RuntimeError):
    """A deliberately non-sensitive snapshot failure."""


@dataclass(frozen=True)
class CountSpec:
    name: str
    where: str


@dataclass(frozen=True)
class TableSpec:
    name: str
    counts: tuple[CountSpec, ...] = ()


RECEIVER_TABLES = (
    TableSpec("compute_evidence_receiver_fences"),
    TableSpec("compute_evidence_receiver_nonces"),
    TableSpec(
        "compute_evidence_receipts",
        (
            CountSpec("quarantine", "publication_state='quarantine'"),
            CountSpec("shadow", "publication_state='shadow'"),
            CountSpec("research_only_true", "json_extract(payload_json,'$.authority.research_only')=1"),
            CountSpec("actionable_false", "json_extract(payload_json,'$.authority.actionable')=0"),
            CountSpec("user_visible_false", "json_extract(payload_json,'$.authority.user_visible')=0"),
            CountSpec("official_false", "json_extract(payload_json,'$.authority.official')=0"),
            CountSpec("live_false", "json_extract(payload_json,'$.authority.live')=0"),
            CountSpec(
                "safe_quarantine",
                "publication_state='quarantine' "
                "AND json_extract(payload_json,'$.authority.research_only')=1 "
                "AND json_extract(payload_json,'$.authority.actionable')=0 "
                "AND json_extract(payload_json,'$.authority.user_visible')=0 "
                "AND json_extract(payload_json,'$.authority.official')=0 "
                "AND json_extract(payload_json,'$.authority.live')=0",
            ),
        ),
    ),
)


MAIN_TABLES = (
    TableSpec("orders", (CountSpec("paper", "account_mode='paper'"), CountSpec("live", "account_mode='live'"))),
    TableSpec("subscription_orders"),
    TableSpec("trades"),
    TableSpec("notifications"),
    TableSpec("quant_events"),
    TableSpec("quant_event_legs"),
    TableSpec("quant_event_deliveries"),
    TableSpec("price_alert_deliveries"),
    TableSpec("telegram_group_deliveries"),
    TableSpec("telegram_delayed_group_deliveries"),
    TableSpec("telegram_service_outbox"),
    TableSpec("official_paper_events_v2"),
    TableSpec("official_paper_event_legs_v2"),
    TableSpec("official_paper_equity_snapshots_v2"),
    TableSpec("official_paper_event_deliveries_v2"),
    TableSpec("official_paper_group_deliveries_v2"),
    TableSpec("official_paper_delayed_group_deliveries_v2"),
    TableSpec("official_option_sim_positions"),
    TableSpec("official_option_sim_events"),
    TableSpec("official_option_sim_event_legs"),
    TableSpec("official_option_sim_equity_snapshots"),
    TableSpec("official_option_sim_worker_fences"),
)


PROFILES: Mapping[str, tuple[TableSpec, ...]] = {"receiver": RECEIVER_TABLES, "main": MAIN_TABLES}


def snapshot(database: Path, profile: str) -> dict[str, Any]:
    specs = PROFILES.get(profile)
    if specs is None:
        raise SnapshotError("snapshot profile is invalid")
    connection = _connect_read_only(database)
    try:
        connection.execute("BEGIN")
        tables = {spec.name: _table_snapshot(connection, spec) for spec in specs}
        connection.execute("ROLLBACK")
    except (OverflowError, sqlite3.Error, SnapshotError, UnicodeError, ValueError) as exc:
        try:
            connection.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise SnapshotError("database snapshot failed") from exc
    finally:
        connection.close()
    bound = {"profile": profile, "tables": tables}
    return {"schema_version": 1, "profile": profile, "tables": tables, "snapshot_sha256": hashlib.sha256(_canonical_json(bound)).hexdigest()}


def _connect_read_only(database: Path) -> sqlite3.Connection:
    path = Path(database).expanduser()
    if not path.is_absolute():
        raise SnapshotError("database file must be absolute")
    try:
        listed = os.lstat(path)
    except OSError as exc:
        raise SnapshotError("database file is unavailable") from exc
    if stat.S_ISLNK(listed.st_mode) or not stat.S_ISREG(listed.st_mode):
        raise SnapshotError("database file is unsafe")
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=5, isolation_level=None)
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.execute("PRAGMA trusted_schema=OFF")
        query_only = connection.execute("PRAGMA query_only").fetchone()
    except sqlite3.Error as exc:
        if connection is not None:
            connection.close()
        raise SnapshotError("database file is unavailable") from exc
    if query_only is None or int(query_only[0]) != 1:
        connection.close()
        raise SnapshotError("database read-only guard is unavailable")
    return connection


def _table_snapshot(connection: sqlite3.Connection, spec: TableSpec) -> dict[str, Any]:
    found = connection.execute("SELECT type FROM sqlite_schema WHERE name=? COLLATE BINARY", (spec.name,)).fetchone()
    if found is None:
        return {"exists": False, "rows": 0, "sha256": None, "counts": {count.name: 0 for count in spec.counts}}
    if found[0] != "table":
        raise SnapshotError("allowlisted database object is not a table")
    quoted_table = _quote_identifier(spec.name)
    description = connection.execute(f"SELECT * FROM {quoted_table} LIMIT 0").description
    if not description:
        raise SnapshotError("allowlisted table has no readable columns")
    columns = tuple(str(item[0]) for item in description)
    quoted_columns = tuple(_quote_identifier(column) for column in columns)
    selection = ",".join(quoted_columns)
    ordering = ",".join(clause for column in quoted_columns for clause in (f"typeof({column}) COLLATE BINARY", f"quote({column}) COLLATE BINARY"))
    hasher = hashlib.sha256()
    hasher.update(HASH_DOMAIN)
    _update_frame(hasher, spec.name.encode())
    for column in columns:
        _update_frame(hasher, column.encode())
    row_count = 0
    for row in connection.execute(f"SELECT {selection} FROM {quoted_table} ORDER BY {ordering}"):
        hasher.update(b"R")
        for value in row:
            _update_value(hasher, value)
        row_count += 1
    hasher.update(b"C")
    _update_frame(hasher, str(row_count).encode("ascii"))
    counts = {count.name: int(connection.execute(f"SELECT count(*) FROM {quoted_table} WHERE {count.where}").fetchone()[0]) for count in spec.counts}
    return {"exists": True, "rows": row_count, "sha256": hasher.hexdigest(), "counts": counts}


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _update_value(hasher: Any, value: Any) -> None:
    if value is None:
        tag, body = b"N", b""
    elif isinstance(value, int):
        tag, body = b"I", str(value).encode("ascii")
    elif isinstance(value, float):
        tag, body = b"F", value.hex().encode("ascii")
    elif isinstance(value, str):
        tag, body = b"T", value.encode()
    elif isinstance(value, (bytes, bytearray, memoryview)):
        tag, body = b"B", bytes(value)
    else:
        raise SnapshotError("allowlisted table contains an unsupported SQLite value")
    hasher.update(tag)
    _update_frame(hasher, body)


def _update_frame(hasher: Any, body: bytes) -> None:
    hasher.update(len(body).to_bytes(8, "big"))
    hasher.update(body)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")).encode()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Emit a content-free Compute Evidence SQLite snapshot.")
    parser.add_argument("--profile", required=True, choices=tuple(PROFILES))
    parser.add_argument("--database", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = snapshot(args.database, args.profile)
    except SnapshotError:
        print("snapshot failed", file=sys.stderr)
        return 2
    print(_canonical_json(result).decode())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
