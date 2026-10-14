"""Мозг ассистента: выбирает языковую модель (Gemini или DeepSeek) и переключается при сбоях."""

import logging
import threading
import time
from typing import Any, Callable

from . import quick
from .config import Settings
from .llm.base import Provider, ProviderError, ToolRunner, Turn
from .llm.prompt import build_system_prompt, with_done_note
from .runtime import runtime

log = logging.getLogger("jarvis.brain")

MAX_HISTORY = 40  # сколько реплик истории держать (старые отбрасываются)
RETRY_DELAY = 2.0

MODES = {"auto": "Авто", "gemini": "Gemini", "deepseek": "DeepSeek"}


class Brain:
    def __init__(
        self,
        settings: Settings,
        on_tool_call: Callable[[str, dict[str, Any]], None] | None = None,
        on_notice: Callable[[str], None] | None = None,
        on_provider: Callable[[str], None] | None = None,
    ):
        self.on_tool_call = on_tool_call or (lambda name, args: None)
        self.on_notice = on_notice or (lambda text: None)
        self.on_provider = on_provider or (lambda name: None)
        self._lock = threading.Lock()
        self.history: list[Turn] = []
        self.last_provider = ""
        self._cooldown_until: dict[str, float] = {}
        self._configure(settings)

    # --- Настройка ----------------------------------------------------------------------

    def _configure(self, settings: Settings) -> None:
        providers: dict[str, Provider] = {}
        if settings.api_key:
            from .llm.gemini import GeminiProvider

            providers["gemini"] = GeminiProvider(settings)
        if settings.deepseek_api_key:
            from .llm.deepseek import DeepSeekProvider

            providers["deepseek"] = DeepSeekProvider(settings)
        if not providers:
            raise ValueError("Не задан ни один API-ключ: укажите GEMINI_API_KEY и/или DEEPSEEK_API_KEY")
        self.settings = settings
        self.providers = providers
        runtime.settings = settings
        # Инструменты «посмотреть на экран» и «поиск Google» работают только через Gemini.
        runtime.client = providers["gemini"].client if "gemini" in providers else None
        self.mode = settings.provider if settings.provider in providers else "auto"
        self.system_prompt = build_system_prompt(settings)
        self._cooldown_until.clear()

    def reconfigure(self, settings: Settings) -> None:
        """Применяет новые ключи/настройки, не теряя историю диалога."""
        with self._lock:
            self._configure(settings)

    def available_modes(self) -> list[str]:
        return ["auto", *self.providers]

    def set_mode(self, mode: str) -> None:
        if mode not in self.available_modes():
            raise ValueError(f"Неизвестный режим: {mode}")
        self.mode = mode
        self._cooldown_until.clear()

    def describe(self) -> str:
        names = [f"{p.name} ({p.model})" for p in self.providers.values()]
        if self.mode == "auto" and len(names) > 1:
            return f"{names[0]}, запасной — {', '.join(names[1:])}"
        if self.mode == "auto":
            return names[0]
        provider = self.providers[self.mode]
        return f"только {provider.name} ({provider.model})"

    def reset(self) -> None:
        """Начать новый диалог (забыть историю)."""
        with self._lock:
            self.history.clear()

    # --- Ответ --------------------------------------------------------------------------

    def _order(self) -> list[tuple[str, Provider]]:
        if self.mode != "auto":
            return [(self.mode, self.providers[self.mode])]
        now = time.monotonic()
        items = list(self.providers.items())
        ready = [item for item in items if self._cooldown_until.get(item[0], 0) <= now]
        cooling = [item for item in items if item not in ready]
        return ready + cooling  # «остывающие» — только в крайнем случае

    def ask(self, text: str) -> str:
        """Отправляет сообщение, выполняет все нужные инструменты и возвращает итоговый ответ."""
        with self._lock:
            runner = ToolRunner(self.on_tool_call)
            if self.settings.fast_commands:
                command = quick.match(text, self.settings.wake_words, self.settings.user_title)
                if command:
                    reply = quick.describe_result(command, runner.run(command.tool, command.args))
                    self._remember(text, reply)
                    self.last_provider = "быстрая команда"
                    return reply
            queue = self._order()
            failures: list[ProviderError] = []
            retried: set[str] = set()
            while queue:
                key, provider = queue.pop(0)
                self.on_provider(provider.name)
                started = time.monotonic()
                try:
                    reply = provider.run_turn(
                        self.system_prompt, self.history, with_done_note(text, runner.done), runner
                    )
                except ProviderError as exc:
                    error = exc
                except Exception as exc:  # неожиданная ошибка не должна ронять ассистента
                    log.exception("Ошибка провайдера %s", provider.name)
                    error = ProviderError(provider.name, f"внутренняя ошибка: {exc}", cooldown=0)
                else:
                    log.info("%s ответил за %.1f с", provider.name, time.monotonic() - started)
                    self._remember(text, reply, runner.reasoning)
                    self.last_provider = provider.name
                    return reply
                log.warning("%s: сбой через %.1f с — %s", provider.name, time.monotonic() - started, error.reason)
                failures.append(error)
                if error.cooldown and len(self.providers) > 1:
                    self._cooldown_until[key] = time.monotonic() + error.cooldown
                if queue:
                    self.on_notice(f"{provider.name} {error.reason}. Отвечаю через {queue[0][1].name}.")
                elif error.retryable and key not in retried:
                    retried.add(key)
                    self.on_notice(f"{provider.name} {error.reason}. Пробую ещё раз…")
                    time.sleep(RETRY_DELAY)
                    queue.append((key, provider))
            details = "; ".join(f"{e.provider} {e.reason}" for e in failures)
            return f"Простите, {self.settings.user_title}, не удалось получить ответ: {details}."

    def _remember(self, text: str, reply: str, reasoning: str = "") -> None:
        self.history += [Turn("user", text), Turn("assistant", reply, reasoning)]
        if len(self.history) > MAX_HISTORY:
            del self.history[: len(self.history) - MAX_HISTORY]
