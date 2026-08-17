"""Explicitly enabled, read-only auto-live broker reconciliation runtime."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from typing import Any, Mapping, Protocol

from core.auto_live_broker_reconciliation import AutoLiveBrokerReconciler
from core.auto_live_control import AutoLiveControlPlane
from core.compat import UTC
from core.database import get_database
from trading.tiger_reconciliation import TigerOrderObservationSource


class ReconciliationRuntimeConfigurationError(ValueError):
    pass


DEFAULT_RECONCILIATION_MARKER = Path("/etc/ciclotrade/enable-auto-live-reconciliation.after-integration")


def _enabled(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ReconciliationRuntimeSettings:
    enabled: bool
    marker_path: Path
    limit: int = 100

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
        *,
        marker_path: str | Path = DEFAULT_RECONCILIATION_MARKER,
    ) -> "ReconciliationRuntimeSettings":
        env = os.environ if environment is None else environment
        enabled = _enabled(env.get("CICLO_AUTO_LIVE_RECONCILIATION_ENABLED", "false"))
        if not enabled:
            raise ReconciliationRuntimeConfigurationError("auto-live reconciliation runtime is disabled")
        marker = Path(marker_path)
        if not marker.is_file():
            raise ReconciliationRuntimeConfigurationError("auto-live reconciliation marker is missing")
        raw_limit = env.get("CICLO_AUTO_LIVE_RECONCILIATION_LIMIT", "100")
        try:
            limit = int(raw_limit)
        except (TypeError, ValueError) as exc:
            raise ReconciliationRuntimeConfigurationError("auto-live reconciliation limit is invalid") from exc
        if isinstance(raw_limit, bool) or not 1 <= limit <= 500:
            raise ReconciliationRuntimeConfigurationError("auto-live reconciliation limit must be 1 to 500")
        return cls(enabled=True, marker_path=marker, limit=limit)


class PendingReconciler(Protocol):
    def reconcile_pending(self, *, limit: int = 100) -> dict[str, int | str]: ...


class AutoLiveReconciliationRuntime:
    def __init__(self, reconciler: PendingReconciler, settings: ReconciliationRuntimeSettings) -> None:
        if not settings.enabled:
            raise ReconciliationRuntimeConfigurationError("auto-live reconciliation runtime is disabled")
        if not settings.marker_path.is_file():
            raise ReconciliationRuntimeConfigurationError("auto-live reconciliation marker is missing")
        self.reconciler = reconciler
        self.settings = settings

    def run_once(self) -> dict[str, int | str]:
        return self.reconciler.reconcile_pending(limit=self.settings.limit)


def build_runtime(settings: ReconciliationRuntimeSettings) -> AutoLiveReconciliationRuntime:
    def clock() -> datetime:
        return datetime.now(UTC)

    control = AutoLiveControlPlane(get_database())
    source = TigerOrderObservationSource(clock=clock)
    reconciler = AutoLiveBrokerReconciler(control, source=source, clock=clock)
    return AutoLiveReconciliationRuntime(reconciler, settings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one read-only auto-live broker reconciliation batch.")
    parser.add_argument("--once", action="store_true", help="Run exactly one bounded batch.")
    args = parser.parse_args(argv)
    if not args.once:
        parser.error("--once is required")
    try:
        settings = ReconciliationRuntimeSettings.from_environment()
        result = build_runtime(settings).run_once()
    except ReconciliationRuntimeConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AutoLiveReconciliationRuntime",
    "ReconciliationRuntimeConfigurationError",
    "ReconciliationRuntimeSettings",
    "build_runtime",
    "main",
]
