"""Owner-scoped AI research workspace.

This module deliberately treats the provider as an untrusted, optional
dependency.  It stores public task facts and server-issued citations, never
chain-of-thought, broker actions, or fabricated assistant text.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlparse

from core.database import DatabaseManager, get_database


STATUSES = ("queued", "running", "succeeded", "partial", "failed", "cancelled", "blocked", "timed_out")
ALLOWED_TOOLS = {"read_research", "read_quote", "read_candles", "read_portfolio", "create_paper_draft"}
REQUIRED_CONFIG = (
    "CICLO_AI_ENABLED",
    "CICLO_AI_ENDPOINT",
    "CICLO_AI_MODEL",
    "CICLO_AI_PROVIDER_VERSION",
    "CICLO_AI_CONTRACT_VERSION",
)
MAX_TEXT = 16_000
MAX_PROVIDER_BYTES = 256 * 1024


class AIWorkspaceError(RuntimeError):
    status_code = 400


class AIWorkspaceNotFound(AIWorkspaceError):
    status_code = 404


class AIWorkspaceIdempotencyConflict(AIWorkspaceError):
    status_code = 409


class AIWorkspaceValidationError(AIWorkspaceError):
    status_code = 400


class AIWorkspaceProviderError(AIWorkspaceError):
    status_code = 503


class Provider(Protocol):
    def complete(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _sha(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _public(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(18)}"


def _text(value: Any, field: str, *, required: bool = False) -> str:
    if not isinstance(value, str):
        if required:
            raise AIWorkspaceValidationError(f"{field} 必须是文本。")
        return ""
    result = value.strip()
    if required and not result:
        raise AIWorkspaceValidationError(f"{field} 不能为空。")
    if len(result) > MAX_TEXT:
        raise AIWorkspaceValidationError(f"{field} 过长。")
    return result


def _json(value: Any, field: str) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise AIWorkspaceValidationError(f"{field} 不是可保存的 JSON。") from exc


class HttpProvider:
    """Fixed-server-config provider adapter with bounded HTTPS transport."""

    def __init__(self, endpoint: str, *, timeout: float = 12.0, max_bytes: int = MAX_PROVIDER_BYTES):
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise AIWorkspaceProviderError("AI provider endpoint 必须是无凭据的 HTTPS 地址。")
        self.endpoint, self.timeout, self.max_bytes = endpoint, timeout, max_bytes

    def complete(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        body = json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read(self.max_bytes + 1)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AIWorkspaceProviderError("AI provider 当前不可用。") from exc
        if len(raw) > self.max_bytes:
            raise AIWorkspaceProviderError("AI provider 响应超过安全上限。")
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AIWorkspaceProviderError("AI provider 响应格式无效。") from exc
        if not isinstance(parsed, dict):
            raise AIWorkspaceProviderError("AI provider 响应必须是 JSON 对象。")
        return parsed


class OpenAIChatProvider:
    """Small OpenAI-compatible adapter for the free provider endpoints."""

    def __init__(
        self,
        *,
        name: str,
        endpoint: str,
        model: str,
        api_key: str,
        provider_version: str,
        contract_version: str,
        timeout: float = 12.0,
        max_bytes: int = MAX_PROVIDER_BYTES,
    ):
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise AIWorkspaceProviderError("AI provider endpoint 必须是无凭据的 HTTPS 地址。")
        if not model.strip() or not api_key.strip():
            raise AIWorkspaceProviderError("AI provider 缺少模型或 API key。")
        self.name = name
        self.endpoint = endpoint.rstrip("/")
        if not self.endpoint.endswith("/chat/completions"):
            self.endpoint += "/chat/completions"
        self.model = model.strip()
        self.api_key = api_key.strip()
        self.provider_version = provider_version
        self.contract_version = contract_version
        self.timeout = timeout
        self.max_bytes = max_bytes

    @staticmethod
    def _citation_allowlist(request: Mapping[str, Any]) -> set[str]:
        context = request.get("context", {})
        citations = context.get("citations", []) if isinstance(context, Mapping) else []
        return {
            item["citation_id"]
            for item in citations
            if isinstance(item, Mapping) and isinstance(item.get("citation_id"), str)
        }

    @staticmethod
    def _message_content(value: Any) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, Mapping) and set(value) == {"text"} and isinstance(value.get("text"), str):
            return value["text"]
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def _messages(self, request: Mapping[str, Any], allowlisted_citations: set[str]) -> list[dict[str, str]]:
        allowed_tools = request.get("allowed_tools", [])
        if not isinstance(allowed_tools, list):
            allowed_tools = []
        allowed_tools = sorted({tool for tool in allowed_tools if tool in ALLOWED_TOOLS})
        exact_keys = ["conclusion", "citations", "support", "counter", "risks", "next_steps", "tool_calls"]
        system = {
            "instruction": "只返回一个 JSON object，必须严格使用 required_output.exact_keys，不能增加、删除或改名。citations 必须是 required_output.citations 的非空子集。不要输出 Markdown、解释、思维链、下单指令或外部操作。",
            "citation_allowlist": sorted(allowlisted_citations),
            "allowed_tools": allowed_tools,
            "provider_version": self.provider_version,
            "contract_version": self.contract_version,
            "required_output": {
                "exact_keys": exact_keys,
                "citations": sorted(allowlisted_citations),
                "schema": {
                    "conclusion": "string 或非空 string[]",
                    "citations": "非空 citation_id[]",
                    "support": "string[]",
                    "counter": "string[]",
                    "risks": "string[]",
                    "next_steps": "string[]",
                    "tool_calls": "[] 或 {name,arguments}[]；name 只能来自 allowed_tools",
                },
                "example": {
                    "conclusion": "现有资料只支持形成待核验的研究结论。",
                    "citations": sorted(allowlisted_citations)[:1],
                    "support": [],
                    "counter": [],
                    "risks": ["数据可能延迟或不完整。"],
                    "next_steps": ["核验资料时间与来源。"],
                    "tool_calls": [],
                },
            },
        }
        context = request.get("context")
        if isinstance(context, Mapping):
            system["context"] = context
        memory = request.get("memory")
        if isinstance(memory, list):
            system["memory"] = memory
        messages = [{"role": "system", "content": json.dumps(system, ensure_ascii=False, separators=(",", ":"))}]
        history = request.get("messages", [])
        if isinstance(history, list):
            for item in history:
                if not isinstance(item, Mapping) or item.get("role") not in {"user", "assistant"}:
                    continue
                messages.append({"role": item["role"], "content": self._message_content(item.get("content", ""))})
        if len(messages) == 1:
            messages.append({"role": "user", "content": self._message_content(request.get("user_message", ""))})
        return messages

    def complete(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        allowlisted_citations = self._citation_allowlist(request)
        body = json.dumps(
            {
                "model": self.model,
                "messages": self._messages(request, allowlisted_citations),
                "response_format": {"type": "json_object"},
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                if not 200 <= int(getattr(response, "status", 200)) < 300:
                    raise AIWorkspaceProviderError("AI provider 当前不可用。")
                raw = response.read(self.max_bytes + 1)
        except AIWorkspaceProviderError:
            raise
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise AIWorkspaceProviderError("AI provider 当前不可用。") from exc
        if len(raw) > self.max_bytes:
            raise AIWorkspaceProviderError("AI provider 响应超过安全上限。")
        try:
            response_json = json.loads(raw.decode("utf-8"))
            content = response_json["choices"][0]["message"]["content"]
            result = json.loads(content) if isinstance(content, str) else content
        except (KeyError, IndexError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AIWorkspaceProviderError("AI provider 响应格式无效。") from exc
        if not isinstance(result, dict):
            raise AIWorkspaceProviderError("AI provider 响应必须是 JSON 对象。")
        citations = result.get("citations")
        if not isinstance(citations, list) or not citations or not all(isinstance(item, str) for item in citations):
            raise AIWorkspaceProviderError("AI provider 引用格式无效。")
        if any(item not in allowlisted_citations for item in citations):
            raise AIWorkspaceProviderError("AI provider 引用了未获服务端许可的来源。")
        tools = result.get("tool_calls", [])
        if not isinstance(tools, list):
            raise AIWorkspaceProviderError("AI provider 工具调用格式无效。")
        allowed_tools = request.get("allowed_tools", [])
        allowed_tools = set(allowed_tools) if isinstance(allowed_tools, list) else set()
        for call in tools:
            if not isinstance(call, Mapping) or call.get("name") not in ALLOWED_TOOLS or call.get("name") not in allowed_tools:
                raise AIWorkspaceProviderError("AI provider 请求了未允许的工具。")
        result["provider_version"] = self.provider_version
        result["contract_version"] = self.contract_version
        return result


class FailoverProvider:
    """Fixed free-provider order: GLM, 混元, then Agnes."""

    ORDER = ("GLM", "混元", "Agnes")

    def __init__(self, providers, *, alert: Callable[[str], Any] | None = None):
        available = {name: provider for name, provider in providers}
        self.providers = [(name, available[name]) for name in self.ORDER if name in available]
        self.provider_names = tuple(name for name, _ in self.providers)
        self.alert = alert

    def complete(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        for _, provider in self.providers:
            try:
                result = provider.complete(request)
                if not isinstance(result, Mapping):
                    raise AIWorkspaceProviderError("AI provider 响应必须是对象。")
                return result
            except (AIWorkspaceProviderError, TimeoutError, OSError):
                continue
        message = f"Ciclo AI 免费服务不可用：{' → '.join(self.ORDER)}"
        if self.alert is not None:
            try:
                self.alert(message)
            except Exception:
                pass
        raise AIWorkspaceProviderError("Ciclo AI 免费服务暂时不可用")


class AIWorkspaceService:
    def __init__(
        self,
        database: DatabaseManager | None = None,
        *,
        provider: Provider | Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        context_loader: Callable[[int, Mapping[str, Any]], str | Mapping[str, Any] | None] | None = None,
        now: Callable[[], str] = _iso,
    ):
        self.db = database or get_database()
        self._managed_provider_chain = provider is None
        self.provider = provider if provider is not None else self._build_default_provider()
        self.context_loader = context_loader
        self._now = now

    def _config(self) -> dict[str, str]:
        return {name: os.getenv(name, "").strip() for name in REQUIRED_CONFIG}

    def _alert_provider_unavailable(self, message: str) -> None:
        chat_id = os.getenv("CICLO_AI_ALERT_CHAT_ID", "").strip()
        if not chat_id:
            return
        try:
            from notification.telegram_bot import send_telegram

            send_telegram(message, chat_id=chat_id)
        except Exception:
            pass

    def _build_default_provider(self) -> FailoverProvider:
        config = self._config()
        candidates = (
            ("GLM", "CICLO_AI"),
            ("混元", "CICLO_AI_HUNYUAN"),
            ("Agnes", "CICLO_AI_AGNES"),
        )
        providers = []
        for name, prefix in candidates:
            endpoint = os.getenv(f"{prefix}_ENDPOINT", "").strip()
            model = os.getenv(f"{prefix}_MODEL", "").strip()
            api_key = os.getenv(f"{prefix}_API_KEY", "").strip()
            if endpoint and model and api_key:
                try:
                    providers.append((name, OpenAIChatProvider(
                        name=name,
                        endpoint=endpoint,
                        model=model,
                        api_key=api_key,
                        provider_version=config["CICLO_AI_PROVIDER_VERSION"],
                        contract_version=config["CICLO_AI_CONTRACT_VERSION"],
                    )))
                except AIWorkspaceProviderError:
                    continue
        return FailoverProvider(providers, alert=self._alert_provider_unavailable)

    def readiness(self) -> dict[str, Any]:
        config = self._config()
        missing = [name for name in REQUIRED_CONFIG if not config[name]]
        enabled = config["CICLO_AI_ENABLED"].lower() in {"1", "true", "yes", "on"}
        if config["CICLO_AI_ENABLED"] and not enabled and "CICLO_AI_ENABLED" not in missing:
            missing.append("CICLO_AI_ENABLED")
        endpoint = urlparse(config["CICLO_AI_ENDPOINT"]) if config["CICLO_AI_ENDPOINT"] else None
        if endpoint and (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password):
            missing.append("CICLO_AI_ENDPOINT")
        if not missing and self._managed_provider_chain and isinstance(self.provider, FailoverProvider):
            if self.provider.provider_names != FailoverProvider.ORDER:
                missing.append("CICLO_AI_PROVIDER_CHAIN")
            if not os.getenv("CICLO_AI_ALERT_CHAT_ID", "").strip():
                missing.append("CICLO_AI_ALERTING")
        if missing:
            return {"ready": False, "status": "unavailable", "missing": sorted(set(missing))}
        return {
            "ready": True,
            "status": "ready",
            "missing": [],
            "provider_version": config["CICLO_AI_PROVIDER_VERSION"],
            "contract_version": config["CICLO_AI_CONTRACT_VERSION"],
            "model": config["CICLO_AI_MODEL"],
        }

    def _require_owner(self, owner_id: int) -> int:
        if isinstance(owner_id, bool) or not isinstance(owner_id, int) or owner_id < 1:
            raise AIWorkspaceValidationError("账户身份无效。")
        found = self.db.fetch_one("SELECT id FROM users WHERE id=? AND is_active=1", (owner_id,))
        if not found:
            raise AIWorkspaceNotFound("账户不存在或已停用。")
        return owner_id

    def _session_row(self, owner_id: int, public_id: str) -> dict[str, Any]:
        row = self.db.fetch_one("SELECT * FROM ai_workspace_sessions WHERE owner_id=? AND public_id=?", (owner_id, public_id))
        if not row:
            raise AIWorkspaceNotFound("AI 会话不存在。")
        return row

    def _session_status(self, conn, owner_id: int, public_id: str) -> str:
        row = conn.execute(
            "SELECT event_type FROM ai_workspace_session_events WHERE owner_id=? AND session_public_id=? ORDER BY seq DESC LIMIT 1",
            (owner_id, public_id),
        ).fetchone()
        return "archived" if row and row[0] == "archived" else "active"

    def create_context_snapshot(self, owner_id: int, context: Mapping[str, Any], *, retention_until: str | None = None) -> dict[str, Any]:
        self._require_owner(owner_id)
        if not isinstance(context, Mapping):
            raise AIWorkspaceValidationError("context snapshot 必须是对象。")
        raw_page_context = context.get("page_context", {})
        if not isinstance(raw_page_context, Mapping):
            raise AIWorkspaceValidationError("page context 必须是对象。")
        allowed_page_context = {"route", "market", "symbol", "timeframe", "account_domain"}
        if set(raw_page_context) - allowed_page_context:
            raise AIWorkspaceValidationError("page context 包含未知字段。")
        page_context: dict[str, str] = {}
        for key, value in raw_page_context.items():
            normalized_value = _text(value, key)
            if len(normalized_value) > 512:
                raise AIWorkspaceValidationError(f"page context {key} 过长。")
            if normalized_value:
                page_context[key] = normalized_value
        citations = context.get("citations")
        if not isinstance(citations, list) or not citations:
            raise AIWorkspaceValidationError("context snapshot 必须包含服务端引用。")
        normalized: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in citations:
            if not isinstance(item, Mapping):
                raise AIWorkspaceValidationError("引用格式无效。")
            # The caller may provide a source label for the server adapter,
            # but the public citation id is always minted here.
            supplied_id = _text(item.get("citation_id"), "citation_id")
            citation_id = _public("cit")
            while citation_id in seen:
                citation_id = _public("cit")
            seen.add(citation_id)
            source_public_id = _text(item.get("source_public_id"), "source_public_id", required=True)
            source_version = item.get("source_version")
            if isinstance(source_version, bool) or not isinstance(source_version, int) or source_version < 1:
                raise AIWorkspaceValidationError("引用版本无效。")
            normalized.append({
                "citation_id": citation_id,
                "source_citation_label": supplied_id,
                "source_kind": _text(item.get("source_kind"), "source_kind", required=True),
                "source_public_id": source_public_id,
                "source_version": source_version,
                "title": _text(item.get("title"), "title", required=True),
                "observed_at": _text(item.get("observed_at"), "observed_at"),
                "available_at": _text(item.get("available_at"), "available_at"),
                "quote_at": _text(item.get("quote_at"), "quote_at"),
            })
        snapshot_id = _public("ctx")
        now = self._now()
        context_json = _json(
            {
                "page_context": page_context,
                "citations": normalized,
                "snapshot_version": 1,
            },
            "context",
        )
        digest = _sha(json.loads(context_json))
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO ai_workspace_context_snapshots(public_id,owner_id,snapshot_version,context_json,context_sha256,created_at,retention_until) VALUES (?,?,?,?,?,?,?)",
                (snapshot_id, owner_id, 1, context_json, digest, now, retention_until),
            )
            for item in normalized:
                conn.execute(
                    "INSERT INTO ai_workspace_citations(public_id,owner_id,snapshot_public_id,citation_kind,source_public_id,source_version,title,observed_at,available_at,quote_at,payload_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (item["citation_id"], owner_id, snapshot_id, item["source_kind"], item["source_public_id"], item["source_version"], item["title"], item["observed_at"] or None, item["available_at"] or None, item["quote_at"] or None, _json(item, "citation"), now),
                )
        return {"public_id": snapshot_id, "snapshot_version": 1, "citation_ids": [item["citation_id"] for item in normalized], "created_at": now}

    def create_session(self, owner_id: int, *, title: str = "新研究会话", context_snapshot_public_id: str | None = None, selectors: Mapping[str, Any] | None = None, idempotency_key: str) -> dict[str, Any]:
        self._require_owner(owner_id)
        if not 8 <= len(idempotency_key) <= 128:
            raise AIWorkspaceValidationError("缺少有效的 Idempotency-Key。")
        title = _text(title, "title") or "新研究会话"
        snapshot_id = context_snapshot_public_id.strip() if isinstance(context_snapshot_public_id, str) and context_snapshot_public_id.strip() else None
        selector_payload = dict(selectors) if isinstance(selectors, Mapping) else None
        if selector_payload is not None:
            allowed_selectors = {"route", "market", "symbol", "timeframe", "question"}
            if set(selector_payload) - allowed_selectors:
                raise AIWorkspaceValidationError("AI context selector 字段无效。")
            for key, value in selector_payload.items():
                if not isinstance(value, str) or len(value.strip()) > 512:
                    raise AIWorkspaceValidationError(f"AI context selector {key} 无效。")
        request = {"title": title, "context_snapshot_public_id": snapshot_id, "selectors": selector_payload}
        digest = _sha(request)
        existing = self.db.fetch_one("SELECT * FROM ai_workspace_sessions WHERE owner_id=? AND idempotency_key=?", (owner_id, idempotency_key))
        if existing:
            if existing["request_sha256"] != digest:
                raise AIWorkspaceIdempotencyConflict("会话幂等键已绑定其他请求。")
            return self._public_session(existing, self._session_status_from_db(owner_id, existing["public_id"]))
        if selectors is not None:
            if self.context_loader is None:
                raise AIWorkspaceProviderError("AI context loader 尚未接入。")
            try:
                loaded = self.context_loader(owner_id, dict(selectors))
            except AIWorkspaceError:
                raise
            except Exception as exc:
                raise AIWorkspaceProviderError("AI context loader 当前不可用。") from exc
            if isinstance(loaded, Mapping):
                loaded_snapshot = self.create_context_snapshot(owner_id, loaded)
                snapshot_id = loaded_snapshot["public_id"]
            elif isinstance(loaded, str) and loaded.strip():
                snapshot_id = loaded.strip()
            elif loaded is not None:
                raise AIWorkspaceProviderError("AI context loader 返回无效快照。")
        if snapshot_id and not self.db.fetch_one("SELECT public_id FROM ai_workspace_context_snapshots WHERE owner_id=? AND public_id=?", (owner_id, snapshot_id)):
            raise AIWorkspaceNotFound("context snapshot 不存在。")
        public_id, now = _public("ais"), self._now()
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO ai_workspace_sessions(public_id,owner_id,title,context_snapshot_public_id,idempotency_key,request_sha256,created_at) VALUES (?,?,?,?,?,?,?)", (public_id, owner_id, title, snapshot_id, idempotency_key, digest, now))
            self._insert_session_event(conn, owner_id, public_id, "created", {"title": title}, idempotency_key, digest, now)
        return {"public_id": public_id, "title": title, "status": "active", "context_snapshot_public_id": snapshot_id, "created_at": now, "messages": []}

    def _session_status_from_db(self, owner_id: int, public_id: str) -> str:
        row = self.db.fetch_one("SELECT event_type FROM ai_workspace_session_events WHERE owner_id=? AND session_public_id=? ORDER BY seq DESC LIMIT 1", (owner_id, public_id))
        return "archived" if row and row["event_type"] == "archived" else "active"

    def _insert_session_event(self, conn, owner_id: int, session_id: str, event_type: str, payload: Mapping[str, Any], idem: str, digest: str, now: str) -> None:
        seq = int(conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM ai_workspace_session_events WHERE owner_id=? AND session_public_id=?", (owner_id, session_id)).fetchone()[0])
        conn.execute("INSERT INTO ai_workspace_session_events(public_id,owner_id,session_public_id,seq,event_type,payload_json,idempotency_key,request_sha256,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (_public("ase"), owner_id, session_id, seq, event_type, _json(payload, "event"), idem, digest, now))

    def _public_session(self, row: Mapping[str, Any], status: str) -> dict[str, Any]:
        return {"public_id": row["public_id"], "title": row["title"], "status": status, "context_snapshot_public_id": row["context_snapshot_public_id"], "created_at": row["created_at"]}

    def list_sessions(self, owner_id: int) -> list[dict[str, Any]]:
        self._require_owner(owner_id)
        rows = self.db.fetch_all("SELECT * FROM ai_workspace_sessions WHERE owner_id=? ORDER BY created_at DESC,id DESC", (owner_id,))
        return [self._public_session(row, self._session_status_from_db(owner_id, row["public_id"])) for row in rows]

    def get_session(self, owner_id: int, public_id: str) -> dict[str, Any]:
        self._require_owner(owner_id)
        row = self._session_row(owner_id, public_id)
        result = self._public_session(row, self._session_status_from_db(owner_id, public_id))
        result["messages"] = self.db.fetch_all("SELECT public_id,role,content_json,created_at FROM ai_workspace_messages WHERE owner_id=? AND session_public_id=? ORDER BY id", (owner_id, public_id))
        for item in result["messages"]:
            item["content"] = json.loads(item.pop("content_json"))
        return result

    def archive_session(self, owner_id: int, public_id: str, idempotency_key: str) -> dict[str, Any]:
        self._require_owner(owner_id)
        self._session_row(owner_id, public_id)
        if not 8 <= len(idempotency_key) <= 128:
            raise AIWorkspaceValidationError("缺少有效的 Idempotency-Key。")
        digest = _sha({"session_public_id": public_id, "action": "archive"})
        existing = self.db.fetch_one("SELECT * FROM ai_workspace_session_events WHERE owner_id=? AND idempotency_key=?", (owner_id, idempotency_key))
        if existing:
            if existing["request_sha256"] != digest:
                raise AIWorkspaceIdempotencyConflict("归档幂等键已绑定其他请求。")
            return self.get_session(owner_id, public_id) | {"status": "archived"}
        with self.db.transaction() as conn:
            if self._session_status(conn, owner_id, public_id) == "active":
                self._insert_session_event(conn, owner_id, public_id, "archived", {"reason": "user_requested"}, idempotency_key, digest, self._now())
        return self.get_session(owner_id, public_id) | {"status": "archived"}

    def _task_public(self, row: Mapping[str, Any]) -> dict[str, Any]:
        return {key: row[key] for key in ("public_id", "session_public_id", "status", "blocked_reason", "error_code", "provider_version", "contract_version", "created_at", "updated_at") if key in row}

    def _insert_task_event(self, conn, owner_id: int, task_id: str, status: str, payload: Mapping[str, Any], now: str) -> None:
        seq = int(conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM ai_workspace_task_events WHERE owner_id=? AND task_public_id=?", (owner_id, task_id)).fetchone()[0])
        conn.execute("INSERT INTO ai_workspace_task_events(public_id,owner_id,task_public_id,seq,status,payload_json,created_at) VALUES (?,?,?,?,?,?,?)", (_public("ate"), owner_id, task_id, seq, status, _json(payload, "task event"), now))

    def _task_result(self, owner_id: int, task_id: str) -> dict[str, Any]:
        task = self.db.fetch_one("SELECT * FROM ai_workspace_tasks WHERE owner_id=? AND public_id=?", (owner_id, task_id))
        if not task:
            raise AIWorkspaceNotFound("AI 任务不存在。")
        assistant = self.db.fetch_one("SELECT public_id,content_json,created_at FROM ai_workspace_messages WHERE owner_id=? AND session_public_id=? AND role='assistant' AND id>(SELECT id FROM ai_workspace_messages WHERE public_id=?) ORDER BY id DESC LIMIT 1", (owner_id, task["session_public_id"], task["user_message_public_id"]))
        if assistant:
            payload = json.loads(assistant["content_json"])
            assistant_public = {"public_id": assistant["public_id"], "structured": payload.get("structured"), "created_at": assistant["created_at"]}
        else:
            assistant_public = None
        return {"task": self._task_public(task), "assistant": assistant_public, "blocked": task["status"] == "blocked"}

    def _context_for_session(self, owner_id: int, session: Mapping[str, Any]) -> dict[str, Any]:
        snapshot_id = session.get("context_snapshot_public_id")
        if not snapshot_id:
            return {"page_context": {}, "citations": []}
        snapshot = self.db.fetch_one(
            "SELECT context_json,context_sha256 FROM ai_workspace_context_snapshots WHERE owner_id=? AND public_id=?",
            (owner_id, snapshot_id),
        )
        if not snapshot:
            raise AIWorkspaceNotFound("context snapshot 不存在。")
        try:
            stored_context = json.loads(snapshot["context_json"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise AIWorkspaceProviderError("AI context snapshot 无法读取。") from exc
        if not isinstance(stored_context, dict) or _sha(stored_context) != snapshot["context_sha256"]:
            raise AIWorkspaceProviderError("AI context snapshot 完整性校验失败。")
        page_context = stored_context.get("page_context", {})
        if not isinstance(page_context, dict):
            raise AIWorkspaceProviderError("AI page context 格式无效。")
        rows = self.db.fetch_all("SELECT public_id,citation_kind,source_public_id,source_version,title,observed_at,available_at,quote_at,payload_json FROM ai_workspace_citations WHERE owner_id=? AND snapshot_public_id=? ORDER BY id", (owner_id, snapshot_id))
        return {
            "snapshot_public_id": snapshot_id,
            "page_context": page_context,
            "citations": [
                {
                    "citation_id": row["public_id"],
                    **{
                        key: row[key]
                        for key in (
                            "citation_kind",
                            "source_public_id",
                            "source_version",
                            "title",
                            "observed_at",
                            "available_at",
                            "quote_at",
                        )
                    },
                }
                for row in rows
            ],
        }

    def _memory_context(self, owner_id: int) -> list[dict[str, Any]]:
        """Return memory only when its latest grant matches the live policy.

        Memory authorization is intentionally fail-closed. A receipt is not
        reusable after a policy is upgraded, retired, tampered with, or
        revoked: it must match the highest policy version and hash currently
        published by the server before memory enters a provider request.
        """
        latest = self.db.fetch_one(
            """SELECT action,policy_type,policy_version,policy_sha256
               FROM account_data_authorization_receipts
               WHERE owner_id=? AND data_kind='ai_memory'
               ORDER BY id DESC LIMIT 1""",
            (owner_id,),
        )
        if not latest or latest.get("action") != "granted":
            return []
        if not self.db.fetch_one(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='account_data_policy_versions'"
        ):
            return []
        current = self.db.fetch_one(
            """SELECT policy_type,policy_version,policy_json,policy_sha256,status
               FROM account_data_policy_versions
               WHERE policy_type='account_data' AND data_kind='ai_memory'
               ORDER BY policy_version DESC LIMIT 1""",
        )
        if not current or current.get("status") != "published":
            return []
        try:
            policy_hash = _sha(json.loads(str(current["policy_json"])))
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
        if (
            latest.get("policy_type") != current.get("policy_type")
            or int(latest.get("policy_version", 0)) != int(current.get("policy_version", 0))
            or not hmac.compare_digest(str(latest.get("policy_sha256", "")), str(current.get("policy_sha256", "")))
            or not hmac.compare_digest(policy_hash, str(current.get("policy_sha256", "")))
        ):
            return []
        rows = self.db.fetch_all("SELECT public_id,memory_key,memory_json,expires_at FROM account_memory_entries WHERE owner_id=? ORDER BY id DESC LIMIT 20", (owner_id,))
        result = []
        for row in rows:
            try:
                result.append({"public_id": row["public_id"], "memory_key": row["memory_key"], "value": json.loads(row["memory_json"]), "expires_at": row["expires_at"]})
            except (TypeError, json.JSONDecodeError):
                continue
        return result

    def _provider_request(self, owner_id: int, session: Mapping[str, Any], content: str) -> dict[str, Any]:
        messages = self.db.fetch_all("SELECT role,content_json FROM ai_workspace_messages WHERE owner_id=? AND session_public_id=? ORDER BY id DESC LIMIT 20", (owner_id, session["public_id"]))
        return {
            "model": self._config()["CICLO_AI_MODEL"],
            "provider_version": self._config()["CICLO_AI_PROVIDER_VERSION"],
            "contract_version": self._config()["CICLO_AI_CONTRACT_VERSION"],
            "user_message": content,
            "messages": [{"role": row["role"], "content": json.loads(row["content_json"])} for row in reversed(messages)],
            "context": self._context_for_session(owner_id, session),
            "memory": self._memory_context(owner_id),
            "allowed_tools": sorted(ALLOWED_TOOLS),
        }

    def _call_provider(self, request: Mapping[str, Any]) -> Mapping[str, Any]:
        adapter = self.provider
        if adapter is None:
            adapter = self._build_default_provider()
        try:
            result = adapter(request) if callable(adapter) else adapter.complete(request)
        except AIWorkspaceError:
            raise
        except TimeoutError:
            raise
        except Exception as exc:
            raise AIWorkspaceProviderError("AI provider 当前不可用。") from exc
        if not isinstance(result, Mapping):
            raise AIWorkspaceProviderError("AI provider 响应必须是对象。")
        return result

    def _normalize_provider(self, raw: Mapping[str, Any], citations: set[str], config: Mapping[str, str]) -> dict[str, Any]:
        forbidden = {"reasoning", "chain_of_thought", "chain_of_thought_text", "internal_thoughts", "analysis_trace"}
        if forbidden.intersection(raw):
            raise AIWorkspaceProviderError("AI provider 返回了禁止保存的内部思维字段。")
        allowed_fields = {"provider_version", "contract_version", "conclusion", "citations", "support", "counter", "risks", "next_steps", "tool_calls"}
        if set(raw) - allowed_fields:
            raise AIWorkspaceProviderError("AI provider 返回了未声明字段。")
        if raw.get("provider_version") != config["CICLO_AI_PROVIDER_VERSION"] or raw.get("contract_version") != config["CICLO_AI_CONTRACT_VERSION"]:
            raise AIWorkspaceProviderError("AI provider 版本不匹配。")
        top_ids = raw.get("citations", [])
        if not isinstance(top_ids, list) or not all(isinstance(item, str) for item in top_ids):
            raise AIWorkspaceProviderError("AI provider 引用格式无效。")
        if not top_ids or any(item not in citations for item in top_ids):
            raise AIWorkspaceProviderError("AI provider 引用了未获服务端许可的来源。")

        def section(name: str, *, required: bool = True) -> dict[str, Any]:
            value = raw.get(name)
            if isinstance(value, str):
                values = [value.strip()] if value.strip() else []
            elif isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value):
                values = [item.strip() for item in value]
            else:
                raise AIWorkspaceProviderError(f"AI provider {name} 格式无效。")
            if required and not values:
                raise AIWorkspaceProviderError(f"AI provider 缺少 {name}。")
            return {"text": values[0] if len(values) == 1 else values, "citation_ids": top_ids}

        structured = {"conclusion": section("conclusion"), "citations": top_ids, "support": section("support", required=False), "counter": section("counter", required=False), "risks": section("risks", required=False), "next_steps": section("next_steps", required=False)}
        tools = raw.get("tool_calls", [])
        if tools is not None:
            if not isinstance(tools, list):
                raise AIWorkspaceProviderError("AI provider 工具调用格式无效。")
            normalized_tools = []
            for call in tools:
                if not isinstance(call, Mapping) or call.get("name") not in ALLOWED_TOOLS:
                    raise AIWorkspaceProviderError("AI provider 请求了未允许的工具。")
                arguments = call.get("arguments", {})
                if not isinstance(arguments, Mapping):
                    raise AIWorkspaceProviderError("AI provider 工具参数无效。")
                normalized_tools.append({"name": call["name"], "arguments": dict(arguments)})
            structured["tool_calls"] = normalized_tools
        return structured

    def _save_drafts(self, conn, owner_id: int, session_id: str, task_id: str, structured: Mapping[str, Any], now: str) -> None:
        for call in structured.get("tool_calls", []):
            if call["name"] != "create_paper_draft":
                continue
            payload = {"kind": "paper_draft_handoff", "arguments": call["arguments"], "requires_user_confirmation": True, "submitted": False}
            encoded = _json(payload, "paper draft")
            conn.execute("INSERT INTO ai_workspace_paper_drafts(public_id,owner_id,session_public_id,task_public_id,draft_json,draft_sha256,created_at) VALUES (?,?,?,?,?,?,?)", (_public("draft"), owner_id, session_id, task_id, encoded, _sha(payload), now))

    def submit_message(self, owner_id: int, session_public_id: str, content: str, idempotency_key: str) -> dict[str, Any]:
        self._require_owner(owner_id)
        session = self._session_row(owner_id, session_public_id)
        if self._session_status_from_db(owner_id, session_public_id) != "active":
            raise AIWorkspaceValidationError("已归档的 AI 会话不能继续写入。")
        content = _text(content, "content", required=True)
        if not 8 <= len(idempotency_key) <= 128:
            raise AIWorkspaceValidationError("缺少有效的 Idempotency-Key。")
        fingerprint = _sha({"session_public_id": session_public_id, "content": content})
        existing = self.db.fetch_one("SELECT public_id,request_fingerprint FROM ai_workspace_tasks WHERE owner_id=? AND idempotency_key=?", (owner_id, idempotency_key))
        if existing:
            if existing["request_fingerprint"] != fingerprint:
                raise AIWorkspaceIdempotencyConflict("消息幂等键已绑定其他请求。")
            return self._task_result(owner_id, existing["public_id"])
        now = self._now()
        message_id, task_id = _public("aim"), _public("ait")
        user_payload = {"text": content}
        config = self._config()
        with self.db.transaction() as conn:
            conn.execute("INSERT INTO ai_workspace_messages(public_id,owner_id,session_public_id,role,content_json,content_sha256,created_at) VALUES (?,?,?,?,?,?,?)", (message_id, owner_id, session_public_id, "user", _json(user_payload, "message"), _sha(user_payload), now))
            conn.execute("INSERT INTO ai_workspace_tasks(public_id,owner_id,session_public_id,user_message_public_id,status,request_fingerprint,idempotency_key,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)", (task_id, owner_id, session_public_id, message_id, "queued", fingerprint, idempotency_key, now, now))
            self._insert_task_event(conn, owner_id, task_id, "queued", {"message_public_id": message_id}, now)
        readiness = self.readiness()
        if not readiness["ready"]:
            with self.db.transaction() as conn:
                conn.execute("UPDATE ai_workspace_tasks SET status='blocked',blocked_reason=?,updated_at=? WHERE owner_id=? AND public_id=?", ("provider_unavailable", self._now(), owner_id, task_id))
                self._insert_task_event(conn, owner_id, task_id, "blocked", {"reason": "provider_unavailable", "missing": readiness["missing"]}, self._now())
            return self._task_result(owner_id, task_id)
        with self.db.transaction() as conn:
            conn.execute("UPDATE ai_workspace_tasks SET status='running',updated_at=? WHERE owner_id=? AND public_id=?", (self._now(), owner_id, task_id))
            self._insert_task_event(conn, owner_id, task_id, "running", {}, self._now())
        try:
            raw = self._call_provider(self._provider_request(owner_id, session, content))
            context = self._context_for_session(owner_id, session)
            structured = self._normalize_provider(raw, {item["citation_id"] for item in context["citations"]}, config)
        except TimeoutError:
            with self.db.transaction() as conn:
                conn.execute("UPDATE ai_workspace_tasks SET status='timed_out',error_code='provider_timeout',updated_at=? WHERE owner_id=? AND public_id=?", (self._now(), owner_id, task_id))
                self._insert_task_event(conn, owner_id, task_id, "timed_out", {"error_code": "provider_timeout"}, self._now())
            return self._task_result(owner_id, task_id)
        except AIWorkspaceProviderError as exc:
            with self.db.transaction() as conn:
                conn.execute("UPDATE ai_workspace_tasks SET status='failed',error_code='provider_rejected',updated_at=? WHERE owner_id=? AND public_id=?", (self._now(), owner_id, task_id))
                self._insert_task_event(conn, owner_id, task_id, "failed", {"error_code": "provider_rejected", "message": str(exc)}, self._now())
            return self._task_result(owner_id, task_id)
        with self.db.transaction() as conn:
            assistant_payload = {"structured": structured, "provider_version": config["CICLO_AI_PROVIDER_VERSION"], "contract_version": config["CICLO_AI_CONTRACT_VERSION"]}
            conn.execute("INSERT INTO ai_workspace_messages(public_id,owner_id,session_public_id,role,content_json,content_sha256,created_at) VALUES (?,?,?,?,?,?,?)", (_public("aim"), owner_id, session_public_id, "assistant", _json(assistant_payload, "assistant"), _sha(assistant_payload), self._now()))
            conn.execute("UPDATE ai_workspace_tasks SET status='succeeded',provider_version=?,contract_version=?,updated_at=? WHERE owner_id=? AND public_id=?", (config["CICLO_AI_PROVIDER_VERSION"], config["CICLO_AI_CONTRACT_VERSION"], self._now(), owner_id, task_id))
            self._save_drafts(conn, owner_id, session_public_id, task_id, structured, self._now())
            self._insert_task_event(conn, owner_id, task_id, "succeeded", {"citation_ids": structured["citations"]}, self._now())
        return self._task_result(owner_id, task_id)

    def get_task(self, owner_id: int, task_public_id: str) -> dict[str, Any]:
        self._require_owner(owner_id)
        return self._task_result(owner_id, task_public_id)["task"]

    def list_task_events(self, owner_id: int, task_public_id: str) -> list[dict[str, Any]]:
        self._require_owner(owner_id)
        if not self.db.fetch_one("SELECT public_id FROM ai_workspace_tasks WHERE owner_id=? AND public_id=?", (owner_id, task_public_id)):
            raise AIWorkspaceNotFound("AI 任务不存在。")
        rows = self.db.fetch_all("SELECT seq,status,payload_json,created_at FROM ai_workspace_task_events WHERE owner_id=? AND task_public_id=? ORDER BY seq", (owner_id, task_public_id))
        return [{"seq": row["seq"], "status": row["status"], "payload": json.loads(row["payload_json"]), "created_at": row["created_at"]} for row in rows]

    def cancel_task(self, owner_id: int, task_public_id: str, idempotency_key: str) -> dict[str, Any]:
        self._require_owner(owner_id)
        task = self.db.fetch_one("SELECT * FROM ai_workspace_tasks WHERE owner_id=? AND public_id=?", (owner_id, task_public_id))
        if not task:
            raise AIWorkspaceNotFound("AI 任务不存在。")
        digest = _sha({"task_public_id": task_public_id, "action": "cancel"})
        if task["cancel_idempotency_key"]:
            if task["cancel_idempotency_key"] != idempotency_key or task["cancel_request_sha256"] != digest:
                raise AIWorkspaceIdempotencyConflict("取消幂等键已绑定其他请求。")
            return self._task_public(task)
        if task["status"] in {"succeeded", "failed", "cancelled", "blocked", "timed_out"}:
            return self._task_public(task)
        now = self._now()
        with self.db.transaction() as conn:
            conn.execute("UPDATE ai_workspace_tasks SET status='cancelled',cancel_requested_at=?,cancel_idempotency_key=?,cancel_request_sha256=?,updated_at=? WHERE owner_id=? AND public_id=?", (now, idempotency_key, digest, now, owner_id, task_public_id))
            self._insert_task_event(conn, owner_id, task_public_id, "cancelled", {"reason": "user_requested"}, now)
        return self.get_task(owner_id, task_public_id)


__all__ = [
    "AIWorkspaceError", "AIWorkspaceIdempotencyConflict", "AIWorkspaceNotFound", "AIWorkspaceProviderError", "AIWorkspaceService", "AIWorkspaceValidationError", "ALLOWED_TOOLS", "FailoverProvider", "HttpProvider", "OpenAIChatProvider"
]
