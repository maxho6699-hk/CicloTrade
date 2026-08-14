#!/usr/bin/env python3
"""Fail-closed verification for a deterministic TradeAI Web release."""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile
from typing import Any
import unicodedata
import stat


SCHEMA = "tradeai.web-release.v1"
MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_TOTAL_MEMBER_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 10_000
REQUIRED_MIGRATIONS = ["0032_membership_promotions.sql", "0033_membership_promotion_settlement.sql", "0034_personal_paper.sql", "0035_entitlement_policy_versions.sql"]
REQUIRED_BACKTEST_MIGRATIONS = ["0012_expanded_research_receipts.sql", "0013_expanded_research_invalidations.sql"]
REQUIRED_BACKTEST_MIGRATION_PATHS = {
    f"migrations/backtest/{name}" for name in REQUIRED_BACKTEST_MIGRATIONS
}
EXPECTED_EXISTING_MIGRATIONS = ["0034_personal_paper.sql"]
LIFECYCLE = {"allowed_actions": ["restart"], "service": "ciclotrade-rewrite-api.service"}
EXACT_FILES = {"app.py", "asgi_app.py", "config.yaml", "requirements.txt"}
ALLOWED_PREFIXES = ("config/", "backtest/", "core/", "data/", "notification/", "payment/", "sandbox_runner/", "scheduler/", "strategies/", "strategy_client/", "trading/", "ui/", "src/apps/api/", "src/packages/contracts/", "src/apps/web/dist/")
HASH = re.compile(r"^[0-9a-f]{64}$")
COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
BLOB = re.compile(r"^[0-9a-f]{40,64}$")
TOP_LEVEL_KEYS = {"artifact", "files", "inputs", "lifecycle", "migrations", "runtime", "schema", "source", "source_date_epoch"}
FILE_KEYS = {"mode", "path", "sha256", "size", "source"}
SECRET_ASSIGNMENT = re.compile(rb"(?im)^\s*(?:(?:const|let|var)\s+)?['\"]?(?:[A-Z][A-Z0-9_]*(?:SECRET|TOKEN|PASSWORD|API_KEY)|(?:secret|token|password|api[_-]?key))['\"]?\s*[:=]\s*['\"]?(?!\$|<|example|replace|your|os\.|env\.)[^\s'\";,})]{8,}")
SENSITIVE_NAME = re.compile(r"(?:^|_)(?:secret|token|password|api[_-]?key)$", re.IGNORECASE)
SENSITIVE_COMPOSITE_NAMES = frozenset({"secret_key", "token_key", "password_hash", "secret_value"})


class ReleaseVerificationError(ValueError):
    """A release manifest or archive is malformed."""


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file_identity(path: Path, limit: int, message: str) -> tuple[int, int, int, int, int]:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise ReleaseVerificationError(message) from error
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size > limit:
        raise ReleaseVerificationError(message)
    return metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns, metadata.st_ctime_ns


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseVerificationError("manifest has duplicate JSON keys")
        result[key] = value
    return result


def read_manifest(path: Path) -> dict[str, Any]:
    _regular_file_identity(path, MAX_MEMBER_BYTES, "manifest must be a small regular file")
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates)
    except (UnicodeDecodeError, json.JSONDecodeError, ReleaseVerificationError) as error:
        raise ReleaseVerificationError("manifest is not valid JSON") from error
    if not isinstance(data, dict) or canonical_json(data) != raw:
        raise ReleaseVerificationError("manifest is not canonical JSON")
    return data


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseVerificationError("Git identity is unavailable") from error


