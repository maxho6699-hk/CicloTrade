"""Read-only compatibility contract for official paper event consumers.

New official simulation events are written to the independent v2 ledger.  The
legacy journal remains readable for historical records, but v2 wins whenever
the immutable external-event identity exists in both ledgers.
"""

from __future__ import annotations

import os
from typing import Any

from core.quant_journal import OfficialPaperJournalV2, QuantJournal


LEGACY = "legacy"
OFFICIAL_PAPER_V2 = "official_paper_v2"
LEGACY_LEDGER_KEY = os.getenv("TRADEAI_SYSTEM_LEDGER_KEY", "tradeai-system")
OFFICIAL_PAPER_V2_LEDGER_KEY = os.getenv(
    "TRADEAI_OFFICIAL_PAPER_V2_LEDGER_KEY", "tradeai-official-paper-v2"
)


def journal_for(database, store: str):
    if store == OFFICIAL_PAPER_V2:
        return OfficialPaperJournalV2(database)
    if store == LEGACY:
        return QuantJournal(database)
    raise ValueError("unknown official-paper event store")


def event_source_key(event: dict[str, Any]) -> tuple[str, str]:
    """Stable cross-ledger identity; v2 wins when both copied one event."""
    return (str(event.get("source") or ""), str(event.get("external_event_id") or ""))


def active_events(
    database, *, include_legacy: bool = True, include_v2: bool = True
) -> list[dict[str, Any]]:
    """Return deterministic, de-duplicated official events with a private store tag."""
    sources = [(OFFICIAL_PAPER_V2, OFFICIAL_PAPER_V2_LEDGER_KEY)] if include_v2 else []
    if include_legacy:
        sources.append((LEGACY, LEGACY_LEDGER_KEY))
    chosen: dict[tuple[str, str], dict[str, Any]] = {}
    for store, ledger_key in sources:
        for event in journal_for(database, store).list_events(ledger_key):
            identity = event_source_key(event)
            # V2 is visited first and therefore wins a copied event.  Events
            # without a usable external identity are still ledger-local.
            if identity == ("", ""):
                identity = (store, str(event["id"]))
            chosen.setdefault(identity, {**event, "_consumer_store": store})
    return sorted(
        chosen.values(),
        key=lambda event: (
            str(event.get("recorded_at") or ""),
            0 if event["_consumer_store"] == OFFICIAL_PAPER_V2 else 1,
            int(event["id"]),
        ),
    )
