"""Джарвис как фоновый агент: значок в трее и автозапуск вместе с Windows.

Окно можно закрыть — агент продолжает слушать «Джарвис» и выполнять команды.
Полностью программа закрывается через пункт «Выход» в меню значка в трее.
"""

import logging
import sys
import threading
from pathlib import Path
from typing import Callable

from .paths import FROZEN, RESOURCES
from .tools._platform import IS_WIN

log = logging.getLogger("jarvis.agent")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Jarvis"


def _launch_command() -> str:
    if FROZEN:
        return f'"{sys.executable}" --background'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{interpreter}" "{RESOURCES / "main.py"}" --background'


def autostart_enabled() -> bool:
    if not IS_WIN:
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, RUN_NAME)
        return True
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    """Включает/выключает запуск Джарвиса при входе в Windows (в фоне, без окна)."""
    if not IS_WIN:
        raise NotImplementedError("Автозапуск настраивается только в Windows")
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, RUN_NAME, 0, winreg.REG_SZ, _launch_command())
        else:
            try:
                winreg.DeleteValue(key, RUN_NAME)
            except FileNotFoundError:
                pass


class Tray:
    """Значок в трее (pystray). Если библиотеки нет — просто ничего не показывает."""

    def __init__(
        self,
        title: str,
        on_open: Callable[[], None],
        on_quit: Callable[[], None],
        is_listening: Callable[[], bool],
        on_toggle_listening: Callable[[], None],
    ):
        self._icon = None
        try:
            import pystray

            from .ui.logo import render

            menu = pystray.Menu(
                pystray.MenuItem("Открыть", lambda: on_open(), default=True),
                pystray.MenuItem(
                    f"Слушать «{title}»", lambda: on_toggle_listening(), checked=lambda _item: is_listening()
                ),
                pystray.Menu.SEPARATOR,
                pystray.MenuItem("Выход", lambda: on_quit()),
            )
            self._icon = pystray.Icon("jarvis", render(64, opacity=1.0, fill=0.66), title, menu)
        except Exception as exc:  # noqa: BLE001 — без трея программа всё равно работает
            log.warning("Значок в трее недоступен: %s", exc)

    @property
    def available(self) -> bool:
        return self._icon is not None

    def start(self) -> None:
        if self._icon is None:
            return
        if IS_WIN:
            self._icon.run_detached()  # своя очередь сообщений Windows в фоне
        else:
            threading.Thread(target=self._icon.run, daemon=True, name="tray").start()

    def refresh(self) -> None:
        if self._icon is not None:
            self._icon.update_menu()

    def notify(self, text: str) -> None:
        if self._icon is not None:
            try:
                self._icon.notify(text, "Джарвис")
            except Exception:  # noqa: BLE001
                pass

    def stop(self) -> None:
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:  # noqa: BLE001
                pass
