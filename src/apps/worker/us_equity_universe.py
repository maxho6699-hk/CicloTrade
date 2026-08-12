"""Versioned, historical US equity universe snapshots for shadow research."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import hashlib
import json
import re
from typing import Iterable


LAYERS = frozenset({"nasdaq100", "sp500", "russell2000", "popular", "watchlist", "holdings_candidates", "earnings_news_events"})
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")
STATUSES = frozenset({"active", "delisted"})
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class UniverseError(ValueError):
    """Raised when a universe cannot prove its historical membership."""


def normalize_symbol(value: str) -> str:
    symbol = value.strip().upper() if isinstance(value, str) else ""
    if not SYMBOL.fullmatch(symbol):
        raise UniverseError("US equity symbol is invalid")
    return symbol


@dataclass(frozen=True)
class UniverseMember:
    symbol: str
    source_layer: str
    inclusion_reason: str
    priority: int
    valid_from: date
    valid_to: date | None
    source_as_of: date
    listing_status: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", normalize_symbol(self.symbol))
        if self.source_layer not in LAYERS:
            raise UniverseError("source_layer is not an allowed universe layer")
        if not isinstance(self.inclusion_reason, str) or not self.inclusion_reason.strip() or len(self.inclusion_reason) > 512:
            raise UniverseError("inclusion_reason is invalid")
        if not isinstance(self.priority, int) or isinstance(self.priority, bool) or not 0 <= self.priority <= 100_000:
            raise UniverseError("priority is invalid")
        if not isinstance(self.valid_from, date) or not isinstance(self.source_as_of, date):
            raise UniverseError("membership dates are invalid")
        if self.valid_to is not None and (not isinstance(self.valid_to, date) or self.valid_to < self.valid_from):
            raise UniverseError("membership valid_to is invalid")
        if self.source_as_of < self.valid_from:
            raise UniverseError("historical source predates membership")
        if self.listing_status not in STATUSES:
            raise UniverseError("listing_status is invalid")
        if self.listing_status == "delisted" and self.valid_to is None:
            raise UniverseError("delisted membership requires a valid_to date")

    def canonical(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "source_layer": self.source_layer,
            "inclusion_reason": self.inclusion_reason.strip(),
            "priority": self.priority,
            "valid_from": self.valid_from.isoformat(),
            "valid_to": self.valid_to.isoformat() if self.valid_to else None,
            "source_as_of": self.source_as_of.isoformat(),
            "listing_status": self.listing_status,
        }


@dataclass(frozen=True)
class UniverseLayerReceipt:
    source_layer: str
    source_sha256: str
    source_as_of: date
    record_count: int

    def __post_init__(self) -> None:
        if self.source_layer not in LAYERS:
            raise UniverseError("source_layer is not an allowed universe layer")
        if not isinstance(self.source_sha256, str) or not SHA256.fullmatch(self.source_sha256):
            raise UniverseError("universe layer source hash is invalid")
        if not isinstance(self.source_as_of, date):
            raise UniverseError("universe layer source date is invalid")
        if not isinstance(self.record_count, int) or isinstance(self.record_count, bool) or self.record_count < 0:
            raise UniverseError("universe layer record count is invalid")

    def canonical(self) -> dict[str, object]:
        return {
            "source_layer": self.source_layer,
            "source_sha256": self.source_sha256,
            "source_as_of": self.source_as_of.isoformat(),
            "record_count": self.record_count,
        }


@dataclass(frozen=True)
class UniverseSnapshot:
    schema_version: int
    as_of: date
    members: tuple[UniverseMember, ...]
    layer_receipts: tuple[UniverseLayerReceipt, ...]
    sha256: str

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(sorted({member.symbol for member in self.members if member.valid_from <= self.as_of and (member.valid_to is None or member.valid_to >= self.as_of)}))

    def canonical_json(self) -> bytes:
        payload = {
            "schema_version": self.schema_version,
            "as_of": self.as_of.isoformat(),
            "layer_receipts": [receipt.canonical() for receipt in self.layer_receipts],
            "members": [member.canonical() for member in self.members],
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def build_universe_snapshot(
    as_of: datetime | date,
    members: Iterable[UniverseMember],
    layer_receipts: Iterable[UniverseLayerReceipt],
) -> UniverseSnapshot:
    snapshot_date = as_of.date() if isinstance(as_of, datetime) else as_of
    if not isinstance(snapshot_date, date):
        raise UniverseError("as_of is invalid")
    prepared = list(members)
    if not prepared or not all(isinstance(member, UniverseMember) for member in prepared):
        raise UniverseError("universe requires historical member records")
    receipts = tuple(sorted(layer_receipts, key=lambda receipt: receipt.source_layer))
    if len(receipts) != len(LAYERS) or {receipt.source_layer for receipt in receipts} != LAYERS:
        raise UniverseError("universe snapshot requires one receipt for every approved layer")
    counts = {layer: 0 for layer in LAYERS}
    latest_member_source: dict[str, date] = {}
    for member in prepared:
        if member.source_as_of > snapshot_date:
            raise UniverseError("a current static constituent list cannot represent historical membership")
        counts[member.source_layer] += 1
        latest_member_source[member.source_layer] = max(
            latest_member_source.get(member.source_layer, member.source_as_of),
            member.source_as_of,
        )
    for receipt in receipts:
        if receipt.source_as_of > snapshot_date:
            raise UniverseError("universe layer receipt is newer than the snapshot date")
        if receipt.record_count != counts[receipt.source_layer]:
            raise UniverseError("universe layer receipt count does not match member records")
        if latest_member_source.get(receipt.source_layer, receipt.source_as_of) > receipt.source_as_of:
            raise UniverseError("universe member source is newer than its layer receipt")
    ordered = tuple(sorted(prepared, key=lambda member: (member.symbol, member.priority, member.source_layer, member.valid_from, member.valid_to or date.max, member.source_as_of, member.listing_status, member.inclusion_reason)))
    canonical = [member.canonical() for member in ordered]
    if len({json.dumps(entry, sort_keys=True, separators=(",", ":")) for entry in canonical}) != len(canonical):
        raise UniverseError("duplicate universe membership records are forbidden")
    canonical_receipts = [receipt.canonical() for receipt in receipts]
    payload = {
        "schema_version": 1,
        "as_of": snapshot_date.isoformat(),
        "layer_receipts": canonical_receipts,
        "members": canonical,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return UniverseSnapshot(1, snapshot_date, ordered, receipts, hashlib.sha256(encoded).hexdigest())
