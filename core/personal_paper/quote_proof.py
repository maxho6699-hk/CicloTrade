from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from core.personal_paper.contracts import SYMBOL, canonical_json
from core.personal_paper.service import VerifiedQuote


SCHEMA = "q1"
MAX_QUOTE_AGE = timedelta(seconds=30)
DEFAULT_TTL_SECONDS = 15
MAX_TTL_SECONDS = 30
MAX_PRICE_MINOR = 10_000_000_000_000
CLAIM_KEYS = {
    "schema", "user_id", "season_id", "market", "symbol", "bid_minor", "ask_minor",
    "last_minor", "as_of", "issued_at", "exp", "nonce",
}
TOKEN = re.compile(r"^q1\.([A-Za-z0-9_-]{16,60})\.([0-9a-f]{64})$")


class QuoteProofError(ValueError):
    """A quote proof cannot be issued or trusted."""


@dataclass(frozen=True)
class ActionableStockQuote:
    market: str
    symbol: str
    bid_minor: int
    ask_minor: int
    last_minor: int
    as_of: datetime
    is_realtime: bool
    actionable: bool


def _utc(value: datetime, label: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise QuoteProofError(f"{label} 必须是 UTC 时区时间。")
    return value.astimezone(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_stamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise QuoteProofError("报价证明无效。")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise QuoteProofError("报价证明无效。") from exc
    parsed = _utc(parsed, "报价时间")
    if _stamp(parsed) != value:
        raise QuoteProofError("报价证明无效。")
    return parsed


def _user_id(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise QuoteProofError("报价证明用户无效。")
    return value


def _strict_positive_minor(value: object) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or value <= 0
        or value > MAX_PRICE_MINOR
    ):
        raise QuoteProofError("报价证明无效。")
    return value


class QuoteProofSignerVerifier:
    """Persist and atomically consume short-lived US-stock quote proofs."""

    def __init__(
        self,
        database: Any,
        secret: bytes,
        *,
        nonce_factory: Callable[[], str] | None = None,
    ) -> None:
        if database is None or not hasattr(database, "_get_connection"):
            raise QuoteProofError("报价证明数据库无效。")
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise QuoteProofError("报价证明密钥必须是至少 32 字节的独立密钥。")
        self.database = database
        self._secret = secret
        self._nonce_factory = nonce_factory or (lambda: secrets.token_urlsafe(18))

    def issue(
        self,
        *,
        user_id: int,
        market: str,
        symbol: str,
        bid_minor: int,
        ask_minor: int,
        last_minor: int,
        as_of: datetime,
        now: datetime,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> str:
        owner = _user_id(user_id)
        now = _utc(now, "now")
        as_of = _utc(as_of, "as_of")
        if market != "US" or not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
            raise QuoteProofError("报价证明只允许有效的美股代码。")
        bid = _strict_positive_minor(bid_minor)
        ask = _strict_positive_minor(ask_minor)
        last = _strict_positive_minor(last_minor)
        if bid > ask:
            raise QuoteProofError("报价证明无效。")
        if as_of > now or now - as_of > MAX_QUOTE_AGE:
            raise QuoteProofError("报价时间无效或已过期。")
        if (
            isinstance(ttl_seconds, bool)
            or not isinstance(ttl_seconds, int)
            or not 1 <= ttl_seconds <= MAX_TTL_SECONDS
        ):
            raise QuoteProofError("报价证明有效期无效。")
        nonce = self._nonce_factory()
        if not isinstance(nonce, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,60}", nonce):
            raise QuoteProofError("报价证明随机标识无效。")
        expires_at = now + timedelta(seconds=ttl_seconds)
        try:
            with self.database._get_connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                season = connection.execute(
                    """SELECT id FROM personal_paper_seasons
                       WHERE user_id=? AND state='active' ORDER BY season_number DESC LIMIT 1""",
                    (owner,),
                ).fetchone()
                if season is None:
                    raise QuoteProofError("报价证明需要有效的个人模拟账户。")
                season_id = str(season["id"])
                claims = {
                    "schema": SCHEMA,
                    "user_id": owner,
                    "season_id": season_id,
                    "market": market,
                    "symbol": symbol,
                    "bid_minor": bid,
                    "ask_minor": ask,
                    "last_minor": last,
                    "as_of": _stamp(as_of),
                    "issued_at": _stamp(now),
                    "exp": _stamp(expires_at),
                    "nonce": nonce,
                }
                canonical = canonical_json(claims)
                signature = self._signature(canonical)
                proof_id = f"{SCHEMA}.{nonce}.{signature}"
                connection.execute(
                    """INSERT INTO personal_paper_quote_proofs
                       (public_id,user_id,season_id,schema_version,nonce,claims_json,signature_sha256,
                        issued_at,expires_at,created_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?)""",
                    (
                        proof_id, owner, season_id, SCHEMA, nonce, canonical, signature,
                        _stamp(now), _stamp(expires_at), _stamp(now),
                    ),
                )
                connection.commit()
        except QuoteProofError:
            raise
        except sqlite3.IntegrityError as exc:
            raise QuoteProofError("报价证明随机标识重复。") from exc
        except sqlite3.DatabaseError as exc:
            raise QuoteProofError("报价证明账户无法验证。") from exc
        return proof_id

    def verify_and_consume(
        self,
        quote_id: str,
        *,
        user_id: int,
        season_id: str,
        market: str,
        symbol: str,
        now: datetime,
        connection: Any,
        request_sha256: str,
        consume: bool = True,
    ) -> VerifiedQuote:
        try:
            owner = _user_id(user_id)
            now = _utc(now, "now")
            if connection is None or not hasattr(connection, "execute"):
                raise QuoteProofError("报价证明事务无效。")
            if not isinstance(quote_id, str) or not isinstance(season_id, str):
                raise QuoteProofError("报价证明无效。")
            if not isinstance(request_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", request_sha256):
                raise QuoteProofError("报价证明无效。")
            matched = TOKEN.fullmatch(quote_id)
            if matched is None:
                raise QuoteProofError("报价证明无效。")
            nonce, supplied_signature = matched.groups()
            row = connection.execute(
                """SELECT * FROM personal_paper_quote_proofs
                   WHERE public_id=? AND user_id=?""",
                (quote_id, owner),
            ).fetchone()
            if row is None:
                raise QuoteProofError("报价证明无效。")
            canonical = str(row["claims_json"])
            expected_signature = self._signature(canonical)
            if not (
                hmac.compare_digest(supplied_signature, str(row["signature_sha256"]))
                and hmac.compare_digest(supplied_signature, expected_signature)
            ):
                raise QuoteProofError("报价证明无效。")
            claims = json.loads(canonical)
            if (
                not isinstance(claims, dict)
                or set(claims) != CLAIM_KEYS
                or canonical_json(claims) != canonical
                or claims.get("schema") != SCHEMA
                or row["schema_version"] != SCHEMA
                or claims.get("nonce") != nonce
                or row["nonce"] != nonce
                or claims.get("user_id") != owner
                or claims.get("season_id") != season_id
                or row["season_id"] != season_id
            ):
                raise QuoteProofError("报价证明无效。")
            account = connection.execute(
                """SELECT 1 FROM personal_paper_seasons
                   WHERE id=? AND user_id=? AND state='active'""",
                (season_id, owner),
            ).fetchone()
            if account is None:
                raise QuoteProofError("报价证明账户无效。")
            if claims.get("market") != "US" or claims.get("market") != market:
                raise QuoteProofError("报价证明无效。")
            if (
                not isinstance(symbol, str)
                or not SYMBOL.fullmatch(symbol)
                or claims.get("symbol") != symbol
            ):
                raise QuoteProofError("报价证明无效。")
            bid = _strict_positive_minor(claims.get("bid_minor"))
            ask = _strict_positive_minor(claims.get("ask_minor"))
            last = _strict_positive_minor(claims.get("last_minor"))
            if bid > ask:
                raise QuoteProofError("报价证明无效。")
            as_of = _parse_stamp(claims.get("as_of"))
            issued_at = _parse_stamp(claims.get("issued_at"))
            expires_at = _parse_stamp(claims.get("exp"))
            if row["issued_at"] != _stamp(issued_at) or row["expires_at"] != _stamp(expires_at):
                raise QuoteProofError("报价证明无效。")
            ttl = expires_at - issued_at
            if (
                not timedelta(0) < ttl <= timedelta(seconds=MAX_TTL_SECONDS)
                or now < issued_at
                or now >= expires_at
                or as_of > issued_at
                or as_of > now
                or now - as_of > MAX_QUOTE_AGE
            ):
                raise QuoteProofError("报价证明无效。")
            if consume:
                connection.execute(
                    """INSERT INTO personal_paper_quote_consumptions
                       (proof_id,user_id,season_id,request_sha256,consumed_at)
                       VALUES(?,?,?,?,?)""",
                    (quote_id, owner, season_id, request_sha256, _stamp(now)),
                )
            return VerifiedQuote(
                proof_id=quote_id,
                market="US",
                symbol=symbol,
                bid_minor=bid,
                ask_minor=ask,
                last_minor=last,
                as_of=as_of,
                state="fresh",
                commission_minor=0,
            )
        except QuoteProofError:
            raise
        except (json.JSONDecodeError, sqlite3.DatabaseError, TypeError, ValueError) as exc:
            raise QuoteProofError("报价证明无效。") from exc

    def verify(
        self, quote_id: str, *, user_id: int, season_id: str, market: str, symbol: str,
        now: datetime, connection: Any, request_sha256: str,
    ) -> VerifiedQuote:
        return self.verify_and_consume(
            quote_id, user_id=user_id, season_id=season_id, market=market, symbol=symbol,
            now=now, connection=connection, request_sha256=request_sha256, consume=False,
        )

    def _signature(self, canonical_claims: str) -> str:
        message = f"{SCHEMA}\0{canonical_claims}".encode("utf-8")
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()


__all__ = ["ActionableStockQuote", "QuoteProofError", "QuoteProofSignerVerifier"]
