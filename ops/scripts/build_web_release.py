#!/usr/bin/env python3
"""Create a deterministic Web release tarball bound to one clean Git tree."""
from __future__ import annotations

import argparse
import gzip
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path, PurePosixPath
import platform
import stat
import subprocess
import tarfile
from typing import Any
import unicodedata


SCHEMA = "tradeai.web-release.v1"
MAX_MEMBER_BYTES = 32 * 1024 * 1024
REQUIRED_MIGRATIONS = (
    "0032_membership_promotions.sql",
    "0033_membership_promotion_settlement.sql",
    "0034_personal_paper.sql",
    "0035_entitlement_policy_versions.sql",
)
REQUIRED_BACKTEST_MIGRATIONS = ("0012_expanded_research_receipts.sql",)
REQUIRED_BACKTEST_MIGRATION_PATHS = frozenset(
    f"migrations/backtest/{name}" for name in REQUIRED_BACKTEST_MIGRATIONS
)
EXPECTED_EXISTING_MIGRATIONS = ("0034_personal_paper.sql",)
LIFECYCLE = {"allowed_actions": ["restart"], "service": "ciclotrade-rewrite-api.service"}
EXACT_FILES = frozenset({"app.py", "asgi_app.py", "config.yaml", "requirements.txt"})
ALLOWED_PREFIXES = (
    "config/", "backtest/", "core/", "data/", "notification/", "payment/", "sandbox_runner/",
    "scheduler/", "strategies/", "strategy_client/", "trading/", "ui/", "src/apps/api/",
    "src/packages/contracts/", "src/apps/web/dist/",
)
BUILDER_FILES = ("build_web_release.py", "verify_web_release.py")
INPUT_FILES = {
    "requirements_sha256": "requirements.txt",
    "web_package_lock_sha256": "src/apps/web/package-lock.json",
}


