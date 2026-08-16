"""Small adapter for publishing real user events to the canonical website inbox."""

from __future__ import annotations

import hashlib
from typing import Any

from core.account_center import AccountCenterService, route_public_id


def _key(stage: str, owner_id: int, source_kind: str, source_public_id: str, source_version: int) -> str:
    material = f"{stage}\x1f{owner_id}\x1f{source_kind}\x1f{source_public_id}\x1f{source_version}"
    return f"inbox-{stage}-{hashlib.sha256(material.encode()).hexdigest()[:24]}"


def publish_website_notification(
    service: AccountCenterService,
    *,
    owner_id: int,
    source_kind: str,
    source_public_id: str,
    source_version: int,
    kind: str,
    title: str,
    body: str,
    severity: str = "info",
    target_kind: str | None = None,
) -> dict[str, Any]:
    """Create one inbox item plus a delivered website-channel receipt.

    Replaying the same source tuple is safe. Network or caller retries cannot
    create a second item or a second website delivery.
    """
    target = None
    if target_kind is not None:
        target = {
            "target_kind": target_kind,
            "public_id": route_public_id(target_kind),
            "version": 1,
        }
    item = service.create_notification(
        owner_id,
        {
            "source_kind": source_kind,
            "source_public_id": source_public_id,
            "source_version": source_version,
            "kind": kind,
            "title": title,
            "body": body,
            "severity": severity,
            "target": target,
        },
        _key("item", owner_id, source_kind, source_public_id, source_version),
    )
    delivery = service.create_delivery(
        owner_id,
        item["public_id"],
        "website",
        _key("delivery", owner_id, source_kind, source_public_id, source_version),
    )
    service.record_delivery_event(
        owner_id,
        delivery["public_id"],
        "delivered",
        _key("delivered", owner_id, source_kind, source_public_id, source_version),
    )
    return {"item_public_id": item["public_id"], "delivery_public_id": delivery["public_id"]}


__all__ = ["publish_website_notification"]
