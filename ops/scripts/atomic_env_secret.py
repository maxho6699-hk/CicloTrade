#!/usr/bin/env python3
"""Atomically install one Compute Evidence secret from standard input.

This tool deliberately accepts only the two approved production environment
files.  It never accepts a directory, relative path, glob, or arbitrary root
file as a target.  The secret is read from stdin so it does not enter shell
history, process arguments, or this tool's output.
"""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import os
from pathlib import Path
import re
import stat
import tempfile
from typing import Mapping, Sequence

try:  # Linux-only account lookups; keep the module importable for offline tests.
    import grp
    import pwd
except ModuleNotFoundError:  # pragma: no cover - exercised only on non-POSIX hosts.
    grp = None
    pwd = None


MAX_ENV_BYTES = 1024 * 1024
MIN_SECRET_BYTES = 32
MAX_SECRET_BYTES = 1024
ENV_KEY = re.compile(r"[A-Z_][A-Z0-9_]*\Z")
ENV_SECRET = re.compile(rb"[A-Za-z0-9_-]+\Z")


@dataclass(frozen=True)
class TargetPolicy:
    path: Path
    owner: str
    group: str
    mode: int


TARGET_POLICIES: Mapping[str, TargetPolicy] = {
    "/opt/CicloTrade/.env": TargetPolicy(Path("/opt/CicloTrade/.env"), "root", "ciclotrade", 0o640),
    "/etc/ciclotrade-worker/compute-evidence.env": TargetPolicy(
        Path("/etc/ciclotrade-worker/compute-evidence.env"), "root", "root", 0o600
    ),
}


class SecretUpdateError(RuntimeError):
    """Safe-to-display error that never includes secret material."""


def update_secret(
    *,
    policy: TargetPolicy,
    owner: str,
    group: str,
    mode: str,
    key: str,
    secret: bytes,
    require_root: bool = True,
) -> Path | None:
    """Back up and atomically replace one env assignment; return the backup path."""
    _validate_request(policy, owner, group, mode, key, secret, require_root=require_root)
    target = policy.path
    _assert_safe_parent(target.parent)
    old = _read_regular_file(target)
    lines = _parse_environment(old)
    if sum(name == key for name, _ in lines) > 1:
        raise SecretUpdateError("environment file contains a duplicate key")
    replacement = key.encode("ascii") + b"=" + secret + b"\n"
    rendered = _render(lines, key, replacement)
    if len(rendered) > MAX_ENV_BYTES:
        raise SecretUpdateError("environment file would exceed its size limit")
    uid = _uid(policy.owner)
    gid = _gid(policy.group)
    if old is None:
        raise SecretUpdateError("approved target environment file does not exist")
    backup = _write_backup(target, old, uid, gid)
    _atomic_replace(target, rendered, uid, gid, policy.mode)
    return backup


def _validate_request(
    policy: TargetPolicy,
    owner: str,
    group: str,
    mode: str,
    key: str,
    secret: bytes,
    *,
    require_root: bool,
) -> None:
    if require_root and os.geteuid() != 0:
        raise SecretUpdateError("must be run as root")
    if owner != policy.owner or group != policy.group or mode != f"{policy.mode:04o}":
        raise SecretUpdateError("target ownership or mode is not approved")
    if not ENV_KEY.fullmatch(key):
        raise SecretUpdateError("environment key is invalid")
    if key != "TRADEAI_COMPUTE_EVIDENCE_SHARED_SECRET":
        raise SecretUpdateError("environment key is outside the fixed Compute Evidence contract")
    if not isinstance(secret, bytes) or not MIN_SECRET_BYTES <= len(secret) <= MAX_SECRET_BYTES:
        raise SecretUpdateError("secret length is outside the permitted range")
    # The exact bytes must survive both systemd EnvironmentFile parsing and
    # the application's deliberately simple dotenv parser.  Unquoted
    # base64url is the shared, unambiguous subset; accepting shell syntax,
    # whitespace, comments, escapes, padding, or arbitrary UTF-8 would let the
    # installer probe and the publisher sign with different byte strings.
    if ENV_SECRET.fullmatch(secret) is None:
        raise SecretUpdateError("secret must use unpadded base64url characters only")


def _assert_safe_parent(path: Path) -> None:
    if not path.is_absolute():
        raise SecretUpdateError("target parent is not absolute")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            item = os.lstat(current)
        except OSError as exc:
            raise SecretUpdateError("target parent cannot be inspected") from exc
        if stat.S_ISLNK(item.st_mode) or not stat.S_ISDIR(item.st_mode):
            raise SecretUpdateError("target parent is not a real directory")


