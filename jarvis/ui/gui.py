"""Окно ассистента на Tkinter: чат, кнопка микрофона и фоновое прослушивание «Джарвис, …»."""

import ctypes
import logging
import queue
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import messagebox, simpledialog
from typing import Any, Callable

from ..assistant import Assistant, greeting
from ..brain import MODES
from ..config import Settings, clean_key, key_problem, save_api_keys
from ..runtime import runtime
from ..tools._platform import IS_WIN
from . import format_tool_call

log = logging.getLogger("jarvis.gui")

FONT = "Segoe UI" if IS_WIN else "Helvetica"
C = {
    "bg": "#0a0f1a",
    "panel": "#101828",
    "border": "#1e2b42",
    "accent": "#37d5ff",
    "text": "#e6edf3",
    "muted": "#7d8ca3",
    "user": "#9ecbff",
    "tool": "#5f6f88",
    "info": "#e3b341",
    "error": "#ff7b72",
}
STATUSES = {
    "idle": ("Готов", "#37d5ff"),
    "background": ("Жду «{name}»", "#2f81f7"),
    "listening": ("Слушаю…", "#3fb950"),
    "thinking": ("Думаю…", "#e3b341"),
    "speaking": ("Говорю…", "#a371f7"),
}


class KeysDialog(simpledialog.Dialog):
    """Окно ввода API-ключей Gemini и DeepSeek (достаточно одного, второй станет запасным)."""

    LINKS = {
        "gemini": ("Gemini — бесплатно: aistudio.google.com/apikey", "https://aistudio.google.com/apikey"),
        "deepseek": ("DeepSeek — платно, дёшево: platform.deepseek.com", "https://platform.deepseek.com/api_keys"),
    }

    def __init__(self, parent: tk.Misc, settings: Settings):
        self.settings = settings
        self.keys: tuple[str, str] | None = None
        super().__init__(parent, "API-ключи")

    def _row(self, master: tk.Frame, row: int, key: str, value: str) -> tk.Entry:
        text, url = self.LINKS[key]
        link = tk.Label(master, text=text, fg="#1f6feb", cursor="hand2", anchor="w")
        link.grid(row=row, column=0, sticky="w", pady=(8, 2))
        link.bind("<Button-1>", lambda _e: webbrowser.open(url))
        entry = tk.Entry(master, width=56, show="*")
        entry.insert(0, value)
        entry.grid(row=row + 1, column=0, sticky="we")
        return entry

    def body(self, master: tk.Frame) -> tk.Entry:
        tk.Label(
            master, justify="left", anchor="w",
            text="Укажите хотя бы один ключ. Если указаны оба, при сбое Gemini\n"
                 "ассистент сам переключится на DeepSeek. Ключи сохраняются в файл .env.",
        ).grid(row=0, column=0, sticky="w")
        self.gemini_entry = self._row(master, 1, "gemini", self.settings.api_key)
        self.deepseek_entry = self._row(master, 3, "deepseek", self.settings.deepseek_api_key)
        return self.gemini_entry

    def buttonbox(self) -> None:
        box = tk.Frame(self)
        tk.Button(box, text="Сохранить", width=12, command=self.ok, default="active").pack(side="left", padx=5, pady=8)
        tk.Button(box, text="Отмена", width=12, command=self.cancel).pack(side="left", padx=5, pady=8)
        self.bind("<Return>", self.ok)
        self.bind("<Escape>", self.cancel)
        box.pack()

    def validate(self) -> bool:
        gemini, deepseek = clean_key(self.gemini_entry.get()), clean_key(self.deepseek_entry.get())
        if not gemini and not deepseek:
            messagebox.showwarning("API-ключи", "Нужен хотя бы один ключ.", parent=self)
            return False
        for name, key in (("Gemini", gemini), ("DeepSeek", deepseek)):
            problem = key_problem(key)
            if problem:
                messagebox.showwarning(
                    "API-ключи", f"Ключ {name} {problem}. Скопируйте его заново, переключив раскладку.", parent=self
                )
                return False
        return True

    def apply(self) -> None:
        self.keys = (clean_key(self.gemini_entry.get()), clean_key(self.deepseek_entry.get()))


