"""Общие типы для языковых моделей (провайдеров): история, ошибки, выполнение инструментов."""

import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol

from .. import tools

log = logging.getLogger("jarvis.llm")

MAX_TOOL_ROUNDS = 30  # сколько шагов с инструментами агент может сделать в одной задаче
MIN_TURN_BUDGET = 90.0  # минимальный общий лимит ожидания модели за один ответ (с), без времени инструментов
TOO_MANY_STEPS = "Задача потребовала слишком много шагов, я остановился."

# Сколько секунд не обращаться к сбойному провайдеру в режиме «Авто» (пока есть запасной).
COOLDOWN_SHORT = 180  # перегрузка, таймаут, нет связи — обычно проходит за минуты
COOLDOWN_QUOTA = 900  # исчерпан лимит запросов
COOLDOWN_LONG = 24 * 3600  # регион, неверный ключ, нет денег — само не пройдёт (сбрасывается при смене ключей)


@dataclass
class Turn:
    """Реплика в истории диалога. История общая для всех провайдеров, поэтому хранится только текст."""

    role: Literal["user", "assistant"]
    text: str
    reasoning: str = ""  # reasoning_content DeepSeek (нужен ему в режиме размышлений)


class ProviderError(Exception):
    """Провайдер не смог ответить — мозг попробует запасной."""

    def __init__(self, provider: str, reason: str, *, retryable: bool = False, cooldown: float = COOLDOWN_SHORT):
        super().__init__(f"{provider}: {reason}")
        self.provider = provider
        self.reason = reason  # понятное пользователю описание
        self.status = 0  # HTTP-код, если он есть
        self.detail = ""  # исходный текст ошибки API
        self.retryable = retryable  # имеет ли смысл повторить тот же запрос чуть позже
        self.cooldown = cooldown  # сколько секунд пропускать этого провайдера в режиме «Авто» (0 — не пропускать)


class Cancelled(Exception):
    """Пользователь остановил запрос кнопкой «Стоп»."""


def run_with_deadline(func: Callable[[], Any], timeout: float, cancel: threading.Event | None = None) -> Any:
    """Выполняет func() в фоновом потоке и ждёт не дольше timeout секунд по настенным часам.

    Таймауты httpx считаются на каждое чтение из сети, поэтому ответ, который приходит «по капле»,
    может тянуться минутами. Здесь лимит общий; брошенный вызов доработает в фоне и будет забыт.
    """
    box: queue.Queue = queue.Queue(maxsize=1)

    def target() -> None:
        try:
            box.put((True, func()))
        except BaseException as exc:  # noqa: BLE001 — передаём любую ошибку в вызывающий поток
            box.put((False, exc))

    threading.Thread(target=target, daemon=True, name="llm-call").start()
    deadline = time.monotonic() + timeout
    while True:
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(f"no answer in {timeout:.0f}s")
        try:
            ok, value = box.get(timeout=min(0.2, remaining))
        except queue.Empty:
            continue
        if ok:
            return value
        raise value


class ToolRunner:
    """Выполняет инструменты в рамках одного запроса и запоминает, что уже сделано.

    Если провайдер упал посреди запроса, запасной получит список сделанного и не повторит действия.
    """

    def __init__(self, on_tool_call: Callable[[str, dict[str, Any]], None], cancel: threading.Event | None = None):
        self.on_tool_call = on_tool_call
        self.cancel = cancel or threading.Event()  # установлен — пользователь нажал «Стоп»
        self.done: list[str] = []
        self.reasoning = ""  # reasoning_content финального ответа (только DeepSeek)

    def check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()

    def run(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.check_cancel()  # после «Стоп» новые действия не выполняем
        self.on_tool_call(name, args)
        result = tools.execute(name, args)
        log.info("tool %s(%s) -> %s", name, args, str(result)[:500])
        short = json.dumps(result, ensure_ascii=False, default=str)
        self.done.append(f"{name}({json.dumps(args, ensure_ascii=False, default=str)}) -> {short[:300]}")
        return result


class Provider(Protocol):
    key: str  # "gemini" | "deepseek"
    name: str  # для пользователя
    model: str

    def run_turn(self, system_prompt: str, history: list[Turn], text: str, runner: ToolRunner) -> str:
        """Отвечает на одно сообщение пользователя, вызывая инструменты через runner."""
        ...


def final_reply(texts: list[str], final: str, runner: ToolRunner) -> str:
    if final:
        return final
    if texts:
        return " ".join(texts)
    return "Готово." if runner.done else "Простите, не получилось сформулировать ответ."