def _read_regular_file(target: Path) -> bytes | None:
    try:
        listed = os.lstat(target)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SecretUpdateError("target cannot be inspected") from exc
    if stat.S_ISLNK(listed.st_mode) or not stat.S_ISREG(listed.st_mode) or listed.st_nlink != 1:
        raise SecretUpdateError("target is not a safe regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target, flags)
    except OSError as exc:
        raise SecretUpdateError("target cannot be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (listed.st_dev, listed.st_ino) or not stat.S_ISREG(opened.st_mode):
            raise SecretUpdateError("target changed while being opened")
        content = _read_limited(descriptor)
    finally:
        os.close(descriptor)
    if b"\x00" in content:
        raise SecretUpdateError("environment file contains a NUL byte")
    return content


def _read_limited(descriptor: int) -> bytes:
    pieces: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, 65_536)
        if not chunk:
            return b"".join(pieces)
        total += len(chunk)
        if total > MAX_ENV_BYTES:
            raise SecretUpdateError("environment file exceeds its size limit")
        pieces.append(chunk)


def _parse_environment(content: bytes | None) -> list[tuple[str | None, bytes]]:
    if content is None:
        return []
    lines: list[tuple[str | None, bytes]] = []
    for line in content.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            line += b"\n"
        match = re.match(rb"([A-Z_][A-Z0-9_]*)=", line)
        lines.append((match.group(1).decode("ascii") if match else None, line))
    if content and not content.endswith(b"\n") and not lines:
        raise SecretUpdateError("environment file is invalid")
    return lines


def _render(lines: list[tuple[str | None, bytes]], key: str, replacement: bytes) -> bytes:
    result: list[bytes] = []
    found = False
    for name, line in lines:
        if name == key:
            result.append(replacement)
            found = True
        else:
            result.append(line)
    if not found:
        result.append(replacement)
    return b"".join(result)


def _write_backup(target: Path, content: bytes, uid: int, gid: int) -> Path:
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.backup-", dir=target.parent)
    backup = Path(name)
    try:
        _write_and_sync(descriptor, content, uid, gid, 0o600)
    except BaseException:
        os.close(descriptor)
        backup.unlink(missing_ok=True)
        raise
    os.close(descriptor)
    return backup


def _atomic_replace(target: Path, content: bytes, uid: int, gid: int, mode: int) -> None:
    descriptor, name = tempfile.mkstemp(prefix=f".{target.name}.new-", dir=target.parent)
    temporary = Path(name)
    try:
        _write_and_sync(descriptor, content, uid, gid, mode)
        os.close(descriptor)
        os.replace(temporary, target)
        _fsync_directory(target.parent)
    except BaseException:
        try:
            os.close(descriptor)
        except OSError:
            pass
        temporary.unlink(missing_ok=True)
        raise


def _write_and_sync(descriptor: int, content: bytes, uid: int, gid: int, mode: int) -> None:
    os.fchmod(descriptor, mode)
    os.fchown(descriptor, uid, gid)
    offset = 0
    while offset < len(content):
        offset += os.write(descriptor, content[offset:])
    os.fsync(descriptor)


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    except OSError as exc:
        raise SecretUpdateError("target directory cannot be synced") from exc
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _uid(owner: str) -> int:
    if pwd is None:
        raise SecretUpdateError("approved owner lookup requires Linux")
    try:
        return pwd.getpwnam(owner).pw_uid
    except KeyError as exc:
        raise SecretUpdateError("approved owner does not exist") from exc


def _gid(group: str) -> int:
    if grp is None:
        raise SecretUpdateError("approved group lookup requires Linux")
    try:
        return grp.getgrnam(group).gr_gid
    except KeyError as exc:
        raise SecretUpdateError("approved group does not exist") from exc


def _secret_from_stdin() -> bytes:
    pieces: list[bytes] = []
    maximum = MAX_SECRET_BYTES + 2
    total = 0
    while total < maximum:
        chunk = os.read(0, maximum - total)
        if not chunk:
            break
        pieces.append(chunk)
        total += len(chunk)
    value = b"".join(pieces)
    if len(value) > MAX_SECRET_BYTES + 1:
        raise SecretUpdateError("secret length is outside the permitted range")
    if value.endswith(b"\n"):
        value = value[:-1]
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Atomically update one approved Compute Evidence env secret from stdin.")
    parser.add_argument("--target", choices=tuple(TARGET_POLICIES), required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--group", required=True)
    parser.add_argument("--mode", required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args(argv)
    try:
        update_secret(
            policy=TARGET_POLICIES[args.target],
            owner=args.owner,
            group=args.group,
            mode=args.mode,
            key=args.key,
            secret=_secret_from_stdin(),
        )
    except SecretUpdateError as exc:
        parser.exit(2, f"error: {exc}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
