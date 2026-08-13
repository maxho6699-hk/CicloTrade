"""Thin API-facing adapter for the feature catalog integration owner."""

from __future__ import annotations

from typing import Any, Mapping

from core.database import DatabaseManager
from core.feature_catalog import FeaturePreferenceStore, resolve_feature_catalog
from core.membership import authoritative_membership_row


class FeatureCatalogAdapter:
    def __init__(self, database: DatabaseManager):
        self.database = database
        self.preferences = FeaturePreferenceStore(database)

    def _authoritative_plan(self, user_id: int) -> str:
        with self.database.transaction() as connection:
            row = connection.execute(
                """SELECT id,plan_type,subscription_expire FROM users
                   WHERE id=? AND is_active=1""",
                (int(user_id),),
            ).fetchone()
            if row is None:
                raise ValueError("feature catalog user is unavailable")
            return str(authoritative_membership_row(connection, row).get("plan_type") or "免费版")

    def read(
        self,
        *,
        user_id: int,
        runtime: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        result = resolve_feature_catalog(self._authoritative_plan(user_id), runtime)
        result["preferences"] = self.preferences.load(user_id)
        return result

    def update_preferences(
        self,
        *,
        user_id: int,
        payload: Mapping[str, object],
        runtime: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        if set(payload) != {"expected_version", "pinned", "recent"}:
            raise ValueError("feature preference payload has unknown or missing fields")
        preferences = self.preferences.replace(
            user_id,
            expected_version=payload["expected_version"],  # type: ignore[arg-type]
            pinned=payload["pinned"],  # type: ignore[arg-type]
            recent=payload["recent"],  # type: ignore[arg-type]
        )
        result = resolve_feature_catalog(self._authoritative_plan(user_id), runtime)
        result["preferences"] = preferences
        return result

    def record_recent(
        self,
        *,
        user_id: int,
        key: str,
        expected_version: int,
        runtime: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        result = resolve_feature_catalog(self._authoritative_plan(user_id), runtime)
        item = next((item for item in result["items"] if item["key"] == key), None)
        if item is None or item["availability"] != "available":
            raise ValueError("only an available feature can be recorded as recent")
        preferences = self.preferences.record_recent(
            user_id,
            key=key,
            expected_version=expected_version,
        )
        result["preferences"] = preferences
        return result
