from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, Callable

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from core.personal_paper import (
    PersonalPaperConflict,
    PersonalPaperRiskRejected,
    PersonalPaperService,
    PersonalPaperValidationError,
)
from core.personal_paper.contracts import SYMBOL
from core.personal_paper.quote_proof import (
    ActionableStockQuote,
    QuoteProofError,
    QuoteProofSignerVerifier,
)


HEADERS = {
    "Cache-Control": "private, no-store", "Pragma": "no-cache",
    "Vary": "Cookie, Authorization", "X-Content-Type-Options": "nosniff",
}


async def _resolved(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


def _user_id(identity: Any) -> int:
    value = identity.get("id", identity.get("user_id")) if isinstance(identity, dict) else getattr(identity, "id", None)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise PersonalPaperValidationError("登录身份无效。")
    return value


class PersonalPaperApi:
    def __init__(
        self,
        service: PersonalPaperService,
        *,
        authenticate: Callable[[Request], Any],
        quote_proofs: QuoteProofSignerVerifier,
        actionable_quote: Callable[..., ActionableStockQuote],
        clock: Callable[[], datetime] | None = None,
    ):
        self.service = service
        self.authenticate = authenticate
        self.quote_proofs = quote_proofs
        self.actionable_quote = actionable_quote
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    @staticmethod
    def _response(payload: dict[str, Any], status: int = 200) -> JSONResponse:
        return JSONResponse(payload, status_code=status, headers=HEADERS)

    async def _identity(self, request: Request) -> int:
        return _user_id(await _resolved(self.authenticate(request)))

    async def issue_quote(self, request: Request) -> Response:
        try:
            user_id = await self._identity(request)
            payload = await request.json()
            if not isinstance(payload, dict) or set(payload) != {"market", "symbol"}:
                raise PersonalPaperValidationError("报价请求字段无效。")
            if (
                payload["market"] != "US"
                or not isinstance(payload["symbol"], str)
                or not SYMBOL.fullmatch(payload["symbol"])
            ):
                raise PersonalPaperValidationError("报价请求只允许有效的美股代码。")
            now = self.clock()
            quote = await run_in_threadpool(
                self.actionable_quote,
                user_id=user_id,
                market=payload["market"],
                symbol=payload["symbol"],
                now=now,
            )
            if (
                not isinstance(quote, ActionableStockQuote)
                or quote.is_realtime is not True
                or quote.actionable is not True
                or quote.market != payload["market"]
                or quote.symbol != payload["symbol"]
            ):
                raise PersonalPaperRiskRejected("当前没有可执行的实时美股报价。")
            proof_id = await run_in_threadpool(
                self.quote_proofs.issue,
                user_id=user_id,
                market=quote.market,
                symbol=quote.symbol,
                bid_minor=quote.bid_minor,
                ask_minor=quote.ask_minor,
                last_minor=quote.last_minor,
                as_of=quote.as_of,
                now=now,
            )
            return self._response(
                {"quote_id": proof_id, "market": quote.market, "symbol": quote.symbol}, 201
            )
        except (QuoteProofError, PersonalPaperRiskRejected) as exc:
            return self._response({"error": str(exc)}, 422)
        except (ValueError, PersonalPaperValidationError) as exc:
            return self._response({"error": str(exc)}, 400)
        except Exception:
            return self._response({"error": "实时报价暂时不可用。"}, 503)

    async def create_season(self, request: Request) -> Response:
        try:
            season = await run_in_threadpool(self.service.create_first_season, await self._identity(request))
            return self._response({"season": season}, 201)
        except PersonalPaperValidationError as exc:
            return self._response({"error": str(exc)}, 400)
        except PersonalPaperConflict as exc:
            return self._response({"error": str(exc)}, 409)

    async def account(self, request: Request) -> Response:
        try:
            season_id = str(request.path_params["season_id"])
            payload = await run_in_threadpool(
                self.service.account_snapshot, await self._identity(request), season_id
            )
            return self._response({"account": payload})
        except PersonalPaperValidationError as exc:
            return self._response({"error": str(exc)}, 400)
        except PersonalPaperConflict as exc:
            return self._response({"error": str(exc)}, 404)

    async def submit_stock_order(self, request: Request) -> Response:
        try:
            payload = await request.json()
            result = await run_in_threadpool(
                self.service.submit_stock_order, await self._identity(request), payload
            )
            return self._response(result, 200 if result["replayed"] else 201)
        except (ValueError, PersonalPaperValidationError) as exc:
            return self._response({"error": str(exc)}, 400)
        except PersonalPaperConflict as exc:
            return self._response({"error": str(exc)}, 409)
        except PersonalPaperRiskRejected as exc:
            return self._response({"error": str(exc)}, 422)

    async def cancel_stock_order(self, request: Request) -> Response:
        try:
            result = await run_in_threadpool(
                self.service.cancel_stock_order, await self._identity(request), await request.json()
            )
            return self._response(result, 200)
        except (ValueError, PersonalPaperValidationError) as exc:
            return self._response({"error": str(exc)}, 400)
        except PersonalPaperConflict as exc:
            return self._response({"error": str(exc)}, 409)


def build_personal_paper_api(
    database: Any,
    *,
    quote_proof_secret: bytes,
    authenticate: Callable[[Request], Any],
    actionable_quote: Callable[..., ActionableStockQuote],
    clock: Callable[[], datetime] | None = None,
) -> PersonalPaperApi:
    resolved_clock = clock or (lambda: datetime.now(timezone.utc))
    proofs = QuoteProofSignerVerifier(database, quote_proof_secret)
    service = PersonalPaperService(database, proofs, clock=resolved_clock)
    return PersonalPaperApi(
        service,
        authenticate=authenticate,
        quote_proofs=proofs,
        actionable_quote=actionable_quote,
        clock=resolved_clock,
    )


__all__ = ["PersonalPaperApi", "build_personal_paper_api"]
