"""Disabled-first, idempotent HTTPS publisher for the isolated 97-symbol spool."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Callable, Mapping, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from core.compat import UTC
from core.expanded_research_contracts import (
    ExpandedResearchError,
    canonical_json,
    receiver_signature,
    sha256_bytes,
    stamp,
    validate_result,
)


MAX_BATCH = 1
MAX_SOURCE_SCAN = 97
SOURCE_PAGE_SIZE = 16
LEASE_SECONDS = 120


class ExpandedResearchPublisherError(RuntimeError):
    pass


class ExpandedResearchPublisherBusy(ExpandedResearchPublisherError):
    pass


class Transport(Protocol):
    def __call__(self, url: str, body: bytes, headers: Mapping[str, str]) -> Mapping[str, Any]: ...


class ExpandedResearchPublisher:
    def __init__(
        self,
        *,
        source_spool: Path,
        state_database: Path,
        base_url: str,
        shared_secret: str | bytes,
        worker_id: str = "expanded-research-publisher",
        enabled: bool = False,
        clock: Callable[[], datetime] | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.source_spool = Path(source_spool).resolve()
        self.state_database = Path(state_database).resolve()
        if self.source_spool == self.state_database:
            raise ValueError("expanded publisher source and state databases must be isolated")
        normalized_base_url = str(base_url).rstrip("/")
        parsed = urlparse(normalized_base_url)
        if normalized_base_url != "https://ciclotrade.com" or parsed.scheme != "https" or parsed.hostname != "ciclotrade.com":
            raise ValueError("expanded publisher base_url is not the sealed CicloTrade endpoint")
        secret = shared_secret.encode("utf-8") if isinstance(shared_secret, str) else shared_secret
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("expanded publisher shared secret must contain at least 32 bytes")
        if not worker_id or len(worker_id) > 128:
            raise ValueError("expanded publisher worker_id is invalid")
        self.base_url = normalized_base_url
        self._secret = secret
        self.worker_id = worker_id
        self.enabled = bool(enabled)
        self.clock = clock or (lambda: datetime.now(UTC))
        self.transport = transport or _https_transport
        self.state_database.parent.mkdir(parents=True, exist_ok=True)
        self._init_state()

    def publish_once(self, *, limit: int = 1) -> dict[str, Any]:
        if not self.enabled:
            return {"state": "disabled", "published": 0, "outbound": False, "user_visible": False}
        if isinstance(limit, bool) or not 1 <= limit <= MAX_BATCH:
            raise ValueError("expanded publisher limit must be 1")
        lease = self._acquire_lease()
        published: list[str] = []
        errors: list[str] = []
        try:
            for row in self._pending_rows(limit):
                result_id = str(row["result_id"])
                key = f"expanded97-{result_id}"
                body = self._canonical_source_body(row["payload_json"], row["payload_sha256"])
                sent_at = stamp(self._now())
                body_sha = sha256_bytes(body)
                headers = {
                    "Content-Type": "application/json",
                    "Idempotency-Key": key,
                    "X-Ciclotrade-Research-Worker-Id": self.worker_id,
                    "X-Ciclotrade-Research-Fencing-Epoch": str(lease["epoch"]),
                    "X-Ciclotrade-Research-Sent-At": sent_at,
                    "X-Ciclotrade-Research-SHA256": body_sha,
                    "X-Ciclotrade-Research-Signature": receiver_signature(
                        self._secret, worker_id=self.worker_id, fencing_epoch=lease["epoch"],
                        idempotency_key=key, sent_at=sent_at, body_sha256=body_sha,
                    ),
                }
                self._mark_attempt(result_id, key, lease["epoch"])
                try:
                    response = self.transport(self.base_url + "/api/rewrite/internal/v1/expanded-research/results", body, headers)
                    self._validate_receipt(response, key=key, result_id=result_id, body_sha256=body_sha)
                    self._mark_sent(result_id, key, response)
                    published.append(result_id)
                except Exception as exc:  # keep the same idempotency key for a safe retry
                    self._mark_error(result_id, key, str(exc))
                    errors.append(result_id)
        finally:
            self._release_lease(lease["epoch"])
        return {
            "state": "published" if published else ("error" if errors else "idle"),
            "published": len(published), "published_ids": published, "errors": errors,
            "outbound": True, "user_visible": False, "research_only": True,
            "actionable": False, "execution": False, "official": False, "live": False,
        }

    def _pending_rows(self, limit: int) -> list[dict[str, Any]]:
        if not self.source_spool.exists() or self.source_spool.is_symlink():
            raise ExpandedResearchPublisherError("expanded publisher source spool is unavailable")
        uri = f"file:{self.source_spool.as_posix()}?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5) as source:
            source.row_factory = sqlite3.Row
            try:
                cursor = source.execute(
                    """SELECT result_id,payload_json,payload_sha256 FROM expanded_research_results
                       ORDER BY created_at ASC,result_id ASC LIMIT ?""", (MAX_SOURCE_SCAN,)
                )
            except sqlite3.Error as exc:
                raise ExpandedResearchPublisherError("expanded publisher source spool schema is invalid") from exc
            pending: list[dict[str, Any]] = []
            scanned = 0
            with self._connect() as state:
                while scanned < MAX_SOURCE_SCAN:
                    page = cursor.fetchmany(min(SOURCE_PAGE_SIZE, MAX_SOURCE_SCAN - scanned))
                    if not page:
                        break
                    scanned += len(page)
                    for row in page:
                        existing = state.execute(
                            "SELECT status FROM expanded_research_publish_state WHERE result_id=?", (row["result_id"],)
                        ).fetchone()
                        if existing is None or existing[0] != "sent":
                            pending.append(dict(row))
                            if len(pending) >= limit:
                                return pending
            return pending

    @staticmethod
    def _canonical_source_body(raw: str, expected_sha256: str) -> bytes:
        try:
            value = validate_result(json.loads(raw))
        except (TypeError, ValueError, json.JSONDecodeError, ExpandedResearchError) as exc:
            raise ExpandedResearchPublisherError("expanded publisher found invalid source evidence") from exc
        body = canonical_json(value)
        if sha256_bytes(body) != expected_sha256:
            raise ExpandedResearchPublisherError("expanded publisher source payload hash does not match sealed evidence")
        return body

    @staticmethod
    def _validate_receipt(response: Mapping[str, Any], *, key: str, result_id: str, body_sha256: str) -> None:
        expected_fields = {
            "accepted", "created", "receipt_key", "result_id", "payload_sha256", "result_sha256", "state",
            "research_only", "shadow", "actionable", "outbound", "user_visible", "execution", "official", "live",
        }
        if not isinstance(response, Mapping) or set(response) != expected_fields:
            raise ExpandedResearchPublisherError("expanded publisher received an invalid receipt")
        if (
            response["accepted"] is not True
            or not isinstance(response["created"], bool)
            or response["receipt_key"] != key
            or response["result_id"] != result_id
            or response["payload_sha256"] != body_sha256
            or response["result_sha256"] != body_sha256
            or response["state"] != "shadow"
            or response["research_only"] is not True
            or response["shadow"] is not True
            or any(response[field] is not False for field in ("actionable", "outbound", "user_visible", "execution", "official", "live"))
        ):
            raise ExpandedResearchPublisherError("expanded publisher receipt did not prove sealed shadow acceptance")

    def _init_state(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """CREATE TABLE IF NOT EXISTS expanded_research_publish_state(
                       result_id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE,
                       status TEXT NOT NULL CHECK(status IN ('pending','sending','sent','unknown','failed')),
                       attempts INTEGER NOT NULL DEFAULT 0, fencing_epoch INTEGER NOT NULL,
                       response_json TEXT, last_error TEXT, updated_at TEXT NOT NULL);
                   CREATE TABLE IF NOT EXISTS expanded_research_publish_lease(
                       lease_name TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                       fencing_epoch INTEGER NOT NULL, expires_at TEXT NOT NULL);
                   CREATE TABLE IF NOT EXISTS expanded_research_publish_fence(
                       owner_id TEXT PRIMARY KEY, highest_epoch INTEGER NOT NULL, updated_at TEXT NOT NULL);"""
            )

    def _acquire_lease(self) -> dict[str, int]:
        now = self._now()
        expiry = now + timedelta(seconds=LEASE_SECONDS)
        now_text, expiry_text = stamp(now), stamp(expiry)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            current = connection.execute("SELECT * FROM expanded_research_publish_lease WHERE lease_name='expanded97'").fetchone()
            if current is not None and current["expires_at"] > now_text and current["owner_id"] != self.worker_id:
                raise ExpandedResearchPublisherBusy("expanded publisher lease is held")
            fence = connection.execute("SELECT highest_epoch FROM expanded_research_publish_fence WHERE owner_id=?", (self.worker_id,)).fetchone()
            epoch = (int(fence[0]) if fence else 0) + 1
            connection.execute(
                "INSERT INTO expanded_research_publish_fence(owner_id,highest_epoch,updated_at) VALUES(?,?,?) ON CONFLICT(owner_id) DO UPDATE SET highest_epoch=excluded.highest_epoch,updated_at=excluded.updated_at",
                (self.worker_id, epoch, now_text),
            )
            connection.execute(
                "INSERT INTO expanded_research_publish_lease(lease_name,owner_id,fencing_epoch,expires_at) VALUES('expanded97',?,?,?) ON CONFLICT(lease_name) DO UPDATE SET owner_id=excluded.owner_id,fencing_epoch=excluded.fencing_epoch,expires_at=excluded.expires_at",
                (self.worker_id, epoch, expiry_text),
            )
        return {"epoch": epoch}

    def _release_lease(self, epoch: int) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM expanded_research_publish_lease WHERE lease_name='expanded97' AND owner_id=? AND fencing_epoch=?", (self.worker_id, epoch))

    def _mark_attempt(self, result_id: str, key: str, epoch: int) -> None:
        now = stamp(self._now())
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO expanded_research_publish_state(result_id,idempotency_key,status,attempts,fencing_epoch,updated_at) VALUES(?,?, 'sending',1,?,?) ON CONFLICT(result_id) DO UPDATE SET status='sending',attempts=attempts+1,fencing_epoch=excluded.fencing_epoch,updated_at=excluded.updated_at",
                (result_id, key, epoch, now),
            )

    def _mark_sent(self, result_id: str, key: str, response: Mapping[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE expanded_research_publish_state SET status='sent',response_json=?,last_error=NULL,updated_at=? WHERE result_id=? AND idempotency_key=?", (json.dumps(dict(response), sort_keys=True), stamp(self._now()), result_id, key))

    def _mark_error(self, result_id: str, key: str, error: str) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE expanded_research_publish_state SET status='unknown',last_error=?,updated_at=? WHERE result_id=? AND idempotency_key=?", (error[:500], stamp(self._now()), result_id, key))

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.state_database, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ExpandedResearchPublisherError("expanded publisher clock must include a timezone")
        return value.astimezone(UTC)


def _https_transport(url: str, body: bytes, headers: Mapping[str, str]) -> Mapping[str, Any]:
    request = Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urlopen(request, timeout=20) as response:
            raw = response.read(64 * 1024)
            return json.loads(raw.decode("utf-8")) if raw else {"status": response.status}
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise ExpandedResearchPublisherError("expanded publisher delivery failed") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="publish one bounded expanded-research result")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if not args.once:
        parser.error("--once is required")
    values = os.environ
    enabled = values.get("TRADEAI_EXPANDED_RESEARCH_PUBLISH_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on"}
    if not enabled:
        print(json.dumps({"state": "disabled", "published": 0, "outbound": False, "user_visible": False}, separators=(",", ":")))
        return 0
    publisher = ExpandedResearchPublisher(
        source_spool=Path(values.get("TRADEAI_EXPANDED_RESEARCH_PUBLISH_SOURCE_SPOOL", "")),
        state_database=Path(values.get("TRADEAI_EXPANDED_RESEARCH_PUBLISH_STATE_DB", "")),
        base_url=values.get("TRADEAI_EXPANDED_RESEARCH_PUBLISH_BASE_URL", "https://ciclotrade.com"),
        shared_secret=values.get("TRADEAI_EXPANDED_RESEARCH_PUBLISH_SHARED_SECRET", ""),
        worker_id=values.get("TRADEAI_EXPANDED_RESEARCH_PUBLISH_WORKER_ID", "expanded-research-publisher"),
        enabled=enabled,
    )
    print(json.dumps(publisher.publish_once(), ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
