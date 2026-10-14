"""Мозг ассистента: выбирает языковую модель (Gemini или DeepSeek) и переключается при сбоях."""

import logging
import threading
import time
from typing import Any, Callable

from . import quick
from .config import Settings
from .llm.base import COOLDOWN_QUOTA, Cancelled, Provider, ProviderError, ToolRunner, Turn
from .llm.prompt import build_system_prompt, with_done_note
from .runtime import runtime

log = logging.getLogger("jarvis.brain")

MAX_HISTORY = 40  # сколько реплик истории держать (старые отбрасываются)
RETRY_DELAY = 2.0
RETRY_BUDGET = 30.0  # повторять запрос, только если с его начала прошло меньше этого времени (с)

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
        self._lock = threading.Lock()  # один запрос к ИИ за раз
        self._history_lock = threading.Lock()  # короткая блокировка только для истории
        self._cancel = threading.Event()  # «Стоп»: прервать текущий запрос к ИИ
        self.history: list[Turn] = []
        self.last_provider = ""
        self.mode = ""
        self._cooldown_until: dict[str, float] = {}
        self._configure(settings)

    # --- Настройка ----------------------------------------------------------------------

    def _configure(self, settings: Settings) -> None:
        providers: dict[str, Provider] = {}
        problems: list[str] = []
        if settings.api_key:
            from .llm.gemini import GeminiProvider

            try:
                providers["gemini"] = GeminiProvider(settings)
            except ValueError as exc:
                problems.append(str(exc))
        if settings.deepseek_api_key:
            from .llm.deepseek import DeepSeekProvider

            try:
                providers["deepseek"] = DeepSeekProvider(settings)
            except ValueError as exc:
                problems.append(str(exc))
        if not providers:
            raise ValueError(
                "; ".join(problems) or "Не задан ни один API-ключ: укажите GEMINI_API_KEY и/или DEEPSEEK_API_KEY"
            )
        for problem in problems:  # один ключ плохой, но второй работает — запускаемся на нём
            self.on_notice(problem)
        self.settings = settings
        self.providers = providers
        runtime.settings = settings
        # Инструменты «посмотреть на экран» и «поиск Google» работают только через Gemini.
        runtime.client = providers["gemini"].client if "gemini" in providers else None
        # Выбор пользователя в окне сохраняем, если этот ИИ по-прежнему доступен.
        if self.mode not in ("auto", *providers):
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
        providers = self.providers
        mode = self.mode
        if mode in providers:
            return f"только {providers[mode].name} ({providers[mode].model})"
        names = [f"{p.name} ({p.model})" for p in providers.values()]
        if len(names) > 1:
            return f"{names[0]}, запасной — {', '.join(names[1:])}"
        return names[0]

    def cancel(self) -> None:
        """Прерывает текущий запрос к ИИ (кнопка «Стоп»)."""
        self._cancel.set()

    def is_quick(self, text: str) -> bool:
        """Будет ли фраза выполнена мгновенно, без ИИ."""
        settings = self.settings
        return settings.fast_commands and quick.match(text, settings.wake_words, settings.user_title) is not None

    def reset(self) -> None:
        """Начать новый диалог (забыть историю)."""
        with self._history_lock:
            self.history.clear()

    # --- Ответ --------------------------------------------------------------------------

    def _order(self) -> list[tuple[str, Provider]]:
        providers = self.providers
        mode = self.mode
        if mode in providers:
            return [(mode, providers[mode])]
        now = time.monotonic()
        left = {key: self._cooldown_until.get(key, 0) - now for key in providers}
        ready = [(k, p) for k, p in providers.items() if left[k] <= 0]
        cooling = sorted(((k, p) for k, p in providers.items() if left[k] > 0), key=lambda item: left[item[0]])
        # «Остывающие» после временного сбоя — в крайнем случае. С долгой паузой (регион, ключ,
        # деньги) — только если больше никого нет: такие ошибки сами не проходят.
        short = [item for item in cooling if left[item[0]] <= COOLDOWN_QUOTA]
        long_ = [item for item in cooling if left[item[0]] > COOLDOWN_QUOTA]
        return ready + short + ([] if ready or short else long_)

    def ask(self, text: str) -> str:
        """Отправляет сообщение, выполняет все нужные инструменты и возвращает итоговый ответ."""
        command = quick.match(text, self.settings.wake_words, self.settings.user_title) if self.settings.fast_commands else None
        if command:
            # Быстрые команды не ждут, пока ИИ ответит на запрос из другого потока.
            reply = quick.describe_result(command, ToolRunner(self.on_tool_call).run(command.tool, command.args))
            self._remember(text, reply)
            self.last_provider = "быстрая команда"
            return reply
        with self._lock:
            return self._ask_llm(text)

    def _ask_llm(self, text: str) -> str:
        self._cancel.clear()
        runner = ToolRunner(self.on_tool_call, self._cancel)
        try:
            return self._try_providers(text, runner)
        except Cancelled:
            reply = "Хорошо, остановился."
            if runner.done:
                done = ", ".join(dict.fromkeys(item.split("(", 1)[0] for item in runner.done))
                reply += f" Уже успел выполнить: {done}."
            self._remember(text, reply)
            return reply

    def _try_providers(self, text: str, runner: ToolRunner) -> str:
        started_at = time.monotonic()
        with self._history_lock:
            history = list(self.history)
        mode = self.mode
        queue = self._order()
        attempted: set[str] = set()
        failures: list[tuple[str, ProviderError]] = []
        retried = False
        while queue:
            key, provider = queue.pop(0)
            attempted.add(key)
            self.on_provider(provider.name)
            started = time.monotonic()
            try:
                reply = provider.run_turn(self.system_prompt, history, with_done_note(text, runner.done), runner)
            except ProviderError as exc:
                error = exc
            except Cancelled:
                raise
            except Exception as exc:  # неожиданная ошибка не должна ронять ассистента
                log.exception("Ошибка провайдера %s", provider.name)
                error = ProviderError(provider.name, f"внутренняя ошибка: {exc}", cooldown=0)
            else:
                log.info("%s ответил за %.1f с", provider.name, time.monotonic() - started)
                self._cooldown_until.pop(key, None)
                self._remember(text, reply, runner.reasoning)
                self.last_provider = provider.name
                return reply
            log.warning("%s: сбой через %.1f с — %s", provider.name, time.monotonic() - started, error.reason)
            failures.append((key, error))
            if error.cooldown and len(self.providers) > 1:
                self._cooldown_until[key] = time.monotonic() + error.cooldown
            if self.mode != mode:  # пользователь выбрал другой ИИ, пока мы ждали
                mode = self.mode
                queue = [item for item in self._order() if item[0] not in attempted]
            if queue:
                self.on_notice(f"{provider.name} {error.reason}. Отвечаю через {queue[0][1].name}.")
                continue
            # Все варианты перепробованы. Один раз повторяем последний временный сбой, если ещё не поздно.
            allowed = dict(self._order())
            retry = next((k for k, e in reversed(failures) if e.retryable and k in allowed), None)
            if retry and not retried and time.monotonic() - started_at < RETRY_BUDGET:
                retried = True
                again = "" if retry == key else f" через {allowed[retry].name}"
                self.on_notice(f"{provider.name} {error.reason}. Пробую ещё раз{again}…")
                if self._cancel.wait(RETRY_DELAY):
                    raise Cancelled()
                queue.append((retry, allowed[retry]))
        reasons = "; ".join(dict.fromkeys(f"{e.provider} {e.reason}" for _, e in failures))
        reply = f"Простите, {self.settings.user_title}, не удалось получить ответ: {reasons}."
        if runner.done:
            done = ", ".join(dict.fromkeys(item.split("(", 1)[0] for item in runner.done))
            reply += f" Но часть действий уже выполнена: {done}."
            self._remember(text, reply)  # чтобы при повторе просьбы ИИ не сделал то же самое ещё раз
        return reply

    def _remember(self, text: str, reply: str, reasoning: str = "") -> None:
        with self._history_lock:
            self.history += [Turn("user", text), Turn("assistant", reply, reasoning)]
            if len(self.history) > MAX_HISTORY:
                del self.history[: len(self.history) - MAX_HISTORY]