class JarvisApp:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.assistant: Assistant | None = None
        self._ui_queue: queue.Queue = queue.Queue()
        self._tasks: queue.Queue = queue.Queue()  # запросы к ИИ — по одному
        self._quick_tasks: queue.Queue = queue.Queue()  # быстрые команды не ждут ИИ
        self._active = 0  # сколько запросов сейчас выполняется (оба рабочих потока)
        self._active_lock = threading.Lock()
        self._wake_stop: threading.Event | None = None
        self._status = "idle"
        self._provider = ""  # какой ИИ сейчас думает
        self._thinking_since = 0.0
        self._blink = False
        self._hwnd = 0

        if IS_WIN:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)  # чёткий текст на мониторах с масштабированием
            except Exception:
                pass
        self.root = tk.Tk()
        self.root.title("J.A.R.V.I.S.")
        self.root.geometry("860x660")
        self.root.minsize(560, 420)
        self.root.configure(bg=C["bg"])
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self._build()
        self.root.after(50, self._poll_ui_queue)
        self.root.after(450, self._animate)

    # --- Разметка ------------------------------------------------------------------------

    def _button(self, parent, text: str, command: Callable, primary: bool = False) -> tk.Button:
        return tk.Button(
            parent, text=text, command=command, font=(FONT, 10, "bold"), relief="flat", bd=0, padx=14, pady=7,
            cursor="hand2", bg=C["accent"] if primary else C["border"], fg=C["bg"] if primary else C["text"],
            activebackground="#7ee6ff" if primary else "#2a3a57", activeforeground=C["bg"] if primary else C["text"],
        )

    def _checkbox(self, parent, text: str, variable: tk.BooleanVar, command: Callable) -> tk.Checkbutton:
        return tk.Checkbutton(
            parent, text=text, variable=variable, command=command, font=(FONT, 10), bg=C["bg"], fg=C["text"],
            selectcolor=C["panel"], activebackground=C["bg"], activeforeground=C["accent"], bd=0,
            highlightthickness=0, cursor="hand2",
        )

    def _build(self) -> None:
        header = tk.Frame(self.root, bg=C["bg"])
        header.pack(fill="x", padx=16, pady=(14, 8))
        self.indicator = tk.Canvas(header, width=36, height=36, bg=C["bg"], highlightthickness=0)
        self.indicator.pack(side="left")
        self._ring = self.indicator.create_oval(3, 3, 33, 33, outline=C["accent"], width=3)
        self._core = self.indicator.create_oval(12, 12, 24, 24, fill=C["accent"], outline="")
        tk.Label(header, text="J.A.R.V.I.S.", font=(FONT, 18, "bold"), fg=C["accent"], bg=C["bg"]).pack(
            side="left", padx=10
        )
        self.status_label = tk.Label(header, text="Запуск…", font=(FONT, 11), fg=C["muted"], bg=C["bg"])
        self.status_label.pack(side="left", padx=(6, 0))
        self._button(header, "Ключи API", self._edit_keys).pack(side="right")
        self.ai_var = tk.StringVar(value=MODES["auto"])
        self.ai_menu = tk.OptionMenu(header, self.ai_var, MODES["auto"])
        self.ai_menu.configure(
            font=(FONT, 10), bg=C["border"], fg=C["text"], activebackground="#2a3a57", activeforeground=C["text"],
            relief="flat", bd=0, highlightthickness=0, cursor="hand2", padx=10, pady=6,
        )
        self.ai_menu["menu"].configure(font=(FONT, 10), bg=C["panel"], fg=C["text"], activebackground=C["border"])
        self.ai_menu.pack(side="right", padx=(0, 8))
        tk.Label(header, text="ИИ:", font=(FONT, 10), fg=C["muted"], bg=C["bg"]).pack(side="right", padx=(0, 6))

        # Нижние панели упаковываем раньше чата, чтобы при маленьком окне сжимался чат, а не поле ввода.
        options = tk.Frame(self.root, bg=C["bg"])
        options.pack(side="bottom", fill="x", padx=16, pady=(4, 12))
        input_row = tk.Frame(self.root, bg=C["bg"])
        input_row.pack(side="bottom", fill="x", padx=16, pady=(10, 4))

        body = tk.Frame(self.root, bg=C["border"])
        body.pack(fill="both", expand=True, padx=16)
        self.chat = tk.Text(
            body, wrap="word", bg=C["panel"], fg=C["text"], font=(FONT, 11), relief="flat", bd=0, padx=14, pady=10,
            highlightthickness=0, state="disabled", cursor="arrow", spacing1=2, spacing3=8, height=10,
        )
        scrollbar = tk.Scrollbar(body, command=self.chat.yview)
        self.chat.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.chat.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        self.chat.tag_configure("who_user", foreground=C["user"], font=(FONT, 11, "bold"))
        self.chat.tag_configure("user", foreground=C["user"])
        self.chat.tag_configure("who_jarvis", foreground=C["accent"], font=(FONT, 11, "bold"))
        self.chat.tag_configure("jarvis", foreground=C["text"])
        self.chat.tag_configure("tool", foreground=C["tool"], font=(FONT, 9))
        self.chat.tag_configure("info", foreground=C["info"], font=(FONT, 10))
        self.chat.tag_configure("error", foreground=C["error"], font=(FONT, 10))

        entry_border = tk.Frame(input_row, bg=C["border"])
        entry_border.pack(side="left", fill="x", expand=True)
        self.entry = tk.Entry(
            entry_border, font=(FONT, 12), bg=C["panel"], fg=C["text"], insertbackground=C["accent"],
            relief="flat", bd=0,
        )
        self.entry.pack(fill="x", padx=1, pady=1, ipady=8, ipadx=8)
        self.entry.bind("<Return>", lambda _e: self._send_text())
        self._button(input_row, "Отправить", self._send_text, primary=True).pack(side="left", padx=(8, 0))
        self.mic_button = self._button(input_row, "● Говорить", self._on_mic)
        self.mic_button.pack(side="left", padx=(8, 0))
        self._button(input_row, "■ Стоп", self._stop_speech).pack(side="left", padx=(8, 0))

        self.wake_var = tk.BooleanVar(value=False)
        self.wake_box = self._checkbox(
            options, f"Слушать фоном («{self.settings.assistant_name}, …»)", self.wake_var, self._toggle_wake
        )
        self.wake_box.pack(side="left")
        self.tts_var = tk.BooleanVar(value=self.settings.tts_engine != "off")
        self._checkbox(options, "Озвучивать ответы", self.tts_var, self._toggle_tts).pack(side="left", padx=(16, 0))
        self._button(options, "Новый диалог", self._reset).pack(side="right")

    # --- Потокобезопасное обновление интерфейса ------------------------------------------

    def _ui(self, func: Callable, *args) -> None:
        self._ui_queue.put((func, args))

    def _call_ui(self, func: Callable, *args) -> Any:
        """Выполняет функцию в потоке интерфейса и ждёт результат."""
        if threading.current_thread() is threading.main_thread():
            return func(*args)
        done = threading.Event()
        box: dict[str, Any] = {}

        def wrapper():
            try:
                box["value"] = func(*args)
            finally:
                done.set()

        self._ui_queue.put((wrapper, ()))
        done.wait()
        return box.get("value")

    def _poll_ui_queue(self) -> None:
        try:
            while True:
                func, args = self._ui_queue.get_nowait()
                try:
                    func(*args)
                except Exception:
                    log.exception("Ошибка обновления интерфейса")
        except queue.Empty:
            pass
        self.root.after(50, self._poll_ui_queue)

    def _append(self, who: str, text: str, tag: str) -> None:
        self.chat.configure(state="normal")
        if who:
            self.chat.insert("end", f"{who}: ", f"who_{tag}")
        self.chat.insert("end", text + "\n", tag)
        self.chat.configure(state="disabled")
        self.chat.see("end")

    def _render_status(self) -> None:
        status = self._status
        if status == "idle" and self.wake_var.get():
            status = "background"
        text, color = STATUSES[status]
        text = text.format(name=self.settings.assistant_name)
        if status == "thinking":  # «Думаю… Gemini · 7 с» — видно, кто и сколько отвечает
            seconds = int(time.monotonic() - self._thinking_since)
            text = " ".join(part for part in (text, self._provider, f"· {seconds} с" if seconds else "") if part)
        self.status_label.configure(text=text, fg=color)
        self.indicator.itemconfigure(self._core, fill=color)
        self.indicator.itemconfigure(self._ring, outline=color)

    def _animate(self) -> None:
        if self._status in ("listening", "thinking", "speaking"):
            self._blink = not self._blink
            color = STATUSES[self._status][1]
            self.indicator.itemconfigure(self._ring, outline=color if self._blink else C["border"])
            if self._status == "thinking":
                self._render_status()
        self.root.after(450, self._animate)

    # --- Методы для Assistant (вызываются из рабочих потоков) ----------------------------

    def show_user(self, text: str, source: str) -> None:
        self._ui(self._append, "Вы (голос)" if source == "voice" else "Вы", text, "user")

    def show_reply(self, text: str) -> None:
        self._ui(self._append, self.settings.assistant_name, text, "jarvis")

    def show_tool(self, name: str, args: dict[str, Any]) -> None:
        self._ui(self._append, "", format_tool_call(name, args), "tool")

    def show_info(self, text: str, error: bool = False) -> None:
        self._ui(self._append, "", text, "error" if error else "info")

    def set_status(self, status: str) -> None:
        def apply():
            with self._active_lock:
                busy = self._active
            # Быстрая команда закончилась, а запрос к ИИ ещё идёт — показываем, что он думает.
            self._status = "thinking" if status == "idle" and busy else status
            self._render_status()

        self._ui(apply)

    def begin_request(self) -> None:
        """Новый запрос: обнуляем таймер «Думаю… N с» и имя ИИ."""

        def apply():
            with self._active_lock:
                busy = self._active
            if busy <= 1:  # параллельная быстрая команда не сбрасывает таймер долгого запроса
                self._thinking_since = time.monotonic()
                self._provider = ""

        self._ui(apply)

    def show_provider(self, name: str) -> None:
        def apply():
            self._provider = name
            self._render_status()

        self._ui(apply)

    def confirm(self, prompt: str) -> bool:
        def ask():
            self.root.deiconify()
            self.root.lift()
            return messagebox.askyesno("Подтверждение", prompt, parent=self.root)

        return bool(self._call_ui(ask))

    def release_focus(self) -> None:
        """Сворачивает окно ассистента, если оно активно, чтобы ввод попал в другую программу."""
        if IS_WIN:
            focused = ctypes.windll.user32.GetForegroundWindow() == self._hwnd
        else:
            focused = bool(self._call_ui(lambda: self.root.focus_displayof() is not None))
        if focused:
            self._call_ui(self.root.iconify)
            time.sleep(0.4)

    # --- Действия пользователя -----------------------------------------------------------

    def _submit(self, text: str, source: str) -> None:
        """Быстрые команды — в свою очередь, чтобы не ждать медленный ответ ИИ."""
        tasks = self._quick_tasks if self.assistant.brain.is_quick(text) else self._tasks
        tasks.put((source, text))

    def _send_text(self) -> None:
        text = self.entry.get().strip()
        if not text or not self.assistant:
            return
        self.entry.delete(0, "end")
        self._submit(text, "text")

    def _on_mic(self) -> None:
        if not self.assistant:
            return
        if self._wake_stop and not self._wake_stop.is_set():
            self.assistant.request_direct_command()
            return

        def listen() -> None:
            text = self.assistant.listen_text()
            if text:
                self._submit(text, "voice")

        threading.Thread(target=listen, daemon=True, name="mic").start()

    def _stop_speech(self) -> None:
        """«■ Стоп»: замолчать и прервать текущий запрос к ИИ."""
        if self.assistant:
            self.assistant.speaker.stop()
            self.assistant.brain.cancel()

    def _toggle_wake(self) -> None:
        if not self.assistant:
            return
        if self.wake_var.get():
            self._wake_stop = threading.Event()
            threading.Thread(
                target=self.assistant.wake_word_loop, args=(self._wake_stop,), daemon=True, name="wake-word"
            ).start()
            self.show_info(f"Фоновое прослушивание включено. Скажите: «{self.settings.assistant_name}, открой браузер».")
        elif self._wake_stop:
            self._wake_stop.set()
            self.show_info("Фоновое прослушивание выключено.")
        self._render_status()

    def _toggle_tts(self) -> None:
        if self.assistant:
            self.assistant.speaker.enabled = self.tts_var.get()
            if not self.tts_var.get():
                self.assistant.speaker.stop()

    def _reset(self) -> None:
        if self.assistant:
            self.assistant.brain.reset()
            self.show_info("Начат новый диалог.")

    def _refresh_ai_menu(self) -> None:
        """Список вариантов ИИ зависит от того, какие ключи заданы."""
        brain = self.assistant.brain
        menu = self.ai_menu["menu"]
        menu.delete(0, "end")
        for mode in brain.available_modes():
            menu.add_command(label=MODES[mode], command=lambda m=mode: self._select_ai(m))
        self.ai_var.set(MODES[brain.mode])

    def _select_ai(self, mode: str) -> None:
        brain = self.assistant.brain
        brain.set_mode(mode)
        self.ai_var.set(MODES[mode])
        self.show_info(f"Мозг: {brain.describe()}.")

    def _edit_keys(self) -> None:
        dialog = KeysDialog(self.root, self.settings)
        if not dialog.keys or not self.assistant:
            return
        gemini, deepseek = dialog.keys
        save_api_keys(self.settings, gemini=gemini, deepseek=deepseek)
        self._tasks.put(("reconfigure", None))

    def _on_close(self) -> None:
        if self._wake_stop:
            self._wake_stop.set()
        if self.assistant:
            self.assistant.speaker.stop()
        self.root.destroy()

    # --- Рабочий поток -------------------------------------------------------------------

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
                elif kind == "reconfigure":
                    self.assistant.brain.reconfigure(self.settings)
                    self._ui(self._refresh_ai_menu)
                    self.show_info(f"Ключи сохранены. Мозг: {self.assistant.brain.describe()}.")
            except Exception as exc:
                log.exception("Ошибка обработки")
                self.show_info(f"Ошибка: {exc}", error=True)
            finally:
                with self._active_lock:
                    self._active -= 1
                self.set_status("idle")

    # --- Запуск --------------------------------------------------------------------------

    def _ask_api_keys(self) -> bool:
        dialog = KeysDialog(self.root, self.settings)
        if not dialog.keys:
            return False
        gemini, deepseek = dialog.keys
        save_api_keys(self.settings, gemini=gemini, deepseek=deepseek)
        return True

    def run(self) -> None:
        self.root.update_idletasks()
        if IS_WIN:
            try:
                self._hwnd = int(self.root.wm_frame(), 16)
            except (ValueError, tk.TclError):
                self._hwnd = 0
        if not self.settings.api_key and not self.settings.deepseek_api_key and not self._ask_api_keys():
            self.root.destroy()
            return
        while self.assistant is None:
            try:
                self.assistant = Assistant(self.settings, self)
            except Exception as exc:
                log.exception("Не удалось запустить ассистента")
                messagebox.showerror("J.A.R.V.I.S.", f"Не удалось запустить ассистента:\n{exc}", parent=self.root)
                # Чаще всего виноват ключ — даём исправить его, а не закрываемся.
                if not self._ask_api_keys():
                    self.root.destroy()
                    return
        runtime.release_focus = self.release_focus
        if self.assistant.voice_error:
            self.show_info(self.assistant.voice_error, error=True)
            self.mic_button.configure(state="disabled")
            self.wake_box.configure(state="disabled")
        self._refresh_ai_menu()
        threading.Thread(target=self._worker, args=(self._tasks,), daemon=True, name="brain").start()
        threading.Thread(target=self._worker, args=(self._quick_tasks,), daemon=True, name="quick").start()
        hello = greeting(self.settings)
        self.show_reply(hello)
        self.show_info(
            f"Мозг: {self.assistant.brain.describe()}. Пишите в поле ниже, нажмите «Говорить» или включите "
            f"фоновое прослушивание и скажите «{self.settings.assistant_name}, …»."
        )
        self._tasks.put(("say", hello))
        self.set_status("idle")
        self.entry.focus_set()
        self.root.mainloop()


def run_gui(settings: Settings) -> None:
    JarvisApp(settings).run()
