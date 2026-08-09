import asyncio
import importlib
import json
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlencode

import pandas as pd
import pytest
from starlette.requests import Request

from core.auth import AuthService
from core.database import DatabaseManager
from src.apps.api.app import (
    ApiError,
    REFRESH_COOKIE,
    app,
    bootstrap,
    locale_preference,
    market_candles,
    market_search,
    session_login,
    session_logout,
    session_refresh,
    watchlist,
)
from src.apps.api.read_model import ReadOnlyLegacyRepository
from src.apps.api.write_service import BrowserWriteService


def _request(
    path: str,
    *,
    method: str = "GET",
    payload: dict | None = None,
    authorization: str | None = None,
    cookie: str | None = None,
    query: dict[str, str] | None = None,
) -> Request:
    body = json.dumps(payload).encode() if payload is not None else b""
    headers = [(b"content-length", str(len(body)).encode()), (b"user-agent", b"pytest")]
    if authorization:
        headers.append((b"authorization", authorization.encode()))
    if cookie:
        headers.append((b"cookie", cookie.encode()))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": urlencode(query or {}).encode(),
        "headers": headers,
        "client": ("127.0.0.1", 50000),
        "server": ("testserver", 80),
        "app": app,
    }
    delivered = False

    async def receive():
        nonlocal delivered
        if delivered:
            return {"type": "http.disconnect"}
        delivered = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _payload(response) -> dict:
    return json.loads(response.body.decode())


def _refresh_cookie(response) -> str:
    parsed = SimpleCookie()
    parsed.load(response.headers["set-cookie"])
    return f"{REFRESH_COOKIE}={parsed[REFRESH_COOKIE].value}"


def _login_token() -> str:
    response = asyncio.run(session_login(_request(
        "/api/rewrite/v1/session", method="POST",
        payload={"email": "browser@example.com", "password": "StrongPass123"},
    )))
    return _payload(response)["access_token"]


@pytest.fixture
def browser_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("JWT_SECRET_KEY", "test-only-secret-that-is-longer-than-32-characters")
    database = DatabaseManager(str(tmp_path / "browser-session.db"))
    auth = AuthService(database)
    auth.register("browser@example.com", "StrongPass123", "Browser Reader", True)
    previous_repository = app.state.repository
    previous_auth = getattr(app.state, "auth_service", None)
    previous_write_service = getattr(app.state, "write_service", None)
    app.state.repository = ReadOnlyLegacyRepository(tmp_path / "browser-session.db")
    app.state.auth_service = auth
    app.state.write_service = BrowserWriteService(database)
    api_module = importlib.import_module("src.apps.api.app")
    with api_module._MARKET_SEARCH_LOCK:
        api_module._MARKET_SEARCH_CACHE.clear()
        api_module._MARKET_SEARCH_RATE.clear()
    try:
        yield {"database": database, "auth": auth}
    finally:
        with api_module._MARKET_SEARCH_LOCK:
            api_module._MARKET_SEARCH_CACHE.clear()
            api_module._MARKET_SEARCH_RATE.clear()
        app.state.repository = previous_repository
        if previous_auth is None:
            del app.state.auth_service
        else:
            app.state.auth_service = previous_auth
        if previous_write_service is None:
            del app.state.write_service
        else:
            app.state.write_service = previous_write_service


def test_login_uses_http_only_refresh_cookie_and_returns_no_refresh_token(browser_api):
    response = asyncio.run(session_login(_request(
        "/api/rewrite/v1/session",
        method="POST",
        payload={"email": "browser@example.com", "password": "StrongPass123"},
    )))
    payload = _payload(response)

    assert "access_token" in payload
    assert "refresh_token" not in payload
    assert "HttpOnly" in response.headers["set-cookie"]
    assert _refresh_cookie(response).startswith(f"{REFRESH_COOKIE}=")


def test_login_refresh_bootstrap_and_logout_flow(browser_api):
    login = asyncio.run(session_login(_request(
        "/api/rewrite/v1/session", method="POST",
        payload={"email": "browser@example.com", "password": "StrongPass123"},
    )))
    login_payload = _payload(login)
    cookie = _refresh_cookie(login)
    read_response = asyncio.run(bootstrap(_request(
        "/api/rewrite/v1/bootstrap",
        authorization=f"Bearer {login_payload['access_token']}",
    )))
    refresh = asyncio.run(session_refresh(_request(
        "/api/rewrite/v1/session/refresh", method="POST", cookie=cookie,
    )))
    refreshed_access = _payload(refresh)["access_token"]
    logout = asyncio.run(session_logout(_request(
        "/api/rewrite/v1/session", method="DELETE",
        authorization=f"Bearer {refreshed_access}", cookie=_refresh_cookie(refresh),
    )))

    assert _payload(read_response)["me"]["display_name"] == "Browser Reader"
    assert _payload(logout)["status"] == "logged_out"
    assert f"{REFRESH_COOKIE}=\"\"" in logout.headers["set-cookie"]


