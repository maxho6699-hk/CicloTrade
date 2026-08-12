"""Canonical, read-only launch catalog for user-facing broker availability.

This catalog describes product readiness only.  It must never be used as an
execution allowlist: every listed provider remains unavailable for user
connection until a separately governed integration changes that fact.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal


BrokerCatalogStatus = Literal[
    "market_data_only",
    "limited_backend_capability",
    "integration_in_progress",
]


@dataclass(frozen=True)
class BrokerCatalogEntry:
    key: str
    display_name: str
    status: BrokerCatalogStatus
    status_label: str
    availability_detail: str
    capabilities: tuple[str, ...] = ()
    connection_available: bool = False

    def public_payload(self) -> dict[str, object]:
        payload = asdict(self)
        payload["capabilities"] = list(self.capabilities)
        return payload


US_LAUNCH_BROKER_CATALOG = (
    BrokerCatalogEntry(
        key="futu_moomoo",
        display_name="Futu / moomoo",
        status="market_data_only",
        status_label="仅行情能力",
        availability_detail="当前仅用于平台侧美股行情，不代表用户券商账户已经连接。",
        capabilities=("market_data",),
    ),
    BrokerCatalogEntry(
        key="tiger",
        display_name="Tiger Brokers",
        status="limited_backend_capability",
        status_label="有限后端能力",
        availability_detail="已有受限后端封装，但尚未开放用户申请、绑定或网页下单。",
        capabilities=("market_data", "us_stock_limit_orders"),
    ),
    BrokerCatalogEntry(
        key="ibkr",
        display_name="Interactive Brokers (IBKR)",
        status="integration_in_progress",
        status_label="接入中",
        availability_detail="美股接入正在开发，当前不能申请、绑定或交易。",
    ),
    BrokerCatalogEntry(
        key="webull",
        display_name="Webull",
        status="integration_in_progress",
        status_label="接入中",
        availability_detail="美股接入正在开发，当前不能申请、绑定或交易。",
    ),
    BrokerCatalogEntry(
        key="longbridge",
        display_name="Longbridge",
        status="integration_in_progress",
        status_label="接入中",
        availability_detail="美股接入正在开发，当前不能申请、绑定或交易。",
    ),
)


def public_us_launch_broker_catalog() -> list[dict[str, object]]:
    return [entry.public_payload() for entry in US_LAUNCH_BROKER_CATALOG]
