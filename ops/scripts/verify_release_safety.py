#!/usr/bin/env python3
"""Fail-closed static release gate for the zero-touch OpenD boundary.

The command only examines local source/artifact bytes.  It never contacts a
server, invokes a service manager, or emits deployment connection details.
"""
from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import stat
import subprocess
import tarfile
from typing import Callable, Iterable
import unicodedata
import zipfile


MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_MEMBER_BYTES = 32 * 1024 * 1024
MAX_TOTAL_MEMBER_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 10_000
ALLOWED_SERVICE = "ciclotrade-rewrite-api.service"
ALLOWED_ACTION = "restart"
FORBIDDEN_COMPONENT = re.compile(
    r"(?:\bfutu\s*-?\s*opend\b|\bfutuopend\b|\bopend\b)", re.IGNORECASE
)
COMMAND_BOUNDARY = re.compile(r"(?<![\w.-])(?:(?P<sudo>sudo)(?P<sudo_args>(?:[ \t]+\S+)*?)[ \t]+)?(?P<command>systemctl|service|futu[ \t]*-?[ \t]*opend|futuopend|opend)(?:\.exe)?(?![\w.-])(?=[ \t])", re.IGNORECASE)
COMMAND_END = re.compile(r"(?:;|&&|\|\||\||\n|`|\))")
OPEND_ACTIONS = frozenset({"start", "stop", "restart", "reload", "try-reload", "reload-or-restart", "reload-or-try-restart", "login", "relogin", "migrate", "kill", "enable", "disable", "daemon-reload"})
SERVICE_LIFECYCLE_ACTIONS = OPEND_ACTIONS
FORBIDDEN_PATH = re.compile(
    r"(?:^|/)(?:ops/opend(?:/|$)|futu-opend\.service$|[^/]*(?:futu\s*-?\s*opend|futuopend|opend)[^/]*)",
    re.IGNORECASE,
)
TEXT_SUFFIXES = {
    ".bat", ".cmd", ".conf", ".env", ".ini", ".js", ".json", ".md", ".ps1", ".py", ".service", ".ts", ".tsx",
    ".sh", ".txt", ".toml", ".yaml", ".yml",
}
NODE_PROCESS_SPAWN = re.compile(r"\b(?:exec|execFile|spawn|spawnSync)\s*\(|\.\s*(?:exec|execFile|spawn|spawnSync)\s*\(", re.IGNORECASE)
NODE_CHILD_PROCESS_MODULE = re.compile(r"\b(?:node:)?child_process\b", re.IGNORECASE)
PYTHON_SUBPROCESS_FUNCTIONS = frozenset({"run", "call", "Popen", "check_call", "check_output"})
SAFE_SUBPROCESS_EXECUTABLES = frozenset({"git", "node", "npm"})


class SafetyError(ValueError):
    """Raised for a rejected release surface; messages contain no secret data."""


def _normalise_member_name(name: str) -> str:
    candidate = name.replace("\\", "/")
    path = PurePosixPath(candidate)
    if not candidate or path.is_absolute() or ".." in path.parts:
        raise SafetyError("unsafe artifact member path")
    return str(path)


def _safe_artifact(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as error:
        raise SafetyError("artifact must be a regular file") from error
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode) or metadata.st_nlink != 1:
        raise SafetyError("artifact must be a regular file")
    if metadata.st_size > MAX_ARTIFACT_BYTES:
        raise SafetyError("artifact exceeds maximum size")


def _release_path_allowed(path: str) -> bool:
    # OpenD source may legitimately exist in the repository, but it must never
    # be part of a candidate release surface.
    return not FORBIDDEN_PATH.search(path.replace("\\", "/"))


def _decode_release_text(content: bytes) -> str:
    """Decode source bytes without accepting NUL-based command obfuscation."""
    utf8_bom = content.startswith(b"\xef\xbb\xbf")
    if content.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        text = content.decode("utf-32", errors="strict")
    elif content.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = content.decode("utf-16", errors="strict")
    else:
        if b"\x00" in content:
            raise SafetyError("NUL-obfuscated release text")
        text = content.decode("utf-8", errors="strict")
    if "\x00" in text:
        raise SafetyError("NUL-obfuscated release text")
    if utf8_bom:
        text = text[1:]
    return unicodedata.normalize("NFKC", text)


