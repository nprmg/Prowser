"""Быстрые команды: простые действия выполняются сразу, без обращения к ИИ.

«Открой блокнот», «закрой хром», «громкость 30», «пауза», «который час» не требуют модели —
их распознаёт простой разбор фразы, а ответ приходит мгновенно. Всё, что сложнее
(«открой блокнот и напиши…», «включи музыку для работы») или неоднозначно, уходит в Gemini/DeepSeek.
Каждый шаблон сравнивается с фразой целиком, поэтому части длинных просьб сюда не попадают.
"""

import re
from dataclasses import dataclass
from typing import Any, Callable

from .tools.apps import APPS

_POLITE = re.compile(r"\b(?:пожалуйста|плиз|please|будь\s+добр\w*|будь\s+любезн\w*)\b")
_COMPOUND = re.compile(r"\b(?:и|а потом|потом|затем|после этого)\b|;")
_FILLERS = re.compile(r"^(?:(?:эй|окей|ок|ok|слушай|ну|а|давай|можешь|можешь ли|быстро)\s+)+")
_VERB_PARTICLES = re.compile(r"^(\w+)(?:-ка|\s+ка)?\s+мне\b|^(\w+)(?:-ка|\s+ка)\b")

# Популярные сайты: «открой ютуб» быстрее открыть напрямую. Проверяются раньше программ,
# поэтому «открой гугл» открывает сайт Google, а не браузер Chrome.
SITES = {
    "ютуб": "https://www.youtube.com", "youtube": "https://www.youtube.com", "ютюб": "https://www.youtube.com",
    "гугл": "https://www.google.com", "google": "https://www.google.com",
    "вк": "https://vk.com", "вконтакте": "https://vk.com", "контакт": "https://vk.com",
    "яндекс": "https://ya.ru", "гитхаб": "https://github.com", "github": "https://github.com",
    "твич": "https://www.twitch.tv", "twitch": "https://www.twitch.tv",
    "википедию": "https://ru.wikipedia.org", "википедия": "https://ru.wikipedia.org",
    "почту": "https://mail.google.com", "гугл почту": "https://mail.google.com",
    "яндекс почту": "https://mail.yandex.ru", "карты": "https://yandex.ru/maps",
    "переводчик": "https://translate.google.com",
}
# Названия, по которым «закрой …» неоднозначно (вкладка сайта или вся программа) — решает ИИ.
_AMBIGUOUS_CLOSE = {"гугл", "google"}


@dataclass
class QuickCommand:
    tool: str
    args: dict[str, Any]
    reply: Callable[[Any], str]  # получает результат инструмента, возвращает фразу для ответа


def _normalize(text: str, wake_words: list[str]) -> str:
    text = text.lower().replace("ё", "е")
    for word in wake_words:
        text = re.sub(rf"\b{re.escape(word)}\w*", " ", text)
    text = _POLITE.sub(" ", text)
    # Знаки препинания из набранного текста. Запятые тоже убираем: все шаблоны сравниваются
    # с фразой целиком, так что «открой блокнот, напиши привет» всё равно уйдёт в ИИ.
    text = re.sub(r"[.,!?…«»\"'—–:]", " ", text)
    text = " ".join(text.split()).strip(" -")
    text = _FILLERS.sub("", text)
    return _VERB_PARTICLES.sub(lambda m: m.group(1) or m.group(2), text)  # «открой-ка мне» -> «открой»


def _app_title(name: str) -> str | None:
    """Только точное совпадение с известным названием — без угадываний."""
    name = name.strip()
    for title, spec in APPS.items():
        if name == title.lower() or name in spec["names"]:
            return title
    return None


def _closed_reply(result: Any, app: str, title: str) -> str:
    if isinstance(result, str) and result.startswith("Закрыто"):
        return f"Закрыл {app}, {title}."
    if isinstance(result, str):
        return result
    return f"{app} не закрылся сам. Скажите «закрой принудительно», если нужно."


