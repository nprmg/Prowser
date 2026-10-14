"""Контроллер: связывает мозг (Gemini), голос и интерфейс (GUI или консоль)."""

import logging
import threading
import time
from datetime import datetime
from typing import Any, Protocol

from .brain import Brain
from .config import Settings
from .runtime import runtime
from .voice import Speaker

log = logging.getLogger("jarvis")


class UI(Protocol):
    def show_user(self, text: str, source: str) -> None: ...
    def show_reply(self, text: str) -> None: ...
    def show_tool(self, name: str, args: dict[str, Any]) -> None: ...
    def show_info(self, text: str, error: bool = False) -> None: ...
    def set_status(self, status: str) -> None: ...  # idle | listening | thinking | speaking
    def confirm(self, prompt: str) -> bool: ...


def greeting(settings: Settings) -> str:
    hour = datetime.now().hour
    if 5 <= hour < 12:
        part = "Доброе утро"
    elif 12 <= hour < 18:
        part = "Добрый день"
    elif 18 <= hour < 23:
        part = "Добрый вечер"
    else:
        part = "Доброй ночи"
    return f"{part}, {settings.user_title}. {settings.assistant_name} к вашим услугам."


class Assistant:
    def __init__(self, settings: Settings, ui: UI):
        self.settings = settings
        self.ui = ui
        self.speaker = Speaker(settings)
        self.brain = Brain(settings, on_tool_call=ui.show_tool)
        self.listener = None
        self.voice_error = ""
        try:
            from .voice.listener import Listener

            self.listener = Listener(settings)
        except Exception as exc:  # нет sounddevice/PortAudio/SpeechRecognition — работаем только чатом
            self.voice_error = f"Голосовой ввод недоступен: {exc}"
            log.warning(self.voice_error)
        self._local = threading.local()
        self._direct_next = threading.Event()
        runtime.confirm = self._confirm
        runtime.notify = self._notify

    # --- Обработка запросов -------------------------------------------------------------

    def handle(self, text: str, source: str = "text") -> str:
        """Обрабатывает одну просьбу пользователя (блокирует до конца ответа и озвучки)."""
        self._local.source = source
        self.ui.show_user(text, source)
        self.ui.set_status("thinking")
        reply = self.brain.ask(text)
        self.ui.show_reply(reply)
        self.say(reply)
        return reply

    def say(self, text: str) -> None:
        if self.speaker.enabled:
            self.ui.set_status("speaking")
            self.speaker.say(text)
        self.ui.set_status("idle")

    def _notify(self, message: str) -> None:
        """Сработал таймер или напоминание."""
        self.ui.show_info(f"⏰ {message}")
        threading.Thread(target=self.say, args=(f"{self.settings.user_title}, напоминаю: {message}",), daemon=True).start()

    # --- Подтверждение опасных действий -------------------------------------------------

    def _confirm(self, prompt: str) -> bool:
        if getattr(self._local, "source", "text") == "voice" and self.listener:
            return self.confirm_by_voice(prompt)
        return self.ui.confirm(prompt)

    def confirm_by_voice(self, prompt: str) -> bool:
        from .voice.listener import ListenerError, parse_yes_no

        self.ui.show_info(f"Подтверждение: {prompt}")
        self.say(f"{prompt} Скажите «да» или «нет».")
        for _attempt in range(2):
            self.ui.set_status("listening")
            try:
                answer = self.listener.listen_once(timeout=7, phrase_time_limit=5) or ""
            except ListenerError as exc:
                self.ui.show_info(str(exc), error=True)
                break
            verdict = parse_yes_no(answer)
            if answer:
                self.ui.show_user(answer, "voice")
            if verdict is not None:
                self.ui.set_status("thinking")
                return verdict
            self.say("Не расслышал. Да или нет?")
        self.ui.set_status("thinking")
        self.ui.show_info("Действие отменено: подтверждение не получено.")
        return False

    # --- Голос --------------------------------------------------------------------------

    def listen_command(self) -> None:
        """Одна голосовая команда без слова-активатора (кнопка микрофона)."""
        from .voice.listener import ListenerError

        if not self.listener:
            self.ui.show_info(self.voice_error, error=True)
            return
        self.speaker.stop()
        self.ui.set_status("listening")
        try:
            text = self.listener.listen_once(timeout=7)
        except ListenerError as exc:
            self.ui.show_info(str(exc), error=True)
            self.ui.set_status("idle")
            return
        if text:
            self.handle(text, "voice")
        else:
            self.ui.show_info("Не расслышал, попробуйте ещё раз.")
            self.ui.set_status("idle")

    def request_direct_command(self) -> None:
        """Во время фонового прослушивания: следующая фраза считается командой без слова-активатора."""
        self.speaker.stop()
        self._direct_next.set()
        self.ui.set_status("listening")

    def wake_word_loop(self, stop: threading.Event) -> None:
        """Слушает микрофон и реагирует на фразы со словом-активатором («Джарвис, …»)."""
        from .voice.listener import ListenerError, split_wake_word

        if not self.listener:
            self.ui.show_info(self.voice_error, error=True)
            return
        errors_in_row = 0
        while not stop.is_set():
            while self.speaker.speaking.is_set() and not stop.is_set():
                time.sleep(0.1)
            try:
                text = self.listener.listen_once(timeout=4)
                errors_in_row = 0
            except ListenerError as exc:
                errors_in_row += 1
                if errors_in_row in (1, 5):
                    self.ui.show_info(str(exc), error=True)
                time.sleep(min(30, 2 * errors_in_row))
                continue
            if stop.is_set() or not text or self.speaker.speaking.is_set():
                continue
            if self._direct_next.is_set():
                self._direct_next.clear()
                self.handle(text, "voice")
                continue
            found, command = split_wake_word(text, self.settings.wake_words)
            if not found:
                log.debug("Без слова-активатора: %s", text)
                continue
            if not command:
                self.ui.show_user(text, "voice")
                self.say(f"Да, {self.settings.user_title}?")
                self.ui.set_status("listening")
                try:
                    command = self.listener.listen_once(timeout=7) or ""
                except ListenerError as exc:
                    self.ui.show_info(str(exc), error=True)
                    command = ""
                if not command:
                    self.ui.set_status("idle")
                    continue
            self.handle(command, "voice")
