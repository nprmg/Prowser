"""Окно Джарвиса на WebView (pywebview): «жидкое стекло», чат, голос, настройки, трей и значок в углу.

Интерфейс — HTML/CSS/JS из jarvis/web. Python отправляет события в окно (window.jarvis.receive),
окно вызывает методы класса Api (window.pywebview.api.*). Агент работает и при закрытом окне:
окно прячется в трей, а «Джарвис, …» по-прежнему слышен.
"""

import ctypes
import itertools
import json
import logging
import os
import queue
import threading
import time
from typing import Any

from ..assistant import Assistant, greeting
from ..config import Settings, clean_key, key_problem, save_env_value
from ..paths import WEB_DIR
from ..runtime import runtime
from ..tools._platform import IS_WIN
from ..voice.profiles import PROFILES

log = logging.getLogger("jarvis.ui")

MASK = "••••••••"  # так в настройках показываются сохранённые ключи


def _masked(key: str) -> str:
    return f"{MASK}{key[-4:]}" if key else ""


class Api:
    """Методы, доступные из JavaScript. Все внутренние ссылки — с подчёркиванием (pywebview их не трогает)."""

    def __init__(self, ui: "WebUI"):
        self._ui = ui

    def ready(self) -> dict:
        return self._ui.on_page_ready()

    def send(self, text: str) -> None:
        self._ui.submit(str(text), "text")

    def mic(self) -> None:
        self._ui.mic()

    def stop(self) -> None:
        self._ui.stop()

    def set_mode(self, mode: str) -> None:
        self._ui.set_mode(str(mode))

    def set_wake(self, enabled: bool) -> None:
        self._ui.set_wake(bool(enabled))

    def set_tts(self, enabled: bool) -> None:
        self._ui.set_tts(bool(enabled))

    def reset(self) -> None:
        self._ui.reset()

    def get_settings(self) -> dict:
        return self._ui.settings_view()

    def save_settings(self, data: dict) -> dict:
        return self._ui.save_settings(data or {})

    def confirm_answer(self, confirm_id: int, ok: bool) -> None:
        self._ui.answer_confirm(int(confirm_id), bool(ok))

    def levels(self) -> dict:
        return self._ui.levels()

    def minimize(self) -> None:
        self._ui.window.minimize()

    def close(self) -> None:
        self._ui.hide_or_quit()


