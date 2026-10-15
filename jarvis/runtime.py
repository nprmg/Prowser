"""Общее состояние, которое интерфейс (GUI/консоль) передаёт инструментам."""

import logging
import threading
from typing import Any, Callable

from .config import Settings

log = logging.getLogger("jarvis")


def _deny(prompt: str) -> bool:
    log.warning("Нет обработчика подтверждения, действие отклонено: %s", prompt)
    return False


class Runtime:
    def __init__(self) -> None:
        self.settings: Settings = Settings()
        # Клиент Gemini (google.genai.Client) — нужен инструментам, которые сами обращаются к модели.
        self.client: Any = None
        # Спросить у пользователя подтверждение опасного действия. Возвращает True, если разрешено.
        self.confirm: Callable[[str], bool] = _deny
        # Асинхронное уведомление пользователя (таймеры, напоминания).
        self.notify: Callable[[str], None] = lambda text: log.info("notify: %s", text)
        # Вызывается перед вводом с клавиатуры, чтобы окно ассистента не перехватило нажатия.
        self.release_focus: Callable[[], None] = lambda: None
        # Установлен, когда пользователь нажал «Стоп» (долгие инструменты, например wait, его проверяют).
        self.cancel = threading.Event()


runtime = Runtime()
