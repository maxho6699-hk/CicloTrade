import asyncio
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest

from core.auth import AuthService
from core.compat import UTC
from core.database import DatabaseManager
from src.apps.api.chart_drawings import ACTIVE_DRAWING_LIMIT, SYMBOL_TOMBSTONE_LIMIT, USER_TOMBSTONE_LIMIT, ChartDrawingConflict, ChartDrawingError, ChartDrawingService
from src.apps.api.app import ApiError, chart_drawings
from src.apps.api.read_model import ReadOnlyLegacyRepository
from starlette.requests import Request


DRAWING_ID = "123e4567-e89b-42d3-a456-426614174000"


@pytest.fixture
def drawings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-secret-that-is-longer-than-32-characters")
    database = DatabaseManager(str(tmp_path / "drawings.db"))
    auth = AuthService(database)
    first = auth.register("one@example.com", "StrongPass123", "One", True)
    second = auth.register("two@example.com", "StrongPass123", "Two", True)
    expiry = (datetime.now(UTC) + timedelta(days=30)).isoformat()
    database.execute("UPDATE users SET plan_type='标准版',subscription_expire=?", (expiry,))
    repository = ReadOnlyLegacyRepository(tmp_path / "drawings.db")
    one = repository.authenticate(auth.login("one@example.com", "StrongPass123", "127.0.0.1", "pytest").access_token)
    two = repository.authenticate(auth.login("two@example.com", "StrongPass123", "127.0.0.1", "pytest").access_token)
    assert first and second
    return database, ChartDrawingService(database), one, two


def payload(*, cross=False, timeframe="日线", operations):
    return {
        "market": "US", "symbol": "AAPL",
        "operations": [{"origin_timeframe": timeframe, "cross_timeframe": cross, **operation} for operation in operations],
    }


def drawing(drawing_id=DRAWING_ID, price=100):
    return {"id": drawing_id, "tool": "segment", "points": [{"time": "2026-08-01", "price": price}, {"time": "2026-08-02", "price": price + 1}]}


def test_migration_and_user_isolation(drawings):
    database, service, one, two = drawings
    assert (Path(__file__).resolve().parents[4] / "migrations" / "0012_user_chart_drawings.sql").is_file()
    assert database.fetch_one("SELECT name FROM sqlite_master WHERE name='user_chart_drawings'")
    service.batch(one, payload(operations=[{"op": "upsert", "revision": None, "drawing": drawing()}]))
    assert service.list(two, market="US", symbol="AAPL", timeframe="日线", cross_timeframe=False)["items"] == []


def test_chart_drawing_handler_requires_bearer_token():
    request = Request({
        "type": "http", "method": "GET", "path": "/api/rewrite/v1/chart-drawings",
        "query_string": b"market=US&symbol=AAPL&timeframe=%E6%97%A5%E7%BA%BF&cross_timeframe=false",
        "headers": [], "app": object(), "client": ("127.0.0.1", 1),
    })
    with pytest.raises(ApiError, match="Bearer") as exc:
        asyncio.run(chart_drawings(request))
    assert exc.value.status == 401


def test_validation_rejects_spoofing_unknown_fields_and_bad_values(drawings):
    _, service, one, _ = drawings
    with pytest.raises(ChartDrawingError):
        service.batch(one, {**payload(operations=[]), "user_id": 999})
    with pytest.raises(ChartDrawingError):
        service.batch(one, payload(operations=[{"op": "upsert", "revision": None, "drawing": {**drawing(), "evil": True}}]))
    with pytest.raises(ChartDrawingError):
        service.batch(one, payload(operations=[{"op": "upsert", "revision": None, "drawing": {**drawing(), "points": [{"time": "../bad", "price": float("inf")}, {"time": "2026-08-02", "price": 1}]}}]))
    with pytest.raises(ChartDrawingError):
        service.batch(one, payload(operations=[{"op": "upsert", "revision": None, "drawing": {**drawing(), "points": [{"time": "2026-08-01T12:00:00Z", "price": 1}, {"time": "2026-08-02", "price": 1}]}}]))
    with pytest.raises(ChartDrawingError):
        service.batch(one, payload(operations=[{"op": "upsert", "revision": None, "drawing": {**drawing(), "points": [{"time": 1.5, "price": 1}, {"time": "2026-08-02", "price": 1}]}}]))
    business_day_id = "123e4567-e89b-42d3-a456-426614174008"
    business_day = {**drawing(business_day_id), "points": [{"time": {"year": 2026, "month": 8, "day": 1}, "price": 1}, {"time": "2026-08-02", "price": 1}]}
    assert service.batch(one, payload(operations=[{"op": "upsert", "revision": None, "drawing": business_day}]))["items"][0]["revision"] == 1
    with pytest.raises(ChartDrawingError):
        service.batch(one, payload(operations=[{"op": "upsert", "revision": None, "drawing": drawing(str(index).zfill(8) + "-e89b-42d3-a456-426614174000")} for index in range(101)]))


