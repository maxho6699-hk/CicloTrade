# -*- coding: utf-8 -*-
"""Paddle Billing 交易创建客户端。"""

from __future__ import annotations

import json
import os
from urllib.request import Request, urlopen


class PaddleClient:
    def __init__(self) -> None:
        self.api_key = os.getenv("PADDLE_API_KEY", "")
        self.environment = os.getenv("PADDLE_ENV", "sandbox").strip().lower()

    @property
    def configured(self) -> bool:
        return bool(self.api_key) and self.environment in {"sandbox", "production"}

    def create_transaction(self, order_no: str, price_id: str) -> dict:
        if self.environment not in {"sandbox", "production"}:
            raise RuntimeError("PADDLE_ENV 必须是 sandbox 或 production；支付已停止。")
        if not self.configured or not price_id:
            raise RuntimeError("Paddle API Key 或对应 Price ID 尚未配置。")
        host = {
            "sandbox": "https://sandbox-api.paddle.com",
            "production": "https://api.paddle.com",
        }[self.environment]
        body = json.dumps({"items": [{"price_id": price_id, "quantity": 1}], "custom_data": {"order_no": order_no}}).encode()
        request = Request(
            f"{host}/transactions",
            data=body,
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))["data"]
