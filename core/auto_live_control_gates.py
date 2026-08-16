"""Independent fail-closed gate evaluation for auto-live resumption/start."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Mapping

from core.broker_authorization import broker_execution_authorized
from core.auto_live_control_common import (
    AutoLiveConflict,
    AutoLiveControlError,
    TERMINAL_STATES,
    _ID_RE,
    _gate,
    _iso,
    _now,
    _opaque,
    _row_public,
    _text,
    canonical_json,
    sha256_json,
    sha256_text,
)
from core.entitlement_consumer import policy_account_limit
from core.membership import resolve_membership_snapshot
from core.auto_live_runtime_integrity import RuntimeIntegrityMixin

REQUIRED_GATE_KEYS = frozenset(
    {
        "entitlement_account_capacity",
        "telegram",
        "broker_authorization",
        "broker_live_environment",
        "platform_switch",
        "global_pause",
        "strategy",
        "risk",
        "data_health",
    }
)


class AutoLiveGatesMixin(RuntimeIntegrityMixin):
    @staticmethod
    def _approved_contract(conn: Any, row: Any, now: datetime) -> Any:
        contract = conn.execute(
            "SELECT * FROM auto_live_strategy_risk_contracts WHERE strategy_version=? AND risk_version=?",
            (row["strategy_version"], row["risk_version"]),
        ).fetchone()
        if not contract or int(contract["is_active"]) != 1:
            return None
        try:
            approved_at = datetime.fromisoformat(str(contract["approved_at"]))
            valid_until = datetime.fromisoformat(str(contract["valid_until"]))
            snapshot = json.loads(str(contract["snapshot_json"]))
            valid_snapshot = isinstance(snapshot, Mapping) and sha256_json(snapshot) == str(contract["snapshot_sha256"])
        except (TypeError, ValueError, json.JSONDecodeError):
            return None
        if approved_at.tzinfo is None or valid_until.tzinfo is None or not valid_snapshot:
            return None
        return contract if approved_at <= now < valid_until else None

    @staticmethod
    def _mandate_contract_matches(row: Any, contract: Any) -> bool:
        if contract is None:
            return False
        try:
            snapshot = json.loads(str(row["snapshot_json"]))
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        expected = {
            "broker_account_id": int(row["broker_account_id"]),
            "strategy_version": str(row["strategy_version"]),
            "risk_version": str(row["risk_version"]),
            "capital_limit_minor": int(row["capital_limit_minor"]),
            "frequency_limit": int(row["frequency_limit"]),
            "valid_from": str(row["valid_from"]),
            "valid_until": str(row["valid_until"]),
        }
        return (
            isinstance(snapshot, Mapping)
            and sha256_json(snapshot) == str(row["snapshot_sha256"])
            and all(snapshot.get(key) == value for key, value in expected.items())
            and str(snapshot.get("strategy_version")) == str(row["strategy_version"])
            and str(snapshot.get("risk_version")) == str(row["risk_version"])
            and str(snapshot.get("contract_snapshot_sha256")) == str(contract["snapshot_sha256"])
        )

    @staticmethod
    def _valid_window(row: Any, now: datetime) -> bool:
        try:
            valid_from = datetime.fromisoformat(str(row["valid_from"]))
            valid_until = datetime.fromisoformat(str(row["valid_until"]))
        except (TypeError, ValueError):
            return False
        return (
            valid_from.tzinfo is not None
            and valid_until.tzinfo is not None
            and valid_from <= now < valid_until
        )

    def _gates(self, conn: Any, row: Any, now: datetime, *, runtime_mode: str = "confirm") -> dict[str, Any]:
        contract = self._approved_contract(conn, row, now)
        contract_ok = self._mandate_contract_matches(row, contract)
        valid_window = self._valid_window(row, now)
        if self.gate_checker is not None:
            supplied = dict(self.gate_checker(conn, row, now))
            gates = [
                _gate(
                    k,
                    bool(supplied.get(k, False)) and (contract_ok if k in {"strategy", "risk"} else True),
                    "provided"
                    if supplied.get(k, False) and (contract_ok if k in {"strategy", "risk"} else True)
                    else "strategy_risk_contract_unproven"
                    if k in {"strategy", "risk"} and not contract_ok
                    else "blocked",
                )
                for k in sorted(REQUIRED_GATE_KEYS)
            ]
            gates.append(
                _gate(
                    "strategy_risk_contract",
                    contract_ok,
                    "approved_exact_snapshot" if contract_ok else "unapproved_or_tampered",
                )
            )
            gates.append(_gate("valid_window", valid_window, "within_valid_window" if valid_window else "outside_valid_window"))
            gates.extend(self._runtime_safety_gates(conn, row, now, mode=runtime_mode))
            return {"all_ok": all(item["ok"] for item in gates), "gates": gates}
        user = conn.execute(
            "SELECT id,plan_type,subscription_expire FROM users WHERE id=?", (row["user_id"],)
        ).fetchone()
        try:
            plan = (
                resolve_membership_snapshot(
                    conn,
                    int(row["user_id"]),
                    now,
                    cached_plan=user["plan_type"],
                    cached_expiry=user["subscription_expire"],
                )["plan_type"]
                if user
                else ""
            )
        except Exception:
            plan = ""
        try:
            capacity = policy_account_limit(conn, str(plan))
        except Exception:
            capacity = 0
        active_count = conn.execute(
            "SELECT COUNT(*) FROM auto_live_mandates WHERE user_id=? AND state='active' AND public_id<>?",
            (row["user_id"], row["public_id"]),
        ).fetchone()[0]
        try:
            telegram = conn.execute(
                """SELECT 1
                   FROM telegram_accounts t
                   JOIN user_settings s ON s.user_id=t.user_id
                  WHERE t.user_id=?
                    AND t.is_active=1
                    AND t.revoked_at IS NULL
                    AND json_extract(s.settings_json,'$.telegram.verified')=1
                    AND json_extract(s.settings_json,'$.telegram.consent')=1
                  UNION
                 SELECT 1
                   FROM telegram_verifications v
                   JOIN user_settings s ON s.user_id=v.user_id
                  WHERE v.user_id=?
                    AND v.consent=1
                    AND v.verified_at IS NOT NULL
                    AND v.expires_at>?
                    AND json_extract(s.settings_json,'$.telegram.verified')=1
                    AND json_extract(s.settings_json,'$.telegram.consent')=1
                  LIMIT 1""",
                (row["user_id"], row["user_id"], _iso(now)),
            ).fetchone()
        except sqlite3.Error:
            telegram = None
        broker = conn.execute(
            "SELECT * FROM broker_accounts WHERE id=? AND user_id=?", (row["broker_account_id"], row["user_id"])
        ).fetchone()
        provider = str(broker["provider"]).casefold() if broker else ""
        environment = bool(
            broker
            and int(broker["is_active"]) == 1
            and str(broker["mode"]).casefold() == "live"
            and str(broker["status"]).casefold() == "authorized"
        )
        try:
            platform = {
                str(r["control_key"]): str(r["control_value"]).casefold()
                for r in conn.execute("SELECT control_key,control_value FROM platform_controls").fetchall()
            }
        except sqlite3.Error:
            platform = {}

        def ready(key: str, values: set[str]) -> bool:
            return platform.get(key, "") in values

        gates = [
            _gate(
                "entitlement_account_capacity",
                capacity > int(active_count),
                "capacity_available" if capacity > int(active_count) else "capacity_unavailable",
            ),
            _gate("telegram", telegram is not None, "active_verified_consented" if telegram else "telegram_unverified"),
            _gate(
                "broker_authorization",
                bool(broker and broker_execution_authorized(broker, str(broker["external_account_id"]))),
                "authorized" if broker else "missing",
            ),
            _gate(
                "broker_live_environment",
                environment and provider == "tiger",
                "live_tiger" if environment and provider == "tiger" else "environment_unproven",
            ),
            _gate(
                "platform_switch",
                ready("auto_live_enabled", {"1", "true", "enabled", "on"}),
                "enabled" if ready("auto_live_enabled", {"1", "true", "enabled", "on"}) else "switch_off",
            ),
            _gate(
                "global_pause",
                ready("global_auto_live_paused", {"0", "false", "disabled", "off"}),
                "not_paused"
                if ready("global_auto_live_paused", {"0", "false", "disabled", "off"})
                else "global_pause_unknown",
            ),
            _gate(
                "strategy",
                ready("auto_live_strategy_gate", {"ready", "ok", "enabled"}) and contract_ok,
                "ready"
                if ready("auto_live_strategy_gate", {"ready", "ok", "enabled"}) and contract_ok
                else "strategy_unproven",
            ),
            _gate(
                "risk",
                ready("auto_live_risk_gate", {"ready", "ok", "enabled"}) and contract_ok,
                "ready"
                if ready("auto_live_risk_gate", {"ready", "ok", "enabled"}) and contract_ok
                else "risk_unproven",
            ),
            _gate(
                "data_health",
                ready("auto_live_data_health_gate", {"healthy", "ready", "ok", "enabled"}),
                "healthy"
                if ready("auto_live_data_health_gate", {"healthy", "ready", "ok", "enabled"})
                else "data_health_unproven",
            ),
        ]
        gates.append(
            _gate(
                "strategy_risk_contract",
                contract_ok,
                "approved_exact_snapshot" if contract_ok else "unapproved_or_tampered",
            )
        )
        gates.append(_gate("valid_window", valid_window, "within_valid_window" if valid_window else "outside_valid_window"))
        gates.extend(self._runtime_safety_gates(conn, row, now, mode=runtime_mode))
        return {"all_ok": all(item["ok"] for item in gates), "gates": gates}

    def evaluate_resume_gates(
        self, user_id: int, mandate_public_id: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        moment = _now(now)
        with self._tx() as conn:
            row = self._mandate(conn, user_id, _text(mandate_public_id, "mandate public id"))
            return self._gates(conn, row, moment, runtime_mode="lifecycle")

    def submit_confirmation(
        self, user_id: int, mandate_public_id: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        public_id = _text(mandate_public_id, "mandate public id")
        moment = _iso(now)
        with self._tx() as conn:
            row = self._mandate(conn, user_id, public_id)
            was_terminal = str(row["state"]) in TERMINAL_STATES
            row = self._expire_if_due(conn, row, _now(now))
            state = str(row["state"])
            if state in {"expired", "revoked"}:
                if not was_terminal:
                    return _row_public(row)
                raise AutoLiveControlError("expired 或 revoked mandate 不可恢复。", 409)
            if state not in {"draft", "paused", "blocked"}:
                raise AutoLiveControlError("当前 mandate 不可进入确认。", 409)
            self._state(
                conn,
                row,
                "pending_confirmation",
                moment,
                "CONFIRMATION_REQUESTED",
                {"snapshot_sha256": row["snapshot_sha256"]},
            )
            updated = conn.execute("SELECT * FROM auto_live_mandates WHERE public_id=?", (public_id,)).fetchone()
            return {
                **_row_public(updated),
                "confirmation_phrase": f"ACTIVATE {public_id}",
                "confirmation_snapshot_sha256": str(updated["snapshot_sha256"]),
            }

    def start_mandate(
        self,
        user_id: int,
        mandate_public_id: str,
        *,
        expected_fencing_epoch: int | None = None,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        key = _text(idempotency_key, "Idempotency-Key", 128) if idempotency_key is not None else ""
        if not key or not _ID_RE.fullmatch(key):
            raise AutoLiveControlError("启动必须提供有效 Idempotency-Key。", 400)
        if isinstance(expected_fencing_epoch, bool) or not isinstance(expected_fencing_epoch, int) or expected_fencing_epoch < 0:
            raise AutoLiveControlError("启动必须提供有效 fencing epoch。", 400)
        moment_dt = _now(now)
        moment = _iso(moment_dt)
        with self._tx() as conn:
            public_id = _text(mandate_public_id, "mandate public id")
            fingerprint = sha256_json({"mandate_public_id": public_id, "expected_fencing_epoch": expected_fencing_epoch})
            self._user(conn, user_id)
            existing = conn.execute(
                "SELECT * FROM auto_live_start_requests WHERE user_id=? AND idempotency_key=?", (user_id, key)
            ).fetchone()
            if existing:
                if str(existing["request_fingerprint"]) != fingerprint:
                    raise AutoLiveConflict("Idempotency-Key 已用于不同启动请求。")
                return self._safe_receipt(json.loads(str(existing["receipt_json"])))
            def persist(receipt: dict[str, Any]) -> dict[str, Any]:
                conn.execute(
                    """INSERT INTO auto_live_start_requests
                    (public_id,user_id,mandate_public_id,idempotency_key,request_fingerprint,expected_fencing_epoch,status,receipt_json,receipt_sha256,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (receipt["public_id"], user_id, public_id, key, fingerprint, expected_fencing_epoch, receipt["status"], canonical_json(receipt), sha256_json(receipt), moment),
                )
                return self._safe_receipt(receipt)
            row = self._mandate(conn, user_id, public_id)
            was_terminal = str(row["state"]) in TERMINAL_STATES
            row = self._expire_if_due(conn, row, moment_dt)
            current = int(row["fencing_epoch"])
            if str(row["state"]) in TERMINAL_STATES:
                if was_terminal:
                    raise AutoLiveControlError("expired 或 revoked mandate 不可恢复。", 409)
                return persist(
                    {
                        "public_id": _opaque("start"),
                        "mandate_public_id": public_id,
                        "actor": {"type": "user", "user_id": int(user_id)},
                        "status": "blocked",
                        "state": str(row["state"]),
                        "runtime_state": "blocked",
                        "fencing_epoch": current,
                        "created_at": moment,
                        "idempotency_key_sha256": sha256_text(key),
                        "request_fingerprint": fingerprint,
                        "all_ok": False,
                        "gates": [_gate("valid_window", False, "outside_valid_window")],
                    }
                )
            if expected_fencing_epoch != current:
                raise AutoLiveConflict("fencing epoch 已过期。")
            if str(row["state"]) != "active":
                raise AutoLiveControlError("只有 active mandate 可启动。", 409)
            gates = self._gates(conn, row, moment_dt, runtime_mode="lifecycle")
            paused = conn.execute("SELECT opening_paused FROM user_controls WHERE user_id=?", (user_id,)).fetchone()
            opening_ok = not int(paused[0] if paused else 1)
            gates["gates"].append(
                _gate("opening_paused", opening_ok, "not_paused" if opening_ok else "user_opening_paused")
            )
            gates["all_ok"] = gates["all_ok"] and opening_ok
            if not gates["all_ok"]:
                self._state(conn, row, "blocked", moment, "START_BLOCKED", gates)
                updated = conn.execute("SELECT * FROM auto_live_mandates WHERE public_id=?", (public_id,)).fetchone()
                receipt = {
                    "public_id": _opaque("start"),
                    "mandate_public_id": public_id,
                    "actor": {"type": "user", "user_id": int(user_id)},
                    "status": "blocked",
                    "state": "blocked",
                    "runtime_state": "blocked",
                    "fencing_epoch": int(updated["fencing_epoch"]),
                    "created_at": moment,
                    "idempotency_key_sha256": sha256_text(key),
                    "request_fingerprint": fingerprint,
                    "all_ok": False,
                    "gates": gates["gates"],
                }
                return persist(receipt)
            next_epoch = current + 1
            changed = conn.execute(
                "UPDATE auto_live_mandates SET fencing_epoch=?,updated_at=? WHERE public_id=? AND state='active' AND fencing_epoch=?",
                (next_epoch, moment, row["public_id"], current),
            ).rowcount
            if changed != 1:
                raise AutoLiveConflict("mandate fencing CAS 失败。")
            self._write_runtime_projection(
                conn,
                mandate_public_id=public_id,
                state="starting",
                fencing_epoch=next_epoch,
                observed_at=moment,
                event_type="start_requested",
                expected_fencing_epoch=current,
            )
            receipt = {
                "public_id": _opaque("start"),
                "mandate_public_id": public_id,
                "actor": {"type": "user", "user_id": int(user_id)},
                "status": "starting",
                "state": "active",
                "runtime_state": "starting",
                "fencing_epoch": next_epoch,
                "created_at": moment,
                "idempotency_key_sha256": sha256_text(key),
                "request_fingerprint": fingerprint,
                "all_ok": True,
                "gates": gates["gates"],
            }
            return persist(receipt)

    def action_gate(
        self, user_id: int, mandate_public_id: str, action: str, *, now: datetime | None = None
    ) -> dict[str, Any]:
        normalized = _text(action, "action", 32).casefold()
        if normalized not in {"open", "start", "cancel", "reduce_exposure", "close_position"}:
            raise AutoLiveControlError("action 无效。")
        moment = _now(now)
        with self._tx() as conn:
            row = self._mandate(conn, user_id, _text(mandate_public_id, "mandate public id"))
            row = self._expire_if_due(conn, row, moment)
            paused = conn.execute("SELECT opening_paused FROM user_controls WHERE user_id=?", (user_id,)).fetchone()
            safe_exit = normalized in {"cancel", "reduce_exposure", "close_position"}
            if safe_exit:
                allowed = True
                reason = "safe_exit"
            else:
                runtime_mode = "opening" if normalized == "open" else "lifecycle"
                gates = self._gates(conn, row, moment, runtime_mode=runtime_mode)
                allowed = (
                    str(row["state"]) == "active"
                    and (normalized not in {"open", "start"} or not int(paused[0] if paused else 1))
                    and gates["all_ok"]
                )
                reason = f"{runtime_mode}_gate" if allowed else f"{runtime_mode}_paused_or_uncertain"
            return {
                "allowed": allowed,
                "action": normalized,
                "can_reduce_exposure": True,
                "reason": reason,
            }

    def _terminalize_row(self, conn: Any, row: Any, target_state: str, now: str, payload: dict[str, Any]) -> Any:
        if str(row["state"]) in TERMINAL_STATES:
            return row
        old_epoch = int(row["fencing_epoch"])
        next_epoch = old_epoch + 1
        changed = conn.execute(
            "UPDATE auto_live_mandates SET state=?,fencing_epoch=?,updated_at=?,confirmed_at=NULL,confirmation_digest=NULL WHERE public_id=? AND state=? AND fencing_epoch=?",
            (target_state, next_epoch, now, row["public_id"], row["state"], old_epoch),
        ).rowcount
        if changed != 1:
            raise AutoLiveConflict("mandate terminal fencing CAS 失败。")
        safe_state = "blocked" if target_state == "revoked" else "paused"
        self._write_runtime_projection(
            conn,
            mandate_public_id=str(row["public_id"]),
            state=safe_state,
            fencing_epoch=next_epoch,
            observed_at=now,
            event_type="terminalized",
            last_error_code=target_state.upper(),
            expected_fencing_epoch=old_epoch,
        )
        self._event(
            conn,
            row,
            "MANDATE_" + target_state.upper(),
            target_state,
            {**payload, "fencing_epoch": next_epoch},
            now,
            str(row["state"]),
        )
        return conn.execute("SELECT * FROM auto_live_mandates WHERE public_id=?", (row["public_id"],)).fetchone()

    def _expire_if_due(self, conn: Any, row: Any, now: datetime) -> Any:
        if str(row["state"]) in TERMINAL_STATES or now < datetime.fromisoformat(str(row["valid_until"])):
            return row
        return self._terminalize_row(conn, row, "expired", _iso(now), {"expired_at": _iso(now)})

    def expire_mandate(self, user_id: int, mandate_public_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        moment_dt = _now(now)
        moment = _iso(moment_dt)
        with self._tx() as conn:
            row = self._mandate(conn, user_id, _text(mandate_public_id, "mandate public id"))
            if str(row["state"]) == "revoked":
                raise AutoLiveControlError("revoked mandate 不可修改。", 409)
            return _row_public(self._terminalize_row(conn, row, "expired", moment, {"expired_at": moment}))

    def revoke_mandate(
        self, user_id: int, mandate_public_id: str, *, reason: str, now: datetime | None = None
    ) -> dict[str, Any]:
        why = _text(reason, "撤销原因", 500)
        moment = _iso(now)
        with self._tx() as conn:
            row = self._mandate(conn, user_id, _text(mandate_public_id, "mandate public id"))
            if str(row["state"]) == "expired":
                raise AutoLiveControlError("expired mandate 不可修改。", 409)
            return _row_public(self._terminalize_row(conn, row, "revoked", moment, {"reason": why}))

    def get_mandate(self, user_id: int, mandate_public_id: str, *, now: datetime | None = None) -> dict[str, Any]:
        with self._tx() as conn:
            row = self._mandate(conn, user_id, _text(mandate_public_id, "mandate public id"))
            row = self._expire_if_due(conn, row, _now(now))
            return _row_public(row)