def test_missing_refresh_cookie_is_anonymous_not_an_error(browser_api):
    response = asyncio.run(session_refresh(_request(
        "/api/rewrite/v1/session/refresh", method="POST",
    )))

    assert response.status_code == 200
    assert _payload(response) == {"authenticated": False}


def test_login_rejects_unknown_fields_and_bad_password(browser_api):
    with pytest.raises(ApiError, match="未知字段"):
        asyncio.run(session_login(_request(
            "/api/rewrite/v1/session", method="POST",
            payload={"email": "browser@example.com", "password": "StrongPass123", "admin": True},
        )))
    with pytest.raises(ApiError) as error:
        asyncio.run(session_login(_request(
            "/api/rewrite/v1/session", method="POST",
            payload={"email": "browser@example.com", "password": "incorrect123"},
        )))
    assert error.value.status == 401


def test_authenticated_market_candles_use_bounded_read_adapter(browser_api, monkeypatch):
    login = asyncio.run(session_login(_request(
        "/api/rewrite/v1/session", method="POST",
        payload={"email": "browser@example.com", "password": "StrongPass123"},
    )))
    access_token = _payload(login)["access_token"]

    class StubSource:
        def bars(self, symbol, period, interval):
            assert (symbol, period, interval) == ("AAPL", "6mo", "1d")
            return pd.DataFrame(
                [{"Open": 100, "High": 102, "Low": 99, "Close": 101, "Volume": 1_000}],
                index=pd.to_datetime(["2026-08-08T00:00:00Z"]),
            )

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(api_module, "get_resilient_data_source", lambda: StubSource())
    monkeypatch.setattr(api_module, "public_market_status", lambda **_: {
        "display_source": "测试行情", "is_realtime": False,
        "freshness": "历史行情", "detail": "测试",
    })
    response = asyncio.run(market_candles(_request(
        "/api/rewrite/v1/market/candles",
        authorization=f"Bearer {access_token}",
        query={"symbol": "AAPL", "timeframe": "日线"},
    )))
    payload = _payload(response)

    assert payload["items"][0]["close"] == 101.0
    assert payload["status"]["freshness"] == "历史行情"


def test_authenticated_market_search_finds_arbitrary_symbol(browser_api, monkeypatch):
    access_token = _login_token()

    class StubSource:
        name = "stub"

        def search(self, query, market, max_results):
            assert (query, market, max_results) == ("PLTR", "美股", 8)
            return [{"symbol": "PLTR", "name": "Palantir Technologies", "exchange": "NASDAQ", "type": "股票"}]

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(api_module, "get_resilient_data_source", lambda *_: StubSource())
    response = asyncio.run(market_search(_request(
        "/api/rewrite/v1/market/search",
        authorization=f"Bearer {access_token}",
        query={"q": "PLTR", "market": "美股"},
    )))

    assert _payload(response)["items"] == [{
        "symbol": "PLTR", "name": "Palantir Technologies", "exchange": "NASDAQ",
        "type": "股票", "market": "US",
    }]


def test_watchlist_api_post_delete_head_and_bootstrap_are_consistent(browser_api):
    access_token = _login_token()
    auth_header = f"Bearer {access_token}"

    added_us = asyncio.run(watchlist(_request(
        "/api/rewrite/v1/watchlist", method="POST", authorization=auth_header,
        payload={"market": "US", "symbol": "pltr"},
    )))
    added_cn = asyncio.run(watchlist(_request(
        "/api/rewrite/v1/watchlist", method="POST", authorization=auth_header,
        payload={"market": "CN", "symbol": "600519.SS"},
    )))
    removed = asyncio.run(watchlist(_request(
        "/api/rewrite/v1/watchlist", method="DELETE", authorization=auth_header,
        payload={"market": "US", "symbol": "PLTR"},
    )))
    head = asyncio.run(watchlist(_request(
        "/api/rewrite/v1/watchlist", method="HEAD", authorization=auth_header,
        payload={"market": "US", "symbol": "MSFT"},
    )))
    bootstrapped = asyncio.run(bootstrap(_request(
        "/api/rewrite/v1/bootstrap", authorization=auth_header,
    )))

    assert _payload(added_us)["watchlists"] == {"us": ["PLTR"], "a_share": []}
    assert _payload(added_cn)["watchlists"] == {"us": ["PLTR"], "a_share": ["600519"]}
    assert _payload(removed)["watchlists"] == {"us": [], "a_share": ["600519"]}
    assert _payload(head)["watchlists"] == {"us": [], "a_share": ["600519"]}
    assert _payload(bootstrapped)["settings"]["watchlists"] == _payload(removed)["watchlists"]


