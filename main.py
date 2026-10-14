"""J.A.R.V.I.S. — голосовой ассистент на Gemini/DeepSeek для управления компьютером.

Запуск:
    python main.py                 окно с чатом и голосом (по умолчанию)
    python main.py --mode chat     текстовый чат в консоли
    python main.py --mode voice    голосовой режим в консоли («Джарвис, …»)
    python main.py --list-mics     список микрофонов (номер укажите в MIC_DEVICE в .env)
    python main.py --provider deepseek   использовать только DeepSeek
"""

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler

from jarvis.config import LOG_PATH, load_settings, save_api_keys


def setup_logging() -> None:
    handlers: list[logging.Handler] = [RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8")]
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )
    # Сетевые библиотеки слишком многословны на уровне INFO.
    for noisy in ("httpx", "httpcore", "google_genai", "urllib3", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def ensure_api_key_console(settings) -> bool:
    if settings.api_key or settings.deepseek_api_key:
        return True
    print("Нужен хотя бы один API-ключ (можно оба: второй станет запасным).")
    print("  Gemini (бесплатно): https://aistudio.google.com/apikey")
    print("  DeepSeek: https://platform.deepseek.com/api_keys")
    gemini = input("Ключ Gemini (Enter — пропустить): ").strip()
    deepseek = input("Ключ DeepSeek (Enter — пропустить): ").strip()
    if not gemini and not deepseek:
        return False
    save_api_keys(settings, gemini=gemini or None, deepseek=deepseek or None)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="J.A.R.V.I.S. — голосовой ассистент на Gemini и DeepSeek")
    parser.add_argument("--mode", choices=["gui", "chat", "voice"], default="gui", help="режим работы")
    parser.add_argument(
        "--provider", choices=["auto", "gemini", "deepseek"], help="какой ИИ использовать (по умолчанию из .env)"
    )
    parser.add_argument("--no-tts", action="store_true", help="не озвучивать ответы")
    parser.add_argument("--list-mics", action="store_true", help="показать микрофоны и выйти")
    args = parser.parse_args()
    setup_logging()

    if args.list_mics:
        from jarvis.voice.listener import list_microphones

        print("\n".join(list_microphones()) or "Микрофоны не найдены")
        return

    settings = load_settings()
    if args.no_tts:
        settings.tts_engine = "off"
    if args.provider:
        settings.provider = args.provider

    if args.mode == "gui":
        from jarvis.ui.gui import run_gui

        run_gui(settings)
        return

    if not ensure_api_key_console(settings):
        sys.exit("API-ключ не задан.")
    from jarvis.ui.console import run_chat, run_voice

    (run_chat if args.mode == "chat" else run_voice)(settings)


if __name__ == "__main__":
    main()
