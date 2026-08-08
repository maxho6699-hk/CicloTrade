# -*- coding: utf-8 -*-
"""Loopback-only OpenD verification controls for the admin console."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import sys
import threading
from time import monotonic, sleep


_CONTROL_ADDRESS = ("127.0.0.1", 22222)
_DEFAULT_CAPTCHA_PATH = Path(
    "/opt/opend/.com.futunn.FutuOpenD/F3CNN/PicVerifyCode.png"
)
_CAPTCHA_RE = re.compile(r"^[A-Za-z0-9]{4}$")
_INPUT_COMMAND_RE = re.compile(r"^input_pic_verify_code -code=[A-Za-z0-9]{4}$")
_PNG_HEADER = b"\x89PNG\r\n\x1a\n"
_MAX_CAPTCHA_BYTES = 512 * 1024
_PROBE_CACHE_SECONDS = 5.0
_probe_cache: dict[tuple[str, int], tuple[float, "OpenDStatus"]] = {}
_probe_lock = threading.Lock()


class OpenDControlError(RuntimeError):
    """A safe, administrator-facing OpenD control error."""


@dataclass(frozen=True)
class OpenDStatus:
    state: str
    message: str

    @property
    def ready(self) -> bool:
        return self.state == "ready"


def clear_opend_probe_cache() -> None:
    with _probe_lock:
        _probe_cache.clear()


def probe_opend_status(
    host: str = "127.0.0.1", port: int = 11111, *, force: bool = False
) -> OpenDStatus:
    """Check OpenD in a killable child process and cache the result briefly."""
    key = (host, int(port))
    now = monotonic()
    with _probe_lock:
        cached = _probe_cache.get(key)
        if not force and cached and now - cached[0] < _PROBE_CACHE_SECONDS:
            return cached[1]

    environment = os.environ.copy()
    if os.name != "nt":
        environment["HOME"] = os.getenv("OPEND_CLIENT_HOME", "/var/lib/ciclotrade")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "data.opend_probe", host, str(port)],
            capture_output=True,
            text=True,
            timeout=2.0,
            check=False,
            env=environment,
        )
    except subprocess.TimeoutExpired:
        status = OpenDStatus("unavailable", "OpenD 响应超时，网站已自动使用备用行情。")
    except OSError:
        status = OpenDStatus("unavailable", "OpenD 状态检测暂不可用。")
    else:
        if result.returncode == 0 and result.stdout.strip() == "READY":
            status = OpenDStatus("ready", "OpenD 已连接，实时行情可用。")
        elif result.returncode == 2:
            status = OpenDStatus("verification_required", "OpenD 正在等待图形验证码。")
        else:
            status = OpenDStatus("unavailable", "OpenD 暂时无法连接，网站已自动使用备用行情。")

    with _probe_lock:
        _probe_cache[key] = (monotonic(), status)
    return status


class OpenDVerificationController:
    """Expose only the two OpenD captcha commands needed by administrators."""

    def __init__(self, captcha_path: Path | None = None) -> None:
        self._captcha_path = captcha_path or _DEFAULT_CAPTCHA_PATH

    @staticmethod
    def _receive(sock: socket.socket, wait_seconds: float = 0.8) -> str:
        chunks: list[bytes] = []
        deadline = monotonic() + wait_seconds
        while monotonic() < deadline and sum(map(len, chunks)) < 32_768:
            try:
                chunk = sock.recv(4096)
            except (TimeoutError, socket.timeout):
                break
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace").strip()

    def _exchange(self, command: str) -> str:
        if command != "req_pic_verify_code" and not _INPUT_COMMAND_RE.fullmatch(command):
            raise OpenDControlError("不允许执行此 OpenD 控制命令。")
        try:
            with socket.create_connection(_CONTROL_ADDRESS, timeout=2.0) as sock:
                sock.settimeout(0.4)
                self._receive(sock, 0.4)
                sock.sendall(f"{command}\r\n".encode("ascii"))
                return self._receive(sock)
        except OSError as exc:
            raise OpenDControlError("OpenD 验证服务暂时无法连接。") from exc

    def _read_captcha(self) -> tuple[tuple[int, int, str], bytes] | None:
        try:
            metadata = self._captcha_path.lstat()
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise OpenDControlError("网站服务暂时无法读取 OpenD 验证码。") from exc
        if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
            raise OpenDControlError("OpenD 验证码文件类型无效。")
        if not 0 < metadata.st_size <= _MAX_CAPTCHA_BYTES:
            raise OpenDControlError("OpenD 验证码图片大小无效。")
        try:
            image = self._captcha_path.read_bytes()
        except OSError as exc:
            raise OpenDControlError("网站服务暂时无法读取 OpenD 验证码。") from exc
        if not image.startswith(_PNG_HEADER):
            raise OpenDControlError("OpenD 返回的验证码图片无效。")
        fingerprint = (metadata.st_mtime_ns, len(image), hashlib.sha256(image).hexdigest())
        return fingerprint, image

    def request_captcha(self) -> bytes:
        previous = self._read_captcha()
        previous_fingerprint = previous[0] if previous else None
        response = self._exchange("req_pic_verify_code")
        if self._failed(response):
            raise OpenDControlError("OpenD 未能生成新的验证码，请稍后重试。")

        deadline = monotonic() + 5.0
        while monotonic() < deadline:
            current = self._read_captcha()
            if current and current[0] != previous_fingerprint:
                return current[1]
            sleep(0.2)
        raise OpenDControlError("OpenD 未生成新的验证码图片，请稍后重试。")

    def submit_captcha(self, code: str) -> str:
        normalized = code.strip()
        if not _CAPTCHA_RE.fullmatch(normalized):
            raise ValueError("请输入图片中的 4 位英文字母或数字。")
        response = self._exchange(f"input_pic_verify_code -code={normalized}")
        if self._failed(response):
            raise OpenDControlError("验证码未通过，请刷新图片后重新输入。")
        clear_opend_probe_cache()
        return "验证码已提交，OpenD 正在自动恢复实时行情。"

    @staticmethod
    def _failed(response: str) -> bool:
        lowered = response.casefold()
        return any(
            token in lowered
            for token in (
                "error",
                "fail",
                "invalid",
                "incorrect",
                "wrong",
                "错误",
                "失敗",
                "失败",
                "不正确",
                "不正確",
            )
        )