def test_watchlist_api_requires_auth_and_invalid_payload_does_not_write(browser_api):
    with pytest.raises(ApiError) as unauthenticated:
        asyncio.run(watchlist(_request(
            "/api/rewrite/v1/watchlist", method="POST",
            payload={"market": "US", "symbol": "PLTR"},
        )))
    assert unauthenticated.value.status == 401

    access_token = _login_token()
    auth_header = f"Bearer {access_token}"
    before = asyncio.run(watchlist(_request(
        "/api/rewrite/v1/watchlist", authorization=auth_header,
    )))
    with pytest.raises(ApiError) as invalid:
        asyncio.run(watchlist(_request(
            "/api/rewrite/v1/watchlist", method="POST", authorization=auth_header,
            payload={"market": "US", "symbol": "PLTR", "unexpected": True},
        )))
    after = asyncio.run(watchlist(_request(
        "/api/rewrite/v1/watchlist", authorization=auth_header,
    )))

    assert invalid.value.status == 400
    assert _payload(after) == _payload(before)


def test_watchlist_api_isolates_identities(browser_api):
    second_user = browser_api["auth"].register(
        "other-browser@example.com", "StrongPass123", "Other", True
    )
    assert second_user is not None
    second_login = browser_api["auth"].login(
        "other-browser@example.com", "StrongPass123", "127.0.0.1", "pytest"
    )
    first_token = _login_token()
    second_token = second_login.access_token

    first = asyncio.run(watchlist(_request(
        "/api/rewrite/v1/watchlist", method="POST", authorization=f"Bearer {first_token}",
        payload={"market": "US", "symbol": "PLTR"},
    )))
    second_before = asyncio.run(watchlist(_request(
        "/api/rewrite/v1/watchlist", authorization=f"Bearer {second_token}",
    )))
    second_delete = asyncio.run(watchlist(_request(
        "/api/rewrite/v1/watchlist", method="DELETE", authorization=f"Bearer {second_token}",
        payload={"market": "US", "symbol": "PLTR"},
    )))

    assert _payload(first)["watchlists"]["us"] == ["PLTR"]
    assert _payload(second_before)["watchlists"] == {"us": [], "a_share": []}
    assert _payload(second_delete)["watchlists"] == {"us": [], "a_share": []}


def test_locale_preference_accepts_supported_values_and_rejects_invalid(browser_api):
    access_token = _login_token()
    auth_header = f"Bearer {access_token}"

    saved = asyncio.run(locale_preference(_request(
        "/api/rewrite/v1/settings/locale", method="PUT", authorization=auth_header,
        payload={"locale": "zh-Hans"},
    )))
    with pytest.raises(ApiError) as invalid:
        asyncio.run(locale_preference(_request(
            "/api/rewrite/v1/settings/locale", method="PUT", authorization=auth_header,
            payload={"locale": "en-US"},
        )))
    bootstrapped = asyncio.run(bootstrap(_request(
        "/api/rewrite/v1/bootstrap", authorization=auth_header,
    )))

    assert _payload(saved) == {"locale": "zh-Hans"}
    assert invalid.value.status == 400
    assert _payload(bootstrapped)["settings"]["ui_locale"] == "zh-Hans"


def test_market_search_a_share_uses_yahoo_and_returns_cn(browser_api, monkeypatch):
    access_token = _login_token()
    calls: list[str | None] = []

    class StubSource:
        name = "Yahoo Finance"

        def search(self, query, market, max_results):
            assert (query, market, max_results) == ("600519", "A股", 8)
            return [{"symbol": "600519.SS", "name": "贵州茅台", "exchange": "上海", "type": "股票"}]

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(
        api_module,
        "get_resilient_data_source",
        lambda name=None: (calls.append(name) or StubSource()),
    )
    response = asyncio.run(market_search(_request(
        "/api/rewrite/v1/market/search", authorization=f"Bearer {access_token}",
        query={"q": "600519", "market": "A股"},
    )))

    assert calls == ["yfinance"]
    assert _payload(response)["items"][0]["market"] == "CN"


