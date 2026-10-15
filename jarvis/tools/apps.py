"""Запуск и закрытие программ, управление окнами."""

import ctypes
import difflib
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Literal

import psutil

from ._platform import IS_MAC, IS_WIN, launch, powershell, run
from .files import known_folder
from .registry import tool

log = logging.getLogger("jarvis.tools")

# Известные программы: голосовые названия, как запускать и как называются их процессы.
APPS: dict[str, dict] = {
    "Блокнот": dict(names=["блокнот", "notepad", "текстовый редактор"], win=["notepad.exe"], proc=["notepad.exe"],
                    linux=["gnome-text-editor", "gedit", "kate", "mousepad", "xed"], mac="TextEdit"),
    "Калькулятор": dict(names=["калькулятор", "calculator", "calc"], win=["calc.exe"],
                        proc=["calculatorapp.exe", "calculator.exe"], linux=["gnome-calculator", "kcalc"], mac="Calculator"),
    "Google Chrome": dict(names=["хром", "гугл хром", "chrome", "google chrome", "гугл"], win=["chrome.exe"],
                         proc=["chrome.exe"], linux=["google-chrome", "chromium", "chromium-browser"], mac="Google Chrome"),
    "Microsoft Edge": dict(names=["edge", "эдж", "майкрософт эдж"], win=["msedge.exe"], proc=["msedge.exe"],
                           linux=["microsoft-edge"], mac="Microsoft Edge"),
    "Firefox": dict(names=["firefox", "файрфокс", "фаерфокс", "мозилла", "мозиллу"], win=["firefox.exe"], proc=["firefox.exe"],
                    linux=["firefox"], mac="Firefox"),
    "Яндекс Браузер": dict(names=["яндекс браузер", "yandex browser", "браузер яндекс"], search=["Yandex", "Яндекс"], proc=["browser.exe"],
                           linux=["yandex-browser"], mac="Yandex"),
    "Opera": dict(names=["опера", "оперу", "opera"], search=["Opera"], proc=["opera.exe"], linux=["opera"], mac="Opera"),
    "Проводник": dict(names=["проводник", "explorer", "мой компьютер", "этот компьютер", "файлы"], win=["explorer.exe"],
                      linux=["nautilus", "dolphin", "thunar", "nemo"], mac="Finder"),
    "Диспетчер задач": dict(names=["диспетчер задач", "task manager"], win=["taskmgr.exe"], proc=["taskmgr.exe"],
                            linux=["gnome-system-monitor", "ksysguard"], mac="Activity Monitor"),
    "Параметры": dict(names=["параметры", "настройки", "settings", "параметры windows"], win=["ms-settings:"],
                      proc=["systemsettings.exe"], linux=["gnome-control-center"], mac="System Settings"),
    "Панель управления": dict(names=["панель управления", "control panel"], win=["control.exe"]),
    "Командная строка": dict(names=["командная строка", "командную строку", "cmd", "консоль"], win=["cmd.exe"], proc=["cmd.exe"]),
    "PowerShell": dict(names=["powershell", "павершелл", "пауэршелл"], win=["powershell.exe"], proc=["powershell.exe"]),
    "Терминал": dict(names=["терминал", "terminal", "windows terminal"], win=["wt.exe"], proc=["windowsterminal.exe"],
                     linux=["gnome-terminal", "konsole", "xfce4-terminal", "xterm"], mac="Terminal"),
    "Paint": dict(names=["paint", "паинт", "пейнт", "рисовалка", "рисовалку"], win=["mspaint.exe"], proc=["mspaint.exe"]),
    "Word": dict(names=["word", "ворд", "microsoft word"], win=["winword.exe"], search=["Word"], proc=["winword.exe"],
                 mac="Microsoft Word"),
    "Excel": dict(names=["excel", "эксель", "ексель"], win=["excel.exe"], search=["Excel"], proc=["excel.exe"],
                  mac="Microsoft Excel"),
    "PowerPoint": dict(names=["powerpoint", "пауэрпоинт", "поверпоинт"], win=["powerpnt.exe"], search=["PowerPoint"],
                       proc=["powerpnt.exe"], mac="Microsoft PowerPoint"),
    "Visual Studio Code": dict(names=["vs code", "vscode", "вс код", "visual studio code"], search=["Visual Studio Code"],
                               win=["code.exe"], proc=["code.exe"], linux=["code"], mac="Visual Studio Code"),
    "Telegram": dict(names=["телеграм", "телеграмм", "телега", "телегу", "telegram"], search=["Telegram"], proc=["telegram.exe"],
                     linux=["telegram-desktop", "telegram"], mac="Telegram"),
    "Discord": dict(names=["дискорд", "discord"], search=["Discord"], proc=["discord.exe"], linux=["discord"], mac="Discord"),
    "Steam": dict(names=["стим", "steam"], win=["steam://open/main"], search=["Steam"], proc=["steam.exe"],
                  linux=["steam"], mac="Steam"),
    "Spotify": dict(names=["спотифай", "spotify"], win=["spotify:"], search=["Spotify"], proc=["spotify.exe"],
                    linux=["spotify"], mac="Spotify"),
    "OBS Studio": dict(names=["обс", "obs", "obs studio"], search=["OBS Studio"], proc=["obs64.exe"], linux=["obs"],
                       mac="OBS"),
    "Ножницы": dict(names=["ножницы", "snipping tool"], win=["snippingtool.exe", "ms-screenclip:"],
                    proc=["snippingtool.exe"]),
    "Камера": dict(names=["камера", "камеру", "camera"], win=["microsoft.windows.camera:"], proc=["windowscamera.exe"],
                   linux=["cheese"], mac="Photo Booth"),
    "Microsoft Store": dict(names=["магазин", "microsoft store", "магазин приложений"], win=["ms-windows-store:"]),
    "Диспетчер устройств": dict(names=["диспетчер устройств", "device manager"], win=["devmgmt.msc"]),
    "WhatsApp": dict(names=["ватсап", "вотсап", "whatsapp", "вацап"], search=["WhatsApp"], proc=["whatsapp.exe", "whatsapp.root.exe"]),
    "Zoom": dict(names=["зум", "zoom"], search=["Zoom"], proc=["zoom.exe"], linux=["zoom"], mac="zoom.us"),
}

