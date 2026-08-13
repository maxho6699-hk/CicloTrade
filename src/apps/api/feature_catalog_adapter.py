"""Thin API-facing adapter for the feature catalog integration owner."""

from __future__ import annotations

from typing import Any, Mapping

from core.database import DatabaseManager
from core.feature_catalog import FeaturePreferenceStore, resolve_feature_catalog


class FeatureCatalogAdapter:
    def __init__(self, database: DatabaseManager):
        self.preferences = FeaturePreferenceStore(database)

    def read(
        self,
        *,
        user_id: int,
        plan: str,
        runtime: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        result = resolve_feature_catalog(plan, runtime)
        result["preferences"] = self.preferences.load(user_id)
        return result

    def update_preferences(
        self,
        *,
        user_id: int,
        plan: str,
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
        result = resolve_feature_catalog(plan, runtime)
        result["preferences"] = preferences
        return result

    def record_recent(
        self,
        *,
        user_id: int,
        plan: str,
        key: str,
        expected_version: int,
        runtime: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        result = resolve_feature_catalog(plan, runtime)
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
