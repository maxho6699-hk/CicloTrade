"""Thin API-facing adapter for the feature catalog integration owner."""

from __future__ import annotations

from typing import Any, Mapping

from core.database import DatabaseManager
from core.entitlement_consumer import verified_capabilities
from core.feature_catalog import FeaturePreferenceStore, resolve_feature_catalog
from core.membership import authoritative_membership_row


class FeatureCatalogAdapter:
    def __init__(self, database: DatabaseManager):
        self.database = database
        self.preferences = FeaturePreferenceStore(database)

    @staticmethod
    def _entitlement_snapshot(connection: Any, user_id: int) -> tuple[str, set[str], bool]:
        row = connection.execute(
            """SELECT u.id,u.plan_type,u.subscription_expire,u.is_admin,r.role FROM users u
               LEFT JOIN admin_roles r ON r.user_id=u.id
               WHERE u.id=? AND u.is_active=1""",
            (int(user_id),),
        ).fetchone()
        if row is None:
            raise ValueError("feature catalog user is unavailable")
        plan = str(authoritative_membership_row(connection, row).get("plan_type") or "免费版")
        return plan, verified_capabilities(connection, plan), bool(row["is_admin"] and row["role"] == "super_admin")

    def _read_entitlement_snapshot(self, user_id: int) -> tuple[str, set[str], bool]:
        with self.database.transaction() as connection:
            return self._entitlement_snapshot(connection, user_id)

    def read(
        self,
        *,
        user_id: int,
        runtime: Mapping[str, object] | None = None,
    ) -> dict[str, Any]:
        plan, capabilities, is_super_admin = self._read_entitlement_snapshot(user_id)
        result = resolve_feature_catalog(plan, runtime, capabilities=capabilities, is_super_admin=is_super_admin)
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
        pinned = payload["pinned"]
        if not isinstance(pinned, list) or any(not isinstance(key, str) for key in pinned):
            raise ValueError("pinned must be a string list")
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            plan, capabilities, is_super_admin = self._entitlement_snapshot(connection, user_id)
            result = resolve_feature_catalog(plan, runtime, capabilities=capabilities, is_super_admin=is_super_admin)
            available = {item["key"] for item in result["items"] if item["availability"] == "available"}
            if any(key not in available for key in pinned):
                raise ValueError("only an available feature can be pinned")
            preferences = self.preferences.replace(
                user_id,
                expected_version=payload["expected_version"],  # type: ignore[arg-type]
                pinned=pinned,
                recent=payload["recent"],  # type: ignore[arg-type]
                connection=connection,
            )
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
        with self.database.transaction() as connection:
            connection.execute("BEGIN IMMEDIATE")
            plan, capabilities, is_super_admin = self._entitlement_snapshot(connection, user_id)
            result = resolve_feature_catalog(plan, runtime, capabilities=capabilities, is_super_admin=is_super_admin)
            item = next((item for item in result["items"] if item["key"] == key), None)
            if item is None or item["availability"] != "available":
                raise ValueError("only an available feature can be recorded as recent")
            preferences = self.preferences.record_recent(
                user_id,
                key=key,
                expected_version=expected_version,
                connection=connection,
            )
        result["preferences"] = preferences
        return result
