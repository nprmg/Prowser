"""Система: время, состояние ПК, звук, яркость, мультимедиа, питание, скриншоты, команды."""

import ctypes
import getpass
import io
import logging
import platform
import re
import shutil
import threading
import time
from datetime import datetime
from typing import Literal

import psutil

from ..runtime import runtime
from ._platform import IS_LINUX, IS_MAC, IS_WIN, ensure_com, powershell, run
from .files import known_folder
from .registry import tool

log = logging.getLogger("jarvis.tools")

_WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
           "ноября", "декабря"]


@tool()
def get_datetime() -> dict:
    """Текущие дата, время и день недели."""
    now = datetime.now()
    return {
        "time": now.strftime("%H:%M"),
        "date": f"{now.day} {_MONTHS[now.month - 1]} {now.year}",
        "weekday": _WEEKDAYS[now.weekday()],
        "iso": now.isoformat(timespec="seconds"),
    }


@tool()
def get_system_info() -> dict:
    """Состояние компьютера: загрузка процессора, оперативная память, диски, батарея, время работы."""
    memory = psutil.virtual_memory()
    info: dict = {
        "os": f"{platform.system()} {platform.release()}",
        "cpu_percent": psutil.cpu_percent(interval=0.5),
        "cpu_cores": psutil.cpu_count(),
        "ram": {
            "used_gb": round(memory.used / 2**30, 1),
            "total_gb": round(memory.total / 2**30, 1),
            "percent": memory.percent,
        },
    }
    disks = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or part.fstype in ("squashfs", "tmpfs") or part.mountpoint.startswith("/snap"):
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        disks.append({
            "disk": part.mountpoint,
            "free_gb": round(usage.free / 2**30, 1),
            "total_gb": round(usage.total / 2**30, 1),
            "used_percent": usage.percent,
        })
    info["disks"] = disks[:8]
    try:
        battery = psutil.sensors_battery()
    except Exception:
        battery = None
    if battery:
        seconds = battery.secsleft
        info["battery"] = {
            "percent": round(battery.percent),
            "charging": battery.power_plugged,
            "minutes_left": seconds // 60 if isinstance(seconds, int) and seconds > 0 else None,
        }
    uptime = time.time() - psutil.boot_time()
    info["uptime"] = f"{int(uptime // 3600)} ч {int(uptime % 3600 // 60)} мин"
    return info


# --- Громкость -----------------------------------------------------------------------------


def _win_endpoint():
    ensure_com()
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    speakers = AudioUtilities.GetSpeakers()
    endpoint = getattr(speakers, "EndpointVolume", None)  # новые версии pycaw
    if endpoint is None:  # старые версии pycaw
        from ctypes import POINTER, cast

        from comtypes import CLSCTX_ALL

        interface = speakers.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        endpoint = cast(interface, POINTER(IAudioEndpointVolume))
    return endpoint


def _press(key: str, presses: int = 1) -> None:
    import pyautogui

    pyautogui.press(key, presses=presses)


def _get_volume() -> int | None:
    try:
        if IS_WIN:
            return round(_win_endpoint().GetMasterVolumeLevelScalar() * 100)
        if IS_MAC:
            return int(run(["osascript", "-e", "output volume of (get volume settings)"]).stdout.strip())
        match = re.search(r"(\d+)%", run(["pactl", "get-sink-volume", "@DEFAULT_SINK@"]).stdout)
        return int(match.group(1)) if match else None
    except Exception as exc:
        log.debug("Не удалось узнать громкость: %s", exc)
        return None


def _set_volume(level: int) -> None:
    level = max(0, min(100, level))
    if IS_WIN:
        try:
            endpoint = _win_endpoint()
            endpoint.SetMasterVolumeLevelScalar(level / 100, None)
            if level > 0:
                endpoint.SetMute(0, None)
            return
        except Exception as exc:
            log.warning("pycaw недоступен (%s), меняю громкость клавишами", exc)
            _press("volumedown", 50)  # каждое нажатие — 2%
            _press("volumeup", round(level / 2))
            return
    if IS_MAC:
        run(["osascript", "-e", f"set volume output volume {level}"])
    elif shutil.which("pactl"):
        run(["pactl", "set-sink-volume", "@DEFAULT_SINK@", f"{level}%"])
    else:
        run(["amixer", "-q", "sset", "Master", f"{level}%"])


@tool()
def set_volume(level: int) -> str:
    """Установить громкость звука в системе.

    Args:
        level: громкость в процентах от 0 до 100
    """
    _set_volume(level)
    current = _get_volume()
    return f"Громкость: {current if current is not None else max(0, min(100, level))}%"


