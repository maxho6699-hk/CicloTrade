from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from core.auth import AuthService
from core.database import DatabaseManager
from src.apps.api.app import app
from src.apps.api.read_model import ReadOnlyLegacyRepository
from src.apps.api.write_service import BrowserWriteService


@pytest.fixture
def browser_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Isolate the shared ASGI app state for browser-facing API tests."""
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
