# -*- coding: utf-8 -*-
"""老虎证券官方 Python SDK 安全封装。"""

from __future__ import annotations

from datetime import datetime
from core.compat import UTC
import os
from typing import Any


def _value(record: Any, name: str, default: Any = None) -> Any:
    if isinstance(record, dict):
        return record.get(name, default)
    return getattr(record, name, default)


def _enum_text(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _timestamp(value: Any) -> str:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000, UTC).isoformat(timespec="seconds")
    return str(value or "")


def normalize_portfolio(assets: Any, positions: list[Any], orders: list[Any]) -> dict[str, Any]:
    """Normalize Tiger SDK objects into a public, account-id-free snapshot."""
    asset = (assets or [None])[0]
    summary = _value(asset, "summary", asset)
    position_rows: list[dict[str, Any]] = []
    for position in positions or []:
        contract = _value(position, "contract", {})
        sec_type = _enum_text(_value(contract, "sec_type", "STK")).upper()
        position_rows.append(
            {
                "instrument_type": "option" if sec_type in {"OPT", "OPTION"} else "stock",
                "symbol": str(_value(contract, "symbol", "--")),
                "currency": _enum_text(_value(contract, "currency", _value(summary, "currency", "USD"))),
                "expiry": str(_value(contract, "expiry", "") or ""),
                "strike": _value(contract, "strike"),
                "right": _enum_text(_value(contract, "put_call", "")),
                "quantity": float(_value(position, "quantity", 0) or 0),
                "average_cost": float(_value(position, "average_cost", 0) or 0),
                "market_price": float(_value(position, "market_price", 0) or 0),
                "market_value": float(_value(position, "market_value", 0) or 0),
                "realized_pnl": float(_value(position, "realized_pnl", 0) or 0),
                "unrealized_pnl": float(_value(position, "unrealized_pnl", 0) or 0),
                "today_pnl": float(_value(position, "today_pnl", 0) or 0),
            }
        )
    order_rows: list[dict[str, Any]] = []
    for order in orders or []:
        contract = _value(order, "contract", {})
        sec_type = _enum_text(_value(contract, "sec_type", "STK")).upper()
        order_rows.append(
            {
                "time": _timestamp(_value(order, "trade_time") or _value(order, "order_time")),
                "instrument_type": "option" if sec_type in {"OPT", "OPTION", "MLEG"} else "stock",
                "symbol": str(_value(contract, "symbol", _value(order, "symbol", "--"))),
                "action": _enum_text(_value(order, "action", "--")),
                "quantity": float(_value(order, "quantity", 0) or 0),
                "filled": float(_value(order, "filled", 0) or 0),
                "avg_fill_price": float(_value(order, "avg_fill_price", 0) or 0),
                "commission": float(_value(order, "commission", 0) or 0),
                "status": _enum_text(_value(order, "status", "--")),
            }
        )
    currency = _enum_text(_value(summary, "currency", "USD")) or "USD"
    return {
        "account": {
            "currency": currency,
            "total_assets": float(_value(summary, "net_liquidation", 0) or 0),
            "available": float(_value(summary, "available_funds", _value(summary, "cash", 0)) or 0),
            "cash": float(_value(summary, "cash", 0) or 0),
            "market_value": float(_value(summary, "gross_position_value", 0) or 0),
            "unrealized_pnl": sum(row["unrealized_pnl"] for row in position_rows),
            "realized_pnl": sum(row["realized_pnl"] for row in position_rows),
            "today_pnl": sum(row["today_pnl"] for row in position_rows),
        },
        "positions": position_rows,
        "orders": sorted(order_rows, key=lambda row: row["time"], reverse=True),
    }


class TigerAPI:
    def __init__(self) -> None:
        self.properties_path = os.getenv("TIGER_PROPERTIES_PATH", "").strip()
        self.tiger_id = os.getenv("TIGER_ID", "")
        self.account = os.getenv("TIGER_ACCOUNT", "")
        self.private_key = os.getenv("TIGER_PRIVATE_KEY", "")
        self.environment = os.getenv("TIGER_ENV", "paper").strip().lower()
        self._client = None

    @property
    def configured(self) -> bool:
        return bool(
            (self.properties_path and os.path.isfile(self.properties_path))
            or (self.tiger_id and self.account and self.private_key)
        )

    def connect(self):
        if not self.configured:
            raise RuntimeError("TIGER_ID、TIGER_ACCOUNT 或 TIGER_PRIVATE_KEY 尚未配置。")
        try:
            from tigeropen.tiger_open_config import TigerOpenClientConfig
            from tigeropen.trade.trade_client import TradeClient
        except ImportError as exc:
            raise RuntimeError("尚未安装老虎证券官方 tigeropen SDK。") from exc
        if self.properties_path:
            if not os.path.isfile(self.properties_path):
                raise RuntimeError("Tiger properties 文件不存在。")
            config = TigerOpenClientConfig(props_path=self.properties_path)
        else:
            config = TigerOpenClientConfig(sandbox_debug=self.environment != "live")
            config.tiger_id = self.tiger_id
            config.account = self.account
            config.private_key = self.private_key
        if not config.tiger_id or not config.account or not config.private_key:
            raise RuntimeError("Tiger properties 缺少 tiger_id、account 或 private_key。")
        self.tiger_id = config.tiger_id
        self.account = config.account
        self.private_key = config.private_key
        self._client = TradeClient(config)
        return self._client

    def account_assets(self):
        client = self._client or self.connect()
        return client.get_assets(account=self.account, segment=True, market_value=True)

    def positions(self, sec_type: str = "STK"):
        client = self._client or self.connect()
        return client.get_positions(account=self.account, sec_type=sec_type)

    def orders(self):
        client = self._client or self.connect()
        return client.get_orders(account=self.account, limit=100)

    def paper_snapshot(self) -> dict[str, Any]:
        if self.environment == "live":
            raise RuntimeError("当前 Tiger 配置不是模拟环境。")
        return normalize_portfolio(
            self.account_assets(),
            [*(self.positions("STK") or []), *(self.positions("OPT") or [])],
            self.orders() or [],
        )

    def place_stock_limit(
        self, symbol: str, side: str, quantity: int, price: float, *, user_id: int
    ):
        operator_id = os.getenv("TRADEAI_LIVE_OPERATOR_USER_ID", "").strip()
        if not operator_id or str(user_id) != operator_id:
            raise RuntimeError("共享 Tiger 账户只允许已配置的实盘操作员下单。")
        if self.environment != "live":
            raise RuntimeError("Tiger 当前不是 live 环境，订单未发送。")
        if os.getenv("TIGER_REAL_TRADING_ENABLED", "false").lower() != "true":
            raise RuntimeError("实盘下单总开关未启用。请先完成老虎模拟盘联调。")
        client = self._client or self.connect()
        from tigeropen.common.util.contract_utils import stock_contract
        from tigeropen.common.util.order_utils import limit_order

        contract = stock_contract(symbol=symbol, currency="USD")
        order = limit_order(self.account, contract, side.upper(), quantity, limit_price=price)
        client.place_order(order)
        return order
