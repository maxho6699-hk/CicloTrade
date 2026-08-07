# -*- coding: utf-8 -*-
"""Config-backed strategy registration with SQLite persistence."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

import yaml

from core.database import DatabaseManager, get_database
from core.plans import can


DEFAULT_CATALOG = Path(__file__).resolve().parents[1] / "strategies" / "catalog.yaml"
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_FAMILIES = {"option", "equity"}
_RISK_LEVELS = {"low", "medium", "high"}
_FREE_STRATEGY_KEYS = frozenset({"option_long_call"})


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _json_object(value: Any, field: str) -> str:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an object")
    try:
        return json.dumps(dict(value), ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must contain valid JSON values") from exc


def _text(value: Any, field: str, *, limit: int = 1000) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} is required")
    if len(result) > limit:
        raise ValueError(f"{field} is too long")
    return result


class StrategyRegistry:
    """Load strategy definitions from YAML and register database extensions."""

    def __init__(
        self,
        database: DatabaseManager | None = None,
        catalog_path: str | Path = DEFAULT_CATALOG,
    ) -> None:
        self.db = database or get_database()
        self.catalog_path = Path(catalog_path)

    @staticmethod
    def _normalise(definition: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(definition, Mapping):
            raise ValueError("strategy definition must be an object")
        key = _text(definition.get("key", definition.get("strategy_key")), "key", limit=64)
        if not _KEY_RE.fullmatch(key):
            raise ValueError("key must use lowercase letters, numbers, hyphens or underscores")
        family = _text(definition.get("family"), "family", limit=16)
        if family not in _FAMILIES:
            raise ValueError("family must be option or equity")
        risk = _text(definition.get("risk", definition.get("risk_level")), "risk", limit=16)
        if risk not in _RISK_LEVELS:
            raise ValueError("risk must be low, medium or high")
        return {
            "strategy_key": key,
            "name": _text(definition.get("name"), "name", limit=120),
            "family": family,
            "engine_key": _text(definition.get("engine", definition.get("engine_key")), "engine", limit=80),
            "scenario": _text(definition.get("scenario"), "scenario"),
            "description": _text(definition.get("description"), "description", limit=4000),
            "risk_level": risk,
            "parameters_json": _json_object(definition.get("parameters", {}), "parameters"),
            "rules_json": _json_object(definition.get("rules", {}), "rules"),
            "example_metrics_json": _json_object(definition.get("example_metrics", {}), "example_metrics"),
            "is_core": int(bool(definition.get("core", definition.get("is_core", False)))),
            "is_active": int(bool(definition.get("active", definition.get("is_active", True)))),
        }

    @staticmethod
    def _decode(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["key"] = result.pop("strategy_key")
        result["engine"] = result.pop("engine_key")
        result["risk"] = result.pop("risk_level")
        for stored, public in (
            ("parameters_json", "parameters"),
            ("rules_json", "rules"),
            ("example_metrics_json", "example_metrics"),
        ):
            result[public] = json.loads(result.pop(stored))
        result["core"] = bool(result.pop("is_core"))
        result["active"] = bool(result.pop("is_active"))
        return result

    def catalog_definitions(self) -> list[dict[str, Any]]:
        if not self.catalog_path.is_file():
            raise FileNotFoundError(f"strategy catalog not found: {self.catalog_path}")
        payload = yaml.safe_load(self.catalog_path.read_text(encoding="utf-8")) or {}
        items = payload.get("strategies") if isinstance(payload, Mapping) else None
        if not isinstance(items, list):
            raise ValueError("strategy catalog must contain a strategies list")
        return [self._normalise(item) for item in items]

    def register(
        self,
        definition: Mapping[str, Any],
        *,
        created_by: int | None = None,
        preserve_active: bool = False,
        audit_actor: int | None = None,
    ) -> dict[str, Any]:
        item = self._normalise(definition)
        now = _utc_now()
        columns = (
            "name", "family", "engine_key", "scenario", "description", "risk_level",
            "parameters_json", "rules_json", "example_metrics_json", "is_core",
        )
        with self.db.transaction() as conn:
            existing = conn.execute(
                "SELECT id FROM strategy_definitions WHERE strategy_key=?",
                (item["strategy_key"],),
            ).fetchone()
            if existing:
                assignments = ",".join(f"{column}=?" for column in columns)
                values = [item[column] for column in columns]
                if not preserve_active:
                    assignments += ",is_active=?"
                    values.append(item["is_active"])
                conn.execute(
                    f"UPDATE strategy_definitions SET {assignments},updated_at=? WHERE strategy_key=?",
                    (*values, now, item["strategy_key"]),
                )
            else:
                conn.execute(
                    """INSERT INTO strategy_definitions
                       (strategy_key,name,family,engine_key,scenario,description,risk_level,
                        parameters_json,rules_json,example_metrics_json,is_core,is_active,
                        created_by,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        item["strategy_key"], item["name"], item["family"], item["engine_key"],
                        item["scenario"], item["description"], item["risk_level"],
                        item["parameters_json"], item["rules_json"], item["example_metrics_json"],
                        item["is_core"], item["is_active"], created_by, now, now,
                    ),
                )
            if audit_actor is not None:
                conn.execute(
                    "INSERT INTO user_action_logs(user_id,action_type,details,created_at) VALUES (?,?,?,?)",
                    (
                        audit_actor, "ADMIN_STRATEGY_UPSERT",
                        json.dumps({"strategy_key": item["strategy_key"], "active": bool(item["is_active"])}, ensure_ascii=False),
                        now,
                    ),
                )
        return self.get(item["strategy_key"])

    def sync_catalog(self) -> list[dict[str, Any]]:
        """Upsert YAML definitions without re-enabling strategies disabled by an admin."""
        registered = []
        for item in self.catalog_definitions():
            public = {
                "key": item["strategy_key"],
                "name": item["name"],
                "family": item["family"],
                "engine": item["engine_key"],
                "scenario": item["scenario"],
                "description": item["description"],
                "risk": item["risk_level"],
                "parameters": json.loads(item["parameters_json"]),
                "rules": json.loads(item["rules_json"]),
                "example_metrics": json.loads(item["example_metrics_json"]),
                "core": bool(item["is_core"]),
                "active": bool(item["is_active"]),
            }
            registered.append(self.register(public, preserve_active=True))
        return registered

    def get(self, strategy_key: str) -> dict[str, Any]:
        row = self.db.fetch_one(
            "SELECT * FROM strategy_definitions WHERE strategy_key=?",
            (strategy_key,),
        )
        if row is None:
            raise KeyError(f"unknown strategy: {strategy_key}")
        return self._decode(row)  # type: ignore[return-value]

    def list(self, *, family: str | None = None, active_only: bool = True) -> list[dict[str, Any]]:
        if family is not None and family not in _FAMILIES:
            raise ValueError("family must be option or equity")
        where, params = [], []
        if active_only:
            where.append("is_active=1")
        if family:
            where.append("family=?")
            params.append(family)
        sql = "SELECT * FROM strategy_definitions"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY is_core DESC,family,name,strategy_key"
        return [self._decode(row) for row in self.db.fetch_all(sql, tuple(params))]  # type: ignore[misc]

    def list_for_plan(self, plan: str, *, family: str | None = None) -> list[dict[str, Any]]:
        """Return active strategies available to a subscription plan."""
        items = self.list(family=family)
        if can(plan, "strategy_all"):
            return items
        return [item for item in items if item["key"] in _FREE_STRATEGY_KEYS]

    def check_plan_access(self, plan: str, name: str) -> bool | None:
        """Return access for a registered strategy; None means manual/legacy name."""
        rows = self.db.fetch_all(
            "SELECT strategy_key,name,family,is_active FROM strategy_definitions WHERE name=?",
            (str(name).strip(),),
        )
        if not rows:
            return None
        row = rows[0]
        if not bool(row["is_active"]):
            raise ValueError("该策略已由研究后台停用。")
        return any(
            item["key"] == row["strategy_key"]
            for item in self.list_for_plan(plan, family=row["family"])
        )

    def set_active(self, strategy_key: str, active: bool, *, audit_actor: int | None = None) -> dict[str, Any]:
        now = _utc_now()
        with self.db.transaction() as conn:
            changed = conn.execute(
                "UPDATE strategy_definitions SET is_active=?,updated_at=? WHERE strategy_key=?",
                (int(bool(active)), now, strategy_key),
            ).rowcount
            if not changed:
                raise KeyError(f"unknown strategy: {strategy_key}")
            if audit_actor is not None:
                conn.execute(
                    "INSERT INTO user_action_logs(user_id,action_type,details,created_at) VALUES (?,?,?,?)",
                    (
                        audit_actor, "ADMIN_STRATEGY_STATE",
                        json.dumps({"strategy_key": strategy_key, "active": bool(active)}, ensure_ascii=False), now,
                    ),
                )
        return self.get(strategy_key)
