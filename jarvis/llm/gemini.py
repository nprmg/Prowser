"""Google Gemini: ручной цикл вызова функций через google-genai."""

import logging
import time

import httpx
from google import genai
from google.genai import errors, types

from .. import tools
from ..config import Settings, key_problem
from . import base
from .base import (
    COOLDOWN_LONG,
    COOLDOWN_QUOTA,
    MAX_TOOL_ROUNDS,
    TOO_MANY_STEPS,
    ProviderError,
    ToolRunner,
    Turn,
    final_reply,
)

log = logging.getLogger("jarvis.llm")


def _response_text(response: types.GenerateContentResponse) -> str:
    candidate = (response.candidates or [None])[0]
    if not candidate or not candidate.content or not candidate.content.parts:
        return ""
    return "".join(p.text for p in candidate.content.parts if p.text and not p.thought).strip()


class GeminiProvider:
    key = "gemini"
    name = "Gemini"

    def __init__(self, settings: Settings):
        problem = key_problem(settings.api_key)
        if problem:
            raise ValueError(f"Ключ Gemini {problem} — исправьте его кнопкой «Ключи API»")
        self.model = settings.model
        self.timeout = settings.gemini_timeout
        self.client = genai.Client(
            api_key=settings.api_key,
            http_options=types.HttpOptions(
                # Таймаут в миллисекундах. Без него SDK ждёт ответа бесконечно.
                timeout=int(self.timeout * 1000),
                # SDK передаёт таймаут и серверу; слишком короткий серверный срок Google превращает
                # в ошибку 500, поэтому серверу даём свой, более длинный срок.
                headers={"X-Server-Timeout": "60"},
            ),
        )
        # Уровни размышлений по возрастанию: если модель не поддерживает уровень (у gemini-3.8-flash
        # нет minimal), берём следующий, а не выключаем совсем — иначе модель возьмёт свой medium.
        levels = ["minimal", "low", "medium", "high"]
        level = settings.thinking_level
        self._thinking_levels = levels[levels.index(level):] if level in levels else []

    def _config(self, system_prompt: str) -> types.GenerateContentConfig:
        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            tools=[types.Tool(function_declarations=[types.FunctionDeclaration(**d) for d in tools.declarations()])],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if self._thinking_levels:
            config.thinking_config = types.ThinkingConfig(thinking_level=self._thinking_levels[0])
        return config

    def run_turn(self, system_prompt: str, history: list[Turn], text: str, runner: ToolRunner) -> str:
        contents = [
            types.Content(role="user" if turn.role == "user" else "model", parts=[types.Part(text=turn.text)])
            for turn in history
        ]
        contents.append(types.Content(role="user", parts=[types.Part(text=text)]))
        texts: list[str] = []
        # Общий лимит ожидания модели за один ответ (все шаги), чтобы не ждать минутами.
        # Время работы инструментов (диалоги подтверждения, команды) сюда не входит.
        budget, waited = max(base.MIN_TURN_BUDGET, self.timeout * 2), 0.0
        for _ in range(MAX_TOOL_ROUNDS):
            runner.check_cancel()
            if budget - waited < 1:
                raise ProviderError(self.name, "отвечает слишком медленно", retryable=True)
            started = time.monotonic()
            try:
                response = self._generate(contents, system_prompt, min(self.timeout, budget - waited), runner)
            finally:
                waited += time.monotonic() - started
            calls = response.function_calls or []
            if not calls:
                return final_reply(texts, _response_text(response), runner)
            if _response_text(response):
                texts.append(_response_text(response))
            # Ответ модели сохраняем целиком: в нём подписи размышлений (thought signatures),
            # без которых Gemini 3 не примет результаты функций.
            contents.append(response.candidates[0].content)
            contents.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part(
                            function_response=types.FunctionResponse(
                                id=call.id, name=call.name, response=runner.run(call.name, dict(call.args or {}))
                            )
                        )
                        for call in calls
                    ],
                )
            )
        return TOO_MANY_STEPS

    def _generate(
        self, contents: list[types.Content], system_prompt: str, timeout: float, runner: ToolRunner
    ) -> types.GenerateContentResponse:
        while True:
            config = self._config(system_prompt)
            config.http_options = types.HttpOptions(timeout=int(timeout * 1000), headers={"X-Server-Timeout": "60"})
            try:
                return base.run_with_deadline(
                    lambda config=config: self.client.models.generate_content(
                        model=self.model, contents=list(contents), config=config
                    ),
                    timeout,
                    runner.cancel,
                )
            except errors.APIError as exc:
                # Модель не поддерживает этот уровень размышлений — пробуем следующий (или без него).
                if self._thinking_levels and exc.code == 400 and "think" in str(exc).lower():
                    log.warning("Gemini: уровень размышлений %s не подошёл: %s", self._thinking_levels[0], exc)
                    self._thinking_levels.pop(0)
                    continue
                raise self._describe(exc) from exc
            except (httpx.TimeoutException, TimeoutError) as exc:
                raise ProviderError(self.name, f"не ответил за {timeout:.0f} с", retryable=True) from exc
            except httpx.TransportError as exc:
                raise ProviderError(
                    self.name, "нет связи с сервером (проверьте интернет, VPN или HTTPS_PROXY)", retryable=True
                ) from exc

    def _describe(self, exc: errors.APIError) -> ProviderError:
        message = str(exc.message or exc)
        lowered = message.lower()
        code = exc.code or 0
        log.warning("Gemini API error %s: %s", code, message)
        if "location is not supported" in lowered:
            return ProviderError(
                self.name, "недоступен в вашем регионе (нужен VPN или HTTPS_PROXY в .env)", cooldown=COOLDOWN_LONG
            )
        if "api key" in lowered or "api_key" in lowered or code in (401, 403):
            return ProviderError(self.name, "отклонил API-ключ (проверьте GEMINI_API_KEY)", cooldown=COOLDOWN_LONG)
        if code == 429:
            return ProviderError(self.name, "исчерпал лимит запросов бесплатного ключа", cooldown=COOLDOWN_QUOTA)
        if code == 404:
            return ProviderError(
                self.name, f"не нашёл модель «{self.model}» (проверьте GEMINI_MODEL)", cooldown=COOLDOWN_LONG
            )
        if code >= 500:
            return ProviderError(self.name, f"сервер перегружен или недоступен (ошибка {code})", retryable=True)
        return ProviderError(self.name, f"ошибка {code}: {message[:200]}", cooldown=0)
