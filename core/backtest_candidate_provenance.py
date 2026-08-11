"""Validation for bounded candidate provenance, including Compute Gate requests."""
from __future__ import annotations

import re
from typing import Any


SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def validate_candidate_provenance(value: Any) -> dict[str, str]:
    base = {"source", "generated_by"}
    compute = base | {"request_id", "request_sha256"}
    fields = frozenset(value) if isinstance(value, dict) else frozenset()
    if not isinstance(value, dict) or fields not in {frozenset(base), frozenset(compute)}:
        raise ValueError("candidate provenance 字段无效。")
    for name in ("source", "generated_by"):
        if not isinstance(value[name], str) or not SAFE_ID.fullmatch(value[name]):
            raise ValueError(f"candidate provenance.{name} 无效。")
    if value["source"] not in {"approved_seed", "autonomous_research", "parameter_search", "derived_candidate"}:
        raise ValueError("candidate provenance.source 不在允许范围。")
    if value["generated_by"] == "compute-gate":
        if fields != frozenset(compute):
            raise ValueError("Compute Gate provenance 必须绑定 request_id 与 request_sha256。")
        if not isinstance(value["request_id"], str) or not SAFE_ID.fullmatch(value["request_id"]):
            raise ValueError("candidate provenance.request_id 无效。")
        if not isinstance(value["request_sha256"], str) or not SHA256.fullmatch(value["request_sha256"]):
            raise ValueError("candidate provenance.request_sha256 无效。")
    elif fields != frozenset(base):
        raise ValueError("非 Compute Gate provenance 不得声明 Compute Gate request。")
    return dict(value)
