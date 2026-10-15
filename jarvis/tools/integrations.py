"""Интеграции с внешними API: погода (Open-Meteo), криптовалюты (CoinGecko), новости (RSS),
Википедия, Telegram и умный дом Home Assistant. Бесплатные сервисы работают без ключей;
Telegram и Home Assistant включаются, когда в настройках указаны их данные."""

import json
import re
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime
from typing import Literal

import httpx

from ..runtime import runtime
from .registry import tool

USER_AGENT = "JarvisAssistant/2.0 (+https://github.com/nprmg/Prowser)"


def _get(url: str, **params) -> httpx.Response:
    response = httpx.get(url, params=params or None, headers={"User-Agent": USER_AGENT}, timeout=12,
                         follow_redirects=True)
    response.raise_for_status()
    return response


# --- Погода ---------------------------------------------------------------------------------

WMO_RU = {
    0: "ясно", 1: "преимущественно ясно", 2: "переменная облачность", 3: "пасмурно",
    45: "туман", 48: "изморозь и туман", 51: "лёгкая морось", 53: "морось", 55: "сильная морось",
    56: "ледяная морось", 57: "сильная ледяная морось", 61: "небольшой дождь", 63: "дождь", 65: "сильный дождь",
    66: "ледяной дождь", 67: "сильный ледяной дождь", 71: "небольшой снег", 73: "снег", 75: "сильный снег",
    77: "снежные зёрна", 80: "небольшой ливень", 81: "ливень", 82: "сильный ливень", 85: "снегопад",
    86: "сильный снегопад", 95: "гроза", 96: "гроза с градом", 99: "сильная гроза с градом",
}


def open_meteo_weather(city: str) -> dict:
    places = _get("https://geocoding-api.open-meteo.com/v1/search", name=city, count=1, language="ru").json()
    if not places.get("results"):
        raise LookupError(f"Не нашёл город «{city}»")
    place = places["results"][0]
    data = _get(
        "https://api.open-meteo.com/v1/forecast",
        latitude=place["latitude"], longitude=place["longitude"], timezone="auto", forecast_days=3,
        wind_speed_unit="ms",
        current="temperature_2m,apparent_temperature,relative_humidity_2m,wind_speed_10m,weather_code",
        daily="weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
    ).json()
    now = data["current"]
    daily = data["daily"]
    return {
        "place": ", ".join(filter(None, [place.get("name"), place.get("admin1"), place.get("country")])),
        "now": {
            "temp_c": round(now["temperature_2m"]),
            "feels_like_c": round(now["apparent_temperature"]),
            "description": WMO_RU.get(now["weather_code"], ""),
            "humidity_percent": now["relative_humidity_2m"],
            "wind_ms": round(now["wind_speed_10m"], 1),
        },
        "forecast": [
            {
                "date": day,
                "min_c": round(daily["temperature_2m_min"][i]),
                "max_c": round(daily["temperature_2m_max"][i]),
                "description": WMO_RU.get(daily["weather_code"][i], ""),
                "rain_chance_percent": daily["precipitation_probability_max"][i],
            }
            for i, day in enumerate(daily["time"])
        ],
    }


# --- Криптовалюты --------------------------------------------------------------------------

COINS = {
    "биткоин": "bitcoin", "биток": "bitcoin", "btc": "bitcoin", "bitcoin": "bitcoin",
    "эфир": "ethereum", "эфириум": "ethereum", "eth": "ethereum", "ethereum": "ethereum",
    "тон": "the-open-network", "toncoin": "the-open-network", "ton": "the-open-network",
    "солана": "solana", "sol": "solana", "usdt": "tether", "тезер": "tether", "доги": "dogecoin",
    "doge": "dogecoin", "xrp": "ripple", "рипл": "ripple", "bnb": "binancecoin",
}


@tool()
def crypto_prices(coins: str = "bitcoin,ethereum") -> dict:
    """Текущий курс криптовалют в долларах и рублях (CoinGecko).

    Args:
        coins: монеты через запятую: «биткоин, эфир, тон» или «bitcoin,solana»
    """
    ids = [COINS.get(c.strip().lower(), c.strip().lower()) for c in coins.split(",") if c.strip()]
    data = _get("https://api.coingecko.com/api/v3/simple/price", ids=",".join(ids), vs_currencies="usd,rub",
                include_24hr_change="true").json()
    if not data:
        raise LookupError("Не нашёл такие монеты")
    return {
        coin: {"usd": v.get("usd"), "rub": v.get("rub"), "change_24h_percent": round(v.get("usd_24h_change") or 0, 2)}
        for coin, v in data.items()
    }


# --- Новости -------------------------------------------------------------------------------

FEEDS = {
    "tass": ("ТАСС", "https://tass.ru/rss/v2.xml"),
    "lenta": ("Лента.ру", "https://lenta.ru/rss/news"),
    "rbc": ("РБК", "https://rssexport.rbc.ru/rbcnews/news/30/full.rss"),
    "bbc": ("BBC News Русская служба", "https://feeds.bbci.co.uk/russian/rss.xml"),
    "meduza": ("Медуза", "https://meduza.io/rss/all"),
}