def test_normal_and_cross_timeframe_queries_are_distinct(drawings):
    _, service, one, _ = drawings
    service.batch(one, payload(operations=[{"op": "upsert", "revision": None, "drawing": drawing()}]))
    cross_id = "123e4567-e89b-42d3-a456-426614174001"
    service.batch(one, payload(cross=True, operations=[{"op": "upsert", "revision": None, "drawing": drawing(cross_id)}]))
    assert len(service.list(one, market="US", symbol="AAPL", timeframe="日线", cross_timeframe=False)["items"]) == 1
    all_items = service.list(one, market="US", symbol="AAPL", timeframe="日线", cross_timeframe=True)["items"]
    assert {(item["id"], item["cross_timeframe"]) for item in all_items} == {(DRAWING_ID, False), (cross_id, True)}


def test_cross_view_mutates_each_record_in_its_own_origin_scope(drawings):
    _, service, one, _ = drawings
    normal_id = DRAWING_ID
    cross_id = "123e4567-e89b-42d3-a456-426614174009"
    service.batch(one, payload(operations=[{"op": "upsert", "revision": None, "drawing": drawing(normal_id)}]))
    service.batch(one, payload(cross=True, timeframe="周线", operations=[{"op": "upsert", "revision": None, "drawing": drawing(cross_id)}]))
    # A cross-timeframe view submits deletes with each drawing's actual scope.
    result = service.batch(one, {
        "market": "US", "symbol": "AAPL", "operations": [
            {"op": "delete", "origin_timeframe": "日线", "cross_timeframe": False, "revision": 1, "drawing_id": normal_id},
            {"op": "delete", "origin_timeframe": "周线", "cross_timeframe": True, "revision": 1, "drawing_id": cross_id},
        ],
    })
    assert all(item["deleted"] for item in result["items"])
    restored = service.batch(one, {
        "market": "US", "symbol": "AAPL", "operations": [
            {"op": "restore", "origin_timeframe": "日线", "cross_timeframe": False, "revision": 2, "drawing_id": normal_id},
            {"op": "restore", "origin_timeframe": "周线", "cross_timeframe": True, "revision": 2, "drawing_id": cross_id},
        ],
    })
    assert {item["revision"] for item in restored["items"]} == {3}


