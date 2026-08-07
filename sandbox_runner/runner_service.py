"""Loopback-only gateway that executes strategy code in an ephemeral Docker container."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
from threading import BoundedSemaphore


MAX_BODY = 70_000
IMAGE = os.getenv("SANDBOX_IMAGE", "ciclotrade-sandbox:1")
RUN_SLOTS = BoundedSemaphore(2)


def execute_in_container(source: str) -> dict:
    name = f"ciclotrade-sandbox-{secrets.token_hex(8)}"
    with tempfile.TemporaryDirectory(prefix="ciclotrade-sandbox-") as directory:
        root = Path(directory)
        root.chmod(0o755)
        strategy = root / "strategy.py"
        strategy.write_text(source, encoding="utf-8")
        strategy.chmod(0o444)
        command = [
            "docker", "run", "--rm", "--pull", "never", "--name", name,
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges", "--pids-limit", "64",
            "--memory", "256m", "--memory-swap", "256m", "--cpus", "0.5",
            "--ulimit", "nofile=64:64", "--user", "65534:65534",
            "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
            "-v", f"{strategy}:/work/strategy.py:ro", IMAGE,
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=6, check=False,
            )
        except subprocess.TimeoutExpired:
            try:
                subprocess.run(
                    ["docker", "rm", "-f", name], capture_output=True, timeout=3, check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            return {"status": "timeout", "error": "策略执行超过 5 秒限制。"}
    try:
        result = json.loads(completed.stdout[-16_384:])
    except (json.JSONDecodeError, TypeError):
        return {"status": "failed", "error": "沙箱没有返回有效结果。"}
    return result if isinstance(result, dict) else {"status": "failed", "error": "沙箱结果格式无效。"}


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        token = os.environ.get("TRADEAI_SANDBOX_TOKEN", "")
        supplied = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if len(token) < 32 or not hmac.compare_digest(supplied, token):
            self._respond(401, {"status": "failed", "error": "unauthorized"})
            return
        if self.path != "/run":
            self._respond(404, {"status": "failed", "error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        if not 1 <= length <= MAX_BODY:
            self._respond(413, {"status": "failed", "error": "payload_too_large"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            source = payload.get("code", "") if isinstance(payload, dict) else ""
        except (UnicodeDecodeError, json.JSONDecodeError):
            source = ""
        if not isinstance(source, str) or not source or len(source.encode("utf-8")) > 65_536:
            self._respond(400, {"status": "failed", "error": "invalid_code"})
            return
        if not RUN_SLOTS.acquire(blocking=False):
            self._respond(503, {"status": "queued", "error": "sandbox_busy"})
            return
        try:
            self._respond(200, execute_in_container(source))
        finally:
            RUN_SLOTS.release()

    def _respond(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:
        return


def main() -> None:
    if len(os.getenv("TRADEAI_SANDBOX_TOKEN", "")) < 32:
        raise SystemExit("TRADEAI_SANDBOX_TOKEN must contain at least 32 characters")
    ThreadingHTTPServer(("127.0.0.1", 8088), Handler).serve_forever()


if __name__ == "__main__":
    main()
