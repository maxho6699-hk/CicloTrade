"""Private storage for administrator-managed payment receiver QR images."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import secrets

from payment.proof_storage import MAX_PAYMENT_PROOF_BYTES, sanitize_payment_image


_KEY = re.compile(r"^[a-f0-9]{32}\.jpg$")
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_PUBLIC_ROOTS = (
    _REPOSITORY_ROOT / "static",
    _REPOSITORY_ROOT / "src" / "apps" / "web" / "public",
    _REPOSITORY_ROOT / "src" / "apps" / "web" / "dist",
)


@dataclass(frozen=True)
class StoredReceiverQr:
    storage_key: str
    sha256: str
    size: int


def receiver_asset_root() -> Path:
    configured = os.getenv("PAYMENT_RECEIVER_ASSET_DIR", "").strip()
    root = (
        Path(configured)
        if configured
        else _REPOSITORY_ROOT / "data" / "payment-receiver-assets"
    ).resolve()
    if root == _REPOSITORY_ROOT or any(root == public or public in root.parents for public in _PUBLIC_ROOTS):
        raise ValueError("收款二维码目录不能位于公开网站目录。")
    root.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        root.chmod(0o700)
    return root


def resolve_receiver_asset(storage_key: str) -> Path:
    key = str(storage_key or "").strip().lower()
    if not _KEY.fullmatch(key):
        raise ValueError("收款二维码存储标识无效。")
    root = receiver_asset_root()
    path = (root / key).resolve()
    if path.parent != root:
        raise ValueError("收款二维码路径无效。")
    return path


def store_receiver_qr(data: bytes, content_type: str = "image/jpeg") -> StoredReceiverQr:
    sanitized = sanitize_payment_image(data, str(content_type or "").lower())
    digest = hashlib.sha256(sanitized).hexdigest()
    for _ in range(3):
        key = f"{secrets.token_hex(16)}.jpg"
        path = resolve_receiver_asset(key)
        try:
            with path.open("xb") as handle:
                handle.write(sanitized)
            if os.name != "nt":
                path.chmod(0o600)
            return StoredReceiverQr(key, digest, len(sanitized))
        except FileExistsError:
            continue
    raise RuntimeError("无法建立收款二维码文件。")


def delete_receiver_qr(storage_key: str) -> None:
    try:
        resolve_receiver_asset(storage_key).unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def read_receiver_qr(storage_key: str, expected_sha256: str) -> bytes:
    digest = str(expected_sha256 or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("收款二维码摘要无效。")
    try:
        path = resolve_receiver_asset(storage_key)
        if not path.is_file() or path.stat().st_size > MAX_PAYMENT_PROOF_BYTES:
            raise ValueError("收款二维码不存在。")
        content = path.read_bytes()
    except OSError as exc:
        raise ValueError("收款二维码无法读取。") from exc
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("收款二维码完整性校验失败。")
    return content
