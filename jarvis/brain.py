"""Мозг ассистента: диалог с Gemini и выполнение вызовов инструментов."""

import getpass
import logging
import platform
import threading
import time
from pathlib import Path
from typing import Any, Callable

import httpx
from google import genai
from google.genai import errors, types

from . import tools
from .config import Settings
from .runtime import runtime
from .tools.files import known_folder

log = logging.getLogger("jarvis.brain")

MAX_TOOL_ROUNDS = 12  # сколько раз подряд модель может вызывать инструменты в одном ответе
MAX_HISTORY = 60  # сколько сообщений истории держать (старые отбрасываются)

SYSTEM_PROMPT = """Ты — {name} (J.A.R.V.I.S.), личный ИИ-ассистент в духе Джарвиса из «Железного человека»: \
умный, вежливый, собранный, с лёгкой британской иронией. Ты работаешь на компьютере пользователя \
и управляешь им с помощью инструментов (функций).

Как отвечать:
- Обращайся к пользователю «{title}». Отвечай на языке пользователя (обычно русский).
- Ответы озвучиваются голосом, поэтому говори коротко: одно-два предложения. Подробно — только если просят.
- Пиши обычным текстом без markdown, списков, эмодзи и ссылок — его читает синтезатор речи.
- Не выдумывай факты. Если актуальных данных нет, честно скажи об этом.

Как действовать:
- Для любого действия на компьютере вызывай подходящий инструмент. Никогда не говори, что что-то сделал, \
если не вызвал инструмент и не получил успешный результат.
- Сложные просьбы выполняй по шагам, вызывая несколько инструментов подряд. Например, «открой блокнот \
и напиши список покупок» — это open_app, а затем type_text.
- Если инструмент вернул ошибку, коротко объясни, что пошло не так, и предложи вариант.
- Дату и время узнавай через get_datetime, погоду — через get_weather, курсы валют — через get_exchange_rates.
- Музыку и видео включай через play_youtube, сайты открывай через open_url, поиск в браузере — web_search.
- Чтобы увидеть, что на экране, используй look_at_screen.
- Опасные действия (выключение, удаление, команды терминала) пользователь подтверждает сам в отдельном \
диалоге — просто вызывай инструмент и не переспрашивай.
- run_command используй, только если для задачи нет специального инструмента.
- Голосовой ввод иногда распознаётся с ошибками. Если просьба непонятна, коротко уточни.

О компьютере:
- ОС: {os}; имя пользователя: {user}
- Домашняя папка: {home}
- Рабочий стол: {desktop}
- Загрузки: {downloads}
"""


def _response_text(response: types.GenerateContentResponse) -> str:
    candidate = (response.candidates or [None])[0]
    if not candidate or not candidate.content or not candidate.content.parts:
        return ""
    return "".join(p.text for p in candidate.content.parts if p.text and not p.thought).strip()


def _is_user_text(content: types.Content) -> bool:
    return content.role == "user" and any(p.text for p in (content.parts or []))


