"""Validation contracts for bounded, research-only backtest jobs."""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, timezone
from typing import Any

from core.backtest_artifacts import ArtifactStore
from core.backtest_candidate_provenance import validate_candidate_provenance

SHA = re.compile(r"^[0-9a-f]{64}$")
SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
PUBLIC_TYPES = {"backtest.run.v1", "backtest.optimize.v1"}
INTERNAL_TYPES = PUBLIC_TYPES | {"candidate.evaluate.v1", "catalog.evaluate.v1", "saved.refresh.v1"}
INPUT_FIELDS = {"artifact_key", "sha256", "bytes", "rows", "dataset_end"}
STAGES = {"queued", "loading", "executing", "finalizing"}
MANIFEST_FIELDS = {
    "schema_version", "evaluation_date", "dataset_end", "code_bundle_sha256", "inputs",
    "candidate_id", "candidate_version", "provenance", "hypothesis", "parent_version",
    "parent_job_id", "parent_manifest_sha256", "parent_result_sha256", "template_key",
    "asset_universe", "search_space", "experiment_budget", "evidence_hashes",
    "promotion_proposal", "parameters", "authority", "risk_contract", "validation_plan",
}
BUDGET_LIMITS = {"runs": 64, "candidates": 64, "folds": 20}
PLAN_QUEUE_LIMITS = {"免费版": (1, 2), "标准版": (4, 8), "高级版": (8, 16), "专业版": (12, 32), "定制版": (20, 50)}
MAX_ATTEMPT_SECONDS = 4 * 60 * 60
EQUITY_TEMPLATES = {"equity.trend.long_flat.v1", "equity.mean_reversion.long_flat.v1", "equity.breakout.long_flat.v1"}
OPTION_TEMPLATES = {
    "option.long_call.v1": "long_call", "option.long_put.v1": "long_put",
    "option.call_debit_spread.v1": "call_debit_spread", "option.put_debit_spread.v1": "put_debit_spread",
    "option.protective_put.v1": "protective_put", "option.collar.v1": "collar",
}
FORBIDDEN_SEARCH_KEY = re.compile(r"(?:code|script|command|module|import|exec|eval|url|path|file|shell)", re.I)


class BacktestQueueError(ValueError):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _stamp(value: datetime | None = None) -> str:
    return (value or _now()).isoformat().replace("+00:00", "Z")


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise BacktestQueueError("JSON 内容必须可序列化且所有数值必须有限。") from exc


