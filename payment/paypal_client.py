# -*- coding: utf-8 -*-
"""PayPal Orders v2 客户端。"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
import json
import os
import threading
import time
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen
from uuid import UUID


class PayPalClient:
    _token_lock = threading.Lock()
    _token_cache: tuple[tuple[str, str, str], str, float] | None = None

    def __init__(self) -> None:
        self.client_id = os.getenv("PAYPAL_CLIENT_ID", "")
        self.client_secret = os.getenv("PAYPAL_CLIENT_SECRET", "")
        self.environment = os.getenv("PAYPAL_ENV", "sandbox").strip().lower()

    @property
    def configured(self) -> bool:
        return bool(self.client_id and self.client_secret)

    @property
    def host(self) -> str:
        if self.environment not in {"sandbox", "live"}:
            raise RuntimeError("PAYPAL_ENV 必须是 sandbox 或 live。")
        return "https://api-m.sandbox.paypal.com" if self.environment == "sandbox" else "https://api-m.paypal.com"

    def _callback_urls(self) -> tuple[str, str]:
        base_url = os.getenv("APP_BASE_URL", "").rstrip("/")
        parsed = urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
            raise RuntimeError("APP_BASE_URL 必须是完整的 HTTP(S) 网址。")
        if (self.environment == "live" or os.getenv("APP_ENV", "development").lower() == "production") and parsed.scheme != "https":
            raise RuntimeError("生产或 PayPal live 回调必须使用 HTTPS。")
        return f"{base_url}/payments/paypal/return", f"{base_url}/payments/paypal/cancel"

    def _access_token(self) -> str:
        if not self.configured:
            raise RuntimeError("PayPal Client ID 或 Secret 尚未配置。")
        cache_key = (self.host, self.client_id, self.client_secret)
        # ponytail: one global lock prevents token refresh stampedes; split by credential only if contention appears.
        with self._token_lock:
            cached = self._token_cache
            if cached and cached[0] == cache_key and time.monotonic() < cached[2]:
                return cached[1]
            auth = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
            request = Request(
                f"{self.host}/v1/oauth2/token",
                data=urlencode({"grant_type": "client_credentials"}).encode(),
                headers={"Authorization": f"Basic {auth}", "Content-Type": "application/x-www-form-urlencoded"},
                method="POST",
            )
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
            token = payload.get("access_token")
            try:
                expires_in = max(0, min(int(payload.get("expires_in", 0)), 86_400))
            except (TypeError, ValueError):
                expires_in = 0
            if not isinstance(token, str) or not token:
                raise RuntimeError("PayPal 未返回有效的 OAuth Token。")
            type(self)._token_cache = (cache_key, token, time.monotonic() + max(0, expires_in - 60))
            return token

    @staticmethod
    def _validated_webhook_headers(headers: dict[str, str]) -> dict[str, str] | None:
        required = {
            "paypal-auth-algo",
            "paypal-cert-url",
            "paypal-transmission-id",
            "paypal-transmission-sig",
            "paypal-transmission-time",
        }
        normalized = {
            str(key).lower(): value.strip()
            for key, value in headers.items()
            if isinstance(value, str)
        }
        if any(not normalized.get(key) or len(normalized[key]) > 4096 for key in required):
            return None
        if any(not normalized[key].isascii() or any(char.isspace() for char in normalized[key]) for key in required):
            return None
        try:
            cert = urlparse(normalized["paypal-cert-url"])
            hostname = (cert.hostname or "").lower()
            port = cert.port
        except ValueError:
            return None
        if (
            cert.scheme != "https"
            or not hostname.endswith(".paypal.com")
            or cert.username
            or cert.password
            or port not in {None, 443}
            or cert.query
            or cert.fragment
        ):
            return None
        try:
            if normalized["paypal-auth-algo"] != "SHA256withRSA":
                return None
            UUID(normalized["paypal-transmission-id"])
            if datetime.fromisoformat(
                normalized["paypal-transmission-time"].replace("Z", "+00:00")
            ).tzinfo is None:
                return None
            if not base64.b64decode(normalized["paypal-transmission-sig"], validate=True):
                return None
        except (ValueError, binascii.Error):
            return None
        return normalized

    @classmethod
    def webhook_headers_valid(cls, headers: dict[str, str]) -> bool:
        return cls._validated_webhook_headers(headers) is not None

    def create_order(self, order_no: str, amount: float, currency: str = "HKD") -> dict:
        return_url, cancel_url = self._callback_urls()
        body = json.dumps(
            {
                "intent": "CAPTURE",
                "purchase_units": [
                    {"reference_id": order_no, "amount": {"currency_code": currency, "value": f"{amount:.2f}"}}
                ],
                "application_context": {"return_url": return_url, "cancel_url": cancel_url},
            }
        ).encode()
        request = Request(
            f"{self.host}/v2/checkout/orders",
            data=body,
            headers={
                "Authorization": f"Bearer {self._access_token()}",
                "Content-Type": "application/json",
                "PayPal-Request-Id": f"create-{order_no}",
            },
            method="POST",
        )
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    def capture_order(self, order_id: str) -> dict:
        if not order_id:
            raise ValueError("PayPal 订单编号不能为空。")
        request = Request(
            f"{self.host}/v2/checkout/orders/{quote(order_id, safe='')}/capture",
            data=b"{}",
            headers={
                "Authorization": f"Bearer {self._access_token()}",
                "Content-Type": "application/json",
                "PayPal-Request-Id": f"capture-{order_id}",
            },
            method="POST",
        )
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))

    def verify_webhook(self, headers: dict[str, str], event: dict) -> bool:
        headers = self._validated_webhook_headers(headers)
        if headers is None:
            return False
        webhook_id = os.getenv("PAYPAL_WEBHOOK_ID", "")
        if not self.configured or not webhook_id:
            raise RuntimeError("PayPal Webhook ID 尚未配置。")
        body = json.dumps(
            {
                "auth_algo": headers.get("paypal-auth-algo", ""),
                "cert_url": headers.get("paypal-cert-url", ""),
                "transmission_id": headers.get("paypal-transmission-id", ""),
                "transmission_sig": headers.get("paypal-transmission-sig", ""),
                "transmission_time": headers.get("paypal-transmission-time", ""),
                "webhook_id": webhook_id,
                "webhook_event": event,
            }
        ).encode()
        request = Request(
            f"{self.host}/v1/notifications/verify-webhook-signature",
            data=body,
            headers={"Authorization": f"Bearer {self._access_token()}", "Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
        return result.get("verification_status") == "SUCCESS"
