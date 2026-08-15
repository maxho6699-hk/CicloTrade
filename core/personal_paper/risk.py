from __future__ import annotations

import hmac
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from core.personal_paper.contracts import (
    PersonalPaperValidationError,
    canonical_json,
    minor_times_quantity,
    normalize_stock_order,
    risk_draft_sha256,
    sha256_json,
)
from core.personal_paper.service import (
    MAX_QUOTE_AGE,
    PersonalPaperConflict,
    PersonalPaperRiskRejected,
    VerifiedQuote,
)
from core.expanded_research_universe_data import UNIVERSE_DATA


RISK_SCHEMA = "r1"
RISK_TTL = timedelta(seconds=30)
CHECK_CODES = (
    "buying_power", "max_loss", "position_concentration", "sector_concentration",
    "drawdown", "event_gap", "liquidity",
)
CHECK_KEYS = {"code", "status", "title", "detail", "value", "limit", "data_state"}
PROOF_PAYLOAD_KEYS = {
    "id", "schema_version", "season_id", "quote_id", "account_version", "draft_sha256",
    "computed_at", "marks_as_of", "created_at", "expires_at", "decision", "risk_level",
    "data_state", "checks", "blocking_reasons", "warnings",
}


class RiskProofError(PersonalPaperRiskRejected):
    pass


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _money(value: int | None) -> float | None:
    return None if value is None else value / 100


def account_as_of(account: dict[str, Any]) -> str:
    return str(account.get("as_of") or "")


def _check(code: str, status: str, title: str, detail: str, value: Any, limit: Any, data_state: str) -> dict[str, Any]:
    if value is not None and not isinstance(value, str):
        value = canonical_json(value)
    if limit is not None and not isinstance(limit, str):
        limit = canonical_json(limit)
    item = {
        "code": code, "status": status, "title": title, "detail": detail,
        "value": value, "limit": limit, "data_state": data_state,
    }
    if set(item) != CHECK_KEYS:
        raise AssertionError("risk check shape drift")
    return item


def _proof_payload_from_row(row: Any) -> dict[str, Any]:
    try:
        payload = {
            "id": row["public_id"], "schema_version": row["schema_version"],
            "season_id": row["season_id"], "quote_id": row["quote_id"],
            "account_version": int(row["account_version"]), "draft_sha256": row["draft_sha256"],
            "computed_at": row["computed_at"], "marks_as_of": row["marks_as_of"],
            "created_at": row["created_at"], "expires_at": row["expires_at"],
            "decision": row["decision"], "risk_level": row["risk_level"],
            "data_state": row["data_state"], "checks": json.loads(row["checks_json"]),
            "blocking_reasons": json.loads(row["blocking_reasons_json"]),
            "warnings": json.loads(row["warnings_json"]),
        }
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RiskProofError("风险证明完整性校验失败。") from exc
    if set(payload) != PROOF_PAYLOAD_KEYS:
        raise RiskProofError("风险证明完整性校验失败。")
    return payload


def _verify_stored_payload(row: Any) -> dict[str, Any]:
    payload = _proof_payload_from_row(row)
    try:
        stored = json.loads(row["proof_payload_json"])
    except (TypeError, json.JSONDecodeError) as exc:
        raise RiskProofError("风险证明完整性校验失败。") from exc
    if not isinstance(stored, dict):
        raise RiskProofError("风险证明完整性校验失败。")
    try:
        canonical = canonical_json(stored)
        digest = sha256_json(stored)
    except PersonalPaperValidationError as exc:
        raise RiskProofError("风险证明完整性校验失败。") from exc
    if (
        set(stored) != PROOF_PAYLOAD_KEYS
        or canonical != row["proof_payload_json"]
        or stored != payload
        or not hmac.compare_digest(digest, str(row["proof_sha256"]))
    ):
        raise RiskProofError("风险证明完整性校验失败。")
    return payload


