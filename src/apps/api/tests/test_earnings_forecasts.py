from __future__ import annotations

import asyncio
from datetime import datetime
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from core.compat import UTC
from core.database import DatabaseManager
from core.earnings_forecast_journal import EarningsForecastJournal
from core.earnings_option_research import OptionLegQuote, evaluate_defined_risk_structure
from src.apps.api.earnings_forecasts import EarningsForecastApi
from src.apps.api.earnings_read_model import (
    EarningsForecastReadModel,
    EarningsResearchNotFound,
    OpaqueIdCodec,
)


AS_OF = datetime(2026, 8, 12, 1, 0, tzinfo=UTC)
HISTORY_AS_OF = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


async def _asgi_get(path: str) -> tuple[int, dict[str, str], dict]:
    messages = []
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    from src.apps.api.app import app

    await app(
        {
            "type": "http", "asgi": {"version": "3.0"},
            "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": path, "raw_path": path.encode(), "query_string": b"",
            "headers": [(b"user-agent", b"pytest-asgi")],
            "client": ("127.0.0.1", 50000), "server": ("testserver", 80),
            "root_path": "",
        },
        receive,
        send,
    )
    start = next(item for item in messages if item["type"] == "http.response.start")
    headers = {name.decode().lower(): value.decode() for name, value in start["headers"]}
    body = b"".join(
        item.get("body", b"") for item in messages
        if item["type"] == "http.response.body"
    )
    return start["status"], headers, json.loads(body.decode())


def _event() -> dict:
    return {
        "event_key": "US:AAPL:2026Q3",
        "revision_no": 1,
        "market": "US",
        "symbol": "AAPL",
        "fiscal_period": "2026Q3",
        "scheduled_at": "2026-08-18T16:15:00-04:00",
        "exchange_timezone": "America/New_York",
        "timing": "AMC",
        "status": "CONFIRMED",
        "source": "private-provider-name",
        "source_event_id": "private-provider-event-id",
        "observed_at": "2026-08-01T12:00:00Z",
        "available_at": "2026-08-01T12:01:00Z",
        "recorded_at": "2026-08-01T12:02:00Z",
        "supersedes_revision_id": None,
    }


def _forecast(event_id: int, *, countdown_day: int = 7) -> dict:
    decision_at = (
        "2026-08-11T20:00:00-04:00"
        if countdown_day == 7
        else "2026-08-17T20:00:00-04:00"
    )
    return {
        "event_revision_id": event_id,
        "countdown_day": countdown_day,
        "decision_at": decision_at,
        "available_cutoff_at": decision_at,
        "model_id": "private-model-name",
        "model_version": "1.0.0",
        "model_artifact_sha256": "a" * 64,
        "input_manifest": {
            "schema_version": 1,
            "historical_backfill": False,
            "evidence": [{
                "source": "private-evidence-source",
                "source_snapshot_id": "private-snapshot-id",
                "observed_at": "2026-08-11T18:00:00Z",
                "available_at": "2026-08-11T18:01:00Z",
                "sha256": "b" * 64,
            }],
        },
        "p_up": 0.5,
        "p_down": 0.3,
        "p_flat": 0.2,
        "flat_band_pct": 1.0,
        "confidence": 0.58,
        "calibration_sample_size": 200,
        "reference_price": 200.0,
        "currency": "USD",
        "price_p10": 180.0,
        "price_p50": 202.0,
        "price_p90": 228.0,
        "estimated_mfe_pct": 14.0,
        "estimated_mae_pct": -10.0,
        "simulated_action": "OBSERVE",
        "narrative": {
            "summary": "Research estimate only.",
            "changed_since_previous": [],
            "supporting_evidence": ["estimate revisions"],
            "counter_evidence": ["valuation"],
        },
        "causal_graph": {"claims": [{
            "kind": "mechanism_hypothesis",
            "claim": "Revisions may support the reaction.",
            "confidence": 0.55,
            "evidence_snapshot_ids": ["private-snapshot-id"],
            "confounders": ["macro shock"],
        }]},
        "risk": {
            "defined_risk": True,
            "max_loss_amount": 0.0,
            "currency": "USD",
            "invalidation_condition": "Schedule changes.",
        },
    }


def _request(path_params: dict[str, str] | None = None, query: str = "") -> Request:
    app = SimpleNamespace(state=SimpleNamespace())
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/rewrite/v1/earnings-forecasts",
        "query_string": query.encode(),
        "headers": [],
        "path_params": path_params or {},
        "app": app,
        "client": ("127.0.0.1", 1),
    })