def match(text: str, wake_words: list[str], title: str) -> QuickCommand | None:
    """Возвращает быструю команду или None, если фразу нужно отдать ИИ."""
    t = _normalize(text, wake_words)
    if not t or _COMPOUND.search(t):
        return None

    m = re.fullmatch(r"(?:открой|открыть|запусти|запустить|включи|включить)\s+(.+)", t)
    if m:
        site = SITES.get(m.group(1))
        if site:
            return QuickCommand("open_url", {"url": site}, lambda r: f"Открываю, {title}.")
        app = _app_title(m.group(1))
        if app:
            return QuickCommand("open_app", {"name": app}, lambda r: f"Открываю {app}, {title}.")

    m = re.fullmatch(r"(?:закрой|закрыть|выключи|выруби)\s+(.+)", t)
    if m and m.group(1) not in _AMBIGUOUS_CLOSE:
        app = _app_title(m.group(1))
        # Только программы с известным процессом: окна проводника, панели управления и т. п.
        # закрываются иначе — с этим справится ИИ через управление окнами.
        if app and APPS[app].get("proc"):
            return QuickCommand("close_app", {"name": app}, lambda r: _closed_reply(r, app, title))

    m = re.fullmatch(
        r"(?:поставь\s+|сделай\s+|установи\s+)?(?:громкость|звук)\s+(?:на\s+)?(\d{1,3})\s*(?:%|процент\w*)?", t
    )
    if m:
        level = min(100, int(m.group(1)))
        return QuickCommand("set_volume", {"level": level}, lambda r: f"Громкость {level} процентов.")
    if re.fullmatch(r"(?:сделай\s+)?(?:по)?громче", t):
        return QuickCommand("change_volume", {"delta": 10}, lambda r: "Громче.")
    if re.fullmatch(r"(?:сделай\s+)?(?:по)?тише", t):
        return QuickCommand("change_volume", {"delta": -10}, lambda r: "Тише.")
    if re.fullmatch(r"(?:выключи звук|отключи звук|без звука|мут)", t):
        return QuickCommand("mute", {"state": "on"}, lambda r: _mute_reply(r, "Звук выключен", title))
    if re.fullmatch(r"(?:включи звук|верни звук)", t):
        return QuickCommand("mute", {"state": "off"}, lambda r: _mute_reply(r, "Звук включён", title))

    if re.fullmatch(r"(?:пауза|поставь на паузу|play|плей)", t):
        return QuickCommand("media_control", {"action": "play_pause"}, lambda r: "Готово.")
    if re.fullmatch(r"(?:стоп музыка|останови музыку|выключи музыку)", t):
        return QuickCommand("media_control", {"action": "stop"}, lambda r: "Остановил.")
    # Только с существительным: одно «следующий» может быть ответом на вопрос ИИ («рассказать следующий?»).
    media = r"(?:трек|песн\w+|видео|композици\w+|музык\w+)"
    if re.fullmatch(rf"(?:следующ\w+|next)\s+{media}", t):
        return QuickCommand("media_control", {"action": "next"}, lambda r: "Следующий.")
    if re.fullmatch(rf"(?:предыдущ\w+|previous)\s+{media}", t):
        return QuickCommand("media_control", {"action": "previous"}, lambda r: "Предыдущий.")

    if re.fullmatch(
        r"(?:сейчас\s+)?(?:который\s+(?:сейчас\s+)?час|сколько\s+(?:сейчас\s+)?врем(?:я|ени))(?:\s+сейчас)?|время",
        t,
    ):
        return QuickCommand("get_datetime", {}, lambda r: f"Сейчас {r['time']}, {title}.")
    if re.fullmatch(r"(?:какое сегодня число|какая сегодня дата|какой сегодня день(?:\s+недели)?)", t):
        return QuickCommand("get_datetime", {}, lambda r: f"Сегодня {r['weekday']}, {r['date']}.")
    if re.fullmatch(r"(?:сделай\s+)?скриншот(?:\s+экрана)?", t):
        return QuickCommand("take_screenshot", {}, lambda r: f"Скриншот сохранён, {title}.")
    if re.fullmatch(r"(?:сверни все окна|сверни все)", t):  # Win+M не возвращает окна при повторе
        return QuickCommand("press_keys", {"keys": "win+m"}, lambda r: "Готово.")
    if re.fullmatch(r"покажи рабочий стол", t):
        return QuickCommand("press_keys", {"keys": "win+d"}, lambda r: "Готово.")
    if re.fullmatch(r"заблокируй(?:\s+(?:компьютер|пк|экран))?", t):
        return QuickCommand("power_action", {"action": "lock"}, lambda r: "Блокирую.")
    return None


def _mute_reply(result: Any, expected: str, title: str) -> str:
    # Без pycaw звук переключается клавишей, и реальный результат может отличаться от просьбы.
    return f"{expected}, {title}." if result == expected else f"{result}, {title}."


def describe_result(command: QuickCommand, result: dict[str, Any]) -> str:
    """Фраза для ответа по результату инструмента."""
    if "error" in result:
        message = str(result["error"])
        if message.startswith("TimeoutExpired"):
            message = "программа не ответила вовремя"
        message = re.sub(r"^[\w.]+: ", "", message)  # «LookupError: …» -> «…»
        return f"Не получилось: {message}"
    try:
        return command.reply(result.get("result"))
    except (KeyError, TypeError):
        return "Готово."
