"""Capability-gated HTTP surface for the official real-quote option simulation."""

from __future__ import annotations

import inspect
from typing import Any, Callable

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.apps.api.official_option_sim_read_model import (
    OfficialOptionSimulationNotFound,
    OfficialOptionSimulationReadModel,
)


_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "Vary": "Cookie, Authorization",
    "X-Content-Type-Options": "nosniff",
}


class OfficialOptionSimulationUnavailable(RuntimeError):
    """The official simulation surface is intentionally not configured."""


async def _resolved(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class OfficialOptionSimulationApi:
    def __init__(
        self,
        read_model: OfficialOptionSimulationReadModel,
        *,
        authenticate: Callable[[Request], Any],
        has_capability: Callable[[Any, str], bool],
    ):
        self.read_model = read_model
        self.authenticate = authenticate
        self.has_capability = has_capability

    @staticmethod
    def _response(payload: dict[str, Any], status_code: int = 200) -> JSONResponse:
        return JSONResponse(payload, status_code=status_code, headers=_HEADERS)

    async def _allowed(self, request: Request) -> bool:
        identity = await _resolved(self.authenticate(request))
        return bool(await _resolved(
            self.has_capability(identity, "option_auto_paper_official")
        ))

    async def overview(self, request: Request) -> Response:
        allowed = await self._allowed(request)
        raw_limit = request.query_params.get("limit", "50")
        if not raw_limit.isdigit() or not 1 <= int(raw_limit) <= 200:
            return self._response({"error": "limit 必须介于 1 与 200。"}, 400)
        try:
            payload = await run_in_threadpool(
                self.read_model.overview,
                has_capability=allowed,
                limit=int(raw_limit),
            )
            return self._response(payload)
        except OfficialOptionSimulationNotFound:
            return self._response({"error": "官方模拟期权记录不存在。"}, 404)
        except Exception:
            return self._response({"error": "官方模拟期权暂时不可用。"}, 503)

    async def detail(self, request: Request) -> Response:
        allowed = await self._allowed(request)
        try:
            payload = await run_in_threadpool(
                self.read_model.detail,
                has_capability=allowed,
                opaque_id=str(request.path_params["position_id"]),
            )
            return self._response(payload)
        except OfficialOptionSimulationNotFound:
            return self._response({"error": "官方模拟期权记录不存在。"}, 404)
        except Exception:
            return self._response({"error": "官方模拟期权暂时不可用。"}, 503)


def _api(request: Request) -> OfficialOptionSimulationApi:
    value = getattr(request.app.state, "official_option_sim_api", None)
    if not isinstance(value, OfficialOptionSimulationApi):
        raise OfficialOptionSimulationUnavailable("官方模拟期权暂时不可用。")
    return value


async def official_option_sim_overview(request: Request) -> Response:
    return await _api(request).overview(request)


async def official_option_sim_detail(request: Request) -> Response:
    return await _api(request).detail(request)


async def official_option_sim_unavailable_handler(
    _: Request,
    exc: OfficialOptionSimulationUnavailable,
) -> Response:
    return JSONResponse(
        {"error": str(exc)},
        status_code=503,
        headers=_HEADERS,
    )


__all__ = [
    "OfficialOptionSimulationApi",
    "OfficialOptionSimulationUnavailable",
    "official_option_sim_detail",
    "official_option_sim_overview",
    "official_option_sim_unavailable_handler",
]
