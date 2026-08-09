"""Private, sanitized storage for browser-submitted payment evidence."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import hashlib
import os
from pathlib import Path
import re
import secrets
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError


MAX_PAYMENT_PROOF_BYTES = 4 * 1024 * 1024
MAX_PAYMENT_PROOF_PIXELS = 20_000_000
ALLOWED_PAYMENT_PROOF_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
_ALLOWED_IMAGE_FORMATS = frozenset({"JPEG", "PNG", "WEBP"})
_STORAGE_KEY = re.compile(r"^[a-f0-9]{32}\.jpg$")
_REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
_PUBLIC_ROOTS = (
    _REPOSITORY_ROOT / "static",
    _REPOSITORY_ROOT / "src" / "apps" / "web" / "public",
    _REPOSITORY_ROOT / "src" / "apps" / "web" / "dist",
)


@dataclass(frozen=True)
class StoredPaymentProof:
    storage_key: str
    sha256: str
    size: int


def payment_proof_root() -> Path:
    configured = os.getenv("PAYMENT_PROOF_DIR", "").strip()
    root = (
        Path(configured)
        if configured
        else Path(__file__).resolve().parents[1] / "data" / "payment-proofs"
    ).resolve()
    if root == _REPOSITORY_ROOT or any(root == public or public in root.parents for public in _PUBLIC_ROOTS):
        raise ValueError("付款凭证目录不能位于公开网站目录。")
    root.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        root.chmod(0o700)
    return root


def resolve_payment_proof(storage_key: str) -> Path:
    key = str(storage_key or "").strip().lower()
    if not _STORAGE_KEY.fullmatch(key):
        raise ValueError("付款凭证存储标识无效。")
    root = payment_proof_root()
    path = (root / key).resolve()
    if path.parent != root:
        raise ValueError("付款凭证路径无效。")
    return path


def sanitize_payment_image(data: bytes, content_type: str) -> bytes:
    if content_type not in ALLOWED_PAYMENT_PROOF_TYPES:
        raise ValueError("付款凭证只接受 JPG、PNG 或 WebP 图片。")
    if not 1 <= len(data) <= MAX_PAYMENT_PROOF_BYTES:
        raise ValueError("付款凭证图片必须小于 4 MB。")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as source:
                if source.format not in _ALLOWED_IMAGE_FORMATS:
                    raise ValueError("付款凭证图片格式无效。")
                if getattr(source, "is_animated", False):
                    raise ValueError("付款凭证不能使用动画图片。")
                width, height = source.size
                if width < 64 or height < 64 or width * height > MAX_PAYMENT_PROOF_PIXELS:
                    raise ValueError("付款凭证图片尺寸无效。")
                source.load()
                normalized = ImageOps.exif_transpose(source)
                if normalized.mode in {"RGBA", "LA"}:
                    background = Image.new("RGB", normalized.size, "white")
                    background.paste(normalized, mask=normalized.getchannel("A"))
                    normalized = background
                else:
                    normalized = normalized.convert("RGB")
                output = BytesIO()
                normalized.save(output, format="JPEG", quality=94, optimize=True, progressive=True)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning, UnidentifiedImageError, OSError) as exc:
        raise ValueError("付款凭证不是有效图片。") from exc
    sanitized = output.getvalue()
    if len(sanitized) > MAX_PAYMENT_PROOF_BYTES:
        raise ValueError("处理后的付款凭证图片仍超过 4 MB。")
    return sanitized


def store_payment_proof(data: bytes, content_type: str) -> StoredPaymentProof:
    sanitized = sanitize_payment_image(data, str(content_type or "").lower())
    digest = hashlib.sha256(sanitized).hexdigest()
    for _ in range(3):
        key = f"{secrets.token_hex(16)}.jpg"
        path = resolve_payment_proof(key)
        try:
            with path.open("xb") as handle:
                handle.write(sanitized)
            if os.name != "nt":
                path.chmod(0o600)
            return StoredPaymentProof(key, digest, len(sanitized))
        except FileExistsError:
            continue
    raise RuntimeError("无法建立付款凭证存储文件。")


def delete_payment_proof(storage_key: str) -> None:
    try:
        resolve_payment_proof(storage_key).unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def verify_payment_proof(storage_key: str, expected_sha256: str) -> bool:
    digest = str(expected_sha256 or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        return False
    try:
        path = resolve_payment_proof(storage_key)
        if not path.is_file() or path.stat().st_size > MAX_PAYMENT_PROOF_BYTES:
            return False
        return hashlib.sha256(path.read_bytes()).hexdigest() == digest
    except (OSError, ValueError):
        return False


def read_payment_proof(storage_key: str, expected_sha256: str) -> bytes:
    """Read one private image only when its stored digest still matches."""
    digest = str(expected_sha256 or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("付款图片摘要无效。")
    try:
        path = resolve_payment_proof(storage_key)
        if not path.is_file() or path.stat().st_size > MAX_PAYMENT_PROOF_BYTES:
            raise ValueError("付款图片不存在。")
        content = path.read_bytes()
    except OSError as exc:
        raise ValueError("付款图片无法读取。") from exc
    if hashlib.sha256(content).hexdigest() != digest:
        raise ValueError("付款图片完整性校验失败。")
    return content