def _clone_forecast(database: DatabaseManager, source_id: int, **changes) -> dict:
    with database.transaction() as connection:
        row = connection.execute(
            "SELECT * FROM earnings_forecast_snapshots WHERE id=?", (source_id,)
        ).fetchone()
        assert row is not None
        payload = dict(row)
        payload.pop("id")
        payload.update(changes)
        payload["logical_run_key"] = changes.get(
            "logical_run_key", f"clone:{payload['idempotency_key']}"
        )
        columns = list(payload)
        cursor = connection.execute(
            f"INSERT INTO earnings_forecast_snapshots ({','.join(columns)}) "
            f"VALUES ({','.join('?' for _ in columns)})",
            [payload[column] for column in columns],
        )
        created = connection.execute(
            "SELECT * FROM earnings_forecast_snapshots WHERE id=?",
            (cursor.lastrowid,),
        ).fetchone()
        return dict(created)


@pytest.fixture
def seeded(tmp_path: Path):
    database = DatabaseManager(str(tmp_path / "earnings-api.db"))
    journal = EarningsForecastJournal(database)
    event = journal.record_event_revision(_event(), idempotency_key="event-api")
    forecast = journal.record_forecast(_forecast(event["id"]), idempotency_key="forecast-api")
    journal.record_forecast(
        _forecast(event["id"], countdown_day=1), idempotency_key="forecast-api-d1"
    )
    result = evaluate_defined_risk_structure(
        structure_type="LONG_STRADDLE",
        spot=200.0,
        decision_at=datetime(2026, 8, 12, 0, 0, tzinfo=UTC),
        legs=[
            OptionLegQuote(
                contract_id=f"AAPL-{right}-200", right=right, strike=200.0,
                expiry="2026-08-21", quantity=1, multiplier=100, bid=4.8, ask=5.2,
                implied_volatility=0.48, delta=0.5 if right == "CALL" else -0.5,
                gamma=0.04, theta=-0.18, vega=0.22, volume=500, open_interest=2_000,
                quote_at="2026-08-11T23:58:00Z", available_at="2026-08-11T23:58:02Z",
            )
            for right in ("CALL", "PUT")
        ],
        terminal_price_samples=[160.0 + index * 0.1 for index in range(801)],
        commission_per_contract=0.65,
        slippage_per_contract=0.35,
        model_expected_move_pct=12.0,
    )
    option = journal.record_option_research(
        forecast["id"], result, idempotency_key="option-api"
    )
    journal.record_outcome({
        "event_revision_id": event["id"], "checkpoint": "NEXT_CLOSE",
        "baseline_price": 200.0, "observed_price": 210.0, "return_pct": 5.0,
        "mfe_pct": 7.0, "mae_pct": -2.0,
        "observed_at": "2026-08-19T20:00:00Z", "available_at": "2026-08-19T20:01:00Z",
        "recorded_at": "2026-08-19T20:02:00Z", "source_snapshot_id": "private-outcome-source",
        "supersedes_outcome_id": None,
    }, idempotency_key="outcome-api")
    codec = OpaqueIdCodec("test-only-opaque-secret-that-is-at-least-32-bytes")
    return {
        "database": database,
        "model": EarningsForecastReadModel(tmp_path / "earnings-api.db", codec),
        "codec": codec,
        "event": event,
        "forecast": forecast,
        "option": option,
    }


def test_locked_dto_reads_count_only_and_has_an_exact_safe_shape(seeded):
    class Trap(EarningsForecastReadModel):
        def _upcoming_research_items(self, *args, **kwargs):
            raise AssertionError("locked path touched sensitive forecast tables")

    model = Trap(seeded["model"].db_path, seeded["codec"])
    payload = model.overview(has_capability=False, as_of=AS_OF)

    assert payload == {
        "state": "locked",
        "feature": "earnings_forecast",
        "required_capability": "earnings_forecast",
        "window_days": 7,
        "confirmed_event_count": 1,
        "reason_code": "legacy_entitlement_required",
        "description": "未来 7 天业绩预测、历史轨迹与复盘仅对历史有效专业权益开放；当前不公开新购或升级。",
        "upgrade_path": None,
    }
    assert not ({"items", "symbols", "probabilities", "history"} & set(payload))


