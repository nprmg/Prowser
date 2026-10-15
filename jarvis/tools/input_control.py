"""Клавиатура, мышь и буфер обмена."""

import time
from typing import Literal

from ..runtime import runtime
from ._platform import IS_MAC, IS_WIN
from .registry import tool

_KEY_ALIASES = {
    "windows": "win", "вин": "win", "виндовс": "win", "super": "win", "meta": "win",
    "control": "ctrl", "контрол": "ctrl", "cmd": "command",
    "return": "enter", "ввод": "enter", "escape": "esc", "пробел": "space", "del": "delete",
    "pgup": "pageup", "page up": "pageup", "pgdn": "pagedown", "page down": "pagedown",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
    "printscreen": "printscreen", "prtsc": "printscreen", "backspace": "backspace",
}


def _pyautogui():
    import pyautogui

    pyautogui.FAILSAFE = False  # иначе курсор в углу экрана блокирует все нажатия
    return pyautogui


def _prepare_input() -> None:
    """Убирает окно ассистента с переднего плана, чтобы ввод попал в нужную программу."""
    runtime.release_focus()
    time.sleep(0.4)


@tool()
def type_text(text: str, press_enter: bool = False) -> str:
    """Напечатать текст в активном окне (туда, где сейчас курсор), как будто его набрали на клавиатуре.

    Args:
        text: текст для ввода (любой язык)
        press_enter: нажать Enter после ввода (например, чтобы отправить сообщение или выполнить поиск)
    """
    import pyperclip

    pyautogui = _pyautogui()
    _prepare_input()
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = None
    # Вставка через буфер обмена работает с кириллицей, в отличие от эмуляции отдельных клавиш.
    pyperclip.copy(text)
    pyautogui.hotkey("command" if IS_MAC else "ctrl", "v")
    time.sleep(0.4)
    if press_enter:
        pyautogui.press("enter")
    if previous is not None:
        pyperclip.copy(previous)
    return "Текст введён"


@tool()
def press_keys(keys: str, times: int = 1) -> str:
    """Нажать клавишу или сочетание клавиш в активном окне.

    Args:
        keys: клавиши латиницей через «+», например «ctrl+c», «alt+tab», «win+d», «enter», «f5»,
            «ctrl+shift+esc», «space», «alt+f4»
        times: сколько раз нажать
    """
    pyautogui = _pyautogui()
    parts = [k.strip().lower() for k in keys.split("+") if k.strip()]
    parts = [_KEY_ALIASES.get(k, k) for k in parts]
    unknown = [k for k in parts if k not in pyautogui.KEYBOARD_KEYS]
    if not parts or unknown:
        raise ValueError(f"Неизвестные клавиши: {', '.join(unknown) or keys}")
    _prepare_input()
    for _ in range(max(1, min(times, 50))):
        if len(parts) > 1:
            pyautogui.hotkey(*parts)
        else:
            pyautogui.press(parts[0])
        time.sleep(0.05)
    return f"Нажато: {'+'.join(parts)}" + (f" ×{times}" if times > 1 else "")


@tool()
def scroll(direction: Literal["up", "down"], amount: int = 5) -> str:
    """Прокрутить страницу или окно под курсором мыши.

    Args:
        direction: up — вверх, down — вниз
        amount: насколько прокрутить (1–20)
    """
    pyautogui = _pyautogui()
    _prepare_input()
    clicks = max(1, min(amount, 20))
    if IS_WIN:
        clicks *= 120  # в Windows одно «деление» колеса = 120 единиц
    pyautogui.scroll(clicks if direction == "up" else -clicks)
    return f"Прокрутил {direction}"


@tool()
def get_clipboard() -> str:
    """Прочитать текст из буфера обмена."""
    import pyperclip

    text = pyperclip.paste()
    return text[:6000] if text else "(буфер обмена пуст)"


@tool()
def set_clipboard(text: str) -> str:
    """Скопировать текст в буфер обмена.

    Args:
        text: текст, который нужно скопировать
    """
    import pyperclip

    pyperclip.copy(text)
    return "Скопировано в буфер обмена"
