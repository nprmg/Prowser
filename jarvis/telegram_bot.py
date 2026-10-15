"""Telegram-мост: пишите Джарвису с телефона — он выполнит команду на компьютере и ответит.

Бот отвечает только чатам из TELEGRAM_CHAT_ID (иначе чужой человек смог бы управлять вашим ПК).
Свой chat id можно узнать, написав боту /start: незнакомому чату бот пришлёт его номер.
"""

import logging
import threading
import time

import httpx

from .runtime import runtime
from .tools.integrations import telegram_api, telegram_send_text

log = logging.getLogger("jarvis.telegram")


class TelegramBridge:
    def __init__(self, assistant):
        self.assistant = assistant
        self._stop = threading.Event()
        self._offset = 0
        self._pending: dict[str, tuple[threading.Event, list]] = {}  # chat_id -> ожидание «да/нет»
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="telegram")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _allowed(self, chat_id: str) -> bool:
        return chat_id in runtime.settings.telegram_chat_ids

    def _loop(self) -> None:
        errors = 0
        while not self._stop.is_set():
            if not runtime.settings.telegram_token:
                self._stop.wait(5)
                continue
            try:
                # Long polling: сервер держит запрос до 25 с, пока не придёт сообщение.
                updates = telegram_api("getUpdates", http_timeout=40, offset=self._offset, timeout=25)
                errors = 0
            except (httpx.HTTPError, RuntimeError) as exc:
                errors += 1
                if errors in (1, 10):
                    log.warning("Telegram недоступен: %s", exc)
                self._stop.wait(min(60, 3 * errors))
                continue
            for update in updates:
                self._offset = max(self._offset, update["update_id"] + 1)
                message = update.get("message") or {}
                text = (message.get("text") or "").strip()
                chat_id = str((message.get("chat") or {}).get("id", ""))
                if text and chat_id:
                    self._on_message(chat_id, text)

    def _on_message(self, chat_id: str, text: str) -> None:
        if not self._allowed(chat_id):
            log.warning("Сообщение из чужого чата %s проигнорировано", chat_id)
            if text.startswith("/start"):
                telegram_send_text(chat_id, f"Ваш chat id: {chat_id}. Добавьте его в настройках Джарвиса, "
                                            "раздел «Интеграции», чтобы управлять компьютером отсюда.")
            return
        pending = self._pending.get(chat_id)
        if pending:  # ответ на вопрос «подтвердить?»
            pending[1].append(text)
            pending[0].set()
            return
        if text.startswith("/start"):
            telegram_send_text(chat_id, f"{runtime.settings.assistant_name} на связи. Что сделать?")
            return
        threading.Thread(target=self._handle, args=(chat_id, text), daemon=True).start()

    def _handle(self, chat_id: str, text: str) -> None:
        try:
            reply = self.assistant.handle(text, "telegram", reply_to=chat_id)
        except Exception as exc:  # noqa: BLE001
            log.exception("Ошибка обработки сообщения из Telegram")
            reply = f"Ошибка: {exc}"
        telegram_send_text(chat_id, reply)

    def confirm(self, chat_id: str, prompt: str, timeout: float = 90) -> bool:
        """Спрашивает «да/нет» в Telegram (для действий, требующих подтверждения)."""
        from .voice.listener import parse_yes_no

        event, answers = threading.Event(), []
        self._pending[chat_id] = (event, answers)
        try:
            telegram_send_text(chat_id, f"{prompt}\nОтветьте «да» или «нет».")
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if not event.wait(deadline - time.monotonic()):
                    break
                event.clear()
                verdict = parse_yes_no(answers.pop(0) if answers else "")
                if verdict is not None:
                    return verdict
                telegram_send_text(chat_id, "Не понял. Ответьте «да» или «нет».")
            telegram_send_text(chat_id, "Не дождался ответа — действие отменено.")
            return False
        finally:
            self._pending.pop(chat_id, None)