# Эти процессы закрывать нельзя — система станет неработоспособной.
_PROTECTED = {
    "explorer.exe", "winlogon.exe", "csrss.exe", "svchost.exe", "lsass.exe", "dwm.exe", "services.exe", "smss.exe",
    "wininit.exe", "system", "registry", "fontdrvhost.exe", "sihost.exe", "systemd", "init", "launchd",
    "loginwindow", "windowserver", "kernel_task", "python.exe", "pythonw.exe",
}

_BAD_SHORTCUT_WORDS = ("uninstall", "удален", "деинсталл", "readme", "справка", "help", "documentation", "website")


def _norm(text: str) -> str:
    return " ".join(text.lower().replace("ё", "е").split())


def _find_spec(name: str) -> tuple[str | None, dict]:
    query = _norm(name).removesuffix(".exe")
    aliases: dict[str, str] = {}
    for title, spec in APPS.items():
        for alias in [_norm(title), *spec["names"]]:
            aliases[alias] = title
    if query in aliases:
        title = aliases[query]
        return title, APPS[title]
    close = difflib.get_close_matches(query, aliases, n=1, cutoff=0.85)
    if close:
        title = aliases[close[0]]
        return title, APPS[title]
    return None, {}


def _best_match(query: str, names: list[str]) -> str | None:
    """Ищет наиболее подходящее имя: точное -> начинается с -> содержит -> похожее."""
    query = _norm(query)
    if not query:
        return None
    normalized = {_norm(n): n for n in names}
    if query in normalized:
        return normalized[query]
    for predicate in (lambda n: n.startswith(query), lambda n: query in n):
        hits = sorted((n for n in normalized if predicate(n)), key=len)
        if hits:
            return normalized[hits[0]]
    close = difflib.get_close_matches(query, normalized, n=1, cutoff=0.75)
    return normalized[close[0]] if close else None


# --- Windows -------------------------------------------------------------------------------


def _app_path(exe: str) -> str | None:
    """Путь к программе из реестра App Paths (так Windows находит chrome.exe, winword.exe и т. п.)."""
    import winreg

    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}") as key:
                value, _ = winreg.QueryValueEx(key, "")
        except OSError:
            continue
        path = os.path.expandvars(str(value).strip('"'))
        if os.path.exists(path):
            return path
    return None