class WebUI:
    def __init__(self, settings: Settings, background: bool = False):
        self.settings = settings
        self.background = background
        self.assistant: Assistant | None = None
        self.window = None
        self.tray = None
        self.overlay = None
        self._page_ready = threading.Event()
        self._buffer: list[dict] = []
        self._buffer_lock = threading.Lock()
        self._tasks: queue.Queue = queue.Queue()  # запросы к ИИ — по одному
        self._quick_tasks: queue.Queue = queue.Queue()  # быстрые команды не ждут ИИ
        self._active = 0
        self._active_lock = threading.Lock()
        self._confirms: dict[int, tuple[threading.Event, list]] = {}
        self._confirm_ids = itertools.count(1)
        self._wake_stop: threading.Event | None = None
        self._status = "idle"
        self._quitting = False
        self._hidden = background

    # --- События в окно ------------------------------------------------------------------

    def emit(self, event: dict) -> None:
        if not self._page_ready.is_set():
            with self._buffer_lock:
                if not self._page_ready.is_set():
                    self._buffer.append(event)
                    return
        self._run_js(event)

    def _run_js(self, event: dict) -> None:
        if self.window is None:
            return
        payload = json.dumps(event, ensure_ascii=False, default=str)
        try:
            self.window.run_js(f"window.jarvis && window.jarvis.receive({payload})")
        except Exception as exc:  # noqa: BLE001 — окно закрывается/ещё не готово
            log.debug("Не удалось отправить событие в окно: %s", exc)

    def on_page_ready(self) -> dict:
        """Окно загрузилось: отдаём состояние и накопленные события."""
        state = self.state()
        with self._buffer_lock:
            pending, self._buffer = self._buffer, []
            self._page_ready.set()
        threading.Thread(target=lambda: [self._run_js(e) for e in pending], daemon=True).start()
        return state

    def state(self) -> dict:
        brain = self.assistant.brain if self.assistant else None
        return {
            "type": "state",
            "assistant_name": self.settings.assistant_name,
            "user_title": self.settings.user_title,
            "modes": brain.available_modes() if brain else ["auto"],
            "mode": brain.mode if brain else "auto",
            "wake": self._wake_stop is not None and not self._wake_stop.is_set(),
            "tts": self.assistant.speaker.enabled if self.assistant else self.settings.tts_engine != "off",
        }

    # --- Методы для Assistant (интерфейс UI) ----------------------------------------------

    def show_user(self, text: str, source: str) -> None:
        self.emit({"type": "user", "text": text, "source": source})

    def show_reply(self, text: str) -> None:
        self.emit({"type": "reply", "text": text})

    def show_tool(self, name: str, args: dict[str, Any]) -> None:
        self.emit({"type": "tool", "name": name, "args": args})

    def show_info(self, text: str, error: bool = False) -> None:
        self.emit({"type": "info", "text": text, "error": error})
        if self._hidden and self.tray is not None and text.startswith("⏰"):
            self.tray.notify(text)

    def set_status(self, status: str) -> None:
        with self._active_lock:
            busy = self._active
        # Быстрая команда закончилась, а запрос к ИИ ещё идёт — по-прежнему «думаю».
        if status == "idle" and busy:
            status = "thinking"
        self._status = status
        self.emit({"type": "status", "status": status})
        if self.overlay is not None:
            self.overlay.set_state(status)

    def begin_request(self) -> None:
        with self._active_lock:
            busy = self._active
        if busy <= 1:
            self.emit({"type": "begin"})

    def show_provider(self, name: str) -> None:
        self.emit({"type": "provider", "name": name})

    def confirm(self, prompt: str) -> bool:
        confirm_id = next(self._confirm_ids)
        event, answer = threading.Event(), []
        self._confirms[confirm_id] = (event, answer)
        self.show_window()
        self.emit({"type": "confirm", "id": confirm_id, "text": prompt})
        try:
            if not event.wait(120):
                self.show_info("Действие отменено: нет ответа.")
                return False
            return bool(answer and answer[0])
        finally:
            self._confirms.pop(confirm_id, None)

    def answer_confirm(self, confirm_id: int, ok: bool) -> None:
        entry = self._confirms.get(confirm_id)
        if entry:
            entry[1].append(ok)
            entry[0].set()

    def release_focus(self) -> None:
        """Если окно Джарвиса активно — сворачиваем его, чтобы ввод попал в нужную программу."""
        if not IS_WIN or self.window is None:
            return
        user32 = ctypes.windll.user32
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), ctypes.byref(pid))
        if pid.value == os.getpid():
            self.window.minimize()
            time.sleep(0.45)

    # --- Действия пользователя ------------------------------------------------------------

    def submit(self, text: str, source: str) -> None:
        text = text.strip()
        if not text:
            return
        if self.assistant is None:
            self.emit({"type": "toast", "text": "Сначала добавьте ключ Gemini или DeepSeek в настройках"})
            return
        tasks = self._quick_tasks if self.assistant.brain.is_quick(text) else self._tasks
        tasks.put((source, text))

    def mic(self) -> None:
        if self.assistant is None:
            return
        if self._wake_stop is not None and not self._wake_stop.is_set():
            self.assistant.request_direct_command()
            return

        def listen() -> None:
            text = self.assistant.listen_text()
            if text:
                self.submit(text, "voice")

        threading.Thread(target=listen, daemon=True, name="mic").start()

    def stop(self) -> None:
        if self.assistant is not None:
            self.assistant.speaker.stop()
            self.assistant.brain.cancel()

    def set_mode(self, mode: str) -> None:
        if self.assistant is None:
            return
        try:
            self.assistant.brain.set_mode(mode)
        except ValueError:
            return
        self.emit({"type": "toast", "text": f"Мозг: {self.assistant.brain.describe()}"})

    def set_tts(self, enabled: bool) -> None:
        if self.assistant is not None:
            self.assistant.speaker.enabled = enabled
            if not enabled:
                self.assistant.speaker.stop()

    def set_wake(self, enabled: bool) -> None:
        if self.assistant is None or self.assistant.listener is None:
            if enabled:
                self.emit({"type": "toast", "text": "Микрофон недоступен"})
            return
        running = self._wake_stop is not None and not self._wake_stop.is_set()
        if enabled and not running:
            self._wake_stop = threading.Event()
            threading.Thread(
                target=self.assistant.wake_word_loop, args=(self._wake_stop,), daemon=True, name="wake-word"
            ).start()
        elif not enabled and running:
            self._wake_stop.set()
        if self.tray is not None:
            self.tray.refresh()
        self.emit(self.state())

    def is_listening(self) -> bool:
        return self._wake_stop is not None and not self._wake_stop.is_set()

    def reset(self) -> None:
        if self.assistant is not None:
            self.assistant.brain.reset()

    def levels(self) -> dict:
        if self.assistant is None:
            return {"mic": 0.0, "speech": 0.0}
        listener = self.assistant.listener
        return {"mic": listener.level if listener else 0.0, "speech": self.assistant.speaker.level()}

    # --- Настройки ------------------------------------------------------------------------

    def settings_view(self) -> dict:
        s = self.settings
        from ..agent import autostart_enabled

        return {
            "voice_profiles": [[key, profile.title] for key, profile in PROFILES.items()],
            "voice_profile": self.assistant.speaker.profile.key if self.assistant else s.voice_profile,
            "gemini_key": _masked(s.api_key),
            "deepseek_key": _masked(s.deepseek_api_key),
            "tts": self.assistant.speaker.enabled if self.assistant else s.tts_engine != "off",
            "listen_on_start": s.listen_on_start,
            "overlay": s.overlay,
            "full_access": s.full_access,
            "confirm_power": s.confirm_power,
            "autostart": autostart_enabled(),
            "fast_commands": s.fast_commands,
            "telegram_token": _masked(s.telegram_token),
            "telegram_chat_ids": ", ".join(s.telegram_chat_ids),
            "ha_url": s.ha_url,
            "ha_token": _masked(s.ha_token),
        }

    def save_settings(self, data: dict) -> dict:
        s = self.settings

        def secret(field: str, env: str, current: str) -> str | None:
            value = str(data.get(field, ""))
            if value.startswith(MASK):  # не меняли
                return current
            value = clean_key(value)
            problem = key_problem(value)
            if problem:
                raise ValueError(f"{env}: ключ {problem}")
            save_env_value(env, value)
            return value

        try:
            gemini = secret("gemini_key", "GEMINI_API_KEY", s.api_key)
            deepseek = secret("deepseek_key", "DEEPSEEK_API_KEY", s.deepseek_api_key)
            telegram = secret("telegram_token", "TELEGRAM_BOT_TOKEN", s.telegram_token)
            ha_token = secret("ha_token", "HOME_ASSISTANT_TOKEN", s.ha_token)
        except ValueError as exc:
            return {"error": str(exc)}
        if not gemini and not deepseek:
            return {"error": "Нужен хотя бы один ключ: Gemini или DeepSeek"}
        s.api_key, s.deepseek_api_key, s.telegram_token, s.ha_token = gemini, deepseek, telegram, ha_token

        flags = {
            "listen_on_start": "LISTEN_ON_START", "overlay": "OVERLAY", "full_access": "FULL_ACCESS",
            "confirm_power": "CONFIRM_POWER", "fast_commands": "FAST_COMMANDS",
        }
        for field, env in flags.items():
            if field in data:
                setattr(s, field, bool(data[field]))
                save_env_value(env, "true" if data[field] else "false")
        s.telegram_chat_ids = [c.strip() for c in str(data.get("telegram_chat_ids", "")).split(",") if c.strip()]
        save_env_value("TELEGRAM_CHAT_ID", ",".join(s.telegram_chat_ids))
        s.ha_url = str(data.get("ha_url", "")).strip()
        save_env_value("HOME_ASSISTANT_URL", s.ha_url)
        if data.get("voice_profile"):
            s.voice_profile = str(data["voice_profile"])
            save_env_value("VOICE_PROFILE", s.voice_profile)
        tts = bool(data.get("tts", True))
        s.tts_engine = "edge" if tts else "off"
        save_env_value("TTS_ENGINE", s.tts_engine)

        if "autostart" in data and IS_WIN:
            from ..agent import set_autostart

            try:
                set_autostart(bool(data["autostart"]))
            except OSError as exc:
                log.warning("Автозапуск: %s", exc)
        self._tasks.put(("apply-settings", None))
        return {"ok": True}

    def _apply_settings(self) -> None:
        if self.assistant is None:
            self._create_assistant()
            return
        self.assistant.brain.reconfigure(self.settings)
        self.assistant.speaker.set_profile(self.settings.voice_profile)
        self.set_tts(self.settings.tts_engine != "off")
        self.assistant.start_integrations()
        self.set_wake(self.settings.listen_on_start)
        if self.overlay is not None:
            self.overlay.enabled = self.settings.overlay
        self.emit(self.state())
        self.emit({"type": "info", "text": f"Мозг: {self.assistant.brain.describe()}."})

    # --- Окно и трей ----------------------------------------------------------------------

    def show_window(self) -> None:
        if self.window is not None:
            self._hidden = False
            self.window.show()
            self.window.restore()

    def hide_or_quit(self) -> None:
        if self.tray is not None and self.tray.available:
            self._hidden = True
            self.window.hide()
            if not getattr(self, "_told_tray", False):
                self._told_tray = True
                self.tray.notify("Джарвис работает в фоне. Откройте его через значок в трее.")
        else:
            self.quit()

    def quit(self) -> None:
        self._quitting = True
        if self._wake_stop is not None:
            self._wake_stop.set()
        if self.assistant is not None:
            self.assistant.speaker.stop()
            if self.assistant.telegram is not None:
                self.assistant.telegram.stop()
        if self.overlay is not None:
            self.overlay.stop()
        if self.tray is not None:
            self.tray.stop()
        if self.window is not None:
            self.window.destroy()

    def _on_closing(self) -> bool | None:
        if self._quitting or self.tray is None or not self.tray.available:
            return None
        threading.Thread(target=self.hide_or_quit, daemon=True).start()
        return False  # не закрывать — спрятать в трей

    # --- Рабочие потоки -------------------------------------------------------------------

    def _worker(self, tasks: queue.Queue) -> None:
        while True:
            kind, payload = tasks.get()
            with self._active_lock:
                self._active += 1
            try:
                if kind in ("text", "voice"):
                    self.assistant.handle(payload, kind)
                elif kind == "say":
                    self.assistant.say(payload)
                elif kind == "apply-settings":
                    self._apply_settings()
            except Exception as exc:  # noqa: BLE001
                log.exception("Ошибка обработки")
                self.show_info(f"Ошибка: {exc}", error=True)
            finally:
                with self._active_lock:
                    self._active -= 1
                self.set_status("idle")

    def _create_assistant(self) -> bool:
        try:
            self.assistant = Assistant(self.settings, self)
        except ValueError:
            self.emit({"type": "info", "text": "Добавьте ключ Gemini или DeepSeek в настройках (кнопка вверху справа)."})
            return False
        except Exception as exc:  # noqa: BLE001
            log.exception("Не удалось запустить ассистента")
            self.emit({"type": "info", "text": f"Не удалось запустить ассистента: {exc}", "error": True})
            return False
        runtime.release_focus = self.release_focus
        if self.assistant.voice_error:
            self.show_info(self.assistant.voice_error, error=True)
        if self.settings.listen_on_start:
            self.set_wake(True)
        self.emit(self.state())
        hello = greeting(self.settings)
        self.show_reply(hello)
        self._tasks.put(("say", hello))
        return True

    def _started(self) -> None:
        """Вызывается pywebview в отдельном потоке, когда окно создано."""
        for tasks, name in ((self._tasks, "brain"), (self._quick_tasks, "quick")):
            threading.Thread(target=self._worker, args=(tasks,), daemon=True, name=name).start()
        from ..agent import Tray

        self.tray = Tray(
            self.settings.assistant_name,
            on_open=self.show_window,
            on_quit=self.quit,
            is_listening=self.is_listening,
            on_toggle_listening=lambda: self.set_wake(not self.is_listening()),
        )
        self.tray.start()
        if IS_WIN:
            try:
                from .overlay import Overlay

                self.overlay = Overlay(level=self.levels)
                self.overlay.enabled = self.settings.overlay
                self.overlay.start()
            except Exception as exc:  # noqa: BLE001 — без значка в углу всё остальное работает
                log.warning("Значок в углу экрана недоступен: %s", exc)
        self._create_assistant()

    def run(self) -> None:
        import webview

        self.window = webview.create_window(
            self.settings.assistant_name,
            url=str(WEB_DIR / "index.html"),
            js_api=Api(self),
            width=1000,
            height=700,
            min_size=(760, 540),
            frameless=True,
            easy_drag=False,
            background_color="#131417",
            hidden=self.background,
        )
        self.window.events.closing += self._on_closing
        webview.start(self._started, private_mode=False, debug=bool(os.environ.get("JARVIS_DEBUG")))


def run_webui(settings: Settings, background: bool = False) -> None:
    WebUI(settings, background).run()
