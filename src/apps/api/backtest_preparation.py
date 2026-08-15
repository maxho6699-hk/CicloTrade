"""Server-owned preparation for bounded browser research backtests.

The browser selects one allowlisted template and a bounded sample window.  It
never supplies executable code, manifests, input hashes, storage paths, or raw
market data.  This service fetches historical daily bars, freezes canonical
point-in-time bytes, builds the worker manifest, and only then exposes the job.
"""
from __future__ import annotations

import csv
from datetime import date, datetime, time, timezone
import hashlib
import io
import json
import math
import re
from typing import Any, Callable, Protocol
from zoneinfo import ZoneInfo

import pandas as pd

from core.backtest_queue import BacktestQueue, BacktestQueueError
from core.plans import backtest_years
from data.datasource import get_resilient_data_source
from src.apps.worker.point_in_time_freezer import (
    CANONICAL_COLUMNS,
    FrozenDailyOhlcv,
    freeze_daily_ohlcv,
)
from src.apps.worker.research_executor import (
    EQUITY_TEMPLATES,
    MINIMUM_BARS,
    research_code_bundle_sha256,
)


NEW_YORK = ZoneInfo("America/New_York")
REQUEST_FIELDS = {
    "schema_version", "type", "template_key", "symbol", "timeframe",
    "sample_years", "lookback",
}
SAMPLE_YEARS = frozenset({1, 3, 10})
SYMBOL = re.compile(r"^[A-Z][A-Z0-9]{0,14}(?:[.-][A-Z0-9]{1,4})?$")
IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")


class DailyBarsSource(Protocol):
    name: str

    def search(
        self, query: str, market: str = "美股", max_results: int = 8
    ) -> list[dict[str, str]]: ...

    def bars(self, symbol: str, period: str, interval: str) -> pd.DataFrame: ...


