# -*- coding: utf-8 -*-
"""Small helpers for safely merging persisted user settings."""

from __future__ import annotations

from datetime import datetime
from core.compat import UTC
import json
from typing import Any

from core.database import DatabaseManager, get_database


def load_user_settings(user_id: int, database: DatabaseManager | None = None) -> dict[str, Any]:
    row = (database or get_database()).fetch_one(
        "SELECT settings_json FROM user_settings WHERE user_id=?", (user_id,)
    )
    if not row:
        return {}
    try:
        value = json.loads(row["settings_json"])
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def merge_user_settings(
    user_id: int,
    updates: dict[str, Any],
    database: DatabaseManager | None = None,
) -> dict[str, Any]:
    db = database or get_database()
    with db.transaction() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT settings_json FROM user_settings WHERE user_id=?", (user_id,)
        ).fetchone()
        try:
            settings = json.loads(row["settings_json"]) if row else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            settings = {}
        settings = settings if isinstance(settings, dict) else {}
        settings.update(updates)
        connection.execute(
            """INSERT INTO user_settings (user_id,settings_json,updated_at) VALUES (?,?,?)
               ON CONFLICT(user_id) DO UPDATE SET
                 settings_json=excluded.settings_json,updated_at=excluded.updated_at""",
            (
                user_id,
                json.dumps(settings, ensure_ascii=False),
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
    return settings
