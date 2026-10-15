"""Консольные режимы: текстовый чат и голосовой режим без окна."""

import threading
from typing import Any

from ..assistant import Assistant, greeting
from ..config import Settings
from . import format_tool_call


class ConsoleUI:
    def __init__(self, settings: Settings):
        self.settings = settings

    def show_user(self, text: str, source: str) -> None:
        if source == "voice":  # набранный текст и так виден в консоли
            print(f"\nВы (голос): {text}")

    def show_reply(self, text: str) -> None:
        print(f"\n{self.settings.assistant_name}: {text}\n")

    def show_tool(self, name: str, args: dict[str, Any]) -> None:
        print(f"  {format_tool_call(name, args)}")

    def show_info(self, text: str, error: bool = False) -> None:
        print(f"{'[!]' if error else '[i]'} {text}")

    def set_status(self, status: str) -> None:
        if status == "listening":
            print("  …слушаю", flush=True)

    def confirm(self, prompt: str) -> bool:
        answer = input(f"\n[?] {prompt} [д/н]: ").strip().lower()
        return answer in ("д", "да", "y", "yes")


def run_chat(settings: Settings) -> None:
    assistant = Assistant(settings, ConsoleUI(settings))
    hello = greeting(settings)
    print(f"\n{settings.assistant_name}: {hello}")
    print(f"[i] Мозг: {assistant.brain.describe()}.")
    print("[i] Команды: /new — новый диалог, /mute — вкл/выкл озвучку, "
          "/ai auto|gemini|deepseek — выбрать ИИ, /exit — выход.\n")
    threading.Thread(target=assistant.say, args=(hello,), daemon=True).start()
    while True:
        try:
            text = input("Вы: ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        command = text.lower()
        if command in ("/exit", "/quit", "выход"):
            break
        if command in ("/new", "/reset"):
            assistant.brain.reset()
            print("[i] Начат новый диалог.")
            continue
        if command.startswith("/ai"):
            mode = command.removeprefix("/ai").strip()
            try:
                assistant.brain.set_mode(mode)
                print(f"[i] Мозг: {assistant.brain.describe()}.")
            except ValueError:
                print(f"[!] Доступно: {', '.join(assistant.brain.available_modes())}")
            continue
        if command == "/mute":
            assistant.speaker.enabled = not assistant.speaker.enabled
            print(f"[i] Озвучка {'включена' if assistant.speaker.enabled else 'выключена'}.")
            continue
        assistant.speaker.stop()
        assistant.handle(text, "text")
    assistant.speaker.stop()


def run_voice(settings: Settings) -> None:
    assistant = Assistant(settings, ConsoleUI(settings))
    if not assistant.listener:
        print(f"[!] {assistant.voice_error}")
        return
    hello = greeting(settings)
    print(f"\n{settings.assistant_name}: {hello}")
    print(f"[i] Скажите «{settings.assistant_name}, …», например «{settings.assistant_name}, который час?». "
          "Ctrl+C — выход.\n")
    assistant.say(hello)
    try:
        assistant.wake_word_loop(threading.Event())
    except KeyboardInterrupt:
        pass
    assistant.speaker.stop()