def _git_blob_bytes(root: Path, blob: str) -> bytes:
    try:
        return subprocess.run(["git", "-C", str(root), "cat-file", "blob", blob], check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseVerificationError("Git blob is unavailable") from error


def _clean(root: Path) -> bool:
    try:
        return not _git(root, "status", "--porcelain=v1", "--untracked-files=all")
    except ReleaseVerificationError:
        return False


def _safe_relpath(name: str) -> str:
    if not isinstance(name, str) or not name or "\\" in name or re.match(r"^[A-Za-z]:", name):
        raise ReleaseVerificationError("unsafe archive path")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or str(path) != name or name.startswith("/"):
        raise ReleaseVerificationError("unsafe archive path")
    return name


def _collision_key(name: str) -> str:
    return unicodedata.normalize("NFKC", name).casefold()


def _forbidden_path(name: str) -> bool:
    normalized = unicodedata.normalize("NFKC", name).replace("\\", "/")
    lower = normalized.casefold()
    parts = lower.split("/")
    basename = parts[-1]
    if basename.endswith((".md", ".markdown")):
        return True
    if any(part in {"tests", "cache", "node_modules", "worker", "logs", "payment-proofs", "payment_proofs"} for part in parts):
        return True
    if lower.startswith("ops/opend/") or (
        lower.startswith("migrations/backtest/")
        and normalized not in REQUIRED_BACKTEST_MIGRATION_PATHS
    ) or "opend" in lower:
        return True
    if ".env" in basename or "worker" in basename or basename.endswith((".map", ".db", ".sqlite", ".sqlite3", ".wal", ".shm", ".log")):
        return True
    return any(value in basename for value in ("credential", "secret", "payment-proof", "payment_proof", "qr")) and not basename.endswith((".py", ".ts", ".tsx", ".js"))


def _allowed_release_path(name: str) -> bool:
    if name in EXACT_FILES:
        return True
    if name.startswith("migrations/"):
        if name in REQUIRED_BACKTEST_MIGRATION_PATHS:
            return True
        return name.count("/") == 1 and name.endswith(".sql")
    return name.startswith(ALLOWED_PREFIXES)


def _sensitive_python_name(name: str) -> bool:
    folded = name.casefold()
    return folded in SENSITIVE_COMPOSITE_NAMES or bool(SENSITIVE_NAME.search(folded))


def _python_assignment_names(target: ast.AST) -> list[str]:
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.List, ast.Tuple)):
        names: list[str] = []
        for item in target.elts:
            names.extend(_python_assignment_names(item))
        return names
    return []


def _contains_python_secret(content: bytes) -> bool:
    try:
        text = content.decode("utf-8", errors="strict")
        if text.startswith("\ufeff"):
            text = text[1:]
        tree = ast.parse(text)
    except (UnicodeDecodeError, SyntaxError):
        return True
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = [target for target in node.targets for target in _python_assignment_names(target)]
            value = node.value
        elif isinstance(node, ast.AnnAssign):
            targets = _python_assignment_names(node.target)
            value = node.value
        elif isinstance(node, ast.NamedExpr):
            targets = _python_assignment_names(node.target)
            value = node.value
        else:
            continue
        if not isinstance(value, ast.Constant) or not isinstance(value.value, (str, bytes)) or len(value.value) < 8:
            continue
        if any(_sensitive_python_name(target) for target in targets):
            return True
    return False


def _contains_secret(path: str, content: bytes) -> bool:
    if b"-----BEGIN " in content and b"PRIVATE KEY-----" in content:
        return True
    if Path(path).suffix.casefold() == ".py":
        return _contains_python_secret(content)
    return bool(SECRET_ASSIGNMENT.search(content))


def _input_hashes(root: Path) -> dict[str, Any]:
    tracked = _tracked_modes(root)
    requested = {
        "requirements_sha256": "requirements.txt",
        "web_package_lock_sha256": "src/apps/web/package-lock.json",
        "build_web_release.py": "ops/scripts/build_web_release.py",
        "verify_web_release.py": "ops/scripts/verify_web_release.py",
    }
    hashes: dict[str, str] = {}
    for name, path in requested.items():
        record = tracked.get(path)
        if record is None or record[1] not in {0o100644, 0o100755}:
            raise ReleaseVerificationError("manifest lock or build inputs mismatch")
        hashes[name] = hashlib.sha256(_git_blob_bytes(root, record[0])).hexdigest()
    return {
        "builders": {name: hashes[name] for name in ("build_web_release.py", "verify_web_release.py")},
        "requirements_sha256": hashes["requirements_sha256"],
        "web_package_lock_sha256": hashes["web_package_lock_sha256"],
    }


def _tracked_modes(root: Path) -> dict[str, tuple[str, int]]:
    try:
        raw = subprocess.run(["git", "-C", str(root), "ls-files", "-s", "-z"], check=True, capture_output=True).stdout
    except (OSError, subprocess.CalledProcessError) as error:
        raise ReleaseVerificationError("tracked source list is unavailable") from error
    result: dict[str, tuple[str, int]] = {}
    for item in raw.split(b"\0"):
        if not item:
            continue
        header, raw_path = item.split(b"\t", 1)
        mode, blob, stage = header.decode("ascii").split()
        if stage == "0":
            result[raw_path.decode("utf-8", errors="strict")] = (blob, int(mode, 8))
    return result


