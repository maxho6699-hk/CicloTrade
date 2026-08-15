from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Protocol

from core.personal_paper.contracts import (
    PersonalPaperValidationError,
    canonical_json,
    minor_times_quantity,
    normalize_stock_order,
    sha256_json,
)


INITIAL_CASH_MINOR = 1_000_000
MAX_QUOTE_AGE = timedelta(seconds=30)


class PersonalPaperConflict(RuntimeError):
    pass


class PersonalPaperRiskRejected(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedQuote:
    proof_id: str
    market: str
    symbol: str
    bid_minor: int
    ask_minor: int
    last_minor: int
    as_of: datetime
    state: str
    commission_minor: int
    expires_at: datetime | None = field(default=None, compare=False)
    observed_at: datetime | None = None
    available_at: datetime | None = None
    session: str | None = None
    freshness: str | None = None
    source: str | None = None

    @property
    def quote_at(self) -> datetime:
        """Canonical quote timestamp; ``as_of`` remains the storage alias."""
        return self.as_of


class QuoteProofVerifier(Protocol):
    def verify_and_consume(
        self, quote_id: str, *, user_id: int, season_id: str, market: str, symbol: str,
        now: datetime, connection: Any, request_sha256: str,
    ) -> VerifiedQuote: ...


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _money(value: int) -> float:
    return value / 100


def _quantity(value: int) -> float:
    return value / 1_000_000


class PersonalPaperService:
    def __init__(
        self, database: Any, quote_verifier: QuoteProofVerifier,
        *, clock: Callable[[], datetime] | None = None,
    ):
        self.database = database
        self.quote_verifier = quote_verifier
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        from core.personal_paper.risk import PersonalPaperRiskProofService
        self.risk_proofs = PersonalPaperRiskProofService(self)

    def issue_risk_proof(self, user_id: int, raw: Any) -> dict[str, Any]:
        return self.risk_proofs.issue(user_id, raw)

    def create_first_season(self, user_id: int) -> dict[str, Any]:
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id < 1:
            raise PersonalPaperValidationError("user_id 无效。")
        now = _stamp(self.clock())
        with self.database._get_connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM personal_paper_seasons WHERE user_id=? AND season_number=1",
                    (user_id,),
                ).fetchone()
                if row is None:
                    season_id = f"pps_{uuid.uuid4().hex}"
                    connection.execute(
                        """INSERT INTO personal_paper_seasons
                           (id,user_id,season_number,state,currency,initial_cash_minor,version,
                            started_at,created_at) VALUES(?,?,1,'active','USD',1000000,0,?,?)""",
                        (season_id, user_id, now, now),
                    )
                    connection.execute(
                        """INSERT INTO personal_paper_account_events
                           (public_id,season_id,sequence,event_type,occurred_at,payload_sha256)
                           VALUES(?,?,0,'SEASON_OPENED',?,?)""",
                        (f"ppae_{uuid.uuid4().hex}", season_id, now,
                         sha256_json({"season_id": season_id, "initial_cash_minor": INITIAL_CASH_MINOR})),
                    )
                    row = connection.execute(
                        "SELECT * FROM personal_paper_seasons WHERE id=?", (season_id,)
                    ).fetchone()
                connection.commit()
                return self._season(row)
            except Exception:
                connection.rollback()
                raise

    def account_snapshot(self, user_id: int, season_id: str) -> dict[str, Any]:
        with self.database._get_connection() as connection:
            season = self._owned_season(connection, user_id, season_id)
            return self._public_account(self._account_state(connection, season))

    def cancel_stock_order(self, user_id: int, raw: Any) -> dict[str, Any]:
        """Cancel one resting order by appending release and cancellation events.

        Orders are immutable audit records.  Their current lifecycle state is the
        last order event, rather than an in-place mutation of ``orders.status``.
        """
        if not isinstance(raw, dict) or set(raw) != {"season_id", "order_id", "account_version"}:
            raise PersonalPaperValidationError("撤单字段不完整或包含未知字段。")
        season_id, order_id, version = raw["season_id"], raw["order_id"], raw["account_version"]
        if not all(isinstance(value, str) and value for value in (season_id, order_id)):
            raise PersonalPaperValidationError("season_id 或 order_id 无效。")
        if isinstance(version, bool) or not isinstance(version, int) or version < 0:
            raise PersonalPaperValidationError("account_version 无效。")
        now_value = self.clock()
        if now_value.tzinfo is None or now_value.utcoffset() is None:
            raise PersonalPaperValidationError("服务时间必须包含时区。")
        now = _stamp(now_value)
        with self.database._get_connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                season = self._owned_season(connection, user_id, season_id)
                order = connection.execute(
                    "SELECT * FROM personal_paper_orders WHERE public_id=? AND season_id=? AND user_id=?",
                    (order_id, season["id"], user_id),
                ).fetchone()
                if order is None:
                    raise PersonalPaperConflict("订单不存在或不属于当前用户。")
                latest = connection.execute(
                    "SELECT event_type FROM personal_paper_order_events WHERE order_id=? ORDER BY sequence DESC",
                    (order_id,),
                ).fetchone()
                if latest and latest["event_type"] == "CANCELLED":
                    account = self._public_account(
                        self._account_state(connection, season, now=now_value)
                    )
                    connection.commit()
                    return {"order": self._public_order(order, "CANCELLED"), "account": account, "replayed": True}
                if order["status"] != "PENDING" or not latest or latest["event_type"] != "ACCEPTED":
                    raise PersonalPaperConflict("只有未成交的挂单可以撤销。")
                if version != season["version"]:
                    raise PersonalPaperConflict("账户已变化，请刷新后再撤单。")
                reserve = connection.execute(
                    """SELECT * FROM personal_paper_account_events
                       WHERE related_order_id=? AND event_type='ORDER_RESERVED'""",
                    (order_id,),
                ).fetchone()
                if reserve is None:
                    raise PersonalPaperConflict("挂单保留记录缺失，无法安全撤单。")
                next_version = int(season["version"]) + 1
                effect = {
                    "event_type": "ORDER_RELEASED", "cash_delta": 0,
                    "reserved_cash_delta": -int(reserve["reserved_cash_delta_minor"]),
                    "position_delta": 0,
                    "reserved_position_delta": -int(reserve["reserved_position_delta_micros"]),
                }
                connection.execute(
                    """INSERT INTO personal_paper_account_events
                       (public_id,season_id,related_order_id,sequence,event_type,market,symbol,side,cash_delta_minor,
                        reserved_cash_delta_minor,position_delta_micros,reserved_position_delta_micros,
                        execution_price_minor,commission_minor,mark_bid_minor,mark_ask_minor,
                        mark_last_minor,quote_as_of,quote_state,occurred_at,payload_sha256)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        f"ppae_{uuid.uuid4().hex}", season["id"], order_id, next_version,
                        "ORDER_RELEASED", reserve["market"], reserve["symbol"], reserve["side"],
                        0, effect["reserved_cash_delta"], 0, effect["reserved_position_delta"], None,
                        0, reserve["mark_bid_minor"], reserve["mark_ask_minor"], reserve["mark_last_minor"],
                        reserve["quote_as_of"], reserve["quote_state"], now, sha256_json(effect),
                    ),
                )
                changed = connection.execute(
                    "UPDATE personal_paper_seasons SET version=? WHERE id=? AND version=?",
                    (next_version, season["id"], season["version"]),
                ).rowcount
                if changed != 1:
                    raise PersonalPaperConflict("账户已变化，请刷新后再撤单。")
                event = {"status": "CANCELLED", "reason": "user_cancelled"}
                connection.execute(
                    """INSERT INTO personal_paper_order_events
                       (public_id,order_id,sequence,event_type,occurred_at,payload_json,payload_sha256)
                       VALUES(?,?,1,'CANCELLED',?,?,?)""",
                    (f"ppoe_{uuid.uuid4().hex}", order_id, now, canonical_json(event), sha256_json(event)),
                )
                season = connection.execute(
                    "SELECT * FROM personal_paper_seasons WHERE id=?", (season["id"],)
                ).fetchone()
                state = self._account_state(connection, season, now=now_value)
                account = self._public_account(state)
                self._record_equity(connection, season, state, now)
                connection.commit()
                return {"order": self._public_order(order, "CANCELLED"), "account": account, "replayed": False}
            except Exception:
                connection.rollback()
                raise

    def submit_stock_order(self, user_id: int, raw: Any) -> dict[str, Any]:
        request = normalize_stock_order(raw)
        if not request.get("risk_proof_id"):
            raise PersonalPaperRiskRejected("必须先生成并确认有效的风险证明。")
        request_hash = sha256_json(request)
        now_value = self.clock()
        if now_value.tzinfo is None or now_value.utcoffset() is None:
            raise PersonalPaperValidationError("服务时间必须包含时区。")
        now = _stamp(now_value)
        with self.database._get_connection() as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                season = self._owned_season(connection, user_id, request["season_id"])
                replay = connection.execute(
                    """SELECT request_sha256,response_json FROM personal_paper_orders
                       WHERE season_id=? AND idempotency_key=?""",
                    (season["id"], request["idempotency_key"]),
                ).fetchone()
                if replay:
                    if replay["request_sha256"] != request_hash:
                        raise PersonalPaperConflict("同一幂等键不能提交不同订单。")
                    response = json.loads(replay["response_json"])
                    response["replayed"] = True
                    connection.commit()
                    return response
                if request["account_version"] != season["version"]:
                    raise PersonalPaperConflict("账户已变化，请刷新后确认订单。")
                self.risk_proofs.verify_and_consume(
                    user_id, request, connection=connection, now=now_value,
                )
                try:
                    quote = self.quote_verifier.verify_and_consume(
                        request["quote_id"], user_id=user_id, season_id=season["id"],
                        market=request["market"], symbol=request["symbol"], now=now_value,
                        connection=connection, request_sha256=request_hash,
                    )
                except Exception as exc:
                    raise PersonalPaperRiskRejected("报价证明不可用，订单未被接受。") from exc
                self._validate_quote(quote, request, now_value)
                before = self._account_state(connection, season, now=now_value)
                status, effect = self._evaluate(request, quote, before)
                order_id = f"ppo_{uuid.uuid4().hex}"
                next_version = int(season["version"]) + 1
                price = quote.ask_minor if request["side"] in {"BUY", "COVER"} else quote.bid_minor
                connection.execute(
                    """INSERT INTO personal_paper_account_events
                       (public_id,season_id,related_order_id,sequence,event_type,market,symbol,side,cash_delta_minor,
                        reserved_cash_delta_minor,position_delta_micros,reserved_position_delta_micros,
                        execution_price_minor,commission_minor,mark_bid_minor,mark_ask_minor,
                        mark_last_minor,quote_as_of,quote_state,occurred_at,payload_sha256)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        f"ppae_{uuid.uuid4().hex}", season["id"], order_id, next_version,
                        effect["event_type"], request["market"], request["symbol"], request["side"],
                        effect["cash_delta"], effect["reserved_cash_delta"],
                        effect["position_delta"], effect["reserved_position_delta"],
                        price if status == "FILLED" else None, quote.commission_minor,
                        quote.bid_minor, quote.ask_minor, quote.last_minor, _stamp(quote.as_of),
                        quote.state, now, sha256_json(effect),
                    ),
                )
                changed = connection.execute(
                    "UPDATE personal_paper_seasons SET version=? WHERE id=? AND version=?",
                    (next_version, season["id"], season["version"]),
                ).rowcount
                if changed != 1:
                    raise PersonalPaperConflict("账户已变化，请刷新后确认订单。")
                season = connection.execute(
                    "SELECT * FROM personal_paper_seasons WHERE id=?", (season["id"],)
                ).fetchone()
                state = self._account_state(connection, season, now=now_value)
                account = self._public_account(state)
                order = {
                    "id": order_id, "season_id": season["id"], "market": request["market"],
                    "symbol": request["symbol"], "side": request["side"],
                    "order_type": request["order_type"], "quantity": _quantity(request["quantity_micros"]),
                    "status": status, "created_at": now, "quote_id": quote.proof_id,
                    "account_version": request["account_version"],
                    "cancel_eligible": status == "PENDING",
                    "cancel_account_version": next_version if status == "PENDING" else None,
                }
                response = {"order": order, "account": account, "replayed": False}
                if status == "PENDING":
                    account["open_orders"] = [order, *account["open_orders"]]
                account["recent_orders"] = [order, *account["recent_orders"]][:50]
                connection.execute(
                    """INSERT INTO personal_paper_orders
                       (public_id,season_id,user_id,idempotency_key,request_sha256,market,instrument_type,
                        symbol,side,order_type,quantity_micros,limit_price_minor,stop_price_minor,
                        time_in_force,quote_proof_id,quote_as_of,account_version,source_kind,
                        source_reference_id,status,response_json,created_at)
                       VALUES(?,?,?,?,?,?,'stock',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        order_id, season["id"], user_id, request["idempotency_key"], request_hash,
                        request["market"], request["symbol"], request["side"], request["order_type"],
                        request["quantity_micros"], request["limit_price_minor"],
                        request["stop_price_minor"], request["time_in_force"], quote.proof_id,
                        _stamp(quote.as_of), request["account_version"], request["source_context"]["kind"],
                        request["source_context"]["reference_id"], status, canonical_json(response), now,
                    ),
                )
                risk_event = {
                    "risk_proof_id": request["risk_proof_id"],
                    "decision": "review_or_allow",
                    "submission_sha256": request_hash,
                }
                connection.execute(
                    """INSERT INTO personal_paper_risk_events
                       (public_id,season_id,order_id,code,allowed,details_json,occurred_at)
                       VALUES(?,?,?,?,?,?,?)""",
                    (f"pper_{uuid.uuid4().hex}", season["id"], order_id, "risk_proof", 1,
                     canonical_json(risk_event), now),
                )
                event = {"status": status, "request_sha256": request_hash}
                connection.execute(
                    """INSERT INTO personal_paper_order_events
                       (public_id,order_id,sequence,event_type,occurred_at,payload_json,payload_sha256)
                       VALUES(?,?,0,'ACCEPTED',?,?,?)""",
                    (f"ppoe_{uuid.uuid4().hex}", order_id, now, canonical_json(event), sha256_json(event)),
                )
                if status == "FILLED":
                    connection.execute(
                        """INSERT INTO personal_paper_fills
                           (public_id,order_id,season_id,market,symbol,side,quantity_micros,
                            price_minor,commission_minor,filled_at,quote_proof_id)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                        (f"ppf_{uuid.uuid4().hex}", order_id, season["id"], request["market"],
                         request["symbol"], request["side"], request["quantity_micros"], price,
                         quote.commission_minor, now, quote.proof_id),
                    )
                self._record_equity(connection, season, state, now)
                connection.commit()
                return response
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _validate_quote(quote: VerifiedQuote, request: dict[str, Any], now: datetime) -> None:
        if not isinstance(quote, VerifiedQuote):
            raise PersonalPaperRiskRejected("报价证明缺失、过期或不匹配。")
        units = (quote.bid_minor, quote.ask_minor, quote.last_minor, quote.commission_minor)
        if (
            quote.proof_id != request["quote_id"]
            or quote.market != request["market"] or quote.symbol != request["symbol"]
            or quote.state != "fresh" or quote.as_of.tzinfo is None
            or (quote.freshness is not None and quote.freshness != "fresh")
            or (quote.expires_at is not None and (quote.expires_at.tzinfo is None or now >= quote.expires_at))
            or quote.as_of > now or now - quote.as_of > MAX_QUOTE_AGE
            or any(isinstance(item, bool) or not isinstance(item, int) for item in units)
            or quote.bid_minor <= 0 or quote.ask_minor < quote.bid_minor
            or quote.last_minor <= 0 or quote.commission_minor < 0
        ):
            raise PersonalPaperRiskRejected("报价证明缺失、过期或不匹配。")

    @staticmethod
    def _evaluate(request, quote, account):
        quantity = request["quantity_micros"]
        positions = {item["symbol"]: item for item in account["positions"]}
        position = positions.get(
            request["symbol"], {"quantity_micros": 0, "reserved_micros": 0, "short_collateral": 0}
        )
        held, reserved, side = position["quantity_micros"], position["reserved_micros"], request["side"]
        if side == "BUY" and held < 0:
            raise PersonalPaperRiskRejected("空头仓位必须先使用 COVER 平仓。")
        if side == "SHORT" and held > 0:
            raise PersonalPaperRiskRejected("多头仓位必须先使用 SELL 平仓。")
        if side == "SELL" and held - max(reserved, 0) < quantity:
            raise PersonalPaperRiskRejected("SELL 不得超过可用多头持仓。")
        if side == "COVER" and -held - max(-reserved, 0) < quantity:
            raise PersonalPaperRiskRejected("COVER 不得超过可用空头持仓。")
        price = quote.ask_minor if side in {"BUY", "COVER"} else quote.bid_minor
        notional = minor_times_quantity(price, quantity)
        if request["order_type"] == "MARKET":
            cash_delta = -(notional + quote.commission_minor) if side in {"BUY", "COVER"} else notional - quote.commission_minor
            collateral_delta = 2 * notional if side == "SHORT" else 0
            if side == "COVER":
                collateral_delta = -round(position["short_collateral"] * quantity / abs(held))
            required = -cash_delta + max(collateral_delta, 0)
            if required > account["buying_power_minor"]:
                raise PersonalPaperRiskRejected("可用购买力不足。")
            return "FILLED", {
                "event_type": "ORDER_FILLED", "cash_delta": cash_delta,
                "reserved_cash_delta": collateral_delta,
                "position_delta": quantity if side in {"BUY", "COVER"} else -quantity,
                "reserved_position_delta": 0,
            }
        reserve_price = request["limit_price_minor"] or request["stop_price_minor"] or price
        reserve = minor_times_quantity(reserve_price, quantity) + quote.commission_minor
        if side in {"BUY", "SHORT"} and reserve > account["buying_power_minor"]:
            raise PersonalPaperRiskRejected("可用购买力不足。")
        return "PENDING", {
            "event_type": "ORDER_RESERVED", "cash_delta": 0,
            "reserved_cash_delta": reserve if side in {"BUY", "SHORT"} else 0,
            "position_delta": 0,
            "reserved_position_delta": quantity if side == "SELL" else (-quantity if side == "COVER" else 0),
        }

    @staticmethod
    def _owned_season(connection, user_id: int, season_id: str):
        row = connection.execute(
            "SELECT * FROM personal_paper_seasons WHERE id=? AND user_id=?", (season_id, user_id)
        ).fetchone()
        if row is None:
            raise PersonalPaperConflict("个人模拟赛季不存在或不属于当前用户。")
        if row["state"] != "active":
            raise PersonalPaperConflict("个人模拟赛季已经结束。")
        return row

    @staticmethod
    def _season(row) -> dict[str, Any]:
        return {
            "id": row["id"], "state": row["state"], "currency": row["currency"],
            "initial_cash": _money(row["initial_cash_minor"]), "started_at": row["started_at"],
            "closed_at": row["closed_at"], "version": row["version"],
        }

    @staticmethod
    def _public_order(
        row: Any, status: str | None = None, *, account_version: int | None = None
    ) -> dict[str, Any]:
        current_status = status or row["current_status"] if "current_status" in row.keys() else status or row["status"]
        cancel_eligible = current_status == "PENDING"
        return {
            "id": row["public_id"], "season_id": row["season_id"], "market": row["market"],
            "symbol": row["symbol"], "side": row["side"], "order_type": row["order_type"],
            "quantity": _quantity(int(row["quantity_micros"])), "status": current_status,
            "created_at": row["created_at"], "quote_id": row["quote_proof_id"],
            "account_version": int(row["account_version"]),
            "cancel_eligible": cancel_eligible,
            "cancel_account_version": account_version if cancel_eligible else None,
        }

    def _order_views(self, connection: Any, season: Any) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        rows = connection.execute(
            """SELECT o.*,
                      CASE WHEN EXISTS(
                          SELECT 1 FROM personal_paper_order_events e
                          WHERE e.order_id=o.public_id AND e.event_type='CANCELLED'
                      ) THEN 'CANCELLED' ELSE o.status END AS current_status
               FROM personal_paper_orders o
               WHERE o.season_id=? AND o.user_id=?
               ORDER BY o.created_at DESC,o.public_id DESC LIMIT 50""",
            (season["id"], season["user_id"]),
        ).fetchall()
        views = [self._public_order(row, account_version=int(season["version"])) for row in rows]
        return [item for item in views if item["status"] == "PENDING"], views

    def _account_state(
        self, connection, season, *, now: datetime | None = None
    ) -> dict[str, Any]:
        now_value = now or self.clock()
        events = connection.execute(
            "SELECT * FROM personal_paper_account_events WHERE season_id=? ORDER BY sequence,public_id",
            (season["id"],),
        ).fetchall()
        cash = int(season["initial_cash_minor"])
        reserved_cash = 0
        positions: dict[str, dict[str, int | str]] = {}
        marks: dict[str, Any] = {}
        realized = 0
        for event in events:
            cash += int(event["cash_delta_minor"])
            reserved_cash += int(event["reserved_cash_delta_minor"])
            symbol = event["symbol"]
            if symbol is None:
                continue
            state = positions.setdefault(symbol, {
                "market": event["market"], "symbol": symbol, "quantity_micros": 0,
                "reserved_micros": 0, "basis_minor": 0, "short_collateral": 0,
            })
            state["reserved_micros"] = int(state["reserved_micros"]) + int(event["reserved_position_delta_micros"])
            if int(event["reserved_cash_delta_minor"]) != 0 and event["side"] in {"SHORT", "COVER"}:
                state["short_collateral"] = int(state["short_collateral"]) + int(event["reserved_cash_delta_minor"])
            marks[symbol] = event
            if event["event_type"] != "ORDER_FILLED":
                continue
            quantity = abs(int(event["position_delta_micros"]))
            notional = minor_times_quantity(int(event["execution_price_minor"]), quantity)
            commission = int(event["commission_minor"])
            prior_quantity = int(state["quantity_micros"])
            prior_basis = int(state["basis_minor"])
            if event["side"] == "BUY":
                state["basis_minor"] = prior_basis + notional + commission
            elif event["side"] == "SELL":
                released = round(prior_basis * quantity / prior_quantity)
                realized += notional - commission - released
                state["basis_minor"] = prior_basis - released
            elif event["side"] == "SHORT":
                state["basis_minor"] = prior_basis + notional - commission
            else:
                released = round(prior_basis * quantity / abs(prior_quantity))
                realized += released - notional - commission
                state["basis_minor"] = prior_basis - released
            state["quantity_micros"] = prior_quantity + int(event["position_delta_micros"])
        market_value = 0
        unrealized = 0
        quote_state = "fresh" if any(int(item["quantity_micros"]) != 0 for item in positions.values()) else "missing"
        quote_times: list[datetime] = []
        for symbol, state in positions.items():
            quantity = int(state["quantity_micros"])
            if quantity == 0:
                continue
            mark = marks[symbol]
            marked_at = datetime.fromisoformat(str(mark["quote_as_of"]).replace("Z", "+00:00"))
            state["mark_bid_minor"] = int(mark["mark_bid_minor"])
            state["mark_ask_minor"] = int(mark["mark_ask_minor"])
            state["mark_last_minor"] = int(mark["mark_last_minor"])
            state["quote_as_of"] = str(mark["quote_as_of"])
            state["quote_state"] = str(mark["quote_state"])
            quote_times.append(marked_at)
            if now_value - marked_at > MAX_QUOTE_AGE:
                quote_state = "stale"
            price = int(mark["mark_bid_minor"] if quantity > 0 else mark["mark_ask_minor"])
            value = minor_times_quantity(price, abs(quantity)) * (1 if quantity > 0 else -1)
            market_value += value
            unrealized += value - int(state["basis_minor"]) if quantity > 0 else int(state["basis_minor"]) + value
        total_equity = cash + market_value
        if total_equity != int(season["initial_cash_minor"]) + realized + unrealized:
            raise PersonalPaperConflict("个人模拟账本余额不平，请联系支持。")
        open_orders, recent_orders = self._order_views(connection, season)
        return {
            "season": self._season(season), "cash_minor": cash,
            "initial_cash_minor": int(season["initial_cash_minor"]),
            "reserved_cash_minor": reserved_cash, "buying_power_minor": cash - reserved_cash,
            "market_value_minor": market_value, "realized_pnl_minor": realized,
            "unrealized_pnl_minor": unrealized, "total_equity_minor": total_equity,
            "as_of": _stamp(min(quote_times) if quote_times else now_value),
            "quote_state": quote_state, "account_version": season["version"],
            "positions": list(positions.values()),
            "open_orders": open_orders, "recent_orders": recent_orders,
        }

    @staticmethod
    def _public_account(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "season": state["season"], "cash": _money(state["cash_minor"]),
            "reserved_cash": _money(state["reserved_cash_minor"]),
            "buying_power": _money(state["buying_power_minor"]),
            "market_value": _money(state["market_value_minor"]),
            "realized_pnl": _money(state["realized_pnl_minor"]),
            "unrealized_pnl": _money(state["unrealized_pnl_minor"]),
            "total_equity": _money(state["total_equity_minor"]), "as_of": state["as_of"],
            "quote_state": state["quote_state"], "account_version": state["account_version"],
            "positions": [
                {"market": item["market"], "symbol": item["symbol"],
                 "quantity": _quantity(int(item["quantity_micros"]))}
                for item in state["positions"] if int(item["quantity_micros"]) != 0
            ],
            "open_orders": state["open_orders"], "recent_orders": state["recent_orders"],
        }

    @staticmethod
    def _record_equity(connection, season, state, now):
        connection.execute(
            """INSERT INTO personal_paper_equity_events
               (public_id,season_id,sequence,cash_minor,reserved_cash_minor,market_value_minor,
                realized_pnl_minor,unrealized_pnl_minor,total_equity_minor,quote_state,as_of)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (f"ppee_{uuid.uuid4().hex}", season["id"], season["version"],
             state["cash_minor"], state["reserved_cash_minor"], state["market_value_minor"],
             state["realized_pnl_minor"], state["unrealized_pnl_minor"],
             state["total_equity_minor"], state["quote_state"], now),
        )


__all__ = [
    "PersonalPaperConflict", "PersonalPaperRiskRejected", "PersonalPaperService", "VerifiedQuote",
]