@pytest.mark.parametrize("raises", [False, True])
def test_market_search_us_falls_back_to_yahoo_on_empty_or_error(browser_api, monkeypatch, raises):
    access_token = _login_token()
    calls: list[str | None] = []

    class PrimarySource:
        name = "OpenD"

        def search(self, query, market, max_results):
            if raises:
                from data.datasource import DataSourceError
                raise DataSourceError("primary unavailable")
            return []

    class YahooSource:
        name = "Yahoo Finance"

        def search(self, query, market, max_results):
            return [{"symbol": "PLTR", "name": "Palantir", "exchange": "NASDAQ", "type": "股票"}]

    sources = iter([PrimarySource(), YahooSource()])
    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(
        api_module,
        "get_resilient_data_source",
        lambda name=None: (calls.append(name) or next(sources)),
    )
    response = asyncio.run(market_search(_request(
        "/api/rewrite/v1/market/search", authorization=f"Bearer {access_token}",
        query={"q": "PLTR", "market": "美股"},
    )))

    assert calls == [None, "yfinance"]
    assert _payload(response)["items"][0]["symbol"] == "PLTR"


def test_market_search_direct_yahoo_failure_is_not_retried(browser_api, monkeypatch):
    access_token = _login_token()
    calls = 0

    class YahooSource:
        name = "Yahoo Finance"

        def search(self, query, market, max_results):
            nonlocal calls
            calls += 1
            from data.datasource import DataSourceError
            raise DataSourceError("Yahoo unavailable")

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(api_module, "get_resilient_data_source", lambda name=None: YahooSource())
    with pytest.raises(ApiError) as error:
        asyncio.run(market_search(_request(
            "/api/rewrite/v1/market/search", authorization=f"Bearer {access_token}",
            query={"q": "PLTR", "market": "美股"},
        )))

    assert error.value.status == 503
    assert calls == 1


def test_market_search_invalid_query_does_not_call_provider(browser_api, monkeypatch):
    access_token = _login_token()
    calls = 0
    api_module = importlib.import_module("src.apps.api.app")

    def factory(name=None):
        nonlocal calls
        calls += 1
        raise AssertionError("provider should not be called")

    monkeypatch.setattr(api_module, "get_resilient_data_source", factory)
    for query, market in (("", "美股"), ("x" * 41, "美股"), ("PLTR", "HK")):
        with pytest.raises(ApiError) as error:
            asyncio.run(market_search(_request(
                "/api/rewrite/v1/market/search", authorization=f"Bearer {access_token}",
                query={"q": query, "market": market},
            )))
        assert error.value.status == 400
    assert calls == 0


def test_market_search_rate_limit_and_cache(browser_api, monkeypatch):
    access_token = _login_token()
    provider_calls = 0

    class YahooSource:
        name = "Yahoo Finance"

        def search(self, query, market, max_results):
            nonlocal provider_calls
            provider_calls += 1
            return [{"symbol": query, "name": query, "exchange": "NASDAQ", "type": "股票"}]

    api_module = importlib.import_module("src.apps.api.app")
    monkeypatch.setattr(api_module, "get_resilient_data_source", lambda name=None: YahooSource())
    first = asyncio.run(market_search(_request(
        "/api/rewrite/v1/market/search", authorization=f"Bearer {access_token}",
        query={"q": "PLTR", "market": "美股"},
    )))
    cached = asyncio.run(market_search(_request(
        "/api/rewrite/v1/market/search", authorization=f"Bearer {access_token}",
        query={"q": "pltr", "market": "美股"},
    )))
    assert _payload(first)["items"][0]["symbol"] == "PLTR"
    assert _payload(cached)["cached"] is True
    assert provider_calls == 1

    with api_module._MARKET_SEARCH_LOCK:
        api_module._MARKET_SEARCH_CACHE.clear()
        api_module._MARKET_SEARCH_RATE.clear()
    for index in range(30):
        asyncio.run(market_search(_request(
            "/api/rewrite/v1/market/search", authorization=f"Bearer {access_token}",
            query={"q": f"Q{index}", "market": "美股"},
        )))
    with pytest.raises(ApiError) as limited:
        asyncio.run(market_search(_request(
            "/api/rewrite/v1/market/search", authorization=f"Bearer {access_token}",
            query={"q": "Q30", "market": "美股"},
        )))

    assert provider_calls == 31
    assert limited.value.status == 429