def sha256_json(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _as_object(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError) as exc:
        raise BacktestQueueError("JSON 内容无效。") from exc
    if not isinstance(value, dict):
        raise BacktestQueueError("JSON 内容必须为对象。")
    return value


def _date(value: Any, label: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise BacktestQueueError(f"{label} 必须是 YYYY-MM-DD。") from exc


def _timestamp(value: Any, label: str) -> str:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise BacktestQueueError(f"{label} 无效。") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BacktestQueueError(f"{label} 必须包含时区。")
    return _stamp(parsed.astimezone(timezone.utc))


def _hash(value: Any, label: str) -> str:
    if not isinstance(value, str) or not SHA.fullmatch(value):
        raise BacktestQueueError(f"{label} 必须为小写 SHA-256。")
    return value


def _number(value: Any, label: str, *, minimum: float = 0.0, maximum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise BacktestQueueError(f"{label} 必须为有限数值。")
    result = float(value)
    if result < minimum or (maximum is not None and result > maximum):
        raise BacktestQueueError(f"{label} 超出允许范围。")
    return result


def validate_authority(value: Any) -> dict[str, Any]:
    authority = _as_object(value)
    required = {
        "origin_site", "deployment_role", "publication_ceiling", "outbound_publish_enabled",
        "user_visible", "execution_eligible", "recommendations_published",
    }
    if set(authority) != required:
        raise BacktestQueueError("research authority 字段无效。")
    if not isinstance(authority["origin_site"], str) or not SAFE_ID.fullmatch(authority["origin_site"]):
        raise BacktestQueueError("research authority origin_site 无效。")
    if authority["deployment_role"] != "strategy_worker" or authority["publication_ceiling"] != "shadow":
        raise BacktestQueueError("Worker 发布权限上限必须锁定为 shadow。", 403)
    for key in ("outbound_publish_enabled", "user_visible", "execution_eligible", "recommendations_published"):
        if authority[key] is not False:
            raise BacktestQueueError("本地 research/shadow 证据不得发布、展示或执行。", 403)
    return authority


def validate_risk_contract(value: Any) -> dict[str, Any]:
    risk = _as_object(value)
    required = {
        "defined_risk", "max_loss_amount", "currency", "max_loss_pct_model_equity",
        "risk_basis_equity", "risk_basis_captured_at", "portfolio_open_risk_cap_pct",
        "daily_new_risk_pause_pct", "quarantine_drawdown_pct", "invalidation_condition",
    }
    if set(risk) != required or risk["defined_risk"] is not True or risk["currency"] != "USD":
        raise BacktestQueueError("风险合同字段无效或不是明确有限亏损。")
    maximum_loss = _number(risk["max_loss_amount"], "max_loss_amount", minimum=0.01)
    model_risk = _number(risk["max_loss_pct_model_equity"], "max_loss_pct_model_equity", minimum=0.0, maximum=0.005)
    basis = _number(risk["risk_basis_equity"], "risk_basis_equity", minimum=0.01)
    if maximum_loss / basis > model_risk + 1e-12:
        raise BacktestQueueError("max_loss_amount 超过候选风险占比上限。")
    _number(risk["portfolio_open_risk_cap_pct"], "portfolio_open_risk_cap_pct", maximum=0.03)
    _number(risk["daily_new_risk_pause_pct"], "daily_new_risk_pause_pct", maximum=0.015)
    _number(risk["quarantine_drawdown_pct"], "quarantine_drawdown_pct", maximum=0.08)
    _timestamp(risk["risk_basis_captured_at"], "risk_basis_captured_at")
    if not isinstance(risk["invalidation_condition"], str) or not 1 <= len(risk["invalidation_condition"].strip()) <= 2_000:
        raise BacktestQueueError("invalidation_condition 无效。")
    return risk


def validate_validation_plan(value: Any) -> dict[str, Any]:
    plan = _as_object(value)
    required = {
        "oos_method", "walk_forward", "cost_multipliers", "stress_tests",
        "minimum_trades", "minimum_coverage_days", "market_regimes",
    }
    if set(plan) != required or plan["oos_method"] != "point_in_time" or plan["walk_forward"] is not True:
        raise BacktestQueueError("候选必须使用 point-in-time OOS 与 Walk-Forward。")
    multipliers = plan["cost_multipliers"]
    if not isinstance(multipliers, list) or len(multipliers) != 2 or {_number(item, "cost_multipliers", minimum=1.0, maximum=2.0) for item in multipliers} != {1.0, 2.0}:
        raise BacktestQueueError("成本测试必须同时包含 1x 与 2x。")
    stress = plan["stress_tests"]
    if not isinstance(stress, list) or not {"gap", "liquidity", "volatility"} <= set(stress):
        raise BacktestQueueError("压力测试必须覆盖跳空、流动性与波动率。")
    if not isinstance(plan["minimum_trades"], int) or isinstance(plan["minimum_trades"], bool) or plan["minimum_trades"] < 30:
        raise BacktestQueueError("minimum_trades 不得低于 30。")
    if not isinstance(plan["minimum_coverage_days"], int) or isinstance(plan["minimum_coverage_days"], bool) or plan["minimum_coverage_days"] < 252:
        raise BacktestQueueError("minimum_coverage_days 不得低于 252。")
    regimes = plan["market_regimes"]
    if not isinstance(regimes, list) or len(set(regimes)) < 3 or not all(isinstance(item, str) and SAFE_ID.fullmatch(item) for item in regimes):
        raise BacktestQueueError("market_regimes 至少需要三个安全标识。")
    return plan


def validate_asset_universe(value: Any, template_key: Any) -> dict[str, Any]:
    universe = _as_object(value)
    common = {"market", "instrument_family", "symbols", "direction", "research_proxy", "data_mode"}
    if frozenset(universe) not in {frozenset(common), frozenset(common | {"option_structure"})}:
        raise BacktestQueueError("asset_universe 字段无效。")
    if universe["market"] != "US" or not isinstance(universe["research_proxy"], bool):
        raise BacktestQueueError("首批候选仅允许美股研究范围。")
    symbols = universe["symbols"]
    symbol_pattern = re.compile(r"^[A-Z][A-Z0-9.-]{0,15}$")
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 64 or len(set(symbols)) != len(symbols) or not all(isinstance(item, str) and symbol_pattern.fullmatch(item) for item in symbols):
        raise BacktestQueueError("asset_universe.symbols 无效。")
    if universe["instrument_family"] == "equity":
        if template_key not in EQUITY_TEMPLATES or universe["direction"] != "long_flat" or universe["research_proxy"] is not False or universe["data_mode"] != "point_in_time_prices" or "option_structure" in universe:
            raise BacktestQueueError("正股候选只允许 long/flat 趋势、均值回归与突破模板。", 403)
    elif universe["instrument_family"] == "option":
        expected = OPTION_TEMPLATES.get(str(template_key))
        if not expected or universe.get("option_structure") != expected or universe["direction"] != "limited_risk":
            raise BacktestQueueError("期权候选只允许白名单内的有限亏损结构。", 403)
        if universe["data_mode"] == "underlying_volatility_proxy":
            if universe["research_proxy"] is not True:
                raise BacktestQueueError("期权代理历史必须显著标记 research_proxy。")
        elif universe["data_mode"] == "historical_contracts":
            if universe["research_proxy"] is not False:
                raise BacktestQueueError("真实合约数据不得误标为代理。")
        else:
            raise BacktestQueueError("期权 data_mode 无效。")
    else:
        raise BacktestQueueError("instrument_family 无效。")
    return universe


def validate_search_space(value: Any, budget: dict[str, Any]) -> dict[str, Any]:
    search = _as_object(value)
    if not 1 <= len(search) <= 12:
        raise BacktestQueueError("candidate search_space 必须包含 1 至 12 个受限维度。")
    combinations = 1
    for key, values in search.items():
        if not isinstance(key, str) or not SAFE_ID.fullmatch(key) or FORBIDDEN_SEARCH_KEY.search(key):
            raise BacktestQueueError("search_space 包含禁止字段。")
        if not isinstance(values, list) or not 1 <= len(values) <= 32:
            raise BacktestQueueError("search_space 每个维度必须为有限候选列表。")
        encoded: set[str] = set()
        for item in values:
            if isinstance(item, bool):
                pass
            elif isinstance(item, (int, float)):
                _number(item, f"search_space.{key}", minimum=-1_000_000, maximum=1_000_000)
            elif isinstance(item, str) and 1 <= len(item) <= 128 and not any(char in item for char in ("\n", "\r", "\x00")):
                pass
            else:
                raise BacktestQueueError("search_space 只允许有限标量。")
            encoded.add(_json(item))
        if len(encoded) != len(values):
            raise BacktestQueueError("search_space 不得包含重复值。")
        combinations *= len(values)
    if combinations > int(budget.get("runs", budget.get("candidates", 1))):
        raise BacktestQueueError("search_space 组合数超过 experiment_budget。")
    return search


def validate_promotion_proposal(value: Any) -> dict[str, Any]:
    proposal = _as_object(value)
    allowed = {"target_stage", "requires_human_approval", "rationale"}
    if set(proposal) - allowed or {"target_stage", "requires_human_approval", "rationale"} - set(proposal):
        raise BacktestQueueError("promotion_proposal 字段无效。")
    if proposal["target_stage"] not in {"research", "shadow", "official_simulation"}:
        raise BacktestQueueError("promotion_proposal 目标阶段无效。")
    if proposal["requires_human_approval"] is not True:
        raise BacktestQueueError("promotion_proposal 必须明确要求人工批准。")
    if not isinstance(proposal["rationale"], str) or not 1 <= len(proposal["rationale"].strip()) <= 2_000:
        raise BacktestQueueError("promotion_proposal rationale 无效。")
    return proposal


def validate_manifest(value: Any) -> dict[str, Any]:
    manifest = _as_object(value)
    if set(manifest) - MANIFEST_FIELDS:
        raise BacktestQueueError("manifest 包含未知字段。")
    if manifest.get("schema_version") != 1:
        raise BacktestQueueError("manifest.schema_version 必须为 1。")
    evaluation = _date(manifest.get("evaluation_date"), "evaluation_date")
    dataset_end = _date(manifest.get("dataset_end"), "dataset_end")
    if dataset_end > evaluation:
        raise BacktestQueueError("dataset_end 不得晚于 evaluation_date。")
    _hash(manifest.get("code_bundle_sha256"), "code_bundle_sha256")
    inputs = manifest.get("inputs")
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 64:
        raise BacktestQueueError("每项任务必须声明 1 至 64 个冻结输入。")
    seen: set[str] = set()
    for item in inputs:
        if not isinstance(item, dict) or set(item) - INPUT_FIELDS or {"artifact_key", "sha256"} - set(item):
            raise BacktestQueueError("输入声明字段无效。")
        key = item["artifact_key"]
        if not isinstance(key, str) or not ArtifactStore.valid_key(key) or key in seen:
            raise BacktestQueueError("输入 artifact_key 无效或重复。")
        seen.add(key)
        _hash(item["sha256"], "inputs.sha256")
        if "bytes" in item and (not isinstance(item["bytes"], int) or isinstance(item["bytes"], bool) or item["bytes"] < 0):
            raise BacktestQueueError("inputs.bytes 无效。")
        if "rows" in item and (not isinstance(item["rows"], int) or isinstance(item["rows"], bool) or item["rows"] < 0):
            raise BacktestQueueError("inputs.rows 无效。")
        if "dataset_end" not in item:
            raise BacktestQueueError("每个冻结输入必须声明 dataset_end。")
        input_end = _date(item["dataset_end"], "inputs.dataset_end")
        if input_end > dataset_end or input_end > evaluation:
            raise BacktestQueueError("inputs.dataset_end 不得晚于任务数据截止日。")
    budget = manifest.get("experiment_budget")
    if not isinstance(budget, dict) or not budget or set(budget) - set(BUDGET_LIMITS):
        raise BacktestQueueError("每项任务都必须声明受限的 experiment_budget。")
    for name, number in budget.items():
        maximum = BUDGET_LIMITS[name]
        if not isinstance(number, int) or isinstance(number, bool) or not 1 <= number <= maximum:
            raise BacktestQueueError(f"experiment_budget.{name} 必须为 1 至 {maximum} 的整数。")
    if "authority" in manifest:
        manifest["authority"] = validate_authority(manifest["authority"])
    if "risk_contract" in manifest:
        manifest["risk_contract"] = validate_risk_contract(manifest["risk_contract"])
    if "validation_plan" in manifest:
        manifest["validation_plan"] = validate_validation_plan(manifest["validation_plan"])
    if "asset_universe" in manifest:
        manifest["asset_universe"] = validate_asset_universe(manifest["asset_universe"], manifest.get("template_key"))
    if "search_space" in manifest:
        manifest["search_space"] = validate_search_space(manifest["search_space"], budget)
    if "parameters" in manifest:
        parameters = _as_object(manifest["parameters"])
        for key, item in parameters.items():
            if not isinstance(key, str) or not SAFE_ID.fullmatch(key) or FORBIDDEN_SEARCH_KEY.search(key):
                raise BacktestQueueError("parameters 包含禁止字段。")
            if isinstance(item, bool):
                continue
            if isinstance(item, (int, float)):
                _number(item, f"parameters.{key}", minimum=-1_000_000, maximum=1_000_000)
            elif not isinstance(item, str) or not 1 <= len(item) <= 128 or any(char in item for char in ("\n", "\r", "\x00")):
                raise BacktestQueueError("parameters 只允许有限标量。")
    evidence_hashes = manifest.get("evidence_hashes")
    if evidence_hashes is not None:
        if not isinstance(evidence_hashes, dict) or len(evidence_hashes) > 64:
            raise BacktestQueueError("evidence_hashes 无效。")
        for key, digest in evidence_hashes.items():
            if not isinstance(key, str) or not ArtifactStore.valid_key(key):
                raise BacktestQueueError("evidence_hashes 键无效。")
            _hash(digest, f"evidence_hashes.{key}")
    if "promotion_proposal" in manifest:
        manifest["promotion_proposal"] = validate_promotion_proposal(manifest["promotion_proposal"])
    for key in MANIFEST_FIELDS - {"inputs"}:
        if key in manifest and len(_json(manifest[key]).encode("utf-8")) > 16_384:
            raise BacktestQueueError(f"{key} 超过限制。")
    return manifest


def validate_candidate_manifest(manifest: dict[str, Any]) -> None:
    required = {
        "candidate_id", "candidate_version", "provenance", "hypothesis", "parent_version",
        "parent_job_id", "parent_manifest_sha256", "parent_result_sha256", "template_key",
        "asset_universe", "search_space", "experiment_budget", "evidence_hashes",
        "authority", "risk_contract", "validation_plan",
    }
    if required - set(manifest):
        raise BacktestQueueError("候选评估缺少来源、假设、父版本、搜索空间、预算或证据哈希。")
    if not isinstance(manifest["candidate_id"], str) or not SAFE_ID.fullmatch(manifest["candidate_id"]):
        raise BacktestQueueError("candidate_id 无效。")
    if not isinstance(manifest["candidate_version"], str) or not SAFE_ID.fullmatch(manifest["candidate_version"]):
        raise BacktestQueueError("candidate_version 无效。")
    try:
        provenance = validate_candidate_provenance(manifest["provenance"])
    except ValueError as exc:
        raise BacktestQueueError(str(exc)) from exc
    if not isinstance(manifest["hypothesis"], str) or not 1 <= len(manifest["hypothesis"].strip()) <= 2_000:
        raise BacktestQueueError("candidate hypothesis 无效。")
    parent_version = manifest["parent_version"]
    if parent_version is not None and (not isinstance(parent_version, str) or not SAFE_ID.fullmatch(parent_version)):
        raise BacktestQueueError("parent_version 无效。")
    parent_job_id = manifest.get("parent_job_id")
    parent_hash = manifest.get("parent_manifest_sha256")
    parent_result_hash = manifest.get("parent_result_sha256")
    if parent_version is not None:
        if not isinstance(parent_job_id, str) or not SAFE_ID.fullmatch(parent_job_id):
            raise BacktestQueueError("parent_job_id 无效。")
        _hash(parent_hash, "parent_manifest_sha256")
        _hash(parent_result_hash, "parent_result_sha256")
    else:
        if any(value is not None for value in (parent_job_id, parent_hash, parent_result_hash)):
            raise BacktestQueueError("根候选不得伪造父任务或父结果。")
        template_key = manifest.get("template_key")
        if provenance["source"] != "approved_seed" or not isinstance(template_key, str) or not SAFE_ID.fullmatch(template_key):
            raise BacktestQueueError("根候选必须来自带 template_key 的人工批准种子。")
    evidence_hashes = manifest["evidence_hashes"]
    if not isinstance(evidence_hashes, dict) or not evidence_hashes:
        raise BacktestQueueError("candidate evidence_hashes 必须非空。")
    declared = {item["artifact_key"]: item["sha256"] for item in manifest["inputs"]}
    if any(declared.get(key) != digest for key, digest in evidence_hashes.items()):
        raise BacktestQueueError("candidate evidence_hashes 必须绑定冻结输入。")


def _validate_candidate_result_evidence(evidence: dict[str, Any], manifest: dict[str, Any]) -> None:
    required = {"kind", "hashes", "authority", "data_contract", "validation", "risk"}
    if set(evidence) != required:
        raise BacktestQueueError("候选证据字段不完整或包含未知字段。")
    authority = validate_authority(evidence["authority"])
    if authority != manifest["authority"]:
        raise BacktestQueueError("候选证据 authority 与冻结 manifest 不一致。", 409)
    data_contract = _as_object(evidence["data_contract"])
    if set(data_contract) != {"research_proxy", "actionable"} or data_contract["actionable"] is not False:
        raise BacktestQueueError("候选证据必须明确不可行动。")
    if data_contract["research_proxy"] is not manifest["asset_universe"]["research_proxy"]:
        raise BacktestQueueError("research_proxy 标记与 manifest 不一致。", 409)
    validation = _as_object(evidence["validation"])
    expected = {"dataset_end", "evaluation_date", "oos_passed", "walk_forward_passed", "cost_multipliers", "cost_1x_passed", "cost_2x_passed", "stress_passed", "multi_regime_passed", "minimum_trades_passed", "minimum_coverage_passed", "candidate_status", "trade_count", "coverage_days", "max_drawdown", "tail_stress_loss_pct", "market_regimes"}
    if set(validation) != expected:
        raise BacktestQueueError("候选 validation 证据字段无效。")
    if validation["dataset_end"] != manifest["dataset_end"] or validation["evaluation_date"] != manifest["evaluation_date"]:
        raise BacktestQueueError("候选 validation 日期未绑定 manifest。", 409)
    for key in ("oos_passed", "walk_forward_passed", "cost_1x_passed", "cost_2x_passed", "stress_passed", "multi_regime_passed", "minimum_trades_passed", "minimum_coverage_passed"):
        if not isinstance(validation[key], bool):
            raise BacktestQueueError(f"validation.{key} 必须为布尔值。")
    if validation["candidate_status"] not in {"rejected", "quarantine", "shadow"}:
        raise BacktestQueueError("候选只能为 rejected、quarantine 或 shadow。")
    if validation["candidate_status"] == "shadow" and not all(validation[key] for key in ("oos_passed", "walk_forward_passed", "cost_1x_passed", "cost_2x_passed", "stress_passed", "multi_regime_passed", "minimum_trades_passed", "minimum_coverage_passed")):
        raise BacktestQueueError("未通过独立门禁的候选不得进入 shadow。")
    if validation["cost_multipliers"] != [1.0, 2.0]:
        raise BacktestQueueError("候选证据必须包含固定 1x/2x 成本结果。")
    if not isinstance(validation["trade_count"], int) or isinstance(validation["trade_count"], bool) or validation["trade_count"] < 0:
        raise BacktestQueueError("validation.trade_count 无效。")
    if not isinstance(validation["coverage_days"], int) or isinstance(validation["coverage_days"], bool) or validation["coverage_days"] < 0:
        raise BacktestQueueError("validation.coverage_days 无效。")
    _number(validation["max_drawdown"], "validation.max_drawdown", maximum=1.0)
    _number(validation["tail_stress_loss_pct"], "validation.tail_stress_loss_pct", maximum=1.0)
    regimes = validation["market_regimes"]
    if not isinstance(regimes, list) or not all(isinstance(item, str) and SAFE_ID.fullmatch(item) for item in regimes):
        raise BacktestQueueError("validation.market_regimes 无效。")
    result_risk = validate_risk_contract(evidence["risk"])
    manifest_risk = manifest["risk_contract"]
    if result_risk["max_loss_amount"] > manifest_risk["max_loss_amount"] or result_risk["max_loss_pct_model_equity"] > manifest_risk["max_loss_pct_model_equity"]:
        raise BacktestQueueError("候选结果风险超过冻结风险预算。", 409)


def validate_result_shape(result: Any, row: dict[str, Any]) -> dict[str, Any]:
    value = _as_object(result)
    if len(_json(value).encode("utf-8")) > 64 * 1024:
        raise BacktestQueueError("结果过大。", 413)
    required = {"job_id", "manifest_sha256", "fencing_epoch", "input_hashes", "output_hashes", "evidence"}
    if required - set(value):
        raise BacktestQueueError("结果缺少任务绑定字段。")
    allowed = required | {"code_bundle_sha256", "promotion_proposal"}
    if set(value) - allowed:
        raise BacktestQueueError("结果包含未授权字段。")
    if value["job_id"] != row["id"] or value["manifest_sha256"] != row["manifest_sha256"]:
        raise BacktestQueueError("结果未绑定当前任务或 manifest。", 409)
    if not isinstance(value["fencing_epoch"], int) or isinstance(value["fencing_epoch"], bool) or value["fencing_epoch"] < 1:
        raise BacktestQueueError("结果 fencing epoch 无效。")
    if value["fencing_epoch"] != row["fencing_epoch"]:
        raise BacktestQueueError("结果 fencing epoch 不匹配。", 409)
    manifest = json.loads(row["manifest_json"])
    if value.get("code_bundle_sha256") != manifest["code_bundle_sha256"]:
        raise BacktestQueueError("结果 code bundle 不匹配。", 409)
    expected_inputs = {item["artifact_key"]: item["sha256"] for item in manifest["inputs"]}
    if value["input_hashes"] != expected_inputs:
        raise BacktestQueueError("结果输入哈希不匹配。", 409)
    output_hashes = value["output_hashes"]
    if not isinstance(output_hashes, dict) or not all(isinstance(key, str) and ArtifactStore.valid_key(key) and isinstance(digest, str) and SHA.fullmatch(digest) for key, digest in output_hashes.items()):
        raise BacktestQueueError("output_hashes 无效。")
    evidence = value["evidence"]
    kind = evidence.get("kind") if isinstance(evidence, dict) else None
    if kind not in {"research", "shadow"}:
        raise BacktestQueueError("结果必须明确标注为 research 或 shadow 证据。")
    if row["job_type"] == "candidate.evaluate.v1":
        hashes = evidence.get("hashes")
        if not isinstance(hashes, dict) or not hashes:
            raise BacktestQueueError("候选结果必须声明 evidence.hashes。")
        if any(not isinstance(key, str) or not ArtifactStore.valid_key(key) or output_hashes.get(key) != digest for key, digest in hashes.items()):
            raise BacktestQueueError("候选 evidence.hashes 必须绑定当前输出。")
        _validate_candidate_result_evidence(evidence, manifest)
    else:
        allowed_evidence = {"kind", "metrics", "limitations", "local_receipt"}
        if set(evidence) - allowed_evidence:
            raise BacktestQueueError("research evidence 包含未授权字段。")
    if "promotion_proposal" in value:
        if row["job_type"] != "candidate.evaluate.v1":
            raise BacktestQueueError("promotion_proposal 仅允许用于候选评估结果。", 403)
        validation = evidence["validation"]
        risk = evidence["risk"]
        if evidence["data_contract"]["research_proxy"] is True:
            raise BacktestQueueError("research proxy 不得提出用户发布晋级建议。", 403)
        if validation["candidate_status"] != "shadow" or not all(validation[key] is True for key in ("oos_passed", "walk_forward_passed", "cost_1x_passed", "cost_2x_passed", "stress_passed", "multi_regime_passed", "minimum_trades_passed", "minimum_coverage_passed")):
            raise BacktestQueueError("未通过验证门的候选不得提出晋级建议。", 403)
        if validation["trade_count"] < manifest["validation_plan"]["minimum_trades"] or validation["coverage_days"] < manifest["validation_plan"]["minimum_coverage_days"]:
            raise BacktestQueueError("样本覆盖不足，不能提出晋级建议。", 403)
        if validation["max_drawdown"] >= risk["quarantine_drawdown_pct"]:
            raise BacktestQueueError("候选已达到 quarantine 回撤线。", 403)
        value["promotion_proposal"] = validate_promotion_proposal(value["promotion_proposal"])

    def scan(item: Any, key: str | None = None) -> None:
        lowered = key.lower() if key else ""
        if lowered and re.search(r"(?:status|approv|active|official|live|publish|execution|actionable|user_visible)", lowered):
            raise BacktestQueueError("研究结果不得声明发布、执行或用户可见状态。")
        if isinstance(item, dict):
            for child_key, child in item.items():
                scan(child, str(child_key))
        elif isinstance(item, list):
            for child in item:
                scan(child)
        elif isinstance(item, str) and re.search(r"\b(?:approved|active|official|live)\b", item.lower()):
            raise BacktestQueueError("研究结果不得包含发布或交易状态。")

    if row["job_type"] != "candidate.evaluate.v1":
        scan(evidence)
    return value


def validate_failure(value: Any) -> dict[str, Any]:
    error = _as_object(value)
    if set(error) != {"error_code", "message", "retryable"}:
        raise BacktestQueueError("失败信息字段无效。")
    if not re.fullmatch(r"[A-Z0-9_]{1,64}", str(error["error_code"])):
        raise BacktestQueueError("error_code 无效。")
    if not isinstance(error["message"], str) or not 1 <= len(error["message"]) <= 512:
        raise BacktestQueueError("失败信息长度无效。")
    if not isinstance(error["retryable"], bool):
        raise BacktestQueueError("retryable 必须为布尔值。")
    return {
        "error_code": str(error["error_code"]),
        "message": "任务暂时失败，等待重试。" if error["retryable"] else "任务执行失败。",
        "retryable": error["retryable"],
    }