def test_professional_overview_and_detail_are_source_anonymous(seeded):
    overview = seeded["model"].overview(has_capability=True, as_of=AS_OF)
    item = overview["items"][0]
    detail = seeded["model"].detail(
        has_capability=True, opaque_event_id=item["event_id"]
    )
    encoded = json.dumps({"overview": overview, "detail": detail}, ensure_ascii=False)

    assert overview["state"] == "research"
    assert overview["research_only"] is True
    assert overview["execution_eligible"] is False
    assert item["latest_forecast"]["p_up"] == 0.5
    assert detail["timeline"][0]["evidence_count"] == 1
    assert detail["timeline"][0]["causal_graph"]["claims"][0]["evidence_count"] == 1
    for secret in (
        "private-provider-name", "private-provider-event-id", "private-evidence-source",
        "private-snapshot-id", "private-outcome-source", "private-model-name",
    ):
        assert secret not in encoded
    assert "payload_sha256" not in encoded
    assert "idempotency_key" not in encoded
    assert detail["timeline"][0]["model_artifact_sha256"] == "a" * 64
    assert detail["timeline"][0]["evidence_manifest_sha256"]
    assert detail["timeline"][0]["action_contract"]["automatic_ordering"] is False


def test_detail_selects_one_point_in_time_authoritative_version_per_countdown_day(seeded):
    base = seeded["forecast"]
    selected = _clone_forecast(
        seeded["database"], base["id"],
        idempotency_key="forecast-api-authority-visible",
        model_version="2.0.0",
        recorded_at="2026-08-12T00:30:00Z",
        p_up=0.6, p_down=0.25, p_flat=0.15,
        payload_sha256="c" * 64,
    )
    _clone_forecast(
        seeded["database"], selected["id"],
        idempotency_key="forecast-api-authority-future",
        model_version="3.0.0",
        decision_at="2026-08-12T02:00:00Z",
        available_cutoff_at="2026-08-12T02:00:00Z",
        recorded_at="2026-08-12T02:01:00Z",
        p_up=0.9, p_down=0.05, p_flat=0.05,
        payload_sha256="d" * 64,
    )

    detail = seeded["model"].detail(
        has_capability=True,
        has_option_capability=True,
        opaque_event_id=seeded["codec"].encode("event", seeded["event"]["id"]),
        as_of=AS_OF,
    )

    assert [item["countdown_day"] for item in detail["timeline"]] == [7]
    assert detail["timeline"][0]["p_up"] == pytest.approx(0.6)
    assert detail["timeline"][0]["option_research"] == {
        "state": "no_data", "items": []
    }
    encoded = json.dumps(detail)
    assert "3.0.0" not in encoded
    assert "private-model-name" not in encoded


def test_detail_discovers_only_capability_safe_opaque_option_references(seeded):
    event_id = seeded["codec"].encode("event", seeded["event"]["id"])
    locked = seeded["model"].detail(
        has_capability=True,
        has_option_capability=False,
        opaque_event_id=event_id,
        as_of=AS_OF,
    )
    assert locked["timeline"][0]["option_research"] == {
        "state": "locked",
        "feature": "earnings_option_research",
        "required_capability": "earnings_option_defined_risk",
        "reason_code": "legacy_entitlement_required",
        "upgrade_path": None,
    }

    available = seeded["model"].detail(
        has_capability=True,
        has_option_capability=True,
        opaque_event_id=event_id,
        as_of=AS_OF,
    )
    reference = available["timeline"][0]["option_research"]
    assert reference["state"] == "available"
    assert reference["items"][0]["structure_type"] == "LONG_STRADDLE"
    option_id = reference["items"][0]["option_id"]
    assert option_id != str(seeded["option"]["id"])
    assert seeded["codec"].decode("option", option_id) == seeded["option"]["id"]
    encoded = json.dumps(available)
    assert "contract_id" not in encoded
    assert "private-evidence-source" not in encoded