@tool()
def news_headlines(source: Literal["any", "tass", "lenta", "rbc", "bbc", "meduza"] = "any", limit: int = 7) -> dict:
    """Свежие заголовки новостей на русском (RSS).

    Args:
        source: источник: any — первый доступный, tass, lenta, rbc, bbc, meduza
        limit: сколько заголовков вернуть
    """
    order = [source] if source in FEEDS else list(FEEDS)
    errors = []
    for key in order:
        title, url = FEEDS[key]
        try:
            root = ET.fromstring(_get(url).content)
        except (httpx.HTTPError, ET.ParseError) as exc:
            errors.append(f"{title}: {exc}")
            continue
        items = [
            {"title": (item.findtext("title") or "").strip(), "published": item.findtext("pubDate") or ""}
            for item in root.iter("item")
        ]
        if items:
            return {"source": title, "headlines": items[: max(1, min(limit, 20))]}
    raise LookupError("Новости недоступны: " + "; ".join(errors))


# --- Википедия -----------------------------------------------------------------------------


@tool()
def wikipedia_summary(query: str) -> dict:
    """Краткая справка из русской Википедии: что это, кто это.

    Args:
        query: что найти, например «Чёрная дыра» или «Илон Маск»
    """
    api = "https://ru.wikipedia.org/w/api.php"
    found = _get(api, action="query", list="search", srsearch=query, srlimit=1, format="json").json()
    hits = found.get("query", {}).get("search", [])
    if not hits:
        raise LookupError(f"В Википедии нет статьи про «{query}»")
    title = hits[0]["title"]
    pages = _get(api, action="query", prop="extracts", exintro=1, explaintext=1, exsentences=4, titles=title,
                 format="json").json()["query"]["pages"]
    extract = next(iter(pages.values())).get("extract", "")
    return {"title": title, "summary": extract[:1500],
            "url": f"https://ru.wikipedia.org/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"}


# --- Telegram ------------------------------------------------------------------------------


def telegram_ready() -> bool:
    return bool(runtime.settings.telegram_token and runtime.settings.telegram_chat_ids)


def telegram_api(method: str, http_timeout: float = 15, **payload) -> dict:
    response = httpx.post(
        f"https://api.telegram.org/bot{runtime.settings.telegram_token}/{method}", json=payload, timeout=http_timeout
    )
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram: {data.get('description', response.status_code)}")
    return data["result"]


def telegram_send_text(chat_id: int | str, text: str) -> None:
    for start in range(0, max(1, len(text)), 4000):  # лимит Telegram — 4096 символов
        telegram_api("sendMessage", chat_id=chat_id, text=text[start:start + 4000])


@tool(enabled=telegram_ready)
def send_telegram(text: str) -> str:
    """Отправить сообщение себе в Telegram (через бота, настроенного в «Интеграциях»).

    Args:
        text: текст сообщения
    """
    for chat_id in runtime.settings.telegram_chat_ids:
        telegram_send_text(chat_id, text)
    return "Отправлено в Telegram"


# --- Home Assistant ------------------------------------------------------------------------


def ha_ready() -> bool:
    return bool(runtime.settings.ha_url and runtime.settings.ha_token)


def _ha(method: str, path: str, payload: dict | None = None) -> object:
    settings = runtime.settings
    response = httpx.request(
        method, settings.ha_url.rstrip("/") + path, json=payload, timeout=12,
        headers={"Authorization": f"Bearer {settings.ha_token}", "Content-Type": "application/json"},
    )
    if response.status_code == 401:
        raise PermissionError("Home Assistant отклонил токен")
    response.raise_for_status()
    return response.json() if response.content else {}


@tool(enabled=ha_ready)
def smart_home_devices(domain: str = "") -> list[dict]:
    """Список устройств умного дома (Home Assistant) и их состояние.

    Args:
        domain: тип устройств: light (свет), switch (розетки), climate (климат), media_player, cover (шторы),
            sensor; пусто — все
    """
    states = _ha("GET", "/api/states")
    rows = []
    for state in states:
        entity = state["entity_id"]
        if domain and not entity.startswith(domain + "."):
            continue
        rows.append({"entity_id": entity, "name": state.get("attributes", {}).get("friendly_name", entity),
                     "state": state.get("state")})
    return rows[:80]


@tool(enabled=ha_ready)
def smart_home_action(entity_id: str, action: str, data_json: str = "") -> str:
    """Управление устройством умного дома (Home Assistant): включить, выключить, переключить, яркость и т. п.

    Args:
        entity_id: устройство из smart_home_devices, например «light.kitchen»
        action: сервис: turn_on, turn_off, toggle, set_temperature, open_cover, close_cover, media_play_pause…
        data_json: дополнительные параметры в JSON, например {"brightness_pct": 40} или {"temperature": 22}
    """
    domain = entity_id.split(".", 1)[0]
    payload = {"entity_id": entity_id}
    if data_json.strip():
        payload.update(json.loads(data_json))
    _ha("POST", f"/api/services/{domain}/{action}", payload)
    return f"{entity_id}: {action} — выполнено"


# --- Мелочи --------------------------------------------------------------------------------


@tool()
def is_day_off(date: str = "") -> dict:
    """Рабочий это день или выходной/праздник по производственному календарю России (isdayoff.ru).

    Args:
        date: дата ГГГГ-ММ-ДД; пусто — сегодня
    """
    day = datetime.strptime(date, "%Y-%m-%d") if date else datetime.now()
    code = _get(f"https://isdayoff.ru/{day:%Y%m%d}").text.strip()
    meaning = {"0": "рабочий день", "1": "выходной или праздник", "2": "сокращённый рабочий день"}
    return {"date": f"{day:%Y-%m-%d}", "status": meaning.get(code, f"неизвестно ({code})")}


def clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", "", text or "").strip()
