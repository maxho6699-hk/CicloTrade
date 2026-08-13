"""Read-only runtime evidence and public entitlement projections."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Mapping

from core.compat import UTC


def _table_exists(conn: Any, name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def runtime_capability_evidence(
    conn: Any,
    *,
    parse_aware: Callable[[Any, str], datetime],
) -> dict[str, dict[str, str]]:
    """Read only successful persisted observations; never probe providers."""
    evidence: dict[str, dict[str, str]] = {}

    def publish(capabilities: tuple[str, ...], observed_at: Any) -> None:
        try:
            verified_at = parse_aware(
                observed_at, "运行证据时间"
            ).isoformat(timespec="seconds")
        except ValueError:
            return
        item = {
            "data_state": "ready",
            "health": "healthy",
            "verified_at": verified_at,
        }
        for capability in capabilities:
            evidence[capability] = dict(item)

    if _table_exists(conn, "official_option_sim_event_legs"):
        row = conn.execute(
            """SELECT quote_at FROM official_option_sim_event_legs
               ORDER BY datetime(quote_at) DESC,id DESC LIMIT 1"""
        ).fetchone()
        if row is not None:
            publish(
                (
                    "option_chain", "option_quote_chart", "option_greeks",
                    "option_iv", "tg_option_signal",
                ),
                row["quote_at"],
            )
    if _table_exists(conn, "earnings_data_snapshots"):
        row = conn.execute(
            """SELECT observed_at FROM earnings_data_snapshots
               WHERE dq_status='PASS'
               ORDER BY datetime(observed_at) DESC,id DESC LIMIT 1"""
        ).fetchone()
        if row is not None:
            publish(
                ("earnings_forecast", "earnings_option_defined_risk"),
                row["observed_at"],
            )
    return evidence


def capability_contracts(
    plan: str,
    *,
    source_plans: list[dict[str, Any]],
    included_capabilities: set[str],
    runtime_evidence: Mapping[str, Mapping[str, Any]] | None = None,
    now: datetime | None = None,
    maximum_age_seconds: int = 300,
) -> list[dict[str, Any]]:
    """Project capabilities with explicit customer-visible fail-closed states."""
    all_capabilities = sorted({
        str(capability)
        for item in source_plans
        for capability in item["capabilities"]
    })
    data_bound = {
        "signal_web", "tg_stock_signal", "tg_option_signal", "option_chain",
        "option_quote_chart", "option_greeks", "option_iv", "earnings_forecast",
        "earnings_option_defined_risk",
    }
    result: list[dict[str, Any]] = []
    for capability in all_capabilities:
        included = capability in included_capabilities
        application = capability == "option_live_beta_apply" and included
        data_state = "not_applicable"
        reason_code = (
            "runtime_approval_required"
            if application
            else "included" if included else "upgrade_required"
        )
        status = (
            "application_required"
            if application
            else "available" if included else "locked"
        )
        if included and capability in data_bound:
            evidence = (runtime_evidence or {}).get(capability)
            if not isinstance(evidence, Mapping):
                status, reason_code, data_state = (
                    "unavailable", "runtime_evidence_missing", "missing",
                )
            else:
                data_state = str(evidence.get("data_state") or "missing")
                health = str(evidence.get("health") or "unknown")
                verified_at = evidence.get("verified_at")
                verified_moment: datetime | None = None
                if isinstance(verified_at, str):
                    try:
                        parsed = datetime.fromisoformat(
                            verified_at.replace("Z", "+00:00")
                        )
                        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                            verified_moment = parsed.astimezone(UTC)
                    except ValueError:
                        pass
                current = (now or datetime.now(UTC)).astimezone(UTC)
                fresh = bool(
                    verified_moment is not None
                    and 0 <= (current - verified_moment).total_seconds()
                    <= maximum_age_seconds
                )
                if data_state != "ready" or health != "healthy" or not fresh:
                    status, reason_code = "unavailable", "runtime_evidence_invalid"
        result.append({
            "key": capability,
            "status": status,
            "reason_code": reason_code,
            "limit": None,
            "data_state": data_state,
        })
    return result