def _shortcut_paths() -> list[Path]:
    """Ярлыки меню «Пуск» (с подпапками) и рабочего стола (только верхний уровень —
    на рабочем столе бывают огромные папки, обход которых занимал бы минуты)."""
    found: list[Path] = []
    for env in ("APPDATA", "PROGRAMDATA"):
        base = os.environ.get(env)
        start_menu = Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs" if base else None
        if start_menu and start_menu.is_dir():
            try:
                found.extend(start_menu.rglob("*"))
            except OSError:
                pass
    for desktop in (known_folder("desktop"), Path(os.environ.get("PUBLIC", r"C:\Users\Public")) / "Desktop"):
        if desktop.is_dir():
            try:
                found.extend(desktop.iterdir())
            except OSError:
                pass
    return found


def _find_shortcut(queries: list[str]) -> Path | None:
    shortcuts: dict[str, Path] = {}
    for path in _shortcut_paths():
        if path.suffix.lower() in (".lnk", ".url", ".appref-ms"):
            name = path.stem
            if not any(bad in name.lower() for bad in _BAD_SHORTCUT_WORDS):
                shortcuts.setdefault(name, path)
    for query in queries:
        match = _best_match(query, list(shortcuts))
        if match:
            return shortcuts[match]
    return None


_start_apps_cache: tuple[float, dict[str, str]] | None = None


def _start_apps() -> dict[str, str]:
    """Все приложения меню «Пуск» (включая Microsoft Store): имя -> AppID. Кэшируется на 5 минут."""
    global _start_apps_cache
    if _start_apps_cache and time.monotonic() - _start_apps_cache[0] < 300:
        return _start_apps_cache[1]
    try:
        result = powershell("Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress", timeout=10)
        data = json.loads(result.stdout.strip() or "[]")
    except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError) as exc:
        log.warning("Не удалось получить список приложений «Пуска»: %s", exc)
        return {}
    if isinstance(data, dict):
        data = [data]
    apps = {item["Name"]: item["AppID"] for item in data if item.get("Name") and item.get("AppID")}
    _start_apps_cache = (time.monotonic(), apps)
    return apps


def _find_start_app(queries: list[str]) -> tuple[str, str] | None:
    """Ищет программу среди всех приложений меню «Пуск», включая приложения из Microsoft Store."""
    apps = _start_apps()
    for query in queries:
        match = _best_match(query, list(apps))
        if match and not any(bad in match.lower() for bad in _BAD_SHORTCUT_WORDS):
            return match, apps[match]
    return None


def _launch_windows(name: str, title: str | None, spec: dict) -> str | None:
    for target in spec.get("win", []):
        if ":" in target:  # URI вида ms-settings: или steam://
            try:
                os.startfile(target)  # type: ignore[attr-defined]
                return title or target
            except OSError:
                continue
        path = _app_path(target) or shutil.which(target)
        if path:
            os.startfile(path)  # type: ignore[attr-defined]
            return title or Path(path).stem
    # Прямой путь к .exe (реестр App Paths или PATH) — мгновенно, поэтому раньше поиска по ярлыкам.
    exe = name if name.lower().endswith(".exe") else f"{name}.exe"
    path = _app_path(exe) or shutil.which(exe)
    if path:
        os.startfile(path)  # type: ignore[attr-defined]
        return Path(path).stem
    queries = [*spec.get("search", []), *([title] if title else []), name]
    shortcut = _find_shortcut(queries)
    if shortcut:
        os.startfile(str(shortcut))  # type: ignore[attr-defined]
        return shortcut.stem
    start_app = _find_start_app(queries)
    if start_app:
        launch(["explorer.exe", f"shell:AppsFolder\\{start_app[1]}"])
        return start_app[0]
    return None


# --- Linux / macOS -------------------------------------------------------------------------


def _launch_other(name: str, title: str | None, spec: dict) -> str | None:
    if IS_MAC:
        for app in (spec.get("mac"), title, name):
            if app and run(["open", "-a", app]).returncode == 0:
                return app
        return None
    for command in [*spec.get("linux", []), name, name.lower().replace(" ", "-")]:
        path = shutil.which(command)
        if path:
            launch([path])
            return command
    return None


@tool()
def open_app(name: str) -> str:
    """Открыть (запустить) программу на компьютере.

    Args:
        name: название программы так, как его назвал пользователь или как оно называется в системе:
            «Google Chrome», «Telegram», «блокнот», «калькулятор», «проводник», «Steam», «Word», «Discord»
    """
    title, spec = _find_spec(name)
    launched = _launch_windows(name, title, spec) if IS_WIN else _launch_other(name, title, spec)
    if not launched:
        raise LookupError(f"Не нашёл программу «{name}». Возможно, она не установлена или называется иначе.")
    return f"Запускаю: {launched}"


