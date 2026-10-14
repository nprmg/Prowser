"""Загрузка настроек из файла .env и переменных окружения."""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv, set_key

ROOT = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE_PATH = ROOT / ".env.example"
LOG_PATH = ROOT / "jarvis.log"

DEFAULT_WAKE_WORDS = "джарвис,jarvis,джервис,жарвис,джарвиз,дварвиз,дарвис,гарвис"


def _get(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _get_bool(name: str, default: bool) -> bool:
    value = _get(name)
    if not value:
        return default
    return value.lower() in ("1", "true", "yes", "on", "да")


def _get_float(name: str, default: float) -> float:
    try:
        return float(_get(name) or default)
    except ValueError:
        return default


def _get_optional_int(name: str) -> int | None:
    value = _get(name)
    try:
        return int(value) if value else None
    except ValueError:
        return None


@dataclass
class Settings:
    api_key: str = ""
    model: str = "gemini-3.8-flash"
    # Уровень "размышлений" модели: minimal / low / medium / high. Пусто — по умолчанию модели.
    thinking_level: str = "low"
    assistant_name: str = "Джарвис"
    user_title: str = "сэр"
    language: str = "ru-RU"
    wake_words: list[str] = field(default_factory=lambda: DEFAULT_WAKE_WORDS.split(","))
    tts_engine: str = "edge"  # edge | system | off
    tts_voice: str = "ru-RU-DmitryNeural"
    tts_rate: str = "+5%"
    mic_device: int | None = None
    phrase_time_limit: float = 15.0
    confirm_dangerous: bool = True
    allow_shell: bool = True
    enable_google_search: bool = False


def load_settings() -> Settings:
    load_dotenv(ENV_PATH, override=False)
    wake_words = [w.strip().lower() for w in _get("WAKE_WORDS", DEFAULT_WAKE_WORDS).split(",") if w.strip()]
    return Settings(
        api_key=_get("GEMINI_API_KEY") or _get("GOOGLE_API_KEY"),
        model=_get("GEMINI_MODEL") or Settings.model,
        thinking_level=_get("GEMINI_THINKING_LEVEL", Settings.thinking_level).lower(),
        assistant_name=_get("ASSISTANT_NAME") or Settings.assistant_name,
        user_title=_get("USER_TITLE") or Settings.user_title,
        language=_get("LANGUAGE") or Settings.language,
        wake_words=wake_words,
        tts_engine=(_get("TTS_ENGINE") or Settings.tts_engine).lower(),
        tts_voice=_get("TTS_VOICE") or Settings.tts_voice,
        tts_rate=_get("TTS_RATE") or Settings.tts_rate,
        mic_device=_get_optional_int("MIC_DEVICE"),
        phrase_time_limit=_get_float("PHRASE_TIME_LIMIT", Settings.phrase_time_limit),
        confirm_dangerous=_get_bool("CONFIRM_DANGEROUS", Settings.confirm_dangerous),
        allow_shell=_get_bool("ALLOW_SHELL", Settings.allow_shell),
        enable_google_search=_get_bool("ENABLE_GOOGLE_SEARCH", Settings.enable_google_search),
    )


def save_api_key(api_key: str) -> None:
    """Сохраняет ключ в .env (создаёт файл из .env.example, если его ещё нет)."""
    if not ENV_PATH.exists():
        if ENV_EXAMPLE_PATH.exists():
            ENV_PATH.write_text(ENV_EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            ENV_PATH.touch()
    set_key(str(ENV_PATH), "GEMINI_API_KEY", api_key, quote_mode="never")
    os.environ["GEMINI_API_KEY"] = api_key
