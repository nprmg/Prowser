"""Мышь и «зрение»: клики по координатам и по описанию элемента на экране."""

import io
import json
import re
import time
from typing import Literal

from ..runtime import runtime
from .registry import tool


def _pyautogui():
    import pyautogui

    pyautogui.FAILSAFE = False
    pyautogui.PAUSE = 0.02
    return pyautogui


@tool()
def get_screen_size() -> dict:
    """Размер основного экрана в пикселях (для кликов по координатам)."""
    width, height = _pyautogui().size()
    return {"width": width, "height": height}


@tool()
def mouse_click(x: int, y: int, button: Literal["left", "right", "middle"] = "left", double: bool = False) -> str:
    """Кликнуть мышью в точке экрана.

    Args:
        x: координата по горизонтали в пикселях (0 — левый край)
        y: координата по вертикали в пикселях (0 — верхний край)
        button: кнопка мыши
        double: двойной клик
    """
    runtime.release_focus()
    pyautogui = _pyautogui()
    pyautogui.click(x=x, y=y, button=button, clicks=2 if double else 1, interval=0.08)
    return f"Клик ({x}, {y})"


@tool()
def mouse_move(x: int, y: int) -> str:
    """Переместить курсор мыши в точку экрана.

    Args:
        x: координата по горизонтали в пикселях
        y: координата по вертикали в пикселях
    """
    _pyautogui().moveTo(x, y, duration=0.15)
    return f"Курсор в ({x}, {y})"


@tool()
def mouse_drag(x1: int, y1: int, x2: int, y2: int) -> str:
    """Перетащить мышью (зажать левую кнопку в одной точке и отпустить в другой).

    Args:
        x1: откуда, по горизонтали
        y1: откуда, по вертикали
        x2: куда, по горизонтали
        y2: куда, по вертикали
    """
    runtime.release_focus()
    pyautogui = _pyautogui()
    pyautogui.moveTo(x1, y1)
    pyautogui.dragTo(x2, y2, duration=0.4, button="left")
    return f"Перетащил ({x1}, {y1}) → ({x2}, {y2})"


def _locate(target: str) -> tuple[int, int] | None:
    """Находит элемент на скриншоте с помощью Gemini и возвращает центр в пикселях экрана."""
    from google.genai import types
    from PIL import ImageGrab

    from ..llm.base import run_with_deadline

    shot = ImageGrab.grab()  # основной экран — в тех же координатах, что и клики pyautogui
    width, height = shot.size
    image = shot.convert("RGB")
    image.thumbnail((1600, 1600))
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=88)
    prompt = (
        f"Найди на скриншоте элемент интерфейса: «{target}». Ответь только JSON вида "
        '{"found": true, "box_2d": [ymin, xmin, ymax, xmax]} с координатами, нормированными к 0–1000. '
        'Если элемента нет — {"found": false}.'
    )
    client = runtime.client
    response = run_with_deadline(
        lambda: client.models.generate_content(
            model=runtime.settings.model,
            contents=[types.Part.from_bytes(data=buffer.getvalue(), mime_type="image/jpeg"), prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json"),
        ),
        timeout=40,
    )
    match = re.search(r"\{.*\}", response.text or "", re.S)
    if not match:
        return None
    data = json.loads(match.group(0))
    box = data.get("box_2d")
    if not data.get("found") or not isinstance(box, list) or len(box) != 4:
        return None
    ymin, xmin, ymax, xmax = (float(v) for v in box)
    return round((xmin + xmax) / 2 / 1000 * width), round((ymin + ymax) / 2 / 1000 * height)


@tool(enabled=lambda: runtime.client is not None)  # нужно «зрение» Gemini
def click_on_screen(target: str, double: bool = False, button: Literal["left", "right"] = "left") -> dict:
    """Найти на экране элемент по описанию и кликнуть по нему: кнопку, ссылку, поле, значок, пункт меню.
    Используй, когда нужно нажать что-то в программе, а специального инструмента нет.

    Args:
        target: что нажать, как можно точнее: «кнопка Сохранить в окне Блокнота», «поле поиска на YouTube»
        double: двойной клик (например, чтобы открыть файл)
        button: кнопка мыши
    """
    runtime.release_focus()
    time.sleep(0.3)
    point = _locate(target)
    if point is None:
        raise LookupError(f"Не нашёл на экране: {target}")
    pyautogui = _pyautogui()
    pyautogui.moveTo(*point, duration=0.12)
    pyautogui.click(button=button, clicks=2 if double else 1, interval=0.08)
    return {"clicked": True, "x": point[0], "y": point[1]}


@tool()
def wait(seconds: float) -> str:
    """Подождать (например, пока откроется программа или загрузится страница), до 30 секунд.

    Args:
        seconds: сколько секунд подождать
    """
    if runtime.cancel.wait(max(0.1, min(30.0, seconds))):
        return "Ожидание прервано"
    return "Подождал"
