from __future__ import annotations

import inspect
from datetime import datetime, timedelta, timezone
from json import JSONDecodeError
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
    DEFAULT_TTL_SECONDS,
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


def _stamp(value: datetime | None) -> str | None:
    if value is None or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _quote_projection(quote: ActionableStockQuote, now: datetime) -> dict[str, Any]:
    quote_at = quote.quote_at or quote.as_of
    prices_complete = all(
        isinstance(value, int) and not isinstance(value, bool) and value > 0
        for value in (quote.bid_minor, quote.ask_minor, quote.last_minor)
    )
    return {
        "bid_minor": quote.bid_minor, "ask_minor": quote.ask_minor, "last_minor": quote.last_minor,
        "quote_at": _stamp(quote_at), "observed_at": _stamp(quote.observed_at),
        "available_at": _stamp(quote.available_at), "expires_at": _stamp(quote.expires_at),
        "session": quote.session, "freshness": quote.freshness if prices_complete else "missing", "source": quote.source,
        "state": "locked", "as_of": _stamp(now),
    }


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
            projection = _quote_projection(quote, now) if isinstance(quote, ActionableStockQuote) else {
                "bid_minor": None, "ask_minor": None, "last_minor": None, "quote_at": None,
                "observed_at": None, "available_at": None, "expires_at": None, "session": None,
                "freshness": "missing", "source": None, "state": "locked", "as_of": _stamp(now),
            }
            if (
                not isinstance(quote, ActionableStockQuote)
                or quote.is_realtime is not True
                or quote.actionable is not True
                or quote.market != payload["market"]
                or quote.symbol != payload["symbol"]
            ):
                return self._response(
                    {"error": "当前没有可执行的实时美股报价。", "quote": projection}, 422
                )
            quote_at = quote.quote_at or quote.as_of
            if (
                not isinstance(quote_at, datetime) or quote_at.tzinfo is None
                or (quote.expires_at is not None and (
                    quote.expires_at.tzinfo is None or quote.expires_at <= now or quote.expires_at <= quote_at
                ))
                or (quote.freshness is not None and quote.freshness != "fresh")
            ):
                return self._response(
                    {"error": "当前报价已过期或缺少可验证字段。", "quote": projection}, 422
                )
            if any(
                isinstance(value, bool) or not isinstance(value, int) or value <= 0
                for value in (quote.bid_minor, quote.ask_minor, quote.last_minor)
            ) or quote.bid_minor > quote.ask_minor:
                return self._response(
                    {"error": "当前报价缺少可验证的 bid/ask/last，已锁定。", "quote": projection}, 422
                )
            proof_id = await run_in_threadpool(
                self.quote_proofs.issue,
                user_id=user_id,
                market=quote.market,
                symbol=quote.symbol,
                bid_minor=quote.bid_minor,
                ask_minor=quote.ask_minor,
                last_minor=quote.last_minor,
                as_of=quote_at,
                now=now,
                quote_at=quote_at,
                observed_at=quote.observed_at,
                available_at=quote.available_at,
                session=quote.session,
                freshness=quote.freshness or ("fresh" if quote.is_realtime else "missing"),
                source=quote.source,
            )
            return self._response(
                {
                    "quote_id": proof_id, "market": quote.market, "symbol": quote.symbol,
                    "bid_minor": quote.bid_minor, "ask_minor": quote.ask_minor,
                    "last_minor": quote.last_minor, "quote_at": _stamp(quote_at),
                    "observed_at": _stamp(quote.observed_at), "available_at": _stamp(quote.available_at),
                    "expires_at": _stamp(now + timedelta(seconds=DEFAULT_TTL_SECONDS)),
                    "session": quote.session,
                    "freshness": quote.freshness or ("fresh" if quote.is_realtime else "missing"),
                    "source": quote.source,
                }, 201
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

    async def risk_proof(self, request: Request) -> Response:
        try:
            result = await run_in_threadpool(
                self.service.issue_risk_proof, await self._identity(request), await request.json()
            )
            return self._response({"risk_proof": result}, 201)
        except (JSONDecodeError, UnicodeDecodeError, PersonalPaperValidationError) as exc:
            return self._response({"error": str(exc)}, 400)
        except PersonalPaperConflict as exc:
            return self._response({"error": str(exc)}, 409)
        except PersonalPaperRiskRejected as exc:
            return self._response({"error": str(exc)}, 422)
        except Exception:
            return self._response({"error": "风险数据暂时不可用。"}, 503)

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