def test_option_detail_preserves_multileg_quote_and_cost_semantics_without_internal_sources(seeded):
    event_id = seeded["codec"].encode("event", seeded["event"]["id"])
    detail = seeded["model"].detail(
        has_capability=True, has_option_capability=True,
        opaque_event_id=event_id, as_of=AS_OF,
    )
    option_id = detail["timeline"][0]["option_research"]["items"][0]["option_id"]
    payload = seeded["model"].option_detail(
        has_forecast_capability=True,
        has_option_capability=True,
        opaque_event_id=event_id,
        opaque_option_id=option_id,
    )

    assert payload["state"] == "research"
    assert payload["structure_type"] == "LONG_STRADDLE"
    assert len(payload["legs"]) == 2
    assert {leg["right"] for leg in payload["legs"]} == {"CALL", "PUT"}
    assert all(leg["quote_at"] and leg["available_at"] for leg in payload["legs"])
    assert payload["total_premium"] > 0
    assert payload["commission_cost"] > 0
    assert payload["slippage_cost"] > 0
    assert payload["max_loss"] > 0
    assert payload["historical_oos_validated"] is False
    assert payload["automatic_ordering"] is False
    assert payload["action_contract"]["entry"]["legs"][0]["limit_price"] == 5.2
    assert payload["action_contract"]["max_loss"] == payload["max_loss"]
    assert payload["action_contract"]["model_artifact_sha256"] == "a" * 64
    assert payload["action_contract"]["evidence_manifest_sha256"]
    assert "payload_sha256" not in payload


def test_invalid_and_nonexistent_opaque_ids_have_the_same_generic_failure(seeded):
    missing = seeded["codec"].encode("event", 999_999)
    messages = []
    for opaque_id in ("not-a-valid-token", missing):
        with pytest.raises(EarningsResearchNotFound) as caught:
            seeded["model"].detail(has_capability=True, opaque_event_id=opaque_id)
        messages.append(str(caught.value))
    assert messages == ["earnings research unavailable", "earnings research unavailable"]


def test_history_statistics_and_no_data_paths_do_not_fabricate_predictions(seeded, tmp_path):
    history = seeded["model"].history(has_capability=True, as_of=HISTORY_AS_OF)
    statistics = seeded["model"].statistics(has_capability=True, as_of=HISTORY_AS_OF)

    assert history["state"] == "research"
    assert len(history["items"]) == 1
    assert statistics["metrics"]["sample_size"] == 1
    assert statistics["metrics"]["paper_total_pnl"] is None
    assert statistics["metrics"]["paper_max_drawdown"] is None

    empty_db = DatabaseManager(str(tmp_path / "empty-earnings.db"))
    empty = EarningsForecastReadModel(
        tmp_path / "empty-earnings.db",
        OpaqueIdCodec("another-test-only-secret-that-is-at-least-32-bytes"),
    )
    payload = empty.overview(has_capability=True, as_of=AS_OF)
    assert payload["data_state"] == "no_data"
    assert payload["items"] == []
    assert empty_db.fetch_one("SELECT COUNT(*) count FROM earnings_forecast_snapshots")["count"] == 0


def test_api_responses_are_private_no_store_and_errors_do_not_leak_sources(seeded):
    identity = SimpleNamespace(id=1)
    api = EarningsForecastApi(
        seeded["model"],
        authenticate=lambda request: identity,
        has_capability=lambda current, capability: True,
        clock=lambda: AS_OF,
    )
    response = asyncio.run(api.overview(_request()))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["vary"] == "Cookie, Authorization"

    class Broken(EarningsForecastReadModel):
        def overview(self, **kwargs):
            raise RuntimeError("private-provider-name at C:/secret/database.db")

    broken = EarningsForecastApi(
        Broken(seeded["model"].db_path, seeded["codec"]),
        authenticate=lambda request: identity,
        has_capability=lambda current, capability: True,
        clock=lambda: AS_OF,
    )
    failure = asyncio.run(broken.overview(_request()))
    payload = json.loads(failure.body)
    assert failure.status_code == 503
    assert payload == {"error": "业绩预测研究暂时不可用。"}
    assert "private-provider-name" not in failure.body.decode()


def test_api_preserves_authoritative_auth_errors_and_returns_400_for_bad_query(seeded):
    class AuthoritativeAuthError(RuntimeError):
        status = 401

    denied = EarningsForecastApi(
        seeded["model"],
        authenticate=lambda request: (_ for _ in ()).throw(
            AuthoritativeAuthError("missing bearer token")
        ),
        has_capability=lambda current, capability: False,
        clock=lambda: AS_OF,
    )
    with pytest.raises(AuthoritativeAuthError, match="missing bearer token"):
        asyncio.run(denied.overview(_request()))

    valid_identity = SimpleNamespace(id=1)
    api = EarningsForecastApi(
        seeded["model"], authenticate=lambda request: valid_identity,
        has_capability=lambda current, capability: True, clock=lambda: AS_OF,
    )
    response = asyncio.run(api.overview(_request(query="limit=not-an-integer")))
    assert response.status_code == 400
    assert json.loads(response.body) == {"error": "limit 必须介于 1 与 200。"}
    response = asyncio.run(api.history(_request(query="cursor=not-valid")))
    assert response.status_code == 400
    assert json.loads(response.body) == {"error": "cursor 无效。"}


