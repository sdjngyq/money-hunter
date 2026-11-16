"""轻量终端输出封装：有 rich 时用 rich，没有也能正常跑。"""

from __future__ import annotations

import sys

try:  # pragma: no cover - 取决于环境
    from rich.console import Console

    _console: "Console | None" = Console()
except Exception:  # pragma: no cover
    _console = None


try:  # Windows 控制台尽量切到 UTF-8，避免中文乱码
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
except Exception:
    pass


def _print(message: str, style: str | None = None) -> None:
    if _console is not None:
        _console.print(message, style=style)
    else:  # pragma: no cover
        print(message)


def info(message: str) -> None:
    _print(message, "cyan")


def ok(message: str) -> None:
    _print(message, "green")


def warn(message: str) -> None:
    _print(message, "yellow")


def error(message: str) -> None:
    _print(message, "bold red")


def say(message: str = "") -> None:
    _print(message)


def console():
    return _console
