# -*- coding: utf-8 -*-
"""老虎证券官方 Python SDK 安全封装。"""

from __future__ import annotations

import os


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
        return client.get_assets(account=self.account)

    def positions(self):
        client = self._client or self.connect()
        return client.get_positions(account=self.account)

    def orders(self):
        client = self._client or self.connect()
        return client.get_open_orders(account=self.account)

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