def _command_tokens(text: str, match: re.Match[str]) -> list[str]:
    """Return one shell command segment, preserving command-chain boundaries."""
    end = COMMAND_END.search(text, match.end())
    segment = text[match.start("command"):end.start() if end else len(text)]
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        raise SafetyError("malformed shell syntax in release text") from None


def _non_option_tokens(tokens: list[str]) -> list[str]:
    return [token for token in tokens if not token.startswith("-")]


def _command_identity(command: str) -> str:
    return re.sub(r"[\s.-]", "", command).lower()


def _lifecycle_index(tokens: list[str]) -> int | None:
    for index, token in enumerate(tokens):
        if token.lower() in SERVICE_LIFECYCLE_ACTIONS:
            return index
    return None


def _command_segment_prefix(text: str, position: int) -> str:
    """Return text before a candidate in its shell command segment."""
    segment_start = max(text.rfind("\n", 0, position), text.rfind(";", 0, position)) + 1
    for delimiter in ("&&", "||"):
        delimiter_index = text.rfind(delimiter, segment_start, position)
        if delimiter_index >= segment_start:
            segment_start = delimiter_index + len(delimiter)
    return text[segment_start:position].strip()


def _markdown_command_fragments(text: str) -> Iterable[str]:
    """Scan only fenced or inline code in Markdown, never ordinary prose."""
    for match in re.finditer(r"```[^\n]*\n(.*?)```|`([^`\n]+)`", text, re.DOTALL):
        yield match.group(1) if match.group(1) is not None else match.group(2)


def _dequote_fragments(text: str) -> str:
    """Expose shell quote-fragmented executable names and actions."""
    return text.replace("'", "").replace('"', "")


def _python_literal(node: ast.AST, values: dict[str, object]) -> object | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, (str, bool)):
        return node.value
    if isinstance(node, ast.Name):
        return values.get(node.id)
    if isinstance(node, (ast.List, ast.Tuple)):
        result = [_python_literal(item, values) for item in node.elts]
        return result if all(item is not None for item in result) else None
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _python_literal(node.left, values), _python_literal(node.right, values)
        return left + right if isinstance(left, str) and isinstance(right, str) else None
    return None


def _python_call_kind(node: ast.Call, subprocess_modules: set[str], subprocess_functions: set[str], os_modules: set[str], os_system_functions: set[str]) -> str | None:
    if isinstance(node.func, ast.Name):
        if node.func.id in subprocess_functions:
            return "subprocess_alias"
        if node.func.id in os_system_functions:
            return "os.system"
    if isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name):
        if node.func.value.id == "subprocess" and node.func.attr in PYTHON_SUBPROCESS_FUNCTIONS:
            return "subprocess_direct"
        if node.func.value.id in subprocess_modules and node.func.attr in PYTHON_SUBPROCESS_FUNCTIONS:
            return "subprocess_alias"
        if node.func.value.id in os_modules and node.func.attr == "system":
            return "os.system"
    return None


def _python_symbol_kind(node: ast.AST, subprocess_modules: set[str], subprocess_functions: set[str], os_modules: set[str], os_system_functions: set[str]) -> str | None:
    if isinstance(node, ast.Name):
        if node.id in subprocess_modules:
            return "subprocess_module"
        if node.id in subprocess_functions:
            return "subprocess_function"
        if node.id in os_modules:
            return "os_module"
        if node.id in os_system_functions:
            return "os_system"
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        if node.value.id in subprocess_modules and node.attr in PYTHON_SUBPROCESS_FUNCTIONS:
            return "subprocess_function"
        if node.value.id in os_modules and node.attr == "system":
            return "os_system"
    return None


