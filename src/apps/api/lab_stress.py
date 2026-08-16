"""Thin HTTP contract for the Lab fixed-scenario stress engine.

`app.py` may attach `lab_stress` to its route table without importing any
account or execution concerns into the calculation module.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping

from starlette.requests import Request
from starlette.responses import JSONResponse

from core.lab_stress import LabStressError, handle_lab_stress, scenario_catalog

LAB_STRESS_PATH = "/api/rewrite/v1/lab/stress"
LAB_STRESS_CATALOG_PATH = "/api/rewrite/v1/lab/stress/catalog"


async def lab_stress_catalog(_: Request) -> JSONResponse:
    return JSONResponse(
        scenario_catalog(),
        status_code=200,
        headers={
            "Cache-Control": "private, no-store",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "Vary": "Cookie, Authorization",
        },
    )


async def lab_stress(
    request: Request,
    *,
    snapshot_provider: Callable[[Request], Mapping[str, Any] | Awaitable[Mapping[str, Any]]],
) -> JSONResponse:
    """Handle a POST body; authentication and snapshot ownership stay in app.py."""
    try:
        payload = await request.json()
    except Exception as exc:
        raise LabStressError("请求内容必须是 JSON 对象。") from exc
    if not isinstance(payload, dict):
        raise LabStressError("请求内容必须是 JSON 对象。")
    if set(payload) != {"scenario_key"}:
        raise LabStressError("压力测试请求只允许 scenario_key；持仓必须由服务端快照提供。")
    value = snapshot_provider(request)
    payload["snapshot"] = await value if hasattr(value, "__await__") else value
    try:
        result = handle_lab_stress(payload)
    except LabStressError:
        raise
    return JSONResponse(result, status_code=200)


def lab_stress_route_contract() -> dict[str, Any]:
    return {"path": LAB_STRESS_PATH, "methods": ["POST"], "handler": lab_stress, "authentication": "app_owned", "execution": False}


__all__ = ["LAB_STRESS_CATALOG_PATH", "LAB_STRESS_PATH", "LabStressError", "lab_stress", "lab_stress_catalog", "lab_stress_route_contract"]
