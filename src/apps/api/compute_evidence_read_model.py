"""Sanitized read model for quarantined generic compute evidence."""

from __future__ import annotations

import json
from typing import Any, Mapping

from core.backtest_queue_database import BacktestQueueDatabase
from core.compute_evidence_contracts import ComputeEvidenceError, validate_package


class ComputeEvidenceReadModel:
    def __init__(self, database: BacktestQueueDatabase) -> None:
        if not isinstance(database, BacktestQueueDatabase):
            raise TypeError("database must be BacktestQueueDatabase")
        self.database = database

    def status(self) -> dict[str, Any]:
        counts = {
            row["publication_state"]: int(row["total"])
            for row in self.database.fetch_all(
                """SELECT publication_state,count(*) AS total
                   FROM compute_evidence_receipts GROUP BY publication_state"""
            )
            if row["publication_state"] in {"quarantine", "shadow"}
        }
        latest = self.database.fetch_one(
            """SELECT received_at FROM compute_evidence_receipts
               WHERE publication_state IN ('quarantine','shadow')
               ORDER BY received_at DESC,receipt_key DESC LIMIT 1"""
        )
        return {
            "available": bool(latest),
            "publication_ceiling": "shadow",
            "research_only": True,
            "actionable": False,
            "user_visible": False,
            "counts": {
                "quarantine": counts.get("quarantine", 0),
                "shadow": counts.get("shadow", 0),
            },
            "last_received_at": latest["received_at"] if latest else None,
        }

    def latest(self) -> dict[str, Any]:
        row = self.database.fetch_one(
            """SELECT * FROM compute_evidence_receipts
               WHERE publication_state IN ('quarantine','shadow')
               ORDER BY received_at DESC,receipt_key DESC LIMIT 1"""
        )
        return self._collection([row] if row else [], limit=1, latest=True)

    def history(self, limit: int = 20) -> dict[str, Any]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("compute evidence history limit must be between 1 and 100")
        rows = self.database.fetch_all(
            """SELECT * FROM compute_evidence_receipts
               WHERE publication_state IN ('quarantine','shadow')
               ORDER BY received_at DESC,receipt_key DESC LIMIT ?""",
            (limit,),
        )
        return self._collection(rows, limit=limit, latest=False)

    def _collection(
        self,
        rows: list[Mapping[str, Any]],
        *,
        limit: int,
        latest: bool,
    ) -> dict[str, Any]:
        items = [item for row in rows if (item := self._item(row)) is not None]
        result: dict[str, Any] = {
            "available": bool(items),
            "publication_ceiling": "shadow",
            "research_only": True,
            "actionable": False,
            "user_visible": False,
        }
        if latest:
            result["evidence"] = items[0] if items else None
        else:
            result["limit"] = limit
            result["items"] = items
        return result

    @staticmethod
    def _item(row: Mapping[str, Any]) -> dict[str, Any] | None:
        try:
            package = validate_package(json.loads(str(row["payload_json"])))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError, ComputeEvidenceError):
            return None
        validation = package["result"]["evidence"]["validation"]
        return {
            "receipt_key": str(row["receipt_key"]),
            "package_id": package["package_id"],
            "publication_state": str(row["publication_state"]),
            "received_at": str(row["received_at"]),
            "completed_at": package["completed_at"],
            "job_id": package["job_id"],
            "candidate_id": package["manifest"]["candidate_id"],
            "candidate_version": package["manifest"]["candidate_version"],
            "market": package["manifest"]["asset_universe"]["market"],
            "instrument_family": "equity",
            "symbols": list(package["manifest"]["asset_universe"]["symbols"]),
            "candidate_status": validation["candidate_status"],
            "attempt_no": package["attempt_no"],
            "compute_fencing_epoch": package["fencing_epoch"],
            "manifest_sha256": package["manifest_sha256"],
            "result_sha256": package["result_sha256"],
            "package_sha256": str(row["package_sha256"]),
            "artifact_count": len(package["artifacts"]),
            "research_only": True,
            "actionable": False,
            "user_visible": False,
        }
