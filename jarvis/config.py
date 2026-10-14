"""Загрузка настроек из файла .env и переменных окружения."""

import os
import re
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
    return value.lower() in ("1", "true", "yes", "on", "да", "enabled")


def clean_key(key: str) -> str:
    """Убирает пробелы и невидимые символы, которые часто попадают в ключ при копировании."""
    return re.sub(r"[\s\u200b-\u200d\u2060\ufeff]", "", key or "")


def key_problem(key: str) -> str | None:
    """Описание проблемы с ключом или None, если ключ выглядит нормально."""
    if key and not (key.isascii() and key.isprintable()):
        return "содержит недопустимые символы (например, русские буквы)"
    return None


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
    # Какую модель использовать: auto — Gemini, а при сбое DeepSeek; gemini; deepseek.
    provider: str = "auto"
    api_key: str = ""  # Gemini
    # Flash-Lite — самая быстрая модель Gemini с большим бесплатным лимитом; 3.8-flash умнее, но медленнее.
    model: str = "gemini-3.5-flash-lite"
    # Уровень "размышлений" модели: minimal / low / medium / high (если уровень не поддерживается,
    # берётся следующий). Пусто — по умолчанию модели.
    thinking_level: str = "minimal"
    gemini_timeout: float = 20.0
    deepseek_api_key: str = ""
    deepseek_model: str = "deepseek-flash"
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_timeout: float = 30.0
    # Режим размышлений DeepSeek: disabled — быстрее (для голосового ассистента), enabled — умнее.
    deepseek_thinking: str = "disabled"
    # Простые команды («открой блокнот», «громкость 30») выполнять сразу, без ИИ.
    fast_commands: bool = True
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
        provider=(_get("LLM_PROVIDER") or Settings.provider).lower(),
        api_key=clean_key(_get("GEMINI_API_KEY") or _get("GOOGLE_API_KEY")),
        model=_get("GEMINI_MODEL") or Settings.model,
        thinking_level=_get("GEMINI_THINKING_LEVEL", Settings.thinking_level).lower(),
        gemini_timeout=_get_float("GEMINI_TIMEOUT", Settings.gemini_timeout),
        deepseek_api_key=clean_key(_get("DEEPSEEK_API_KEY")),
        deepseek_model=_get("DEEPSEEK_MODEL") or Settings.deepseek_model,
        deepseek_base_url=(_get("DEEPSEEK_BASE_URL") or Settings.deepseek_base_url).rstrip("/"),
        deepseek_timeout=_get_float("DEEPSEEK_TIMEOUT", Settings.deepseek_timeout),
        deepseek_thinking="enabled" if _get_bool("DEEPSEEK_THINKING", False) else "disabled",
        fast_commands=_get_bool("FAST_COMMANDS", Settings.fast_commands),
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


def save_env_value(name: str, value: str) -> None:
    """Сохраняет параметр в .env (создаёт файл из .env.example, если его ещё нет)."""
    if not ENV_PATH.exists():
        if ENV_EXAMPLE_PATH.exists():
            ENV_PATH.write_text(ENV_EXAMPLE_PATH.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            ENV_PATH.touch()
    set_key(str(ENV_PATH), name, value, quote_mode="never")
    os.environ[name] = value


def save_api_keys(settings: Settings, gemini: str | None = None, deepseek: str | None = None) -> None:
    """Сохраняет ключи в .env и в настройки. None — не менять, пустая строка — удалить ключ."""
    if gemini is not None:
        gemini = clean_key(gemini)
        save_env_value("GEMINI_API_KEY", gemini)
        settings.api_key = gemini
    if deepseek is not None:
        deepseek = clean_key(deepseek)
        save_env_value("DEEPSEEK_API_KEY", deepseek)
        settings.deepseek_api_key = deepseek
