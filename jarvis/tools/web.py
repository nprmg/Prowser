"""Браузер, поиск, YouTube, погода, курсы валют."""

import json
import re
import urllib.parse
import urllib.request
import webbrowser
from typing import Literal

from ..runtime import runtime
from .registry import tool

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
}

_SEARCH_URLS = {
    "google": "https://www.google.com/search?q={}",
    "yandex": "https://yandex.ru/search/?text={}",
    "youtube": "https://www.youtube.com/results?search_query={}",
    "wikipedia": "https://ru.wikipedia.org/w/index.php?search={}",
    "maps": "https://www.google.com/maps/search/{}",
    "images": "https://www.google.com/search?tbm=isch&q={}",
    "bing": "https://www.bing.com/search?q={}",
    "duckduckgo": "https://duckduckgo.com/?q={}",
}


def _fetch(url: str, timeout: float = 10) -> str:
    request = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace")


@tool()
def open_url(url: str) -> str:
    """Открыть сайт или ссылку в браузере.

    Args:
        url: адрес сайта, например «youtube.com», «vk.com» или полная ссылка
    """
    url = url.strip()
    if not re.match(r"^[a-z][a-z0-9+.-]*:", url, re.I):
        url = "https://" + url
    webbrowser.open(url)
    return f"Открыл {url}"


@tool()
def web_search(
    query: str,
    engine: Literal["google", "yandex", "youtube", "wikipedia", "maps", "images", "bing", "duckduckgo"] = "google",
) -> str:
    """Открыть в браузере страницу с результатами поиска.

    Args:
        query: поисковый запрос
        engine: где искать: google, yandex, youtube, wikipedia, maps (карты), images (картинки), bing, duckduckgo
    """
    url = _SEARCH_URLS.get(engine, _SEARCH_URLS["google"]).format(urllib.parse.quote_plus(query))
    webbrowser.open(url)
    return f"Открыл поиск «{query}» ({engine})"


@tool()
def play_youtube(query: str) -> str:
    """Найти и сразу включить видео или музыку на YouTube.

    Args:
        query: что включить, например «lofi hip hop radio» или «Imagine Dragons Believer»
    """
    search_url = _SEARCH_URLS["youtube"].format(urllib.parse.quote_plus(query))
    try:
        match = re.search(r"watch\?v=([\w-]{11})", _fetch(search_url))
    except OSError:
        match = None
    if match:
        webbrowser.open(f"https://www.youtube.com/watch?v={match.group(1)}")
        return f"Включаю на YouTube: {query}"
    webbrowser.open(search_url)
    return f"Не удалось выбрать видео автоматически, открыл поиск YouTube: {query}"


def _ru_desc(block: dict) -> str:
    items = block.get("lang_ru") or block.get("weatherDesc") or [{"value": ""}]
    return items[0].get("value", "")


@tool()
def get_weather(city: str = "") -> dict:
    """Узнать текущую погоду и прогноз на 3 дня.

    Args:
        city: город (по-русски или по-английски). Пусто — определить по местоположению компьютера.
    """
    if city.strip():
        try:  # Open-Meteo точнее и надёжнее; wttr.in — запасной вариант и для «где я»
            from .integrations import open_meteo_weather

            return open_meteo_weather(city.strip())
        except Exception:  # noqa: BLE001 — любой сбой: пробуем второй сервис
            pass
    data = json.loads(_fetch(f"https://wttr.in/{urllib.parse.quote(city)}?format=j1&lang=ru", timeout=15))
    current = data["current_condition"][0]
    area = (data.get("nearest_area") or [{}])[0]
    place = (area.get("areaName") or [{"value": city}])[0]["value"]
    forecast = []
    for day in data.get("weather", [])[:3]:
        hourly = day.get("hourly") or [{}]
        midday = hourly[len(hourly) // 2]
        forecast.append({
            "date": day.get("date"),
            "min_c": day.get("mintempC"),
            "max_c": day.get("maxtempC"),
            "description": _ru_desc(midday),
            "rain_chance_percent": midday.get("chanceofrain"),
        })
    return {
        "place": place,
        "now": {
            "temp_c": current.get("temp_C"),
            "feels_like_c": current.get("FeelsLikeC"),
            "description": _ru_desc(current),
            "humidity_percent": current.get("humidity"),
            "wind_kmh": current.get("windspeedKmph"),
        },
        "forecast": forecast,
    }


@tool()
def get_exchange_rates(currencies: str = "USD,EUR,CNY") -> dict:
    """Официальные курсы валют ЦБ РФ к рублю на сегодня.

    Args:
        currencies: коды валют через запятую, например «USD,EUR» или «KZT,BYN,TRY»
    """
    data = json.loads(_fetch("https://www.cbr-xml-daily.ru/daily_json.js"))
    rates = {}
    for code in (c.strip().upper() for c in currencies.split(",") if c.strip()):
        valute = data["Valute"].get(code)
        if valute:
            rates[code] = {"name": valute["Name"], "rub": round(valute["Value"] / valute["Nominal"], 4)}
        else:
            rates[code] = "нет данных"
    return {"date": data.get("Date", "")[:10], "rates": rates}


@tool(enabled=lambda: runtime.settings.enable_google_search and runtime.client is not None)
def search_internet(query: str) -> dict:
    """Найти актуальную информацию в интернете через Google Поиск (новости, события, факты, цены)
    и вернуть краткую сводку с источниками. Используй для вопросов о свежих событиях.

    Args:
        query: что нужно найти
    """
    from google.genai import types

    from ..llm.base import run_with_deadline

    client = runtime.client
    response = run_with_deadline(
        lambda: client.models.generate_content(
            model=runtime.settings.model,
            contents=f"Найди в интернете и кратко ответь на русском: {query}",
            config=types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())]),
        ),
        timeout=45,
    )
    sources = []
    candidate = (response.candidates or [None])[0]
    metadata = getattr(candidate, "grounding_metadata", None)
    for chunk in (getattr(metadata, "grounding_chunks", None) or [])[:5]:
        if chunk.web:
            sources.append({"title": chunk.web.title, "url": chunk.web.uri})
    return {"answer": response.text, "sources": sources}


@tool()
def download_file(url: str, folder: str = "downloads", filename: str = "") -> dict:
    """Скачать файл из интернета в папку (по умолчанию «Загрузки»).

    Args:
        url: прямая ссылка на файл
        folder: куда сохранить: «загрузки», «рабочий стол» или путь к папке
        filename: имя файла; пусто — взять из ссылки
    """
    import httpx

    from .files import resolve_path

    target_dir = resolve_path(folder)
    target_dir.mkdir(parents=True, exist_ok=True)
    name = filename or urllib.parse.unquote(urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]) or "download"
    target = target_dir / re.sub(r'[<>:"/\\|?*]', "_", name)
    size = 0
    with httpx.stream("GET", url, headers=_HEADERS, follow_redirects=True, timeout=30) as response:
        response.raise_for_status()
        with open(target, "wb") as file:
            for chunk in response.iter_bytes():
                file.write(chunk)
                size += len(chunk)
    return {"saved_to": str(target), "size_mb": round(size / 2**20, 2)}
