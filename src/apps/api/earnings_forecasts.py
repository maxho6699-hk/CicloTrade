"""HTTP handlers for private, capability-gated earnings research."""

from __future__ import annotations

from datetime import datetime
import inspect
from typing import Any, Callable

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from src.apps.api.earnings_read_model import (
    EarningsForecastReadModel,
    EarningsResearchNotFound,
)


_HEADERS = {
    "Cache-Control": "private, no-store",
    "Pragma": "no-cache",
    "Vary": "Cookie, Authorization",
    "X-Content-Type-Options": "nosniff",
}


class EarningsForecastRequestError(ValueError):
    """Public request validation failure; safe to return as HTTP 400."""


class EarningsForecastUnavailable(RuntimeError):
    """The private earnings read model is not configured for this process."""


async def _resolved(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


class EarningsForecastApi:
    def __init__(
        self,
        read_model: EarningsForecastReadModel,
        *,
        authenticate: Callable[[Request], Any],
        has_capability: Callable[[Any, str], bool],
        clock: Callable[[], datetime],
    ):
        self.read_model = read_model
        self.authenticate = authenticate
        self.has_capability = has_capability
        self.clock = clock

    @staticmethod
    def _response(payload: dict[str, Any], status_code: int = 200) -> JSONResponse:
        return JSONResponse(payload, status_code=status_code, headers=_HEADERS)

    @staticmethod
    def _integer(request: Request, name: str, default: int, maximum: int) -> int:
        raw = request.query_params.get(name)
        if raw is None:
            return default
        if not raw.isdigit() or not 1 <= int(raw) <= maximum:
            raise EarningsForecastRequestError(f"{name} 必须介于 1 与 {maximum}。")
        return int(raw)

    async def _identity_and_capability(
        self, request: Request, capability: str
    ) -> tuple[Any, bool]:
        identity = await _resolved(self.authenticate(request))
        allowed = await _resolved(self.has_capability(identity, capability))
        return identity, bool(allowed)

    async def _safe(self, operation: Callable[[], Any]) -> Response:
        try:
            payload = await run_in_threadpool(operation)
            return self._response(payload)
        except EarningsResearchNotFound:
            return self._response({"error": "业绩预测研究不存在。"}, 404)
        except Exception:
            return self._response({"error": "业绩预测研究暂时不可用。"}, 503)

    async def overview(self, request: Request) -> Response:
        identity, allowed = await self._identity_and_capability(
            request, "earnings_forecast"
        )
        option_allowed = bool(await _resolved(
            self.has_capability(identity, "earnings_option_defined_risk")
        ))
        try:
            window = self._integer(request, "window_days", 7, 30)
            limit = self._integer(request, "limit", 100, 200)
            as_of = self.clock()
        except EarningsForecastRequestError as exc:
            return self._response({"error": str(exc)}, 400)
        except Exception:
            return self._response({"error": "业绩预测研究暂时不可用。"}, 503)
        return await self._safe(lambda: self.read_model.overview(
            has_capability=allowed, as_of=as_of, window_days=window, limit=limit,
            has_option_capability=option_allowed,
        ))

    async def history(self, request: Request) -> Response:
        identity, allowed = await self._identity_and_capability(
            request, "earnings_forecast"
        )
        option_allowed = bool(await _resolved(
            self.has_capability(identity, "earnings_option_defined_risk")
        ))
        try:
            limit = self._integer(request, "limit", 50, 200)
            cursor = request.query_params.get("cursor")
            if cursor:
                try:
                    self.read_model.codec.decode("history", cursor)
                except EarningsResearchNotFound as exc:
                    raise EarningsForecastRequestError("cursor 无效。") from exc
            as_of = self.clock()
        except EarningsForecastRequestError as exc:
            return self._response({"error": str(exc)}, 400)
        except Exception:
            return self._response({"error": "业绩预测研究暂时不可用。"}, 503)
        return await self._safe(lambda: self.read_model.history(
            has_capability=allowed, as_of=as_of, limit=limit, cursor=cursor,
            has_option_capability=option_allowed,
        ))

    async def statistics(self, request: Request) -> Response:
        _, allowed = await self._identity_and_capability(
            request, "earnings_forecast"
        )
        try:
            as_of = self.clock()
        except Exception:
            return self._response({"error": "业绩预测研究暂时不可用。"}, 503)
        return await self._safe(lambda: self.read_model.statistics(
            has_capability=allowed, as_of=as_of
        ))

    async def detail(self, request: Request) -> Response:
        identity, allowed = await self._identity_and_capability(
            request, "earnings_forecast"
        )
        option_allowed = bool(await _resolved(
            self.has_capability(identity, "earnings_option_defined_risk")
        ))
        try:
            event_id = str(request.path_params["event_id"])
            as_of = self.clock()
        except Exception:
            return self._response({"error": "业绩预测研究暂时不可用。"}, 503)
        return await self._safe(lambda: self.read_model.detail(
            has_capability=allowed, opaque_event_id=event_id, as_of=as_of,
            has_option_capability=option_allowed,
        ))

    async def option_detail(self, request: Request) -> Response:
        identity, forecast_allowed = await self._identity_and_capability(
            request, "earnings_forecast"
        )
        option_allowed = bool(await _resolved(
            self.has_capability(identity, "earnings_option_defined_risk")
        ))
        try:
            event_id = str(request.path_params["event_id"])
            option_id = str(request.path_params["option_id"])
            as_of = self.clock()
        except Exception:
            return self._response({"error": "业绩预测研究暂时不可用。"}, 503)
        return await self._safe(lambda: self.read_model.option_detail(
            has_forecast_capability=forecast_allowed,
            has_option_capability=option_allowed,
            opaque_event_id=event_id, opaque_option_id=option_id, as_of=as_of,
        ))


def _api(request: Request) -> EarningsForecastApi:
    value = getattr(request.app.state, "earnings_forecast_api", None)
    if not isinstance(value, EarningsForecastApi):
        raise EarningsForecastUnavailable("业绩预测研究暂时不可用。")
    return value


async def earnings_forecast_overview(request: Request) -> Response:
    return await _api(request).overview(request)


async def earnings_forecast_history(request: Request) -> Response:
    return await _api(request).history(request)


async def earnings_forecast_statistics(request: Request) -> Response:
    return await _api(request).statistics(request)


async def earnings_forecast_detail(request: Request) -> Response:
    return await _api(request).detail(request)


async def earnings_option_detail(request: Request) -> Response:
    return await _api(request).option_detail(request)
