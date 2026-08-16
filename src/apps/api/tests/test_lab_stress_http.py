import asyncio
from datetime import datetime, timezone
from src.apps.api.lab_stress import lab_stress, lab_stress_catalog


class Request:
    async def json(self):
        return {"scenario_key": "market_drawdown"}


def test_handler_returns_audited_result():
    response = asyncio.run(lab_stress(Request(), snapshot_provider=lambda _: {"account_mode": "personal_paper", "currency": "USD", "as_of": datetime.now(timezone.utc).isoformat(), "data_status": "fresh", "positions": [{"symbol": "AAPL", "quantity": 1, "last_trade_price": 100}]}))
    assert response.status_code == 200
    assert response.body


def test_handler_can_use_server_snapshot_provider():
    request = Request()
    response = asyncio.run(lab_stress(request, snapshot_provider=lambda _: {"account_mode": "personal_paper", "currency": "USD", "as_of": datetime.now(timezone.utc).isoformat(), "data_status": "fresh", "positions": [{"symbol": "AAPL", "quantity": 1, "last_trade_price": 100}]}))
    assert response.status_code == 200


def test_handler_rejects_client_snapshot_tampering():
    class Tampered(Request):
        async def json(self):
            return {"scenario_key": "market_drawdown", "snapshot": {"currency": "USD"}}
    try:
        asyncio.run(lab_stress(Tampered(), snapshot_provider=lambda _: {}))
    except Exception as error:
        assert "只允许 scenario_key" in str(error)
    else:
        raise AssertionError("client snapshot was accepted")


def test_catalog_handler_returns_exact_server_contract():
    response = asyncio.run(lab_stress_catalog(Request()))
    assert response.status_code == 200
    assert b'"catalog_sha256"' in response.body
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