class BacktestPreparationService:
    """Prepare one genuine, fixed-input backtest run for an authenticated user."""

    def __init__(
        self,
        queue: BacktestQueue,
        *,
        data_source: DailyBarsSource | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.queue = queue
        self.data_source = data_source
        self.now = now or (lambda: datetime.now(timezone.utc))

    def prepare(
        self,
        owner_id: int,
        plan: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> tuple[dict[str, Any], bool]:
        request = _validate_request(payload, plan)
        if not isinstance(owner_id, int) or isinstance(owner_id, bool) or owner_id < 1:
            raise BacktestQueueError("回测任务缺少有效所有者。", 401)
        if not IDEMPOTENCY_KEY.fullmatch(str(idempotency_key)):
            raise BacktestQueueError("Idempotency-Key 必须为 8 至 128 个安全字符。")
        fingerprint = _request_sha256(request)
        existing = self.queue.find_idempotent(owner_id, idempotency_key)
        if existing is not None:
            if _prepared_request_sha256(existing) != fingerprint:
                raise BacktestQueueError("Idempotency-Key 已用于不同请求。", 409)
            if existing["status"] != "preparing":
                if not self.queue.inputs_ready(existing["id"], owner_id):
                    raise BacktestQueueError("该幂等任务的冻结输入尚未完整准备。", 409)
                return existing, False

        moment = self.now()
        if not isinstance(moment, datetime) or moment.tzinfo is None or moment.utcoffset() is None:
            raise BacktestQueueError("服务器评估时间无效。", 503)
        moment = moment.astimezone(timezone.utc)
        try:
            source = self.data_source or get_resilient_data_source()
            profile = _us_equity_profile(source, request["symbol"])
            frame = source.bars(
                request["symbol"],
                period=f"{request['sample_years']}y",
                interval="1d",
            )
            frozen = _freeze_frame(
                request["symbol"], frame, moment, request["sample_years"]
            )
        except BacktestQueueError:
            raise
        except Exception as exc:
            raise BacktestQueueError("历史行情无法完成可信冻结，请稍后重试。", 503) from exc

        manifest = _manifest(
            request,
            frozen,
            moment,
            fingerprint,
            profile,
            _adapter_name(source),
        )
        job, created = self.queue.enqueue(
            owner_id,
            {"type": "backtest.run.v1", "manifest": manifest},
            idempotency_scope=f"user:{owner_id}",
            idempotency_key=idempotency_key,
            plan=plan,
            preparing=True,
        )
        try:
            self.queue.register_input(
                job["id"],
                "prices.csv",
                frozen.canonical_csv,
                frozen.sha256,
                frozen.row_count,
                "text/csv",
            )
            job = self.queue.release_prepared(job["id"], owner_id)
        except Exception:
            if created:
                try:
                    self.queue.cancel(job["id"], owner_id)
                except Exception:
                    # The preparing state independently prevents claim(), even
                    # if durable input registration already completed.
                    pass
            raise
        return self.queue.get(job["id"], owner_id), created


def _validate_request(value: Any, plan: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != REQUEST_FIELDS:
        raise BacktestQueueError("回测请求字段无效；manifest 与执行材料只能由服务器生成。")
    schema_version = value.get("schema_version")
    if (
        not isinstance(schema_version, int)
        or isinstance(schema_version, bool)
        or schema_version != 1
        or value.get("type") != "backtest.run.v1"
    ):
        raise BacktestQueueError("当前仅开放 backtest.run.v1。", 403)
    template = value.get("template_key")
    symbol = value.get("symbol")
    timeframe = value.get("timeframe")
    sample_years = value.get("sample_years")
    lookback = value.get("lookback")
    if template not in EQUITY_TEMPLATES:
        raise BacktestQueueError("当前仅开放三个美股 long/flat 服务端模板。", 403)
    if not isinstance(symbol, str) or not SYMBOL.fullmatch(symbol):
        raise BacktestQueueError("美股代码无效。")
    if timeframe != "1d":
        raise BacktestQueueError("当前回测仅开放日线。", 403)
    if not isinstance(sample_years, int) or isinstance(sample_years, bool) or sample_years not in SAMPLE_YEARS:
        raise BacktestQueueError("历史样本年限无效。")
    plan_name = str(plan)
    plan_limit = backtest_years(plan_name) if plan_name in {
        "免费版", "标准版", "高级版", "专业版", "定制版"
    } else 1
    if sample_years > plan_limit:
        raise BacktestQueueError("当前会员方案不支持该历史样本年限。", 403)
    if not isinstance(lookback, int) or isinstance(lookback, bool) or not 2 <= lookback <= 250:
        raise BacktestQueueError("lookback 必须为 2 至 250 的整数。")
    return {
        "schema_version": 1,
        "type": "backtest.run.v1",
        "template_key": template,
        "symbol": symbol,
        "timeframe": "1d",
        "sample_years": sample_years,
        "lookback": lookback,
    }


def _request_sha256(request: dict[str, Any]) -> str:
    body = json.dumps(
        request, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _prepared_request_sha256(job: dict[str, Any]) -> str | None:
    manifest = job.get("manifest")
    provenance = manifest.get("provenance") if isinstance(manifest, dict) else None
    value = provenance.get("browser_request_sha256") if isinstance(provenance, dict) else None
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) else None


def _freeze_frame(
    symbol: str, frame: pd.DataFrame, moment: datetime, years: int
) -> FrozenDailyOhlcv:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise ValueError("daily source returned no rows")
    required = {"Open", "High", "Low", "Close", "Volume"}
    if not required.issubset(frame.columns):
        raise ValueError("daily source columns are incomplete")
    cutoff = _subtract_years(moment.astimezone(NEW_YORK).date(), years)
    rows: list[tuple[str, ...]] = []
    seen: set[date] = set()
    for timestamp, values in frame.sort_index().iterrows():
        session = _session_date(timestamp)
        if session < cutoff:
            continue
        if session in seen:
            raise ValueError("daily source contains duplicate sessions")
        seen.add(session)
        opened = datetime.combine(session, time(9, 30), tzinfo=NEW_YORK).astimezone(timezone.utc)
        closed = datetime.combine(session, time(16), tzinfo=NEW_YORK).astimezone(timezone.utc)
        available = datetime.combine(session, time(23, 59, 59), tzinfo=NEW_YORK).astimezone(timezone.utc)
        if closed > moment or available > moment:
            continue
        prices = tuple(_positive_float(values[name]) for name in ("Open", "High", "Low", "Close"))
        volume = _positive_volume(values["Volume"])
        rows.append(
            (
                symbol,
                session.isoformat(),
                _timestamp(opened),
                _timestamp(closed),
                _timestamp(available),
                *(format(item, ".12g") for item in prices),
                str(volume),
            )
        )
    if len(rows) < MINIMUM_BARS:
        raise ValueError("daily source does not contain enough completed sessions")
    target = io.StringIO(newline="")
    writer = csv.writer(target, lineterminator="\n")
    writer.writerow(CANONICAL_COLUMNS)
    writer.writerows(rows)
    return freeze_daily_ohlcv(
        target.getvalue().encode("utf-8"), as_of=moment, allowed_symbols={symbol}
    )


def _manifest(
    request: dict[str, Any],
    frozen: FrozenDailyOhlcv,
    moment: datetime,
    fingerprint: str,
    profile: dict[str, str],
    adapter: str,
) -> dict[str, Any]:
    captured_at = datetime.combine(
        moment.date(), time.min, tzinfo=timezone.utc
    ).isoformat(timespec="seconds").replace("+00:00", "Z")
    return {
        "schema_version": 1,
        "evaluation_date": moment.date().isoformat(),
        "dataset_end": frozen.dataset_end.isoformat(),
        "code_bundle_sha256": research_code_bundle_sha256(),
        "inputs": [frozen.manifest_input()],
        "template_key": request["template_key"],
        "provenance": {
            "source": "authenticated_server_market_data",
            "adapter": adapter,
            "asset_type": profile["type"],
            "exchange": profile["exchange"],
            "browser_request_sha256": fingerprint,
            "sample_years": request["sample_years"],
            "timeframe": "1d",
            "availability_policy": "synthetic_23_59_59_america_new_york_completed_session_v1",
        },
        "asset_universe": {
            "market": "US",
            "instrument_family": "equity",
            "symbols": [request["symbol"]],
            "direction": "long_flat",
            "research_proxy": False,
            "data_mode": "point_in_time_prices",
        },
        "search_space": {"lookback": [request["lookback"]]},
        "parameters": {"lookback": request["lookback"]},
        "experiment_budget": {"runs": 1, "folds": 3},
        "evidence_hashes": {"prices.csv": frozen.sha256},
        "authority": {
            "origin_site": "ciclotrade-api",
            "deployment_role": "strategy_worker",
            "publication_ceiling": "shadow",
            "outbound_publish_enabled": False,
            "user_visible": False,
            "execution_eligible": False,
            "recommendations_published": False,
        },
        "risk_contract": {
            "defined_risk": True,
            "max_loss_amount": 500.0,
            "currency": "USD",
            "max_loss_pct_model_equity": 0.005,
            "risk_basis_equity": 100_000.0,
            "risk_basis_captured_at": captured_at,
            "portfolio_open_risk_cap_pct": 0.03,
            "daily_new_risk_pause_pct": 0.015,
            "quarantine_drawdown_pct": 0.08,
            "invalidation_condition": "Frozen long-flat research risk boundary breached.",
        },
        "validation_plan": {
            "oos_method": "point_in_time",
            "walk_forward": True,
            "cost_multipliers": [1.0, 2.0],
            "stress_tests": ["gap", "liquidity", "volatility"],
            "minimum_trades": 30,
            "minimum_coverage_days": 252,
            "market_regimes": ["bull", "bear", "sideways"],
        },
    }


def _us_equity_profile(source: DailyBarsSource, symbol: str) -> dict[str, str]:
    matches = source.search(symbol, market="美股", max_results=8)
    if not isinstance(matches, list):
        raise BacktestQueueError("无法验证股票类别。", 503)
    exact = next(
        (
            item
            for item in matches
            if isinstance(item, dict)
            and str(item.get("symbol") or "").strip().upper() == symbol
        ),
        None,
    )
    if exact is None or exact.get("type") not in {"股票", "ETF"}:
        raise BacktestQueueError("当前仅允许经服务端验证的美股股票或 ETF。", 403)
    exchange = str(exact.get("exchange") or "").strip()
    if not exchange or len(exchange) > 128 or any(char in exchange for char in "\r\n\x00"):
        raise BacktestQueueError("股票交易所信息无效。", 503)
    return {"type": str(exact["type"]), "exchange": exchange}


def _adapter_name(source: DailyBarsSource) -> str:
    value = str(getattr(source, "name", type(source).__name__)).strip()
    if not value or len(value) > 128 or any(char in value for char in "\r\n\x00"):
        raise BacktestQueueError("历史行情适配器身份无效。", 503)
    return value


def _subtract_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year - years)
    except ValueError:
        return value.replace(year=value.year - years, day=28)


def _session_date(value: Any) -> date:
    timestamp = pd.Timestamp(value)
    if pd.isna(timestamp):
        raise ValueError("daily source timestamp is missing")
    return timestamp.tz_convert(NEW_YORK).date() if timestamp.tzinfo is not None else timestamp.date()


def _positive_float(value: Any) -> float:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0:
        raise ValueError("daily source price is invalid")
    return parsed


def _positive_volume(value: Any) -> int:
    parsed = float(value)
    if not math.isfinite(parsed) or parsed <= 0 or not parsed.is_integer():
        raise ValueError("daily source volume is invalid")
    return int(parsed)


def _timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
