from __future__ import annotations

import asyncio

from core.signal_import_portal import SignalImportPortalService
from src.apps.api.signal_imports import SIGNAL_IMPORT_ROUTES, SignalImportsApiError, signal_import_error_handler


def test_signal_import_routes_are_owner_scoped_and_expose_safe_surface():
    paths = {route.path for route in SIGNAL_IMPORT_ROUTES}
    assert "/api/rewrite/v1/signal-imports/readiness" in paths
    assert "/api/rewrite/v1/signal-imports" in paths
    assert "/api/rewrite/v1/signal-imports/{job_public_id:str}" in paths
    assert all("/export" in path or "signal-imports" in path for path in paths)
    assert SignalImportPortalService


def test_api_error_handler_is_private_no_store():
    class Request:
        pass

    response = asyncio.run(signal_import_error_handler(Request(), SignalImportsApiError("bad")))
    assert response.status_code == 400
    assert dict(response.headers)["cache-control"] == "private, no-store"