def test_idempotency_stale_revision_soft_delete_restore_and_atomic_batch(drawings):
    _, service, one, _ = drawings
    create = {"op": "upsert", "revision": None, "drawing": drawing()}
    assert service.batch(one, payload(operations=[create]))["items"][0]["revision"] == 1
    assert service.batch(one, payload(operations=[create]))["items"][0]["revision"] == 1
    with pytest.raises(ChartDrawingConflict):
        service.batch(one, payload(operations=[{"op": "upsert", "revision": 9, "drawing": drawing(price=102)}]))
    deleted = service.batch(one, payload(operations=[{"op": "delete", "revision": 1, "drawing_id": DRAWING_ID}]))
    assert deleted["items"][0]["deleted"] is True
    # Retrying the same delete accepts only the immediately previous revision of the tombstone.
    assert service.batch(one, payload(operations=[{"op": "delete", "revision": 1, "drawing_id": DRAWING_ID}]))["items"][0]["revision"] == 2
    deleted_view = service.list(one, market="US", symbol="AAPL", timeframe="日线", cross_timeframe=False)
    assert deleted_view["items"] == []
    assert deleted_view["tombstones"] == [{
        "drawing_id": DRAWING_ID,
        "origin_timeframe": "日线",
        "cross_timeframe": False,
        "revision": 2,
    }]
    assert deleted_view["tombstones_truncated"] is False
    assert service.batch(one, payload(operations=[{"op": "restore", "revision": 2, "drawing_id": DRAWING_ID}]))["items"][0]["revision"] == 3
    other_id = "123e4567-e89b-42d3-a456-426614174002"
    with pytest.raises(ChartDrawingConflict):
        service.batch(one, payload(operations=[{"op": "upsert", "revision": None, "drawing": drawing(other_id)}, {"op": "delete", "revision": 99, "drawing_id": DRAWING_ID}]))
    assert service.list(one, market="US", symbol="AAPL", timeframe="日线", cross_timeframe=False)["items"][0]["id"] == DRAWING_ID
    assert service.batch(one, payload(operations=[{"op": "delete", "revision": 3, "drawing_id": DRAWING_ID}]))["items"][0]["revision"] == 4
    assert service.list(one, market="US", symbol="AAPL", timeframe="日线", cross_timeframe=False)["items"] == []


def test_symbol_capacity_matches_cross_view_and_tombstones_are_bounded(drawings):
    _, service, one, _ = drawings
    assert ACTIVE_DRAWING_LIMIT == 200
    assert USER_TOMBSTONE_LIMIT == 2_000
    assert SYMBOL_TOMBSTONE_LIMIT == 500
    values = [drawing(f"00000000-0000-4000-8000-{index:012x}") for index in range(ACTIVE_DRAWING_LIMIT)]
    for start in range(0, ACTIVE_DRAWING_LIMIT, 100):
        service.batch(one, payload(cross=True, operations=[{"op": "upsert", "revision": None, "drawing": item} for item in values[start:start + 100]]))
    aggregate = service.list(one, market="US", symbol="AAPL", timeframe="日线", cross_timeframe=True)
    assert len(aggregate["items"]) == ACTIVE_DRAWING_LIMIT
    assert aggregate["truncated"] is False
    with pytest.raises(ChartDrawingError, match="最多保存"):
        service.batch(one, payload(cross=True, operations=[{"op": "upsert", "revision": None, "drawing": drawing("ffffffff-ffff-4fff-8fff-ffffffffffff")}]))


def test_batch_prunes_old_symbol_tombstones_without_background_job(drawings):
    database, service, one, _ = drawings
    now = datetime.now(UTC).isoformat(timespec="seconds")
    rows = []
    for index in range(SYMBOL_TOMBSTONE_LIMIT + 1):
        drawing_id = f"10000000-0000-4000-8000-{index:012x}"
        rows.append((one.id, "US", "AAPL", "日线", 0, drawing_id, json.dumps(drawing(drawing_id)), 2, now, now, now))
    with database.transaction() as connection:
        connection.executemany(
            """INSERT INTO user_chart_drawings
               (user_id,market,symbol,origin_timeframe,cross_timeframe,drawing_id,drawing_json,revision,deleted_at,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
    active_id = "20000000-0000-4000-8000-000000000000"
    service.batch(one, payload(operations=[{"op": "upsert", "revision": None, "drawing": drawing(active_id)}]))
    remaining = database.fetch_one(
        "SELECT COUNT(*) AS count FROM user_chart_drawings WHERE user_id=? AND market='US' AND symbol='AAPL' AND deleted_at IS NOT NULL",
        (one.id,),
    )
    assert remaining["count"] == SYMBOL_TOMBSTONE_LIMIT
    active = database.fetch_one(
        "SELECT deleted_at FROM user_chart_drawings WHERE user_id=? AND market='US' AND symbol='AAPL' AND drawing_id=?",
        (one.id, active_id),
    )
    assert active == {"deleted_at": None}
