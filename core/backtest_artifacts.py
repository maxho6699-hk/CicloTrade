"""Immutable, job-scoped filesystem storage for canonical backtest artifacts."""
from __future__ import annotations

import errno
import hashlib
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Iterable


_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SHA = re.compile(r"^[0-9a-f]{64}$")


class ArtifactError(ValueError):
    pass


def artifact_limit(value: str | None = None) -> int:
    try:
        parsed = int(value if value is not None else os.getenv("TRADEAI_BACKTEST_ARTIFACT_MAX_BYTES", str(8 * 1024 * 1024)))
    except (TypeError, ValueError):
        return 8 * 1024 * 1024
    return min(max(parsed, 1024), 64 * 1024 * 1024)


class ArtifactStore:
    def __init__(self, root: str | Path | None = None, max_bytes: int | None = None):
        root_path = Path(root or os.getenv("TRADEAI_BACKTEST_ARTIFACT_DIR", "data/backtest-artifacts")).expanduser()
        missing: list[str] = []
        existing = root_path
        while not existing.exists():
            missing.append(existing.name)
            parent = existing.parent
            if parent == existing:
                break
            existing = parent
        resolved = existing.resolve(strict=existing.exists())
        for part in reversed(missing):
            resolved /= part
        self.root = resolved
        self.max_bytes = artifact_limit(str(max_bytes)) if max_bytes is not None else artifact_limit()

    @staticmethod
    def valid_key(value: str) -> bool:
        return bool(_KEY.fullmatch(str(value))) and value not in {".", ".."}

    @staticmethod
    def _part(value: str, label: str) -> str:
        if not ArtifactStore.valid_key(value):
            raise ArtifactError(f"{label} 无效。")
        return value

    def storage_key(self, job_id: str, direction: str, artifact_key: str, attempt_no: int = 0) -> str:
        job = self._part(str(job_id), "任务标识")
        key = self._part(str(artifact_key), "artifact_key")
        if direction not in {"input", "output"}:
            raise ArtifactError("artifact direction 无效。")
        if not isinstance(attempt_no, int) or isinstance(attempt_no, bool) or attempt_no < 0:
            raise ArtifactError("artifact attempt 无效。")
        return f"{job}/{direction}/a{attempt_no}--{key}"

    def _path(self, storage_key: str) -> Path:
        parts = storage_key.split("/")
        if len(parts) != 3:
            raise ArtifactError("存储键无效。")
        job = self._part(parts[0], "任务标识")
        direction = parts[1]
        stored_name = self._part(parts[2], "存储文件名")
        if direction not in {"input", "output"} or not re.fullmatch(r"a[0-9]+--[A-Za-z0-9][A-Za-z0-9._-]{0,127}", stored_name):
            raise ArtifactError("存储键无效。")
        safe = f"{job}/{direction}/{stored_name}"
        path = (self.root / safe).resolve()
        if self.root != path and self.root not in path.parents:
            raise ArtifactError("artifact 路径越界。")
        return path

    def create_temp(self) -> tuple[int, str]:
        self._mkdir_durable(self.root)
        return tempfile.mkstemp(prefix=".upload-", dir=self.root)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        if os.name != "posix":
            return
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def _mkdir_durable(self, path: Path) -> None:
        missing: list[Path] = []
        current = path
        while not current.exists():
            missing.append(current)
            if current.parent == current:
                break
            current = current.parent
        path.mkdir(parents=True, exist_ok=True)
        for directory in reversed(missing):
            self._fsync_directory(directory)
            self._fsync_directory(directory.parent)

    def _digest_file(self, path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as handle:
            while chunk := handle.read(64 * 1024):
                size += len(chunk)
                if size > self.max_bytes:
                    raise ArtifactError("artifact 超过大小限制。")
                digest.update(chunk)
        return digest.hexdigest(), size

    def finalize_temp(
        self,
        temporary: str | Path,
        job_id: str,
        direction: str,
        artifact_key: str,
        expected_sha256: str,
        attempt_no: int = 0,
    ) -> tuple[str, int]:
        if not _SHA.fullmatch(str(expected_sha256)):
            raise ArtifactError("artifact SHA-256 无效。")
        temporary_path = Path(temporary)
        try:
            resolved_temporary = temporary_path.resolve(strict=True)
        except OSError as exc:
            raise ArtifactError("artifact 临时文件不存在。") from exc
        if (
            temporary_path.is_symlink()
            or resolved_temporary.parent != self.root
            or not resolved_temporary.name.startswith(".upload-")
            or not resolved_temporary.is_file()
        ):
            raise ArtifactError("artifact 临时文件不属于受控存储。")
        temporary_path = resolved_temporary
        try:
            digest, size = self._digest_file(temporary_path)
            if digest != expected_sha256:
                raise ArtifactError("artifact SHA-256 不匹配。")
            key = self.storage_key(job_id, direction, artifact_key, attempt_no)
            destination = self._path(key)
            self._mkdir_durable(destination.parent)
            try:
                # A hard link is an atomic, exclusive publication of the fully
                # fsynced temporary file.  It never replaces an existing path.
                os.link(temporary_path, destination)
                self._fsync_directory(destination.parent)
            except OSError as exc:
                if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}:
                    raise ArtifactError("artifact 存储空间不足。") from exc
                if exc.errno not in {errno.EEXIST, errno.EACCES, errno.EPERM} or not destination.exists():
                    raise ArtifactError("artifact 无法原子冻结。") from exc
                existing_digest, existing_size = self._digest_file(destination)
                if existing_digest != digest or existing_size != size:
                    raise ArtifactError("artifact 已冻结且内容不同。")
            return key, size
        finally:
            try:
                temporary_path.unlink()
                self._fsync_directory(self.root)
            except FileNotFoundError:
                pass

    def write(
        self,
        job_id: str,
        direction: str,
        artifact_key: str,
        body: bytes,
        expected_sha256: str,
        attempt_no: int = 0,
    ) -> tuple[str, int]:
        if not isinstance(body, bytes) or len(body) > self.max_bytes:
            raise ArtifactError("artifact 超过大小限制。")
        try:
            fd, temporary = self.create_temp()
        except OSError as exc:
            if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}:
                raise ArtifactError("artifact 存储空间不足。") from exc
            raise ArtifactError("artifact 临时存储初始化失败。") from exc
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            return self.finalize_temp(temporary, job_id, direction, artifact_key, expected_sha256, attempt_no)
        except OSError as exc:
            if os.path.exists(temporary):
                os.unlink(temporary)
            if exc.errno in {errno.ENOSPC, getattr(errno, "EDQUOT", errno.ENOSPC)}:
                raise ArtifactError("artifact 存储空间不足。") from exc
            raise ArtifactError("artifact 写入失败。") from exc
        except Exception:
            if os.path.exists(temporary):
                os.unlink(temporary)
            raise

    def read(self, storage_key: str, expected_sha256: str | None = None) -> bytes:
        path = self._path(storage_key)
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ArtifactError("artifact 不存在。") from exc
        if len(data) > self.max_bytes:
            raise ArtifactError("artifact 超过大小限制。")
        if expected_sha256 is not None:
            if not _SHA.fullmatch(str(expected_sha256)) or hashlib.sha256(data).hexdigest() != expected_sha256:
                raise ArtifactError("artifact 完整性验证失败。")
        return data

    def reconcile_orphans(
        self,
        registered_storage_keys: Iterable[str],
        *,
        minimum_age_seconds: int = 3_600,
        now: float | None = None,
    ) -> dict[str, object]:
        """Remove only aged temporary or unregistered files inside this store."""
        if not isinstance(minimum_age_seconds, int) or isinstance(minimum_age_seconds, bool) or minimum_age_seconds < 60:
            raise ArtifactError("orphan cleanup 最小保留时间不得低于 60 秒。")
        registered = {str(key) for key in registered_storage_keys}
        current = time.time() if now is None else float(now)
        removed: list[str] = []
        self._mkdir_durable(self.root)
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.is_symlink():
                continue
            relative = path.relative_to(self.root).as_posix()
            temporary = path.parent == self.root and path.name.startswith(".upload-")
            valid_artifact = False
            if not temporary:
                try:
                    valid_artifact = self._path(relative) == path.resolve()
                except ArtifactError:
                    continue
            if not temporary and (not valid_artifact or relative in registered):
                continue
            try:
                age = current - path.stat().st_mtime
            except OSError:
                continue
            if age < minimum_age_seconds:
                continue
            try:
                path.unlink()
                self._fsync_directory(path.parent)
                removed.append(relative)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise ArtifactError("orphan cleanup 无法安全删除 artifact。") from exc
        for directory in sorted((path for path in self.root.rglob("*") if path.is_dir() and not path.is_symlink()), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                continue
            try:
                self._fsync_directory(directory.parent)
            except OSError as exc:
                raise ArtifactError("orphan cleanup 无法持久化目录删除。") from exc
        return {"removed": removed, "removed_count": len(removed)}
