"""Мелкие кроссплатформенные помощники для инструментов."""

import os
import subprocess
import sys

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run(args: list[str], timeout: float = 20) -> subprocess.CompletedProcess:
    """Запускает команду и ждёт её завершения (без всплывающего окна консоли на Windows)."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=NO_WINDOW,
    )


def powershell(script: str, timeout: float = 20) -> subprocess.CompletedProcess:
    prefix = "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
    return run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", prefix + script],
        timeout=timeout,
    )


def launch(args: list[str]) -> None:
    """Запускает программу отдельно от ассистента и не ждёт её завершения."""
    subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=not IS_WIN,
        close_fds=True,
    )


def open_default(target: str) -> None:
    """Открывает файл, папку, ссылку или URI программой по умолчанию."""
    if IS_WIN:
        os.startfile(target)  # type: ignore[attr-defined]
    elif IS_MAC:
        launch(["open", target])
    else:
        launch(["xdg-open", target])


def ensure_com() -> None:
    """COM нужно инициализировать в каждом потоке, который обращается к Windows API звука/речи."""
    if IS_WIN:
        try:
            import comtypes

            comtypes.CoInitialize()
        except (ImportError, OSError):
            pass