class Brain:
    def __init__(self, settings: Settings, on_tool_call: Callable[[str, dict[str, Any]], None] | None = None):
        if not settings.api_key:
            raise ValueError("Не задан GEMINI_API_KEY")
        self.settings = settings
        self.client = genai.Client(api_key=settings.api_key)
        runtime.settings = settings
        runtime.client = self.client
        self.on_tool_call = on_tool_call or (lambda name, args: None)
        self._lock = threading.Lock()
        self._use_thinking = bool(settings.thinking_level)
        self.chat = self._new_chat()

    # --- Настройка диалога --------------------------------------------------------------

    def _system_prompt(self) -> str:
        return SYSTEM_PROMPT.format(
            name=self.settings.assistant_name,
            title=self.settings.user_title,
            os=f"{platform.system()} {platform.release()}",
            user=getpass.getuser(),
            home=Path.home(),
            desktop=known_folder("desktop"),
            downloads=known_folder("downloads"),
        )

    def _config(self) -> types.GenerateContentConfig:
        function_declarations = [types.FunctionDeclaration(**d) for d in tools.declarations()]
        config = types.GenerateContentConfig(
            system_instruction=self._system_prompt(),
            tools=[types.Tool(function_declarations=function_declarations)],
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        if self._use_thinking:
            config.thinking_config = types.ThinkingConfig(thinking_level=self.settings.thinking_level)
        return config

    def _new_chat(self, history: list[types.Content] | None = None):
        return self.client.chats.create(model=self.settings.model, config=self._config(), history=history or [])

    def reset(self) -> None:
        """Начать новый диалог (забыть историю)."""
        with self._lock:
            self.chat = self._new_chat()

    # --- Основной цикл ------------------------------------------------------------------

    def ask(self, text: str) -> str:
        """Отправляет сообщение, выполняет все нужные инструменты и возвращает итоговый ответ."""
        with self._lock:
            history = self._history()
            try:
                return self._run(text)
            except Exception as exc:
                log.exception("Ошибка при обработке запроса")
                # Откатываем незавершённый обмен, чтобы в истории не остались вызовы без ответов.
                self.chat = self._new_chat(history)
                return self._describe_error(exc)

    def _run(self, text: str) -> str:
        response = self._send(text)
        texts: list[str] = []
        used_tools = False
        for _ in range(MAX_TOOL_ROUNDS):
            calls = response.function_calls or []
            if not calls:
                break
            if _response_text(response):
                texts.append(_response_text(response))
            parts = []
            for call in calls:
                args = dict(call.args or {})
                self.on_tool_call(call.name, args)
                result = tools.execute(call.name, args)
                log.info("tool %s(%s) -> %s", call.name, args, str(result)[:500])
                parts.append(
                    types.Part(function_response=types.FunctionResponse(id=call.id, name=call.name, response=result))
                )
            used_tools = True
            response = self._send(parts)
        else:
            raise RuntimeError("Задача потребовала слишком много шагов, я остановился.")
        self._trim_history()
        final = _response_text(response)
        if final:
            return final
        if texts:
            return " ".join(texts)
        return "Готово." if used_tools else "Простите, не получилось сформулировать ответ."

    def _send(self, message: Any) -> types.GenerateContentResponse:
        attempt = 0
        while True:
            attempt += 1
            try:
                return self.chat.send_message(message)
            except errors.ServerError:
                if attempt >= 3:
                    raise
                time.sleep(1.5 * attempt)
            except errors.ClientError as exc:
                # Старые модели (например, 2.5) не знают thinking_level — пробуем без него.
                if self._use_thinking and exc.code == 400 and "think" in str(exc).lower():
                    log.warning("Модель не поддерживает thinking_level, отключаю его: %s", exc)
                    self._use_thinking = False
                    self.chat = self._new_chat(self._history())
                    continue
                raise

    def _history(self) -> list[types.Content]:
        # SDK возвращает свой внутренний список, поэтому берём копию.
        return list(self.chat.get_history(curated=True))

    def _trim_history(self) -> None:
        history = self._history()
        if len(history) <= MAX_HISTORY:
            return
        start = len(history) - MAX_HISTORY
        while start < len(history) and not _is_user_text(history[start]):
            start += 1
        self.chat = self._new_chat(history[start:])

    def _describe_error(self, exc: Exception) -> str:
        if isinstance(exc, errors.APIError):
            message = str(exc.message or exc)
            lowered = message.lower()
            if "location is not supported" in lowered:
                return ("Gemini API недоступен из вашего региона. Включите VPN или укажите прокси "
                        "в HTTPS_PROXY в файле .env.")
            if "api key" in lowered or "api_key" in lowered or exc.code in (401, 403):
                return "Gemini отклонил API-ключ. Проверьте GEMINI_API_KEY в файле .env."
            if exc.code == 429:
                return "Превышен лимит запросов к Gemini. Подождите немного или проверьте квоту ключа."
            if exc.code == 404:
                return f"Модель «{self.settings.model}» недоступна. Укажите другую в GEMINI_MODEL в файле .env."
            if exc.code and exc.code >= 500:
                return "Сервер Gemini сейчас перегружен. Попробуйте чуть позже."
            return f"Ошибка Gemini ({exc.code}): {message}"
        if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
            return "Нет связи с сервером Gemini. Проверьте интернет."
        return f"Произошла ошибка: {exc}"
