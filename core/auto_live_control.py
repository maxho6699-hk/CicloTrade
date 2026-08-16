from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Callable, Iterator, Mapping

from core.auto_live_control_common import (
    MANDATE_STATES,
    TERMINAL_STATES,
    SUPPORTED_BROKERS,
    AutoLiveConflict,
    AutoLiveControlError,
    _HEX64_RE,
    _iso,
    _now,
    _opaque,
    _row_public,
    _text,
    _timestamp,
    canonical_json,
    sha256_json,
)
from core.auto_live_control_gates import AutoLiveGatesMixin
from core.auto_live_control_snapshot import AutoLiveSnapshotMixin
from core.database import DatabaseManager


class AutoLiveControlPlane(AutoLiveGatesMixin, AutoLiveSnapshotMixin):
    def __init__(
        self,
        database: DatabaseManager | sqlite3.Connection,
        *,
        gate_checker: Callable[..., Mapping[str, Any]] | None = None,
    ) -> None:
        self.db = database
        if isinstance(database, sqlite3.Connection):
            database.row_factory = sqlite3.Row
        self.gate_checker = gate_checker

    @contextmanager
    def _tx(self) -> Iterator[Any]:
        if isinstance(self.db, DatabaseManager):
            with self.db.transaction() as conn:
                conn.execute("BEGIN IMMEDIATE")
                yield conn
            return
        conn = self.db
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    @staticmethod
    def _user(conn: Any, user_id: int) -> Any:
        row = conn.execute("SELECT id,is_active FROM users WHERE id=?", (int(user_id),)).fetchone()
        if not row or int(row["is_active"]) != 1:
            raise AutoLiveControlError("账户不可用。", 403)
        return row

    @staticmethod
    def _mandate(conn: Any, user_id: int, public_id: str) -> Any:
        row = conn.execute(
            "SELECT * FROM auto_live_mandates WHERE public_id=? AND user_id=?", (public_id, int(user_id))
        ).fetchone()
        if not row:
            raise AutoLiveControlError("mandate 不存在。", 404)
        return row

    @staticmethod
    def _event(
        conn: Any,
        row: Any,
        event_type: str,
        to_state: str,
        payload: Mapping[str, Any],
        now: str,
        from_state: str | None = None,
    ) -> None:
        body = dict(payload)
        conn.execute(
            """INSERT INTO auto_live_mandate_events
            (event_id,mandate_public_id,user_id,event_type,from_state,to_state,payload_json,payload_sha256,created_at)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                _opaque("mante"),
                row["public_id"],
                row["user_id"],
                event_type,
                from_state if from_state is not None else row["state"],
                to_state,
                canonical_json(body),
                sha256_json(body),
                now,
            ),
        )

    @staticmethod
    def _state(conn: Any, row: Any, state: str, now: str, event_type: str, payload: Mapping[str, Any]) -> None:
        previous = str(row["state"])
        if previous == state:
            return
        conn.execute(
            "UPDATE auto_live_mandates SET state=?,updated_at=?,confirmed_at=NULL,confirmation_digest=NULL WHERE public_id=?",
            (state, now, row["public_id"]),
        )
        AutoLiveControlPlane._event(conn, row, event_type, state, payload, now, previous)

    def create_mandate(
        self,
        user_id: int,
        payload: Mapping[str, Any],
        *,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        allowed = {
            "broker_account_id",
            "strategy_version",
            "risk_version",
            "capital_limit_minor",
            "frequency_limit",
            "valid_from",
            "valid_until",
        }
        if not isinstance(payload, Mapping) or set(payload) != allowed:
            raise AutoLiveControlError("mandate 字段不完整或包含未知字段。")
        broker_id = payload["broker_account_id"]
        if isinstance(broker_id, bool) or not isinstance(broker_id, int) or broker_id <= 0:
            raise AutoLiveControlError("券商账户绑定无效。")
        strategy = _text(payload["strategy_version"], "策略版本")
        risk = _text(payload["risk_version"], "风险版本")
        try:
            capital = int(payload["capital_limit_minor"])
            frequency = int(payload["frequency_limit"])
        except (TypeError, ValueError) as exc:
            raise AutoLiveControlError("资本或频率边界无效。") from exc
        if capital <= 0 or frequency <= 0:
            raise AutoLiveControlError("资本与频率边界必须为正数。")
        start, end = _timestamp(payload["valid_from"], "valid_from"), _timestamp(payload["valid_until"], "valid_until")
        if end <= start:
            raise AutoLiveControlError("有效期边界无效。")
        key = None
        if idempotency_key is not None:
            key = _text(idempotency_key, "Idempotency-Key", 128)
            if len(key) < 8:
                raise AutoLiveControlError("Idempotency-Key 必须为 8 至 128 个字符。")
        request_digest = sha256_json(
            {
                "broker_account_id": broker_id,
                "strategy_version": strategy,
                "risk_version": risk,
                "capital_limit_minor": capital,
                "frequency_limit": frequency,
                "valid_from": start,
                "valid_until": end,
            }
        )
        moment_dt = _now(now)
        moment = _iso(moment_dt)
        with self._tx() as conn:
            self._user(conn, user_id)
            if key is not None:
                replay = conn.execute(
                    "SELECT request_sha256,mandate_public_id FROM auto_live_mandate_requests WHERE owner_id=? AND idempotency_key=?",
                    (int(user_id), key),
                ).fetchone()
                if replay:
                    if str(replay["request_sha256"]) != request_digest:
                        raise AutoLiveConflict(
                            "相同 Idempotency-Key 不得建立不同 mandate。"
                        )
                    row = self._mandate(conn, user_id, str(replay["mandate_public_id"]))
                    broker_public_id = self._ensure_broker_ref(
                        conn, int(user_id), int(row["broker_account_id"]), moment
                    )
                    return {**_row_public(row), "broker_account_public_id": broker_public_id}
            broker = conn.execute("SELECT id,user_id,provider FROM broker_accounts WHERE id=?", (broker_id,)).fetchone()
            if (
                not broker
                or int(broker["user_id"]) != int(user_id)
                or str(broker["provider"]).casefold() not in SUPPORTED_BROKERS
            ):
                raise AutoLiveControlError("券商账户不存在或不受支持。", 403)
            contract = self._approved_contract(
                conn,
                {"strategy_version": strategy, "risk_version": risk},
                moment_dt,
            )
            if contract is None:
                raise AutoLiveControlError("策略/风险合同未获服务端批准或已失效。", 403)
            broker_public_id = self._ensure_broker_ref(conn, int(user_id), int(broker_id), moment)
            public_id = _opaque("mandate")
            snapshot = {
                "schema_version": 1,
                "broker_account_id": int(broker_id),
                "strategy_version": strategy,
                "risk_version": risk,
                "capital_limit_minor": capital,
                "frequency_limit": frequency,
                "valid_from": start,
                "valid_until": end,
                "contract_snapshot_sha256": str(contract["snapshot_sha256"]),
            }
            digest = sha256_json(snapshot)
            conn.execute(
                """INSERT INTO auto_live_mandates
                (public_id,user_id,broker_account_id,strategy_version,risk_version,capital_limit_minor,frequency_limit,
                 valid_from,valid_until,state,snapshot_json,snapshot_sha256,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,'draft',?,?,?,?)""",
                (
                    public_id,
                    int(user_id),
                    broker_id,
                    strategy,
                    risk,
                    capital,
                    frequency,
                    start,
                    end,
                    canonical_json(snapshot),
                    digest,
                    moment,
                    moment,
                ),
            )
            row = conn.execute("SELECT * FROM auto_live_mandates WHERE public_id=?", (public_id,)).fetchone()
            self._event(conn, row, "MANDATE_CREATED", "draft", {"snapshot_sha256": digest}, moment, None)
            if key is not None:
                conn.execute(
                    """INSERT INTO auto_live_mandate_requests
                       (public_id,owner_id,idempotency_key,request_sha256,mandate_public_id,created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (_opaque("mreq"), int(user_id), key, request_digest, public_id, moment),
                )
            return {**_row_public(row), "broker_account_public_id": broker_public_id}

    def resume_mandate(self, user_id: int, mandate_public_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        moment_dt = _now(now)
        moment = _iso(moment_dt)
        with self._tx() as conn:
            row = self._mandate(conn, user_id, _text(mandate_public_id, "mandate public id"))
            was_terminal = str(row["state"]) in TERMINAL_STATES
            row = self._expire_if_due(conn, row, moment_dt)
            if str(row["state"]) in TERMINAL_STATES:
                if not was_terminal:
                    return _row_public(row)
                raise AutoLiveControlError("expired 或 revoked mandate 不可恢复。", 409)
            if str(row["state"]) not in {"paused", "blocked"}:
                raise AutoLiveControlError("只有 paused 或 blocked mandate 可恢复。", 409)
            gates = self._gates(conn, row, moment_dt, runtime_mode="lifecycle")
            if not gates["all_ok"]:
                self._state(conn, row, "blocked", moment, "RESUME_BLOCKED", gates)
                updated = conn.execute(
                    "SELECT * FROM auto_live_mandates WHERE public_id=?", (row["public_id"],)
                ).fetchone()
                return {**_row_public(updated), **gates}
            self._state(
                conn, row, "pending_confirmation", moment, "RESUME_CONFIRMATION_REQUESTED", {"gates": gates["gates"]}
            )
            updated = conn.execute("SELECT * FROM auto_live_mandates WHERE public_id=?", (row["public_id"],)).fetchone()
            return {
                **_row_public(updated),
                **gates,
                "confirmation_phrase": f"ACTIVATE {row['public_id']}",
                "confirmation_snapshot_sha256": str(updated["snapshot_sha256"]),
            }

    def confirm_mandate(
        self,
        user_id: int,
        mandate_public_id: str,
        confirmation_phrase: str | Mapping[str, Any],
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        phrase_value: Any = confirmation_phrase
        if isinstance(confirmation_phrase, Mapping):
            if set(confirmation_phrase) != {"mandate_public_id", "snapshot_sha256", "confirmation_phrase"}:
                raise AutoLiveControlError("确认字段不完整或包含未知字段。")
            if confirmation_phrase["mandate_public_id"] != mandate_public_id:
                raise AutoLiveControlError("确认 mandate 不匹配。", 409)
            if not isinstance(confirmation_phrase["snapshot_sha256"], str) or not _HEX64_RE.fullmatch(
                confirmation_phrase["snapshot_sha256"]
            ):
                raise AutoLiveControlError("确认快照哈希无效。", 409)
            phrase_value = confirmation_phrase["confirmation_phrase"]
        phrase = _text(phrase_value, "确认短语", 160)
        moment_dt = _now(now)
        moment = _iso(moment_dt)
        with self._tx() as conn:
            row = self._mandate(conn, user_id, _text(mandate_public_id, "mandate public id"))
            was_terminal = str(row["state"]) in TERMINAL_STATES
            row = self._expire_if_due(conn, row, moment_dt)
            if str(row["state"]) != "pending_confirmation":
                if str(row["state"]) in TERMINAL_STATES:
                    if not was_terminal:
                        return _row_public(row)
                    raise AutoLiveControlError("expired 或 revoked mandate 不可恢复。", 409)
                raise AutoLiveControlError("mandate 尚未等待精确确认。", 409)
            if (
                isinstance(confirmation_phrase, Mapping)
                and confirmation_phrase["snapshot_sha256"] != row["snapshot_sha256"]
            ):
                raise AutoLiveControlError("确认快照已变化，必须重新获取确认内容。", 409)
            expected = f"ACTIVATE {row['public_id']}"
            if phrase != expected:
                raise AutoLiveControlError("确认短语不匹配。", 409)
            gates = self._gates(conn, row, moment_dt)
            if not gates["all_ok"]:
                self._state(conn, row, "blocked", moment, "CONFIRMATION_BLOCKED", gates)
                updated = conn.execute(
                    "SELECT * FROM auto_live_mandates WHERE public_id=?", (row["public_id"],)
                ).fetchone()
                return {**_row_public(updated), **gates}
            digest = sha256_json(
                {
                    "mandate_public_id": row["public_id"],
                    "snapshot_sha256": row["snapshot_sha256"],
                    "confirmation_phrase": phrase,
                }
            )
            conn.execute(
                "UPDATE auto_live_mandates SET state='active',confirmed_at=?,confirmation_digest=?,updated_at=? WHERE public_id=?",
                (moment, digest, moment, row["public_id"]),
            )
            self._event(
                conn,
                row,
                "MANDATE_CONFIRMED",
                "active",
                {"confirmation_digest": digest, "snapshot_sha256": row["snapshot_sha256"]},
                moment,
                "pending_confirmation",
            )
            updated = conn.execute("SELECT * FROM auto_live_mandates WHERE public_id=?", (row["public_id"],)).fetchone()
            self._initialize_confirmed_runtime(conn, updated, moment)
            return {**_row_public(updated), **gates}


AutoLiveControlPlane.create = AutoLiveControlPlane.create_mandate
AutoLiveControlPlane.submit = AutoLiveControlPlane.submit_confirmation
AutoLiveControlPlane.confirm = AutoLiveControlPlane.confirm_mandate
AutoLiveControlPlane.resume = AutoLiveControlPlane.resume_mandate
AutoLiveControlPlane.pause = AutoLiveControlPlane.request_pause
AutoLiveControlPlane.expire = AutoLiveControlPlane.expire_mandate
AutoLiveControlPlane.revoke = AutoLiveControlPlane.revoke_mandate
AutoLiveControlPlane.create_from_public_ref = AutoLiveControlPlane.create_mandate_from_public_ref
AutoLiveControlPlane.pause_broker_public_ref = AutoLiveControlPlane.request_pause_public_ref

__all__ = [
    "AutoLiveConflict",
    "AutoLiveControlError",
    "AutoLiveControlPlane",
    "MANDATE_STATES",
    "canonical_json",
    "sha256_json",
]
