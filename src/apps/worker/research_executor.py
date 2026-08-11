"""Deterministic, side-effect-free US equity long/flat research execution."""

from __future__ import annotations

import base64
from datetime import date, datetime, time, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import platform
from statistics import fmean, pstdev
import sys
import tempfile
from typing import Any, Callable, Iterable, Mapping

from core import backtest_source_snapshot
from core.backtest_source_snapshot import SourceSnapshotError, validate_source_snapshot
from src.apps.worker import point_in_time_freezer
from src.apps.worker.point_in_time_freezer import DailyBar, FrozenDailyOhlcv, PointInTimeError, freeze_daily_ohlcv


EQUITY_TEMPLATES = frozenset({"equity.trend.long_flat.v1", "equity.mean_reversion.long_flat.v1", "equity.breakout.long_flat.v1"})
BASE_COST_BPS = 5.0
LOOKBACK = 10
MINIMUM_BARS = 60
MAX_REQUEST_BYTES = 32 * 1024 * 1024


class ResearchExecutionError(ValueError):
    """Raised when a request is outside the bounded research contract."""


def execute_research(manifest: Mapping[str, Any], input_bytes_by_key: Mapping[str, bytes]) -> dict[str, Any]:
    """Run one fixed template against one canonical frozen daily input."""
    if not isinstance(manifest, Mapping) or not isinstance(input_bytes_by_key, Mapping):
        raise ResearchExecutionError("manifest and inputs must be mappings")
    template, symbols = _template(manifest)
    dataset, input_hashes = _freeze_manifest_input(manifest, input_bytes_by_key, symbols)
    if dataset.row_count < MINIMUM_BARS:
        raise ResearchExecutionError(f"at least {MINIMUM_BARS} frozen daily bars are required")
    signal, risk, plan = _signal(template), _risk(manifest), _plan(manifest)
    costs = {label: _metrics(dataset.bars, signal, multiplier) for label, multiplier in (("1x", 1.0), ("2x", 2.0))}
    oos_start = max(LOOKBACK + 1, len(dataset.bars) // 2)
    oos = _metrics(dataset.bars, signal, 1.0, start=oos_start)
    folds = _chronological_folds(dataset.bars, signal)
    stress = {
        "gap": _metrics(dataset.bars, signal, 1.0, gap_penalty=True),
        "liquidity": _metrics(dataset.bars, signal, 2.0, liquidity_penalty=True),
        "volatility": _metrics(dataset.bars, signal, 1.0, volatility_penalty=True),
    }
    regimes = _regimes(dataset.bars)
    validation = _validation(dataset, manifest, costs, oos, folds, stress, regimes, risk, plan)
    return {
        "schema_version": 1,
        "runner": "equity-research-v1",
        "code_bundle_sha256": research_code_bundle_sha256(),
        "template_key": template,
        "input_hashes": input_hashes,
        "metrics": {"costs": costs, "oos": {"start_index": oos_start, "metrics": oos}, "chronological_folds": folds, "stress": stress, "regime_counts": regimes},
        "validation": validation,
        "risk": risk,
        "limitations": ["historical daily bars only", "long-flat template scope", "no side effects"],
    }


def execute_all_templates(manifest: Mapping[str, Any], input_bytes_by_key: Mapping[str, bytes]) -> dict[str, dict[str, Any]]:
    return {template: execute_research({**manifest, "template_key": template}, input_bytes_by_key) for template in sorted(EQUITY_TEMPLATES)}


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Bounded local equity research executor")
    parser.add_argument("--request-file")
    parser.add_argument("--output-file")
    args = parser.parse_args(argv)
    if bool(args.request_file) != bool(args.output_file):
        parser.error("--request-file and --output-file must be supplied together")
    try:
        source = _request_bytes(args.request_file) if args.request_file else sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(source) > MAX_REQUEST_BYTES:
            raise ResearchExecutionError("request exceeds the fixed size limit")
        result = _execute_request(source)
    except (ResearchExecutionError, PointInTimeError, ValueError, TypeError) as exc:
        print(f"research execution refused: {exc}", file=sys.stderr)
        return 2
    encoded = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if args.output_file:
        _atomic_output(args.output_file, encoded)
    else:
        print(encoded.decode())
    return 0


def _request_bytes(value: str) -> bytes:
    path = Path(value)
    if not path.is_absolute():
        raise ResearchExecutionError("request file must be absolute")
    body = path.read_bytes()
    if len(body) > MAX_REQUEST_BYTES:
        raise ResearchExecutionError("request file exceeds the fixed size limit")
    return body


def _execute_request(body: bytes) -> dict[str, Any]:
    request = json.loads(body)
    if not isinstance(request, dict) or set(request) != {"manifest", "inputs"} or not isinstance(request["inputs"], dict):
        raise ResearchExecutionError("request must contain only manifest and inputs")
    inputs = {key: base64.b64decode(value, validate=True) for key, value in request["inputs"].items() if isinstance(key, str) and isinstance(value, str)}
    if len(inputs) != len(request["inputs"]):
        raise ResearchExecutionError("inputs must be base64 strings")
    return execute_research(request["manifest"], inputs)


def _atomic_output(value: str, body: bytes) -> None:
    output = Path(value)
    if not output.is_absolute() or not output.parent.is_dir():
        raise ResearchExecutionError("output file must be inside an existing absolute directory")
    descriptor, temporary = tempfile.mkstemp(prefix=".research-", dir=output.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def research_code_bundle_sha256() -> str:
    runtime = json.dumps(
        {
            "implementation": sys.implementation.name,
            "python_version": platform.python_version(),
            "platform_system": platform.system(),
            "platform_machine": platform.machine(),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    sources = (
        Path(__file__).read_bytes(),
        Path(point_in_time_freezer.__file__).read_bytes(),
        Path(backtest_source_snapshot.__file__).read_bytes(),
        runtime,
    )
    return hashlib.sha256(b"\0".join(sources)).hexdigest()


def _template(manifest: Mapping[str, Any]) -> tuple[str, tuple[str, ...]]:
    template, universe = manifest.get("template_key"), manifest.get("asset_universe")
    expected = {"market": "US", "instrument_family": "equity", "direction": "long_flat", "research_proxy": False, "data_mode": "point_in_time_prices"}
    symbols = universe.get("symbols") if isinstance(universe, Mapping) else None
    if template not in EQUITY_TEMPLATES or not isinstance(symbols, list) or len(symbols) != 1 or any(universe.get(key) != value for key, value in expected.items()) or not all(isinstance(symbol, str) for symbol in symbols):
        raise ResearchExecutionError("asset universe is outside the long/flat equity scope")
    return template, tuple(symbols)


def _freeze_manifest_input(manifest: Mapping[str, Any], supplied: Mapping[str, bytes], symbols: tuple[str, ...]) -> tuple[FrozenDailyOhlcv, dict[str, str]]:
    inputs = manifest.get("inputs")
    if not isinstance(inputs, list) or not all(isinstance(item, Mapping) for item in inputs):
        raise ResearchExecutionError("manifest inputs are invalid")
    descriptors = {item.get("artifact_key"): item for item in inputs}
    if len(descriptors) != len(inputs) or not all(isinstance(key, str) for key in descriptors):
        raise ResearchExecutionError("manifest input keys are invalid")
    candidate = "candidate_id" in manifest
    required = {"source.csv", "source-snapshot.json", "prices.csv"} if candidate else {"prices.csv"}
    if set(descriptors) != required or set(supplied) != required:
        raise ResearchExecutionError("required frozen inputs are missing")
    hashes = _verify_descriptors(descriptors, supplied, manifest.get("evidence_hashes"), candidate)
    item, body = descriptors["prices.csv"], supplied["prices.csv"]
    snapshot = _snapshot_value(supplied["source-snapshot.json"]) if candidate else None
    as_of = _snapshot_as_of(snapshot) if snapshot else _evaluation_as_of(manifest.get("evaluation_date"))
    dataset = freeze_daily_ohlcv(body, as_of=as_of, allowed_symbols=symbols)
    if dataset.sha256 != hashes["prices.csv"] or item.get("bytes") not in {None, len(body)} or item.get("rows") not in {None, dataset.row_count} or item.get("dataset_end") != dataset.dataset_end.isoformat() or manifest.get("dataset_end") != dataset.dataset_end.isoformat():
        raise ResearchExecutionError("input is not the declared canonical frozen dataset")
    if candidate:
        _verify_snapshot(snapshot, descriptors, dataset, manifest, symbols[0])
    return dataset, hashes


def _verify_descriptors(descriptors: Mapping[str, Mapping[str, Any]], supplied: Mapping[str, bytes], evidence: Any, candidate: bool) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for key, descriptor in descriptors.items():
        body, digest = supplied[key], descriptor.get("sha256")
        if not isinstance(body, bytes) or not isinstance(digest, str) or hashlib.sha256(body).hexdigest() != digest or descriptor.get("bytes") not in {None, len(body)}:
            raise ResearchExecutionError("input bytes do not match their frozen descriptors")
        hashes[key] = digest
    if candidate and (not isinstance(evidence, Mapping) or dict(evidence) != hashes):
        raise ResearchExecutionError("candidate evidence hashes do not match frozen inputs")
    return hashes


def _snapshot_value(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise ResearchExecutionError("source snapshot is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ResearchExecutionError("source snapshot is not an object")
    return value


def _snapshot_as_of(value: Mapping[str, Any]) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value.get("as_of")).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResearchExecutionError("source snapshot as_of is invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ResearchExecutionError("source snapshot as_of must include a timezone")
    return parsed.astimezone(timezone.utc)


def _verify_snapshot(
    value: Mapping[str, Any],
    descriptors: Mapping[str, Mapping[str, Any]],
    dataset: FrozenDailyOhlcv,
    manifest: Mapping[str, Any],
    symbol: str,
) -> None:
    try:
        evaluation = date.fromisoformat(str(manifest.get("evaluation_date")))
        validated = validate_source_snapshot(
            dict(value),
            evaluation_date=evaluation,
            manifest_dataset_end=dataset.dataset_end,
            inputs=descriptors,
        )
    except (SourceSnapshotError, ValueError) as exc:
        raise ResearchExecutionError(str(exc)) from exc
    if validated["symbol"] != symbol:
        raise ResearchExecutionError("source snapshot symbol does not match the asset universe")


def _evaluation_as_of(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ResearchExecutionError("evaluation_date must be ISO date") from exc
    if parsed.time() != time.min:
        raise ResearchExecutionError("evaluation_date must not include a time")
    return datetime.combine(parsed.date(), time.max, tzinfo=timezone.utc)


def _risk(manifest: Mapping[str, Any]) -> dict[str, Any]:
    source = manifest.get("risk_contract")
    needed = {"defined_risk", "max_loss_amount", "currency", "max_loss_pct_model_equity", "risk_basis_equity", "risk_basis_captured_at", "portfolio_open_risk_cap_pct", "daily_new_risk_pause_pct", "quarantine_drawdown_pct", "invalidation_condition"}
    if not isinstance(source, Mapping) or set(source) != needed or source.get("defined_risk") is not True:
        raise ResearchExecutionError("a bounded risk contract is required")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for key, value in source.items() if key not in {"defined_risk", "currency", "risk_basis_captured_at", "invalidation_condition"}):
        raise ResearchExecutionError("risk contract contains an invalid numeric limit")
    return dict(source)


def _plan(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    plan = manifest.get("validation_plan")
    required = {"oos_method", "walk_forward", "cost_multipliers", "stress_tests", "minimum_trades", "minimum_coverage_days", "market_regimes"}
    if not isinstance(plan, Mapping) or set(plan) != required or plan.get("oos_method") != "point_in_time" or plan.get("walk_forward") is not True or plan.get("cost_multipliers") != [1.0, 2.0] or set(plan.get("stress_tests", [])) < {"gap", "liquidity", "volatility"}:
        raise ResearchExecutionError("validation plan is outside the research contract")
    if not isinstance(plan["minimum_trades"], int) or not isinstance(plan["minimum_coverage_days"], int) or not isinstance(plan["market_regimes"], list):
        raise ResearchExecutionError("validation plan has invalid thresholds")
    return plan


def _signal(template: str) -> Callable[[list[DailyBar]], int]:
    if template == "equity.trend.long_flat.v1":
        return lambda history: int(history[-1].close > fmean(bar.close for bar in history[-5:]))
    if template == "equity.mean_reversion.long_flat.v1":
        return lambda history: int(history[-1].close < fmean(bar.close for bar in history[-10:]) - pstdev([bar.close for bar in history[-10:]]))
    return lambda history: int(history[-1].close > max(bar.close for bar in history[-10:-1]))


def _metrics(bars: tuple[DailyBar, ...], signal: Callable[[list[DailyBar]], int], cost_multiplier: float, *, start: int = LOOKBACK + 1, stop: int | None = None, gap_penalty: bool = False, liquidity_penalty: bool = False, volatility_penalty: bool = False) -> dict[str, Any]:
    stop = len(bars) if stop is None else stop
    if start <= LOOKBACK or stop - start < 2:
        raise ResearchExecutionError("evaluation slice is too short for the template lookback")
    returns: list[float] = []
    position = trades = 0
    for entry in range(start, stop - 1):
        decision, opened, next_open = bars[entry - 1], bars[entry], bars[entry + 1]
        if decision.available_at > opened.session_open_at:
            raise ResearchExecutionError("decision used data unavailable at the execution open")
        desired = signal(list(bars[:entry]))
        turnover = int(desired != position)
        cost = turnover * BASE_COST_BPS * cost_multiplier / 10_000
        value = desired * (next_open.open / opened.open - 1.0) - cost
        overnight = next_open.open / opened.close - 1.0
        if gap_penalty and desired:
            value += min(0.0, overnight) * 0.5
        if liquidity_penalty and turnover:
            value -= BASE_COST_BPS * cost_multiplier / 10_000
        if volatility_penalty:
            value *= 1.5 if value < 0 else 0.75
        if not math.isfinite(value):
            raise ResearchExecutionError("non-finite execution result")
        returns.append(value)
        trades += turnover
        position = desired
    if position:
        returns.append(-BASE_COST_BPS * cost_multiplier / 10_000)
        trades += 1
    curve = _equity_curve(returns)
    return {"return_pct": _round(curve[-1] - 1.0), "max_drawdown": _round(_max_drawdown(curve)), "tail_stress_loss_pct": _round(_tail_loss(returns)), "trades": trades, "observations": len(returns)}


def _chronological_folds(bars: tuple[DailyBar, ...], signal: Callable[[list[DailyBar]], int]) -> list[dict[str, Any]]:
    start, width = max(LOOKBACK + 1, len(bars) // 2), (len(bars) - max(LOOKBACK + 1, len(bars) // 2)) // 3
    if width < 3:
        raise ResearchExecutionError("insufficient bars for chronological validation folds")
    return [{"fold": fold + 1, "metrics": _metrics(bars, signal, 1.0, start=start + fold * width, stop=len(bars) if fold == 2 else start + (fold + 1) * width)} for fold in range(3)]


def _validation(dataset: FrozenDailyOhlcv, manifest: Mapping[str, Any], costs: Mapping[str, Mapping[str, Any]], oos: Mapping[str, Any], folds: list[Mapping[str, Any]], stress: Mapping[str, Mapping[str, Any]], regimes: Mapping[str, int], risk: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    actual_regimes = [name for name, count in regimes.items() if count > 0]
    minimums = costs["1x"]["trades"] >= plan["minimum_trades"] and dataset.row_count >= plan["minimum_coverage_days"] and set(plan["market_regimes"]) <= set(actual_regimes)
    oos_gate = oos["return_pct"] > 0 and oos["max_drawdown"] < risk["quarantine_drawdown_pct"]
    folds_gate = all(fold["metrics"]["return_pct"] > 0 and fold["metrics"]["max_drawdown"] < risk["quarantine_drawdown_pct"] for fold in folds)
    stress_gate = all(value["max_drawdown"] < risk["quarantine_drawdown_pct"] and value["tail_stress_loss_pct"] <= risk["daily_new_risk_pause_pct"] for value in stress.values())
    passed = bool(minimums and oos_gate and folds_gate and stress_gate)
    return {"dataset_end": dataset.dataset_end.isoformat(), "evaluation_date": manifest["evaluation_date"], "oos_passed": passed, "walk_forward_passed": passed, "cost_multipliers": [1.0, 2.0], "stress_passed": passed, "trade_count": costs["1x"]["trades"], "coverage_days": dataset.row_count, "max_drawdown": costs["1x"]["max_drawdown"], "tail_stress_loss_pct": stress["volatility"]["tail_stress_loss_pct"], "market_regimes": actual_regimes}


def _regimes(bars: Iterable[DailyBar]) -> dict[str, int]:
    counts, series = {"bull": 0, "bear": 0, "sideways": 0}, list(bars)
    for index in range(5, len(series)):
        change = series[index].close / series[index - 5].close - 1.0
        counts["bull" if change > 0.01 else "bear" if change < -0.01 else "sideways"] += 1
    return counts


def _equity_curve(returns: Iterable[float]) -> list[float]:
    curve = [1.0]
    for value in returns:
        curve.append(curve[-1] * (1.0 + value))
    return curve


def _max_drawdown(curve: Iterable[float]) -> float:
    high = 1.0
    maximum = 0.0
    for value in curve:
        high = max(high, value)
        maximum = max(maximum, 1.0 - value / high)
    return maximum


def _tail_loss(returns: list[float]) -> float:
    return max(0.0, -fmean(sorted(returns)[:max(1, math.ceil(len(returns) * 0.05))]))


def _round(value: float) -> float:
    return float(format(value, ".12g"))


if __name__ == "__main__":
    raise SystemExit(main())