class ReleaseBuildError(ValueError):
    """The supplied checkout cannot safely produce a release."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(root: Path, *args: str) -> str:
    try:
        completed = subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseBuildError("Git identity is unavailable") from error
    return completed.stdout.decode("utf-8", errors="strict").strip()


def ensure_clean_checkout(root: Path) -> None:
    if _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise ReleaseBuildError("source repository is not clean")


def _tracked_records(root: Path) -> list[tuple[str, str, int]]:
    try:
        raw = subprocess.run(["git", "-C", str(root), "ls-files", "-s", "-z"], check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseBuildError("tracked source list is unavailable") from error
    records: list[tuple[str, str, int]] = []
    for item in raw.split(b"\0"):
        if not item:
            continue
        header, raw_path = item.split(b"\t", 1)
        mode, blob, stage = header.decode("ascii").split()
        if stage != "0":
            raise ReleaseBuildError("source index has unresolved entries")
        records.append((raw_path.decode("utf-8", errors="strict"), blob, int(mode, 8)))
    return records


def _git_blob_bytes(root: Path, blob: str) -> bytes:
    try:
        return subprocess.run(["git", "-C", str(root), "cat-file", "blob", blob], check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseBuildError("Git blob is unavailable") from error


def _is_forbidden(path: str) -> bool:
    normalized = unicodedata.normalize("NFKC", path).replace("\\", "/")
    lower = normalized.casefold()
    parts = lower.split("/")
    if any(part in {"tests", "cache", "node_modules", "worker", "logs", "payment-proofs", "payment_proofs"} for part in parts):
        return True
    if lower.startswith("ops/opend/") or (
        lower.startswith("migrations/backtest/")
        and normalized not in REQUIRED_BACKTEST_MIGRATION_PATHS
    ):
        return True
    name = parts[-1].casefold()
    if name.endswith((".md", ".markdown")):
        return True
    if ".env" in name or "worker" in name or name.endswith((".map", ".db", ".sqlite", ".sqlite3", ".wal", ".shm", ".log")):
        return True
    if any(word in name for word in ("credential", "secret", "payment-proof", "payment_proof", "qr")) and not name.endswith((".py", ".ts", ".tsx", ".js")):
        return True
    return "opend" in lower


def allowed_release_path(path: str) -> bool:
    if path in EXACT_FILES:
        return True
    if path.startswith("migrations/"):
        if path in REQUIRED_BACKTEST_MIGRATION_PATHS:
            return True
        return path.count("/") == 1 and path.endswith(".sql")
    return path.startswith(ALLOWED_PREFIXES)


def _release_records(root: Path) -> list[tuple[str, str, int]]:
    records = [(path, blob, mode) for path, blob, mode in _tracked_records(root) if allowed_release_path(path) and not _is_forbidden(path)]
    paths = {path for path, _, _ in records}
    required = {"app.py", "asgi_app.py", "requirements.txt", "src/apps/web/dist/index.html"}
    required.update(f"migrations/{name}" for name in REQUIRED_MIGRATIONS)
    required.update(REQUIRED_BACKTEST_MIGRATION_PATHS)
    if required - paths:
        raise ReleaseBuildError("release allowlist is missing required runtime inputs")
    ordered = sorted(records)
    collisions = {unicodedata.normalize("NFKC", path).casefold() for path, _, _ in ordered}
    if len(collisions) != len(ordered):
        raise ReleaseBuildError("release paths have casefold or Unicode collisions")
    return ordered


def _safe_source_file(root: Path, relative: str, git_mode: int) -> tuple[Path, int]:
    path = root / PurePosixPath(relative)
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ReleaseBuildError("tracked release entry is unavailable") from error
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ReleaseBuildError("release entries must be regular files")
    if metadata.st_size > MAX_MEMBER_BYTES:
        raise ReleaseBuildError("release entry exceeds maximum size")
    if git_mode not in {0o100644, 0o100755}:
        raise ReleaseBuildError("release entry has an unsafe Git mode")
    return path, 0o755 if git_mode == 0o100755 else 0o644


def _assert_source_snapshot(root: Path, commit: str, tree: str, records: list[tuple[str, str, int]]) -> None:
    ensure_clean_checkout(root)
    if _git(root, "rev-parse", "HEAD") != commit or _git(root, "rev-parse", "HEAD^{tree}") != tree:
        raise ReleaseBuildError("source identity changed during build")
    current = [(path, blob, mode) for path, blob, mode in _tracked_records(root) if allowed_release_path(path) and not _is_forbidden(path)]
    if current != records:
        raise ReleaseBuildError("source index changed during build")
    for relative, blob, git_mode in records:
        path, _ = _safe_source_file(root, relative, git_mode)
        if path.read_bytes() != _git_blob_bytes(root, blob):
            raise ReleaseBuildError("source repository bytes do not match Git blob")


def _runtime_version(command: str) -> str:
    try:
        if command == "node":
            return subprocess.run(["node", "--version"], check=True, capture_output=True, text=True).stdout.strip()
        if command == "npm":
            return subprocess.run(["npm", "--version"], check=True, capture_output=True, text=True).stdout.strip()
        return "unavailable"
    except (OSError, subprocess.CalledProcessError):
        return "unavailable"


def _source_date_epoch(root: Path, requested: int | None) -> int:
    if requested is not None:
        return requested
    raw = os.environ.get("SOURCE_DATE_EPOCH")
    if raw is not None:
        try:
            return int(raw)
        except ValueError as error:
            raise ReleaseBuildError("SOURCE_DATE_EPOCH must be an integer") from error
    return int(_git(root, "show", "-s", "--format=%ct", "HEAD"))


def _input_hashes(root: Path) -> dict[str, Any]:
    tracked = {path: (blob, mode) for path, blob, mode in _tracked_records(root)}
    requested = dict(INPUT_FILES)
    requested.update({name: f"ops/scripts/{name}" for name in BUILDER_FILES})
    hashes: dict[str, str] = {}
    for name, path in requested.items():
        record = tracked.get(path)
        if record is None or record[1] not in {0o100644, 0o100755}:
            raise ReleaseBuildError("release builder input is unavailable")
        hashes[name] = sha256_bytes(_git_blob_bytes(root, record[0]))
    return {
        "builders": {name: hashes[name] for name in BUILDER_FILES},
        "requirements_sha256": hashes["requirements_sha256"],
        "web_package_lock_sha256": hashes["web_package_lock_sha256"],
    }


def build_release(root: Path, artifact: Path, manifest: Path, *, baseline: str, source_date_epoch: int | None = None) -> dict[str, Any]:
    root, artifact, manifest = root.resolve(), artifact.resolve(), manifest.resolve()
    if artifact == manifest or artifact.suffixes[-2:] != [".tar", ".gz"]:
        raise ReleaseBuildError("artifact must be a distinct .tar.gz path")
    for output in (artifact, manifest):
        try:
            output.relative_to(root)
        except ValueError:
            continue
        raise ReleaseBuildError("release outputs must be outside the source checkout")
    ensure_clean_checkout(root)
    commit = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    baseline_commit = _git(root, "rev-parse", "--verify", f"{baseline}^{{commit}}")
    try:
        subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", baseline_commit, commit], check=True, capture_output=True)
    except subprocess.CalledProcessError as error:
        raise ReleaseBuildError("baseline is not an ancestor of the release commit") from error
    epoch = _source_date_epoch(root, source_date_epoch)
    if epoch < 0:
        raise ReleaseBuildError("SOURCE_DATE_EPOCH must be non-negative")
    records = _release_records(root)
    _assert_source_snapshot(root, commit, tree, records)
    inputs = _input_hashes(root)
    files: list[dict[str, Any]] = []
    artifact.parent.mkdir(parents=True, exist_ok=True)
    with artifact.open("wb") as destination:
        with gzip.GzipFile(filename="", mode="wb", fileobj=destination, mtime=epoch) as compressed:
            with tarfile.open(mode="w", fileobj=compressed, format=tarfile.USTAR_FORMAT) as archive:
                for relative, blob, git_mode in records:
                    path, mode = _safe_source_file(root, relative, git_mode)
                    content = _git_blob_bytes(root, blob)
                    if path.read_bytes() != content:
                        raise ReleaseBuildError("source repository bytes do not match Git blob")
                    info = tarfile.TarInfo(relative)
                    info.size, info.mode, info.mtime = len(content), mode, epoch
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    archive.addfile(info, BytesIO(content))
                    files.append({"mode": mode, "path": relative, "sha256": sha256_bytes(content), "size": len(content), "source": blob})
    _assert_source_snapshot(root, commit, tree, records)
    data: dict[str, Any] = {
        "artifact": {"file": artifact.name, "sha256": sha256_file(artifact), "size": artifact.stat().st_size},
        "files": files,
        "inputs": inputs,
        "lifecycle": LIFECYCLE,
        "migrations": {
            "expected_existing": list(EXPECTED_EXISTING_MIGRATIONS),
            "required": list(REQUIRED_MIGRATIONS),
            "required_backtest": list(REQUIRED_BACKTEST_MIGRATIONS),
        },
        "runtime": {"node": _runtime_version("node"), "npm": _runtime_version("npm"), "python": platform.python_version()},
        "schema": SCHEMA,
        "source": {"baseline": baseline_commit, "commit": commit, "tree": tree},
        "source_date_epoch": epoch,
    }
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_bytes(canonical_json(data))
    _assert_source_snapshot(root, commit, tree, records)
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build deterministic Web release tarball")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--baseline", required=True)
    args = parser.parse_args(argv)
    try:
        data = build_release(args.root, args.artifact, args.manifest, baseline=args.baseline)
    except (ReleaseBuildError, OSError, ValueError):
        print('{"state":"rejected"}')
        return 2
    print(json.dumps({"artifact": data["artifact"], "state": "built"}, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