class PersonalPaperRiskProofService:
    """Issue and consume append-only risk proofs for the isolated personal paper account."""

    def __init__(self, paper_service: Any):
        self.paper = paper_service

    def issue(self, user_id: int, raw: Any) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise PersonalPaperValidationError("风险证明请求必须为对象。")
        expected = {
            "season_id", "market", "symbol", "side", "order_type",
            "quantity", "limit_price", "stop_price", "time_in_force", "quote_id",
            "account_version", "source_context",
        }
        if set(raw) != expected:
            raise PersonalPaperValidationError("风险证明请求字段不完整或包含未知字段。")
        order = normalize_stock_order({"idempotency_key": f"risk_{uuid.uuid4().hex}", **raw})
        now_value = self.paper.clock()
        if now_value.tzinfo is None or now_value.utcoffset() is None:
            raise PersonalPaperValidationError("服务时间必须包含时区。")
        now = _stamp(now_value)
        with self.paper.database._get_connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                season = self.paper._owned_season(connection, user_id, order["season_id"])
                if int(season["version"]) != order["account_version"]:
                    raise PersonalPaperConflict("账户已变化，请刷新后重新生成风险证明。")
                try:
                    verifier = getattr(self.paper.quote_verifier, "verify", None)
                    if verifier is None:
                        raise RiskProofError("报价证明验证器不支持预览验证。")
                    quote = verifier(
                        order["quote_id"], user_id=user_id, season_id=season["id"],
                        market=order["market"], symbol=order["symbol"], now=now_value,
                        connection=connection, request_sha256=risk_draft_sha256(order),
                    )
                except Exception as exc:
                    raise RiskProofError("报价证明不可用，无法生成风险证明。") from exc
                self.paper._validate_quote(quote, order, now_value)
                before = self.paper._account_state(connection, season, now=now_value)
                decision, level, state, checks, blocking, warnings = self._evaluate(
                    order, quote, before, connection=connection, now=now_value,
                )
                proof_id = f"rpf_{uuid.uuid4().hex}"
                expires_at = _stamp(now_value + RISK_TTL)
                payload = {
                    "id": proof_id, "schema_version": RISK_SCHEMA, "season_id": season["id"],
                    "quote_id": order["quote_id"], "account_version": order["account_version"],
                    "draft_sha256": risk_draft_sha256(order), "computed_at": now,
                    "marks_as_of": account_as_of(before), "created_at": now,
                    "expires_at": expires_at, "decision": decision, "risk_level": level,
                    "data_state": state, "checks": checks,
                    "blocking_reasons": blocking, "warnings": warnings,
                }
                proof_payload_json = canonical_json(payload)
                proof_sha256 = sha256_json(payload)
                connection.execute(
                    """INSERT INTO personal_paper_risk_proofs
                       (public_id,user_id,season_id,quote_id,account_version,draft_sha256,schema_version,
                        computed_at,marks_as_of,created_at,expires_at,decision,risk_level,data_state,checks_json,
                        blocking_reasons_json,warnings_json,proof_payload_json,proof_sha256)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (proof_id, user_id, season["id"], order["quote_id"], order["account_version"],
                     payload["draft_sha256"], RISK_SCHEMA, payload["computed_at"], payload["marks_as_of"], now, expires_at, decision, level, state,
                     canonical_json(checks), canonical_json(blocking), canonical_json(warnings),
                     proof_payload_json, proof_sha256),
                )
                event = {
                    "event_type": "ISSUED", "decision": decision,
                    "draft_sha256": payload["draft_sha256"], "proof_sha256": proof_sha256,
                }
                connection.execute(
                    """INSERT INTO personal_paper_risk_proof_events
                       (public_id,proof_id,user_id,season_id,event_type,payload_json,occurred_at,payload_sha256)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (f"rppe_{uuid.uuid4().hex}", proof_id, user_id, season["id"], "ISSUED",
                     canonical_json(event), now, sha256_json(event)),
                )
                connection.commit()
                return {**payload, "proof_sha256": proof_sha256}
            except Exception:
                connection.rollback()
                raise

    def verify_and_consume(self, user_id: int, order: dict[str, Any], *, connection: Any, now: datetime) -> None:
        proof_id = order.get("risk_proof_id")
        if not isinstance(proof_id, str):
            raise RiskProofError("必须先生成并确认有效的风险证明。")
        row = connection.execute(
            "SELECT * FROM personal_paper_risk_proofs WHERE public_id=? AND user_id=? AND season_id=?",
            (proof_id, user_id, order["season_id"]),
        ).fetchone()
        if row is None:
            raise RiskProofError("风险证明不存在或不属于当前个人模拟账户。")
        payload = _verify_stored_payload(row)
        draft_hash = risk_draft_sha256(order)
        if payload["draft_sha256"] != draft_hash or payload["quote_id"] != order["quote_id"] \
                or payload["account_version"] != order["account_version"]:
            raise RiskProofError("订单字段已变化，风险证明已失效。")
        expires = datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00"))
        if now >= expires:
            raise RiskProofError("风险证明已过期，请重新确认订单。")
        if payload["decision"] == "reject":
            raise RiskProofError("风险证明拒绝该订单，无法提交。")
        consumed = connection.execute(
            "SELECT draft_sha256,idempotency_key FROM personal_paper_risk_proof_consumptions WHERE proof_id=?",
            (proof_id,),
        ).fetchone()
        if consumed is not None:
            if consumed["draft_sha256"] != draft_hash or consumed["idempotency_key"] != order["idempotency_key"]:
                raise RiskProofError("风险证明已被其他订单请求消费。")
            return
        connection.execute(
            """INSERT INTO personal_paper_risk_proof_consumptions
               (proof_id,user_id,season_id,draft_sha256,idempotency_key,consumed_at) VALUES(?,?,?,?,?,?)""",
            (proof_id, user_id, order["season_id"], draft_hash, order["idempotency_key"], _stamp(now)),
        )
        event = {"event_type": "CONSUMED", "draft_sha256": draft_hash, "idempotency_key": order["idempotency_key"]}
        connection.execute(
            """INSERT INTO personal_paper_risk_proof_events
               (public_id,proof_id,user_id,season_id,event_type,payload_json,occurred_at,payload_sha256)
               VALUES(?,?,?,?,?,?,?,?)""",
            (f"rppe_{uuid.uuid4().hex}", proof_id, user_id, order["season_id"], "CONSUMED",
             canonical_json(event), _stamp(now), sha256_json(event)),
        )

    def _evaluate(
        self,
        order: dict[str, Any],
        quote: VerifiedQuote,
        account: dict[str, Any],
        *,
        connection: Any,
        now: datetime,
    ):
        side = order["side"]
        quantity = int(order["quantity_micros"])
        price = quote.ask_minor if side in {"BUY", "COVER"} else quote.bid_minor
        reserve_price = order["limit_price_minor"] or order["stop_price_minor"] or price
        notional = minor_times_quantity(reserve_price, quantity)
        equity = max(int(account["total_equity_minor"]), 0)
        semantic_rejection = None
        try:
            self.paper._evaluate(order, quote, account)
        except PersonalPaperRiskRejected as exc:
            semantic_rejection = str(exc)
        buying_need = 0
        if side in {"BUY", "COVER"}:
            buying_need = notional + quote.commission_minor
        elif side == "SHORT":
            buying_need = 2 * notional + quote.commission_minor
        available = int(account["buying_power_minor"])
        buying = _check(
            "buying_power", "pass" if buying_need <= available else "fail", "购买力",
            "当前购买力足以覆盖该订单的保留资金。" if buying_need <= available else "当前购买力不足以覆盖该订单。",
            {"required": _money(buying_need), "available": _money(available)},
            {"required_max": _money(available)}, "fresh",
        )
        if side == "SHORT":
            max_loss = _check("max_loss", "fail", "单笔最大亏损", "裸 SHORT 的理论最大亏损不封顶，个人模拟拒绝提交。", {"usd": None, "pct": None, "unbounded": True}, {"usd": None, "pct": None}, "fresh")
        else:
            loss_minor = notional + quote.commission_minor if side == "BUY" else 0
            loss_pct = (loss_minor / equity * 100) if equity else None
            status = "pass" if equity and (loss_pct is None or loss_pct <= 10) else "warn"
            max_loss = _check("max_loss", status, "单笔最大亏损", "按当前报价估算，最大亏损不超过投入本金。", {"usd": _money(loss_minor), "pct": loss_pct, "unbounded": False}, {"usd": _money(equity * 10 // 100), "pct": 10}, "fresh" if equity else "stale")
        positions = {item["symbol"]: item for item in account["positions"]}
        current = positions.get(order["symbol"], {})
        held = int(current.get("quantity_micros", 0))
        projected_quantity = held + quantity if side in {"BUY", "COVER"} else held - quantity
        projected_mark = (
            quote.bid_minor if projected_quantity > 0
            else quote.ask_minor if projected_quantity < 0
            else 0
        )
        projected = abs(minor_times_quantity(projected_mark, projected_quantity))
        position_pct = projected / equity * 100 if equity else None
        concentration = _check("position_concentration", "pass" if position_pct is not None and position_pct <= 25 else "warn", "股票集中度", "按当前报价估算该股票占组合权益比例。", {"usd": _money(projected), "pct": position_pct}, {"pct": 25}, "fresh" if equity else "stale")
        sector_map = {
            item["symbol"]: item["industry"]
            for tier in ("tier_a", "tier_c") for item in UNIVERSE_DATA.get(tier, [])
            if item.get("asset_kind") == "equity"
        }
        symbols = {
            symbol for symbol, item in positions.items()
            if int(item.get("quantity_micros", 0)) != 0
        } | {order["symbol"]}
        if not symbols.issubset(sector_map):
            sector = _check("sector_concentration", "unknown", "行业集中度", "股票不在已冻结的研究宇宙 taxonomy 内，无法安全推断行业集中度。", None, {"pct": 35}, "missing")
        else:
            sector_values: dict[str, int] = {}
            marks_valid = True
            for symbol, item in positions.items():
                held_quantity = int(item.get("quantity_micros", 0))
                if held_quantity == 0 or symbol == order["symbol"]:
                    continue
                try:
                    marked_at = datetime.fromisoformat(str(item["quote_as_of"]).replace("Z", "+00:00"))
                    mark_price = int(item["mark_bid_minor"] if held_quantity > 0 else item["mark_ask_minor"])
                    if (
                        item.get("quote_state") != "fresh" or mark_price <= 0
                        or marked_at > now or now - marked_at > MAX_QUOTE_AGE
                    ):
                        marks_valid = False
                        break
                except (KeyError, TypeError, ValueError):
                    marks_valid = False
                    break
                exposure = abs(minor_times_quantity(mark_price, held_quantity))
                industry = sector_map[symbol]
                sector_values[industry] = sector_values.get(industry, 0) + exposure
            if not marks_valid:
                sector = _check("sector_concentration", "unknown", "行业集中度", "至少一只持仓股票缺少新鲜可信的独立账本报价，行业集中度不能用目标股票报价代算。", None, {"pct": 35}, "missing")
            else:
                target_industry = sector_map[order["symbol"]]
                sector_values[target_industry] = (
                    sector_values.get(target_industry, 0) + projected
                )
                sector_value = sector_values[target_industry]
                sector_pct = sector_value / equity * 100 if equity else None
                sector = _check("sector_concentration", "pass" if sector_pct is not None and sector_pct <= 35 else "warn", "行业集中度", "使用各持仓股票最近的独立账本报价与冻结研究宇宙 v1 taxonomy 估算，不等同于实时 GICS。", {"industry": target_industry, "usd": _money(sector_value), "pct": sector_pct}, {"pct": 35}, "partial" if equity else "stale")
        peak_row = connection.execute(
            "SELECT MAX(total_equity_minor) AS peak FROM personal_paper_equity_events WHERE season_id=?",
            (account["season"]["id"],),
        ).fetchone()
        peak = max(int(account["initial_cash_minor"]), equity, int(peak_row["peak"] or 0))
        drawdown_pct = ((peak - equity) / peak * 100) if peak else None
        drawdown = _check("drawdown", "pass" if drawdown_pct is not None and drawdown_pct <= 20 else "warn", "当前回撤", "基于个人模拟追加式权益账本的历史峰值计算；行情状态仍以当前报价证明为准。", {"pct": drawdown_pct, "peak_usd": _money(peak), "current_usd": _money(equity)}, {"pct": 20}, "stale" if account["quote_state"] in {"stale", "missing"} else "fresh")
        event_row = connection.execute(
            """SELECT e.* FROM earnings_event_revisions e
               WHERE e.market='US' AND e.symbol=? AND e.status='CONFIRMED'
                 AND e.scheduled_at>? AND e.available_at<=? AND e.recorded_at<=?
                 AND NOT EXISTS (
                     SELECT 1 FROM earnings_event_revisions newer
                     WHERE newer.supersedes_revision_id=e.id
                       AND newer.available_at<=? AND newer.recorded_at<=?
                 )
               ORDER BY e.scheduled_at ASC,e.revision_no DESC,e.id DESC LIMIT 1""",
            (order["symbol"], _stamp(quote.as_of), _stamp(quote.as_of), _stamp(quote.as_of),
             _stamp(quote.as_of), _stamp(quote.as_of)),
        ).fetchone()
        if event_row is None:
            event = _check("event_gap", "unknown", "财报/事件跳空", "当前可信事件账本没有可用的未来财报快照，无法安全判断跳空风险。", None, None, "missing")
        else:
            event = _check("event_gap", "warn", "财报/事件跳空", "存在已确认的未来财报事件，提交前应人工复核跳空风险。", {"scheduled_at": event_row["scheduled_at"], "revision_id": event_row["id"], "payload_sha256": event_row["payload_sha256"]}, {"scheduled_at": "must_be_known"}, "partial")
        spread_pct = (quote.ask_minor - quote.bid_minor) / quote.ask_minor * 100
        liquidity = _check("liquidity", "warn", "流动性", "报价价差可计算，但成交量与盘口深度未接入，保留人工复核。", {"spread_pct": spread_pct}, {"spread_pct": 2}, "partial")
        checks = [buying, max_loss, concentration, sector, drawdown, event, liquidity]
        blocking = [item["detail"] for item in checks if item["status"] == "fail"]
        if semantic_rejection and semantic_rejection not in blocking:
            blocking.append(semantic_rejection)
        warnings = [item["detail"] for item in checks if item["status"] in {"warn", "unknown"}]
        decision = "reject" if blocking else ("review" if warnings else "allow")
        level = "blocked" if decision == "reject" else ("moderate" if decision == "review" else "low")
        states = {item["data_state"] for item in checks}
        data_state = "missing" if "missing" in states else ("partial" if "partial" in states else "fresh")
        return decision, level, data_state, checks, blocking, warnings


__all__ = ["PersonalPaperRiskProofService", "RiskProofError", "CHECK_CODES"]
