"""Entrypoint inside the disposable strategy container."""

from __future__ import annotations

import ast
from contextlib import redirect_stderr, redirect_stdout
import json

import backtrader as bt


BLOCKED_MODULES = {
    "builtins", "ctypes", "ftplib", "http", "importlib", "os", "pathlib",
    "requests", "shutil", "socket", "subprocess", "sys", "urllib",
}


class CappedOutput:
    def __init__(self, limit: int = 4_096):
        self.limit = limit
        self.parts: list[str] = []
        self.size = 0

    def write(self, value: str) -> int:
        remaining = self.limit - self.size
        if remaining > 0:
            text = str(value)[:remaining]
            self.parts.append(text)
            self.size += len(text)
        return len(value)

    def flush(self) -> None:
        return

    def value(self) -> str:
        return "".join(self.parts)


def validate(tree: ast.AST) -> None:
    for node in ast.walk(tree):
        if isinstance(node, ast.Import) and any(alias.name.split(".", 1)[0] in BLOCKED_MODULES for alias in node.names):
            raise ValueError("blocked import")
        if isinstance(node, ast.ImportFrom) and (node.module or "").split(".", 1)[0] in BLOCKED_MODULES:
            raise ValueError("blocked import")
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise ValueError("blocked attribute")


def main() -> None:
    output, errors = CappedOutput(), CappedOutput()
    try:
        source = open("/work/strategy.py", encoding="utf-8").read(65_537)
        if not source or len(source.encode("utf-8")) > 65_536:
            raise ValueError("invalid code size")
        tree = ast.parse(source, filename="strategy.py", mode="exec")
        validate(tree)
        namespace = {"__name__": "user_strategy"}
        with redirect_stdout(output), redirect_stderr(errors):
            exec(compile(tree, "strategy.py", "exec"), namespace)
        strategies = sorted(
            name for name, value in namespace.items()
            if isinstance(value, type) and value is not bt.Strategy and issubclass(value, bt.Strategy)
        )
        if not strategies:
            raise ValueError("没有找到 Backtrader Strategy 子类")
        result = {"status": "completed", "strategy_classes": strategies, "stdout": output.value(), "stderr": errors.value()}
    except Exception as exc:
        result = {"status": "failed", "error": f"{type(exc).__name__}: {str(exc)[:300]}", "stdout": output.value(), "stderr": errors.value()}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