def _matching_processes(name: str) -> list[psutil.Process]:
    _title, spec = _find_spec(name)
    targets = set(spec.get("proc", []))
    query = _norm(name).replace(" ", "")
    own = {os.getpid(), os.getppid()}
    found = []
    for proc in psutil.process_iter(["pid", "name"]):
        pname = (proc.info["name"] or "").lower()
        if proc.pid in own or pname in _PROTECTED:
            continue
        base = pname.removesuffix(".exe")
        if targets:
            if pname in targets or base in targets:
                found.append(proc)
        elif len(query) >= 3 and (base == query or query in base):
            found.append(proc)
    return found


def _confirm_close(name: str, force: bool = False) -> str | None:
    return f"Принудительно завершить «{name}»? Несохранённые данные будут потеряны." if force else None


@tool(confirm=_confirm_close)
def close_app(name: str, force: bool = False) -> dict | str:
    """Закрыть программу (все её окна и процессы).

    Args:
        name: название программы, например «Google Chrome», «Telegram», «блокнот»
        force: завершить принудительно, если программа не закрылась обычным способом
    """
    processes = _matching_processes(name)
    if not processes:
        return f"Программа «{name}» сейчас не запущена."
    exe_names = sorted({p.info["name"] for p in processes})
    if IS_WIN and not force:
        for exe in exe_names:  # taskkill без /F — вежливо просит окна закрыться
            run(["taskkill", "/IM", exe, "/T"], timeout=15)
    else:
        for proc in processes:
            try:
                proc.kill() if force else proc.terminate()
            except psutil.Error:
                pass
    _gone, alive = psutil.wait_procs(processes, timeout=4)
    if alive:
        return {
            "closed": False,
            "still_running": len(alive),
            "hint": "Программа не закрылась (возможно, свернулась в трей или ждёт сохранения). Можно повторить с force=true.",
        }
    return f"Закрыто: {', '.join(exe_names)}"


@tool()
def list_running_apps() -> list[dict]:
    """Список открытых сейчас программ и их окон."""
    if IS_WIN:
        result = powershell(
            "Get-Process | Where-Object { $_.MainWindowTitle } | Select-Object ProcessName, MainWindowTitle "
            "| ConvertTo-Json -Compress"
        )
        data = json.loads(result.stdout.strip() or "[]")
        if isinstance(data, dict):
            data = [data]
        return [{"program": d["ProcessName"], "window": d["MainWindowTitle"]} for d in data]
    processes = sorted(
        psutil.process_iter(["name", "memory_info"]),
        key=lambda p: p.info["memory_info"].rss if p.info["memory_info"] else 0,
        reverse=True,
    )
    return [{"program": p.info["name"]} for p in processes[:25]]


def _window_process(hwnd: int) -> str:
    pid = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))  # type: ignore[attr-defined]
    try:
        return psutil.Process(pid.value).name().lower()
    except psutil.Error:
        return ""


def _find_window(title: str):
    import pygetwindow as gw

    windows = [w for w in gw.getAllWindows() if w.title.strip() and w.visible]
    query = _norm(title)
    by_title = [w for w in windows if query in _norm(w.title)]
    if by_title:
        return by_title[0]
    _t, spec = _find_spec(title)
    targets = set(spec.get("proc", [])) or {query.replace(" ", "") + ".exe"}
    for window in windows:
        if _window_process(window._hWnd) in targets:
            return window
    return None


@tool()
def window_control(title: str, action: Literal["activate", "minimize", "maximize", "restore", "close"] = "activate") -> str:
    """Управление окном: переключиться на него, свернуть, развернуть на весь экран, восстановить или закрыть.

    Args:
        title: часть заголовка окна или название программы, например «Chrome», «Блокнот», «YouTube»
        action: activate — переключиться на окно, minimize — свернуть, maximize — развернуть,
            restore — восстановить обычный размер, close — закрыть окно
    """
    if not IS_WIN:
        raise NotImplementedError("Управление окнами пока поддерживается только в Windows.")
    window = _find_window(title)
    if window is None:
        raise LookupError(f"Не нашёл окно «{title}».")
    if action == "activate":
        import pyautogui

        if window.isMinimized:
            window.restore()
        pyautogui.press("alt")  # обход запрета Windows на перехват фокуса фоновым процессом
        try:
            window.activate()
        except Exception:
            ctypes.windll.user32.SetForegroundWindow(window._hWnd)  # type: ignore[attr-defined]
    else:
        getattr(window, action)()
    time.sleep(0.2)
    return f"{action}: {window.title}"