def _manifest_files(data: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    violations: list[str] = []
    files = data.get("files")
    if not isinstance(files, list) or not files:
        return [], ["manifest files must be a non-empty list"]
    result: list[dict[str, Any]] = []
    paths: set[str] = set()
    collisions: set[str] = set()
    prior = ""
    for item in files:
        if not isinstance(item, dict):
            violations.append("manifest file entry is invalid")
            continue
        if set(item) != FILE_KEYS:
            violations.append("manifest file entry has unexpected keys")
        try:
            name = _safe_relpath(item.get("path"))
        except ReleaseVerificationError as error:
            violations.append(str(error))
            continue
        if name in paths:
            violations.append("manifest has duplicate paths")
        paths.add(name)
        key = _collision_key(name)
        if key in collisions:
            violations.append("manifest has casefold or Unicode path collisions")
        collisions.add(key)
        if prior and name <= prior:
            violations.append("manifest files are not sorted")
        prior = name
        if _forbidden_path(name):
            violations.append("manifest contains forbidden release path")
        if not _allowed_release_path(name):
            violations.append("manifest contains a path outside the release allowlist")
        if type(item.get("size")) is not int or item["size"] < 0 or item["size"] > MAX_MEMBER_BYTES:
            violations.append("manifest file size is invalid")
        if type(item.get("mode")) is not int or item["mode"] not in {0o644, 0o755}:
            violations.append("manifest file mode is invalid")
        if not isinstance(item.get("sha256"), str) or not HASH.fullmatch(item["sha256"]):
            violations.append("manifest file hash is invalid")
        if not isinstance(item.get("source"), str) or not BLOB.fullmatch(item["source"]):
            violations.append("manifest source blob is invalid")
        result.append(item)
    return result, violations


def _validate_manifest_shape(data: dict[str, Any], root: Path) -> list[str]:
    violations: list[str] = []
    if set(data) != TOP_LEVEL_KEYS:
        violations.append("manifest has unexpected top-level keys")
    if data.get("schema") != SCHEMA:
        violations.append("manifest schema mismatch")
    artifact = data.get("artifact")
    if not isinstance(artifact, dict) or set(artifact) != {"file", "sha256", "size"} or not isinstance(artifact.get("file"), str) or not artifact["file"] or "/" in artifact["file"] or "\\" in artifact["file"] or not HASH.fullmatch(str(artifact.get("sha256", ""))) or type(artifact.get("size")) is not int or artifact["size"] < 0 or artifact["size"] > MAX_ARTIFACT_BYTES:
        violations.append("manifest artifact is invalid")
    source = data.get("source")
    if not isinstance(source, dict) or set(source) != {"baseline", "commit", "tree"}:
        violations.append("manifest source identity is invalid")
    elif not all(isinstance(source.get(key), str) and COMMIT.fullmatch(source[key]) for key in source):
        violations.append("manifest source identity is invalid")
    if data.get("lifecycle") != LIFECYCLE:
        violations.append("manifest lifecycle is not restricted to Rewrite API restart")
    if data.get("migrations") != {
        "expected_existing": EXPECTED_EXISTING_MIGRATIONS,
        "required": REQUIRED_MIGRATIONS,
        "required_backtest": REQUIRED_BACKTEST_MIGRATIONS,
    }:
        violations.append("manifest migration contract mismatch")
    if type(data.get("source_date_epoch")) is not int or data["source_date_epoch"] < 0:
        violations.append("manifest SOURCE_DATE_EPOCH is invalid")
    runtime = data.get("runtime")
    if not isinstance(runtime, dict) or set(runtime) != {"node", "npm", "python"} or not all(isinstance(value, str) and value for value in runtime.values()):
        violations.append("manifest runtime versions are invalid")
    inputs = data.get("inputs")
    if not isinstance(inputs, dict) or set(inputs) != {"builders", "requirements_sha256", "web_package_lock_sha256"}:
        return violations + ["manifest build inputs are invalid"]
    if not all(isinstance(inputs.get(name), str) and HASH.fullmatch(inputs[name]) for name in ("requirements_sha256", "web_package_lock_sha256")) or not isinstance(inputs.get("builders"), dict) or set(inputs["builders"]) != {"build_web_release.py", "verify_web_release.py"}:
        return violations + ["manifest build inputs are invalid"]
    try:
        expected_inputs = _input_hashes(root)
        if inputs != expected_inputs:
            violations.append("manifest lock or build inputs mismatch")
    except OSError:
        violations.append("manifest lock or build inputs mismatch")
    return violations


def _verify_identity(data: dict[str, Any], root: Path) -> list[str]:
    if not _clean(root):
        return ["source repository is not clean"]
    source = data.get("source")
    if not isinstance(source, dict):
        return ["manifest source identity is invalid"]
    violations: list[str] = []
    try:
        if source.get("commit") != _git(root, "rev-parse", "HEAD"):
            violations.append("source commit mismatch")
        if source.get("tree") != _git(root, "rev-parse", "HEAD^{tree}"):
            violations.append("source tree mismatch")
        baseline = _git(root, "rev-parse", "--verify", f"{source.get('baseline', '')}^{{commit}}")
        if baseline != source.get("baseline"):
            violations.append("source baseline mismatch")
        else:
            subprocess.run(["git", "-C", str(root), "merge-base", "--is-ancestor", baseline, "HEAD"], check=True, capture_output=True)
    except (ReleaseVerificationError, subprocess.CalledProcessError):
        violations.append("source baseline mismatch")
    return violations


def _gzip_metadata(artifact: Path, epoch: int) -> list[str]:
    try:
        with artifact.open("rb") as handle:
            header = handle.read(10)
    except OSError:
        return ["artifact is not a readable gzip tar archive"]
    if len(header) != 10 or header[:3] != b"\x1f\x8b\x08" or header[3] != 0 or int.from_bytes(header[4:8], "little") != epoch or header[8] != 2 or header[9] != 255:
        return ["gzip metadata is not deterministic"]
    return []


def _archive_members(artifact: Path, epoch: int) -> tuple[dict[str, tuple[bytes, int]], list[str]]:
    violations = _gzip_metadata(artifact, epoch)
    members: dict[str, tuple[bytes, int]] = {}
    collisions: set[str] = set()
    total = 0
    prior = ""
    if violations:
        return members, violations
    try:
        with tarfile.open(artifact, "r|gz") as archive:
            for index, member in enumerate(archive, start=1):
                if index > MAX_MEMBERS:
                    return members, violations + ["archive has too many members"]
                try:
                    name = _safe_relpath(member.name)
                except ReleaseVerificationError as error:
                    return members, violations + [str(error)]
                if prior and name <= prior:
                    violations.append("archive members are not sorted")
                prior = name
                if name in members:
                    violations.append("archive has duplicate paths")
                    continue
                key = _collision_key(name)
                if key in collisions:
                    violations.append("archive has casefold or Unicode path collisions")
                collisions.add(key)
                if not member.isfile() or member.issym() or member.islnk() or member.isdev() or member.isfifo():
                    violations.append("archive links or non-regular entries are forbidden")
                    continue
                if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                    return members, violations + ["archive member exceeds size limit"]
                total += member.size
                if total > MAX_TOTAL_MEMBER_BYTES:
                    return members, violations + ["archive exceeds expanded size limit"]
                if member.mode & 0o7000 or member.mode & 0o002 or member.mode & 0o777 not in {0o644, 0o755} or member.uid != 0 or member.gid != 0 or member.uname or member.gname or member.mtime != epoch or member.pax_headers:
                    violations.append("archive member metadata is not deterministic")
                handle = archive.extractfile(member)
                content = handle.read(member.size + 1) if handle else b""
                if len(content) != member.size:
                    return members, violations + ["archive member content is truncated"]
                if _forbidden_path(name):
                    violations.append("archive contains forbidden release path")
                if not _allowed_release_path(name):
                    violations.append("archive contains a path outside the release allowlist")
                if _contains_secret(name, content):
                    violations.append("archive contains a secret")
                members[name] = (content, member.mode & 0o777)
    except (OSError, EOFError, tarfile.TarError):
        violations.append("artifact is not a readable gzip tar archive")
    return members, violations


def _verify_dist(members: dict[str, tuple[bytes, int]]) -> list[str]:
    index = members.get("src/apps/web/dist/index.html")
    if index is None:
        return ["web dist index.html is missing"]
    try:
        text = index[0].decode("utf-8")
    except UnicodeDecodeError:
        return ["web dist index.html is invalid"]
    references = re.findall(r"(?:src|href)=[\"']/?(assets/[^\"'#?]+)", text, flags=re.IGNORECASE)
    if not references:
        return ["web dist is missing a hashed asset reference"]
    paths = {path.removeprefix("src/apps/web/dist/") for path in members}
    violations = ["web dist references a missing asset" for reference in references if reference not in paths]
    if not any(re.search(r"-[A-Za-z0-9_-]{8,}\.[A-Za-z0-9]+$", reference) for reference in references):
        violations.append("web dist is missing a hashed asset reference")
    return violations


def _release_safety_gate(root: Path, artifact: Path) -> list[str]:
    path = Path(__file__).resolve().with_name("verify_release_safety.py")
    try:
        spec = importlib.util.spec_from_file_location("release_safety_gate", path)
        if not spec or not spec.loader:
            raise ImportError
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return [f"release safety gate: {item}" for item in module.verify(root, artifact=artifact)]
    except Exception:
        return ["release safety artifact gate is unavailable"]


def verify_release(root: Path, artifact: Path, manifest: Path) -> list[str]:
    root, artifact, manifest = root.resolve(), artifact.absolute(), manifest.absolute()
    try:
        manifest_identity = _regular_file_identity(manifest, MAX_MEMBER_BYTES, "manifest must be a small regular file")
        data = read_manifest(manifest)
    except (OSError, ReleaseVerificationError) as error:
        return [str(error)]
    violations = _validate_manifest_shape(data, root)
    violations.extend(_verify_identity(data, root))
    files, file_violations = _manifest_files(data)
    violations.extend(file_violations)
    artifact_data = data.get("artifact") if isinstance(data.get("artifact"), dict) else {}
    try:
        artifact_identity = _regular_file_identity(artifact, MAX_ARTIFACT_BYTES, "artifact must be a bounded regular file")
    except ReleaseVerificationError:
        violations.append("artifact must be a bounded regular file")
        return sorted(set(violations))
    if artifact_data.get("file") != artifact.name or artifact_data.get("size") != artifact_identity[2] or artifact_data.get("sha256") != sha256_file(artifact):
        violations.append("artifact hash mismatch")
    members, archive_violations = _archive_members(artifact, data.get("source_date_epoch", -1))
    violations.extend(archive_violations)
    manifest_paths = {item["path"] for item in files if isinstance(item.get("path"), str)}
    if not REQUIRED_BACKTEST_MIGRATION_PATHS <= manifest_paths:
        violations.append("manifest is missing required backtest migration")
    if not REQUIRED_BACKTEST_MIGRATION_PATHS <= set(members):
        violations.append("archive is missing required backtest migration")
    if set(members) - manifest_paths:
        violations.append("archive has paths absent from manifest")
    if manifest_paths - set(members):
        violations.append("archive is missing manifest paths")
    tracked = _tracked_modes(root)
    for item in files:
        path = item.get("path")
        if not isinstance(path, str) or path not in members:
            continue
        content, mode = members[path]
        if item.get("sha256") != hashlib.sha256(content).hexdigest() or item.get("size") != len(content) or item.get("mode") != mode:
            violations.append("archive member does not match manifest")
        source = tracked.get(path)
        if source is None or source[0] != item.get("source") or (0o755 if source[1] == 0o100755 else 0o644) != item.get("mode"):
            violations.append("manifest file does not match tracked source")
        elif _git_blob_bytes(root, source[0]) != content:
            violations.append("archive member does not match tracked Git blob")
    violations.extend(_verify_dist(members))
    violations.extend(_release_safety_gate(root, artifact))
    violations.extend(_verify_identity(data, root))
    try:
        if _regular_file_identity(manifest, MAX_MEMBER_BYTES, "manifest must be a small regular file") != manifest_identity:
            violations.append("manifest changed during verification")
        if _regular_file_identity(artifact, MAX_ARTIFACT_BYTES, "artifact must be a bounded regular file") != artifact_identity:
            violations.append("artifact changed during verification")
    except ReleaseVerificationError:
        violations.append("manifest or artifact changed during verification")
    return sorted(set(violations))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify deterministic Web release tarball")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args(argv)
    violations = verify_release(args.root, args.artifact, args.manifest)
    if violations:
        print(json.dumps({"state": "rejected", "violations": violations}, separators=(",", ":")))
        return 2
    print('{"state":"accepted"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