def test_defined_risk_option_capability_denies_advanced_and_allows_professional(seeded):
    event_id = seeded["codec"].encode("event", seeded["event"]["id"])
    option_id = seeded["codec"].encode("option", seeded["option"]["id"])
    request = _request({"event_id": event_id, "option_id": option_id})
    option_visible_at = datetime.fromisoformat(seeded["option"]["recorded_at"])

    advanced = SimpleNamespace(id=1, effective_plan="高级版")
    advanced_api = EarningsForecastApi(
        seeded["model"], authenticate=lambda current: advanced,
        has_capability=lambda identity, capability: capability == "earnings_forecast",
        clock=lambda: option_visible_at,
    )
    locked = asyncio.run(advanced_api.option_detail(request))
    assert json.loads(locked.body)["required_capability"] == (
        "earnings_option_defined_risk"
    )

    professional = SimpleNamespace(id=2, effective_plan="专业版")
    professional_api = EarningsForecastApi(
        seeded["model"], authenticate=lambda current: professional,
        has_capability=lambda identity, capability: capability in {
            "earnings_forecast", "earnings_option_defined_risk"
        },
        clock=lambda: option_visible_at,
    )
    allowed = asyncio.run(professional_api.option_detail(request))
    assert allowed.status_code == 200
    assert json.loads(allowed.body)["state"] == "research"


def test_rewrite_app_registers_private_earnings_routes_and_preserves_401(
    seeded, monkeypatch
):
    from src.apps.api.app import ApiError, app

    expected = {
        "/api/rewrite/v1/earnings-forecasts",
        "/api/rewrite/v1/earnings-forecasts/history",
        "/api/rewrite/v1/earnings-forecasts/statistics",
        "/api/rewrite/v1/earnings-forecasts/{event_id:str}",
        "/api/rewrite/v1/earnings-forecasts/{event_id:str}/options/{option_id:str}",
    }
    assert expected <= {route.path for route in app.routes}

    api = EarningsForecastApi(
        seeded["model"],
        authenticate=lambda request: (_ for _ in ()).throw(
            ApiError("缺少 Bearer Access Token。", 401)
        ),
        has_capability=lambda identity, capability: False,
        clock=lambda: AS_OF,
    )
    monkeypatch.setattr(app.state, "earnings_forecast_api", api)
    status, headers, payload = asyncio.run(
        _asgi_get("/api/rewrite/v1/earnings-forecasts")
    )
    assert status == 401
    assert payload["error"] == "缺少 Bearer Access Token。"
    assert payload["code"] == "authentication_required"
    assert len(payload["correlation_id"]) == 32
    assert headers["cache-control"] == "no-store"


def test_rewrite_app_fails_closed_when_earnings_api_is_not_configured(monkeypatch):
    from src.apps.api.app import app

    monkeypatch.setattr(app.state, "earnings_forecast_api", None)
    status, headers, payload = asyncio.run(
        _asgi_get("/api/rewrite/v1/earnings-forecasts")
    )
    assert status == 503
    assert payload["error"] == "业绩预测研究暂时不可用。"
    assert payload["code"] == "earnings_forecast_unavailable"
    assert len(payload["correlation_id"]) == 32
    assert headers["cache-control"] == "private, no-store"
    assert headers["vary"] == "Cookie, Authorization"


def test_rewrite_app_builds_earnings_api_only_with_a_strong_opaque_id_secret(
    monkeypatch,
):
    module = importlib.import_module("src.apps.api.app")

    for name in ("EARNINGS_OPAQUE_ID_SECRET", "JWT_SECRET_KEY", "SECRET_KEY"):
        monkeypatch.delenv(name, raising=False)
    assert module._build_earnings_forecast_api() is None

    monkeypatch.setenv("EARNINGS_OPAQUE_ID_SECRET", "e" * 31)
    assert module._build_earnings_forecast_api() is None

    monkeypatch.setenv("EARNINGS_OPAQUE_ID_SECRET", "e" * 32)
    assert isinstance(module._build_earnings_forecast_api(), EarningsForecastApi)
