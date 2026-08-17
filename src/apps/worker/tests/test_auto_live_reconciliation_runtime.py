from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.apps.worker.auto_live_reconciliation_runtime import (
    AutoLiveReconciliationRuntime,
    ReconciliationRuntimeConfigurationError,
    ReconciliationRuntimeSettings,
)


def test_reconciliation_runtime_is_disabled_by_default_and_limit_is_bounded(tmp_path):
    marker = tmp_path / "enable-auto-live-reconciliation.after-integration"
    with pytest.raises(ReconciliationRuntimeConfigurationError, match="disabled"):
        ReconciliationRuntimeSettings.from_environment({}, marker_path=marker)
    with pytest.raises(ReconciliationRuntimeConfigurationError, match="marker"):
        ReconciliationRuntimeSettings.from_environment(
            {"CICLO_AUTO_LIVE_RECONCILIATION_ENABLED": "true"}, marker_path=marker
        )
    marker.write_text("reviewed\n", encoding="utf-8")
    with pytest.raises(ReconciliationRuntimeConfigurationError, match="limit"):
        ReconciliationRuntimeSettings.from_environment(
            {"CICLO_AUTO_LIVE_RECONCILIATION_ENABLED": "true", "CICLO_AUTO_LIVE_RECONCILIATION_LIMIT": "0"},
            marker_path=marker,
        )
    settings = ReconciliationRuntimeSettings.from_environment(
        {"CICLO_AUTO_LIVE_RECONCILIATION_ENABLED": "true", "CICLO_AUTO_LIVE_RECONCILIATION_LIMIT": "25"},
        marker_path=marker,
    )
    assert settings.limit == 25 and settings.marker_path == marker
    with pytest.raises(ReconciliationRuntimeConfigurationError, match="marker"):
        AutoLiveReconciliationRuntime(
            object(),
            ReconciliationRuntimeSettings(enabled=True, limit=20, marker_path=tmp_path / "missing-marker"),
        )


def test_reconciliation_runtime_calls_only_bounded_pending_lookup(tmp_path):
    calls = []

    class Reconciler:
        def reconcile_pending(self, *, limit):
            calls.append(limit)
            return {"status": "completed", "total": 2, "resolved": 1, "unresolved": 1, "failed": 0}

    marker = tmp_path / "enable-auto-live-reconciliation.after-integration"
    marker.write_text("reviewed\n", encoding="utf-8")
    runtime = AutoLiveReconciliationRuntime(
        Reconciler(),
        ReconciliationRuntimeSettings(enabled=True, limit=20, marker_path=marker),
    )
    result = runtime.run_once()
    assert calls == [20]
    assert result == {"status": "completed", "total": 2, "resolved": 1, "unresolved": 1, "failed": 0}
    assert json.dumps(result, sort_keys=True) == '{"failed": 0, "resolved": 1, "status": "completed", "total": 2, "unresolved": 1}'


def test_systemd_templates_are_double_gated_oneshot_and_never_enable_sending():
    root = Path(__file__).resolve().parents[4]
    service = (root / "ops" / "ciclotrade-auto-live-reconciliation.service").read_text(encoding="utf-8")
    timer = (root / "ops" / "ciclotrade-auto-live-reconciliation.timer").read_text(encoding="utf-8")
    assert "ConditionPathExists=/etc/ciclotrade/enable-auto-live-reconciliation.after-integration" in service
    assert "Type=oneshot" in service
    assert "auto_live_reconciliation_runtime --once" in service
    assert "EnvironmentFile=/opt/CicloTrade/.env" in service
    assert "PrivateNetwork=false" in service
    assert "TIGER_REAL_TRADING_ENABLED=true" not in service
    assert "OnUnitActiveSec=1min" in timer
    assert "Persistent=false" in timer
    assert "[Install]" not in timer
