"""Таймеры и напоминания (живут, пока запущен ассистент)."""

import itertools
import re
import threading
from datetime import datetime, timedelta

from ..runtime import runtime
from .registry import tool

_timers: dict[int, tuple[threading.Timer, datetime, str]] = {}
_ids = itertools.count(1)
_lock = threading.Lock()


def _fire(timer_id: int, message: str) -> None:
    with _lock:
        _timers.pop(timer_id, None)
    runtime.notify(message)


def _schedule(seconds: float, message: str) -> dict:
    when = datetime.now() + timedelta(seconds=seconds)
    with _lock:
        timer_id = next(_ids)
        timer = threading.Timer(seconds, _fire, args=(timer_id, message))
        timer.daemon = True
        _timers[timer_id] = (timer, when, message)
        timer.start()
    return {"timer_id": timer_id, "fires_at": when.strftime("%d.%m %H:%M:%S"), "message": message}


@tool()
def set_timer(seconds: int, message: str = "Время вышло") -> dict:
    """Поставить таймер: по истечении времени ассистент скажет сообщение голосом и покажет его в чате.

    Args:
        seconds: через сколько секунд сработать (5 минут = 300, 1 час = 3600)
        message: что сказать, когда таймер сработает
    """
    if seconds <= 0:
        raise ValueError("Время должно быть больше нуля")
    return _schedule(seconds, message)


@tool()
def set_reminder(at: str, message: str) -> dict:
    """Напомнить о чём-то в указанное время (сегодня или завтра, если это время уже прошло).

    Args:
        at: время в формате ЧЧ:ММ, например «18:30»
        message: текст напоминания
    """
    match = re.match(r"^\s*(\d{1,2})[:.\s-](\d{2})", at)
    if not match:
        raise ValueError("Укажите время в формате ЧЧ:ММ")
    now = datetime.now()
    target = now.replace(hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return _schedule((target - now).total_seconds(), message)


@tool()
def list_timers() -> list[dict]:
    """Показать активные таймеры и напоминания."""
    with _lock:
        return [
            {"timer_id": tid, "fires_at": when.strftime("%d.%m %H:%M:%S"), "message": message}
            for tid, (_timer, when, message) in sorted(_timers.items())
        ]


@tool()
def cancel_timer(timer_id: int) -> str:
    """Отменить таймер или напоминание.

    Args:
        timer_id: номер таймера (из set_timer или list_timers)
    """
    with _lock:
        entry = _timers.pop(timer_id, None)
    if not entry:
        raise LookupError(f"Таймер {timer_id} не найден")
    entry[0].cancel()
    return f"Таймер {timer_id} отменён"
