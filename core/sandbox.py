# -*- coding: utf-8 -*-
"""Preflight validation and isolated strategy-runner submission."""

from __future__ import annotations

import ast
from datetime import datetime
from core.compat import UTC
import hashlib
import json
import os
from urllib import error, parse, request

from core.database import DatabaseManager, get_database
from core.plans import can


MAX_CODE_BYTES = 65_536
BLOCKED_MODULES = {"os", "sys", "subprocess", "socket", "pathlib", "shutil", "requests", "urllib", "http", "ftplib", "ctypes", "builtins", "importlib"}
BLOCKED_CALLS = {"open", "exec", "eval", "compile", "__import__", "input", "globals", "locals", "getattr", "setattr", "delattr"}
BLOCKED_METHODS = BLOCKED_CALLS | {"system", "popen", "connect", "urlopen", "request", "read_text", "write_text", "unlink", "remove", "rmdir", "walk"}


def validate_user_code(source: str) -> None:
    if not source or len(source.encode("utf-8")) > MAX_CODE_BYTES:
        raise ValueError("策略代碼必須介於 1 byte 與 64 KB。")
    try:
        tree = ast.parse(source, mode="exec")
    except SyntaxError as exc:
        raise ValueError(f"策略代碼語法錯誤：第 {exc.lineno or 0} 行。") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = {alias.name.split(".", 1)[0] for alias in node.names}
            if names & BLOCKED_MODULES:
                raise ValueError("策略代碼包含文件、網路或系統模組。")
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".", 1)[0] in BLOCKED_MODULES:
            raise ValueError("策略代碼包含文件、網路或系統模組。")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BLOCKED_CALLS:
            raise ValueError(f"策略代碼不得呼叫 {node.func.id}。")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in BLOCKED_METHODS:
            raise ValueError(f"策略代碼不得呼叫 {node.func.attr}。")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError("策略代碼不得存取雙底線屬性。")


class SandboxClient:
    def __init__(self, database: DatabaseManager | None = None):
        self.db = database or get_database()

    def submit(self, user_id: int, plan: str, source: str, filename: str = "strategy.py") -> dict:
        if not can(plan, "code_import"):
            raise PermissionError("策略代碼匯入僅限專業版與定制版。")
        validate_user_code(source)
        digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
        now = datetime.now(UTC).isoformat(timespec="seconds")
        existing = self.db.fetch_one(
            "SELECT id,status FROM signal_import_jobs WHERE user_id=? AND import_type='code' AND source_hash=?",
            (user_id, digest),
        )
        if existing:
            return {"job_id": existing["id"], "status": existing["status"], "created": False}
        with self.db.transaction() as conn:
            cursor = conn.execute(
                """INSERT INTO signal_import_jobs
                   (user_id,import_type,filename,status,row_count,error_message,report_json,source_hash,created_at,completed_at)
                   VALUES (?, 'code',?, 'quarantined',0,NULL,'{}',?,?,NULL)""",
                (user_id, filename[:160], digest, now),
            )
            job_id = int(cursor.lastrowid)
            conn.execute(
                "INSERT INTO strategy_code_submissions(job_id,source_code,syntax_valid,sandbox_status,created_at) VALUES (?,?,1,'not_configured',?)",
                (job_id, source, now),
            )
        endpoint = os.getenv("TRADEAI_SANDBOX_URL", "").strip()
        token = os.getenv("TRADEAI_SANDBOX_TOKEN", "").strip()
        if not endpoint:
            return {"job_id": job_id, "status": "quarantined", "sandbox": "not_configured", "created": True}
        parsed = parse.urlparse(endpoint)
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("沙箱服務必須使用 HTTPS 或本機回環位址。")
        if len(token) < 32:
            raise RuntimeError("沙箱服務 Token 尚未安全配置。")
        payload = json.dumps({"job_id": job_id, "code": source}, ensure_ascii=False).encode("utf-8")
        try:
            with request.urlopen(
                request.Request(endpoint, data=payload, headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"}, method="POST"),
                timeout=8,
            ) as response:
                result = json.loads(response.read(65_536).decode("utf-8"))
        except (error.URLError, TimeoutError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            self.db.execute(
                "UPDATE strategy_code_submissions SET sandbox_status='submission_failed' WHERE job_id=?",
                (job_id,),
            )
            raise RuntimeError("隔離沙箱暫時無法接收任務；代碼已保留在隔離佇列。") from exc
        remote_status = str(result.get("status", "queued")) if isinstance(result, dict) else "queued"
        remote_status = remote_status if remote_status in {"completed", "failed", "timeout", "queued"} else "failed"
        job_status = "validated" if remote_status == "completed" else "failed" if remote_status in {"failed", "timeout"} else "quarantined"
        error_message = str(result.get("error", ""))[:500] if isinstance(result, dict) else ""
        completed_at = datetime.now(UTC).isoformat(timespec="seconds") if job_status != "quarantined" else None
        with self.db.transaction() as conn:
            conn.execute(
                "UPDATE strategy_code_submissions SET sandbox_status=? WHERE job_id=?",
                (remote_status, job_id),
            )
            conn.execute(
                "UPDATE signal_import_jobs SET status=?,error_message=?,report_json=?,completed_at=? WHERE id=?",
                (job_status, error_message or None, json.dumps(result, ensure_ascii=False), completed_at, job_id),
            )
        return {"job_id": job_id, "status": job_status, "sandbox": remote_status, "created": True}