def _propagate_python_process_aliases(tree: ast.AST, subprocess_modules: set[str], subprocess_functions: set[str], os_modules: set[str], os_system_functions: set[str]) -> None:
    assignments = [node for node in ast.walk(tree) if isinstance(node, (ast.Assign, ast.AnnAssign)) and node.value is not None]
    changed = True
    while changed:
        changed = False
        for node in assignments:
            kind = _python_symbol_kind(node.value, subprocess_modules, subprocess_functions, os_modules, os_system_functions)
            if kind is None:
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            destination = {
                "subprocess_module": subprocess_modules,
                "subprocess_function": subprocess_functions,
                "os_module": os_modules,
                "os_system": os_system_functions,
            }[kind]
            for target in targets:
                if isinstance(target, ast.Name) and target.id not in destination:
                    destination.add(target.id)
                    changed = True


def _scan_python_process_spawns(label: str, text: str) -> list[str]:
    """Fail closed for Python process APIs except proven literal tool probes."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [f"{label}: invalid Python release text"]
    subprocess_modules = {"subprocess"}
    subprocess_functions: set[str] = set()
    os_modules = {"os"}
    os_system_functions: set[str] = set()
    values: dict[str, object] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "subprocess":
                    subprocess_modules.add(alias.asname or alias.name)
                if alias.name == "os":
                    os_modules.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module in {"subprocess", "os"}:
            for alias in node.names:
                if node.module == "subprocess" and alias.name in PYTHON_SUBPROCESS_FUNCTIONS:
                    subprocess_functions.add(alias.asname or alias.name)
                if node.module == "os" and alias.name == "system":
                    os_system_functions.add(alias.asname or alias.name)
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            value = _python_literal(node.value, values) if node.value is not None else None
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name):
                    if value is None:
                        values.pop(target.id, None)
                    else:
                        values[target.id] = value
    _propagate_python_process_aliases(tree, subprocess_modules, subprocess_functions, os_modules, os_system_functions)
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        kind = _python_call_kind(node, subprocess_modules, subprocess_functions, os_modules, os_system_functions)
        if kind == "os.system":
            violations.append(f"{label}: process-spawn lifecycle invocation")
            continue
        if kind == "subprocess_alias":
            violations.append(f"{label}: process-spawn lifecycle invocation")
            continue
        if kind != "subprocess_direct":
            continue
        shell = next((keyword.value for keyword in node.keywords if keyword.arg == "shell"), ast.Constant(False))
        command = node.args[0] if node.args else None
        executable = command.elts[0].value if isinstance(command, (ast.List, ast.Tuple)) and command.elts and isinstance(command.elts[0], ast.Constant) and isinstance(command.elts[0].value, str) else None
        if _python_literal(shell, values) is not False or executable not in SAFE_SUBPROCESS_EXECUTABLES:
            violations.append(f"{label}: process-spawn lifecycle invocation")
    return violations


def _scan_node_process_spawns(label: str, text: str) -> list[str]:
    return [f"{label}: process-spawn lifecycle invocation"] if NODE_CHILD_PROCESS_MODULE.search(text) or NODE_PROCESS_SPAWN.search(text) else []


def _is_release_control_label(label: str) -> bool:
    return label.replace("\\", "/").startswith(("ops/", "config/", "docs/rewrite/"))


def _scan_command_text(label: str, content: bytes) -> list[str]:
    violations: list[str] = []
    try:
        text = _decode_release_text(content)
    except (SafetyError, UnicodeDecodeError):
        return [f"{label}: invalid or NUL-obfuscated release text"]
    fragments = _markdown_command_fragments(text) if Path(label).suffix.lower() == ".md" else [text]
    for fragment in fragments:
        for candidate in {fragment, _dequote_fragments(fragment)}:
            for match in COMMAND_BOUNDARY.finditer(candidate):
                end = COMMAND_END.search(candidate, match.end())
                remainder = candidate[match.end():end.start() if end else len(candidate)]
                if not any(re.search(rf"(?<![\w-]){re.escape(action)}(?![\w-])", remainder, re.IGNORECASE) for action in SERVICE_LIFECYCLE_ACTIONS):
                    continue
                try:
                    tokens = _command_tokens(candidate, match)
                except SafetyError:
                    return [f"{label}: malformed release command"]
                if not tokens:
                    continue
                command = _command_identity(match.group("command"))
                arguments = tokens[1:]
                action_index = _lifecycle_index(arguments)
                if action_index is None:
                    continue
                if _command_segment_prefix(candidate, match.start()):
                    violations.append(f"{label}: non-standard command wrapper")
                    continue
                if match.group("sudo") and match.group("sudo_args"):
                    violations.append(f"{label}: non-standard sudo wrapper")
                    continue
                if command in {"futuopend", "opend"}:
                    violations.append(f"{label}: OpenD lifecycle invocation")
                    continue
                if command == "service":
                    violations.append(f"{label}: non-whitelisted service action")
                    continue
                # Every lifecycle action is denied unless it exactly restarts the one
                # approved Rewrite API unit. Read-only status commands are not actions.
                if command != "systemctl":
                    violations.append(f"{label}: non-whitelisted service action")
                    continue
                if any(argument != "--no-block" for argument in arguments[:action_index]):
                    violations.append(f"{label}: non-whitelisted systemctl option")
                    continue
                if action_index != len(arguments) - 2 or arguments[action_index].lower() != ALLOWED_ACTION or arguments[action_index + 1].lower() != ALLOWED_SERVICE:
                    violations.append(f"{label}: non-whitelisted service action")
    suffix = Path(label).suffix.lower()
    if _is_release_control_label(label) and suffix == ".py":
        violations.extend(_scan_python_process_spawns(label, text))
    elif _is_release_control_label(label) and suffix in {".js", ".ts", ".tsx"}:
        violations.extend(_scan_node_process_spawns(label, text))
    return violations


def _scan_file(path: Path, label: str) -> list[str]:
    if path.is_symlink() or not path.is_file():
        return [f"{label}: release entry must be a regular file"]
    if path.stat().st_size > MAX_MEMBER_BYTES:
        return [f"{label}: release entry exceeds maximum size"]
    if not _release_path_allowed(label):
        return [f"{label}: forbidden OpenD release path"]
    content = path.read_bytes()
    if path.suffix.lower() not in TEXT_SUFFIXES and not content.startswith(b"#!"):
        return []
    return _scan_command_text(label, content)


def tracked_release_surface(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z", "--", "ops", "config", "docs/rewrite"],
        check=True, capture_output=True,
    )
    return [root / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def scan_tracked_surface(root: Path) -> list[str]:
    violations: list[str] = []
    for path in tracked_release_surface(root):
        relative = path.relative_to(root).as_posix()
        if relative.startswith("ops/opend/"):
            continue
        violations.extend(_scan_file(path, relative))
    return violations


def _scan_archive_members(members: Iterable[tuple[str, int, bool, bytes | Callable[[], bytes] | None]]) -> list[str]:
    violations: list[str] = []
    count = 0
    total = 0
    names: set[str] = set()
    collisions: set[str] = set()
    for name, size, is_regular, content in members:
        count += 1
        if count > MAX_MEMBERS:
            return ["artifact has too many members"]
        try:
            name = _normalise_member_name(name)
        except SafetyError as error:
            violations.append(str(error))
            continue
        if name in names:
            violations.append("artifact has duplicate member paths")
            continue
        names.add(name)
        key = unicodedata.normalize("NFKC", name).casefold()
        if key in collisions:
            violations.append("artifact has casefold or Unicode member path collisions")
        collisions.add(key)
        if not is_regular:
            violations.append(f"{name}: non-regular archive entry is forbidden")
            continue
        if type(size) is not int or size < 0 or size > MAX_MEMBER_BYTES:
            violations.append(f"{name}: release entry exceeds maximum size")
            return violations
        total += size
        if total > MAX_TOTAL_MEMBER_BYTES:
            return ["artifact exceeds expanded size limit"]
        if not _release_path_allowed(name):
            violations.append(f"{name}: forbidden OpenD release path")
            continue
        if callable(content):
            content = content()
        if content is not None and (Path(name).suffix.lower() in TEXT_SUFFIXES or content.startswith(b"#!")):
            violations.extend(_scan_command_text(name, content))
    return violations


def scan_artifact(path: Path) -> list[str]:
    _safe_artifact(path)
    if tarfile.is_tarfile(path):
        with tarfile.open(path, "r|*") as archive:
            def stream_members() -> Iterable[tuple[str, int, bool, bytes | None]]:
                for member in archive:
                    def content(member=member) -> bytes:
                        handle = archive.extractfile(member)
                        return handle.read(member.size + 1) if handle else b""

                    yield member.name, member.size, member.isfile(), content

            return _scan_archive_members(stream_members())
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            def members() -> Iterable[tuple[str, int, bool, bytes | Callable[[], bytes] | None]]:
                for info in archive.infolist():
                    kind = (info.external_attr >> 16) & 0o170000
                    regular = not info.is_dir() and kind in {0, 0o100000}
                    yield info.filename, info.file_size, regular, lambda info=info: archive.read(info, pwd=None)

            return _scan_archive_members(members())
    raise SafetyError("artifact must be a tar or zip archive")


def scan_manifest(path: Path) -> list[str]:
    """Validate paths named by a local checksum/file-list manifest.

    A manifest is deliberately treated as data, not a shell script: each
    non-empty line names its final whitespace-delimited path field.  This
    accommodates common ``sha256  relative/path`` manifests without executing
    or resolving any of their contents.
    """
    _safe_artifact(path)
    if path.stat().st_size > MAX_MEMBER_BYTES:
        raise SafetyError("manifest exceeds maximum size")
    violations: list[str] = []
    names: set[str] = set()
    collisions: set[str] = set()
    for raw in path.read_text(encoding="utf-8", errors="strict").splitlines():
        entry = raw.strip()
        if not entry or entry.startswith("#"):
            continue
        name = entry.split(maxsplit=1)[-1].lstrip("*").strip()
        try:
            name = _normalise_member_name(name)
        except SafetyError as error:
            violations.append(str(error))
            continue
        if name in names:
            violations.append("manifest has duplicate member paths")
        names.add(name)
        key = unicodedata.normalize("NFKC", name).casefold()
        if key in collisions:
            violations.append("manifest has casefold or Unicode member path collisions")
        collisions.add(key)
        if not _release_path_allowed(name):
            violations.append(f"{name}: forbidden OpenD release path")
    return violations


def receipt_contract() -> dict[str, object]:
    return {
        "schema": "ciclotrade.release-readonly-receipt.v1",
        "service": ALLOWED_SERVICE,
        "allowed_action": ALLOWED_ACTION,
        "read_only_fields": ["MainPID", "ActiveEnterTimestamp", "QOTRIGHT"],
        "forbidden_fields": ["host", "ip", "account", "secret", "token"],
        "rule": "pre and post values must be recorded verbatim and compared; this gate does not collect them",
    }


def verify(root: Path, artifact: Path | None = None, manifest: Path | None = None) -> list[str]:
    violations = scan_tracked_surface(root)
    if artifact is not None:
        violations.extend(scan_artifact(artifact))
    if manifest is not None:
        violations.extend(scan_manifest(manifest))
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Static zero-touch OpenD release safety gate")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--artifact", type=Path)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--receipt-contract", action="store_true")
    args = parser.parse_args(argv)
    if args.receipt_contract:
        print(json.dumps(receipt_contract(), sort_keys=True, separators=(",", ":")))
        return 0
    try:
        violations = verify(
            args.root.resolve(),
            args.artifact.resolve() if args.artifact else None,
            args.manifest.resolve() if args.manifest else None,
        )
    except (SafetyError, OSError, subprocess.CalledProcessError):
        print('{"state":"rejected"}')
        return 2
    if violations:
        print(json.dumps({"state": "rejected", "violations": sorted(set(violations))}, separators=(",", ":")))
        return 2
    print('{"state":"accepted"}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
