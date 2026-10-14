"""Общие типы для языковых моделей (провайдеров): история, ошибки, выполнение инструментов."""

import json
import logging
from dataclasses import dataclass
from typing import Any, Callable, Literal, Protocol

from .. import tools

log = logging.getLogger("jarvis.llm")

MAX_TOOL_ROUNDS = 12  # сколько раз подряд модель может вызывать инструменты в одном ответе
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


class ToolRunner:
    """Выполняет инструменты в рамках одного запроса и запоминает, что уже сделано.

    Если провайдер упал посреди запроса, запасной получит список сделанного и не повторит действия.
    """

    def __init__(self, on_tool_call: Callable[[str, dict[str, Any]], None]):
        self.on_tool_call = on_tool_call
        self.done: list[str] = []
        self.reasoning = ""  # reasoning_content финального ответа (только DeepSeek)

    def run(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
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
