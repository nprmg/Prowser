"""DeepSeek: OpenAI-совместимый Chat Completions API с вызовом функций (через httpx, без лишних зависимостей)."""

import json
import logging
import time
from typing import Any

import httpx

from .. import tools
from ..config import Settings
from .base import COOLDOWN_LONG, MAX_TOOL_ROUNDS, TOO_MANY_STEPS, ProviderError, ToolRunner, Turn, final_reply

log = logging.getLogger("jarvis.llm")


class DeepSeekProvider:
    key = "deepseek"
    name = "DeepSeek"

    def __init__(self, settings: Settings):
        self.model = settings.deepseek_model
        self.timeout = settings.deepseek_timeout
        # Размышления у DeepSeek включены по умолчанию и сильно замедляют ответ,
        # поэтому режим всегда передаём явно (по умолчанию — disabled).
        self.thinking = settings.deepseek_thinking if settings.deepseek_thinking in ("enabled", "disabled") else ""
        self.client = httpx.Client(
            base_url=settings.deepseek_base_url,
            headers={"Authorization": f"Bearer {settings.deepseek_api_key}"},
            # Таймаут на соединение и на паузу между порциями данных; общий лимит — в _complete.
            timeout=httpx.Timeout(min(self.timeout, 10.0), connect=10.0),
        )

    @staticmethod
    def _tools() -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": d["name"],
                    "description": d["description"],
                    "parameters": d.get("parameters_json_schema") or {"type": "object", "properties": {}},
                },
            }
            for d in tools.declarations()
        ]

    def run_turn(self, system_prompt: str, history: list[Turn], text: str, runner: ToolRunner) -> str:
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        for turn in history:
            message: dict[str, Any] = {"role": turn.role, "content": turn.text}
            if turn.reasoning and self.thinking == "enabled":
                # В режиме размышлений с инструментами DeepSeek требует reasoning_content прошлых ответов.
                message["reasoning_content"] = turn.reasoning
            messages.append(message)
        messages.append({"role": "user", "content": text})
        texts: list[str] = []
        for _ in range(MAX_TOOL_ROUNDS):
            message = self._complete(messages)
            calls = message.get("tool_calls") or []
            content = (message.get("content") or "").strip()
            if not calls:
                runner.reasoning = message.get("reasoning_content") or ""
                return final_reply(texts, content, runner)
            if content:
                texts.append(content)
            assistant: dict[str, Any] = {"role": "assistant", "content": message.get("content") or "", "tool_calls": calls}
            # В режиме размышлений с инструментами DeepSeek требует вернуть reasoning_content в том же запросе.
            if message.get("reasoning_content"):
                assistant["reasoning_content"] = message["reasoning_content"]
            messages.append(assistant)
            for call in calls:
                function = call.get("function") or {}
                try:
                    args = json.loads(function.get("arguments") or "{}")
                    result = runner.run(function.get("name", ""), args if isinstance(args, dict) else {})
                except json.JSONDecodeError:
                    result = {"error": "Аргументы функции не являются корректным JSON, повторите вызов."}
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id", ""),
                    "content": json.dumps(result, ensure_ascii=False, default=str),
                })
        return TOO_MANY_STEPS

    def _complete(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        while True:
            try:
                return self._request(messages)
            except ProviderError as exc:
                # Если в режиме размышлений API не принял историю (нет reasoning_content у старых
                # ответов, например написанных Gemini), продолжаем без размышлений.
                if self.thinking == "enabled" and exc.status == 400 and "reasoning" in exc.detail.lower():
                    log.warning("DeepSeek: отключаю режим размышлений из-за ошибки: %s", exc.detail)
                    self.thinking = "disabled"
                    continue
                raise

    def _request(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "tools": self._tools(),
            "tool_choice": "auto",
            "max_tokens": 2048,
            "stream": False,
        }
        if self.thinking:
            body["thinking"] = {"type": self.thinking}
        deadline = time.monotonic() + self.timeout
        try:
            # Под нагрузкой DeepSeek держит соединение пустыми строками, поэтому читаем тело
            # по частям и сами следим за общим временем ожидания.
            with self.client.stream("POST", "/chat/completions", json=body) as response:
                chunks = []
                for chunk in response.iter_bytes():
                    chunks.append(chunk)
                    if time.monotonic() > deadline:
                        raise httpx.ReadTimeout("deadline")
                raw = b"".join(chunks)
                status = response.status_code
        except httpx.TimeoutException as exc:
            raise ProviderError(self.name, f"не ответил за {self.timeout:.0f} с", retryable=True) from exc
        except httpx.TransportError as exc:
            raise ProviderError(self.name, "нет связи с сервером (проверьте интернет)", retryable=True) from exc
        try:
            data = json.loads(raw.decode("utf-8", "replace") or "{}")
        except json.JSONDecodeError:
            data = {}
        if status != 200:
            raise self._describe(status, data, raw)
        try:
            choice = data["choices"][0]
            message = choice["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(self.name, "вернул пустой ответ", retryable=True) from exc
        if choice.get("finish_reason") == "insufficient_system_resource":
            raise ProviderError(self.name, "перегружен (не хватило ресурсов сервера)", retryable=True)
        return message

    def _describe(self, status: int, data: dict[str, Any], raw: bytes) -> ProviderError:
        error = data.get("error") if isinstance(data, dict) else None
        detail = (error.get("message") if isinstance(error, dict) else None) or raw.decode("utf-8", "replace")[:300]
        log.warning("DeepSeek API error %s: %s", status, detail)
        if status == 401:
            reason, kwargs = "отклонил API-ключ (проверьте DEEPSEEK_API_KEY)", {"cooldown": COOLDOWN_LONG}
        elif status == 402:
            reason, kwargs = "сообщил, что на счёте закончились деньги — пополните баланс", {"cooldown": COOLDOWN_LONG}
        elif status == 429:
            reason, kwargs = "просит подождать: слишком много запросов", {"retryable": True, "cooldown": 60}
        elif status >= 500:
            reason, kwargs = f"сервер перегружен или недоступен (ошибка {status})", {"retryable": True}
        elif "model" in detail.lower() and ("exist" in detail.lower() or "not found" in detail.lower()):
            reason, kwargs = f"не знает модель «{self.model}» (проверьте DEEPSEEK_MODEL)", {"cooldown": COOLDOWN_LONG}
        else:
            reason, kwargs = f"ошибка {status}: {detail[:200]}", {"cooldown": 0}
        error_ = ProviderError(self.name, reason, **kwargs)
        error_.status, error_.detail = status, detail
        return error_