@tool()
def change_volume(delta: int) -> str:
    """Сделать звук громче или тише.

    Args:
        delta: на сколько процентов изменить: положительное — громче, отрицательное — тише (обычно 10 или -10)
    """
    current = _get_volume()
    if current is None and IS_WIN:
        _press("volumeup" if delta > 0 else "volumedown", max(1, abs(delta) // 2))
        return "Громкость изменена"
    new_level = max(0, min(100, (current or 50) + delta))
    _set_volume(new_level)
    return f"Громкость: {new_level}%"


@tool()
def mute(state: Literal["toggle", "on", "off"] = "toggle") -> str:
    """Выключить или включить звук системы.

    Args:
        state: on — выключить звук, off — включить звук обратно, toggle — переключить
    """
    if IS_WIN:
        try:
            endpoint = _win_endpoint()
            muted = bool(endpoint.GetMute())
            new_state = not muted if state == "toggle" else state == "on"
            endpoint.SetMute(int(new_state), None)
            return "Звук выключен" if new_state else "Звук включён"
        except Exception:
            _press("volumemute")
            return "Переключил звук"
    if IS_MAC:
        value = {"on": "true", "off": "false"}.get(state)
        if value is None:
            value = "not (output muted of (get volume settings))"
        run(["osascript", "-e", f"set volume output muted {value}"])
    else:
        run(["pactl", "set-sink-mute", "@DEFAULT_SINK@", {"on": "1", "off": "0"}.get(state, "toggle")])
    return "Готово"


# --- Мультимедиа и яркость -----------------------------------------------------------------

_MEDIA_KEYS = {"play_pause": "playpause", "next": "nexttrack", "previous": "prevtrack", "stop": "stop"}
_PLAYERCTL = {"play_pause": "play-pause", "next": "next", "previous": "previous", "stop": "stop"}


@tool()
def media_control(action: Literal["play_pause", "next", "previous", "stop"]) -> str:
    """Управление воспроизведением музыки и видео (как мультимедийные клавиши клавиатуры).

    Args:
        action: play_pause — пауза/продолжить, next — следующий трек, previous — предыдущий, stop — стоп
    """
    if IS_LINUX and shutil.which("playerctl"):
        run(["playerctl", _PLAYERCTL[action]])
    else:
        _press(_MEDIA_KEYS[action])
    return f"Медиа: {action}"


def _sbc():
    try:
        import screen_brightness_control as sbc
    except ImportError as exc:
        raise RuntimeError("Не установлен пакет screen-brightness-control") from exc
    return sbc


@tool()
def set_brightness(level: int) -> str:
    """Установить яркость экрана (ноутбук или монитор с поддержкой DDC/CI).

    Args:
        level: яркость в процентах от 0 до 100
    """
    level = max(0, min(100, level))
    _sbc().set_brightness(level)
    return f"Яркость: {level}%"


@tool()
def change_brightness(delta: int) -> str:
    """Сделать экран ярче или темнее.

    Args:
        delta: на сколько процентов изменить: положительное — ярче, отрицательное — темнее
    """
    sbc = _sbc()
    current = sbc.get_brightness()
    current = current[0] if isinstance(current, list) else current
    level = max(0, min(100, int(current) + delta))
    sbc.set_brightness(level)
    return f"Яркость: {level}%"


# --- Питание -------------------------------------------------------------------------------

_POWER_LABELS = {
    "shutdown": "выключить компьютер",
    "restart": "перезагрузить компьютер",
    "sleep": "перевести компьютер в спящий режим",
    "hibernate": "перевести компьютер в гибернацию",
    "logout": "выйти из учётной записи",
}


def _confirm_power(action: str, delay_seconds: int = 0) -> str | None:
    if action not in _POWER_LABELS:
        return None
    when = f" через {delay_seconds} с" if delay_seconds else ""
    return f"Подтвердите: {_POWER_LABELS[action]}{when}?"


def _later(seconds: float, func, *args) -> None:
    timer = threading.Timer(seconds, func, args=args)
    timer.daemon = True
    timer.start()


@tool(confirm=_confirm_power)
def power_action(
    action: Literal["shutdown", "restart", "sleep", "hibernate", "lock", "logout", "cancel"],
    delay_seconds: int = 0,
) -> str:
    """Питание и сеанс: выключить, перезагрузить, сон, гибернация, заблокировать экран, выйти из системы
    или отменить запланированное выключение.

    Args:
        action: shutdown, restart, sleep, hibernate, lock, logout или cancel (отмена выключения/перезагрузки)
        delay_seconds: задержка в секундах для выключения и перезагрузки (например 3600 — через час)
    """
    delay = max(5, delay_seconds)  # даём ассистенту договорить
    if IS_WIN:
        if action == "shutdown":
            run(["shutdown", "/s", "/t", str(delay)])
        elif action == "restart":
            run(["shutdown", "/r", "/t", str(delay)])
        elif action == "cancel":
            run(["shutdown", "/a"])
        elif action == "lock":
            ctypes.windll.user32.LockWorkStation()  # type: ignore[attr-defined]
        elif action == "sleep":
            _later(3, ctypes.windll.powrprof.SetSuspendState, False, False, False)  # type: ignore[attr-defined]
        elif action == "hibernate":
            _later(3, run, ["shutdown", "/h"])
        elif action == "logout":
            _later(3, run, ["shutdown", "/l"])
    elif IS_MAC:
        scripts = {
            "shutdown": 'tell app "System Events" to shut down',
            "restart": 'tell app "System Events" to restart',
            "logout": 'tell app "System Events" to log out',
        }
        if action in scripts:
            _later(delay, run, ["osascript", "-e", scripts[action]])
        elif action in ("sleep", "hibernate"):
            _later(3, run, ["pmset", "sleepnow"])
        elif action == "lock":
            run(["pmset", "displaysleepnow"])
        else:
            raise NotImplementedError("Отмена выключения на macOS не поддерживается.")
    elif action in ("shutdown", "restart"):
        if delay_seconds >= 60:
            run(["shutdown", "-h" if action == "shutdown" else "-r", f"+{round(delay_seconds / 60)}"])
        else:
            _later(delay, run, ["systemctl", "poweroff" if action == "shutdown" else "reboot"])
    else:
        commands = {
            "cancel": ["shutdown", "-c"],
            "sleep": ["systemctl", "suspend"],
            "hibernate": ["systemctl", "hibernate"],
            "lock": ["loginctl", "lock-session"],
            "logout": ["loginctl", "terminate-user", getpass.getuser()],
        }
        _later(3 if action in ("sleep", "hibernate", "logout") else 0, run, commands[action])
    if action in ("shutdown", "restart"):
        return f"{_POWER_LABELS[action].capitalize()} через {delay} с. Отменить: power_action(action='cancel')."
    return f"Выполняю: {action}"


# --- Экран ---------------------------------------------------------------------------------


def _grab_screen():
    from PIL import ImageGrab

    try:
        return ImageGrab.grab(all_screens=True)
    except TypeError:
        return ImageGrab.grab()


@tool()
def take_screenshot() -> dict:
    """Сделать скриншот экрана и сохранить его в папку «Изображения/Jarvis»."""
    folder = known_folder("pictures") / "Jarvis"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"screenshot_{datetime.now():%Y-%m-%d_%H-%M-%S}.png"
    _grab_screen().save(path)
    return {"saved_to": str(path)}


@tool()
def look_at_screen(question: str = "Опиши, что сейчас на экране") -> str:
    """Посмотреть на экран пользователя: сделать снимок и проанализировать его.
    Используй, когда спрашивают, что на экране, просят прочитать текст, объяснить ошибку в открытом окне и т. п.

    Args:
        question: что именно нужно понять по изображению экрана
    """
    from google.genai import types

    image = _grab_screen().convert("RGB")
    image.thumbnail((1920, 1920))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=85)
    response = runtime.client.models.generate_content(
        model=runtime.settings.model,
        contents=[
            types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/jpeg"),
            f"Это скриншот экрана пользователя. {question}. Ответь по-русски, кратко и по делу.",
        ],
    )
    return response.text or "Не удалось разобрать изображение."


# --- Команды -------------------------------------------------------------------------------


@tool(
    confirm=lambda command, timeout_seconds=60: f"Выполнить команду?\n\n{command}",
    enabled=lambda: runtime.settings.allow_shell,
)
def run_command(command: str, timeout_seconds: int = 60) -> dict:
    """Выполнить команду в терминале (PowerShell в Windows, bash в Linux/macOS) и вернуть её вывод.
    Используй, только если задачу нельзя решить другими инструментами.

    Args:
        command: команда, например «ipconfig», «Get-Date» или «winget upgrade --all»
        timeout_seconds: максимальное время выполнения в секундах
    """
    timeout = max(1, min(timeout_seconds, 600))
    result = powershell(command, timeout=timeout) if IS_WIN else run(["bash", "-c", command], timeout=timeout)
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    if len(output) > 4000:
        output = output[:4000] + "\n…(вывод обрезан)"
    return {"exit_code": result.returncode, "output": output or "(нет вывода)"}
