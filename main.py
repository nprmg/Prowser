"""J.A.R.V.I.S. — голосовой ассистент на Gemini/DeepSeek для управления компьютером.

Запуск:
    python main.py                 окно с чатом и голосом (по умолчанию)
    python main.py --mode chat     текстовый чат в консоли
    python main.py --mode voice    голосовой режим в консоли («Джарвис, …»)
    python main.py --list-mics     список микрофонов (номер укажите в MIC_DEVICE в .env)
    python main.py --provider deepseek   использовать только DeepSeek
    python main.py --background    запуск в фоне (значок в трее, окно скрыто) — так стартует автозапуск
    python main.py --tk            старое простое окно (если новое не открывается)
    python main.py --selftest      проверка, что все модули загружаются (для сборки .exe)
"""

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler

from jarvis.config import LOG_PATH, clean_key, key_problem, load_settings, save_api_keys


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
    gemini = clean_key(input("Ключ Gemini (Enter — пропустить): "))
    deepseek = clean_key(input("Ключ DeepSeek (Enter — пропустить): "))
    if not gemini and not deepseek:
        return False
    for name, key in (("Gemini", gemini), ("DeepSeek", deepseek)):
        if key_problem(key):
            print(f"Ключ {name} {key_problem(key)}. Скопируйте его заново.")
            return False
    save_api_keys(settings, gemini=gemini or None, deepseek=deepseek or None)
    return True


def selftest() -> int:
    """Проверка сборки: все модули импортируются, инструменты регистрируются, ресурсы на месте."""
    import importlib

    from jarvis import paths, tools

    modules = [
        "jarvis.brain", "jarvis.assistant", "jarvis.quick", "jarvis.agent", "jarvis.telegram_bot",
        "jarvis.llm.gemini", "jarvis.llm.deepseek", "jarvis.voice.speaker", "jarvis.voice.effects",
        "jarvis.voice.listener", "jarvis.ui.logo", "jarvis.ui.webui", "jarvis.ui.overlay",
        "webview", "edge_tts", "pygame", "numpy", "sounddevice", "speech_recognition", "PIL", "psutil",
    ]
    failed = []
    for name in modules:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{name}: {exc!r}")
    for resource in (paths.WEB_DIR / "index.html", paths.WEB_DIR / "app.js", paths.WEB_DIR / "style.css"):
        if not resource.exists():
            failed.append(f"нет файла {resource}")
    print(f"tools: {len(tools.TOOLS)}; data dir: {paths.DATA}")
    print("\n".join(failed) if failed else "SELFTEST OK")
    return 1 if failed else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="J.A.R.V.I.S. — голосовой ассистент на Gemini и DeepSeek")
    parser.add_argument("--mode", choices=["gui", "chat", "voice"], default="gui", help="режим работы")
    parser.add_argument(
        "--provider", choices=["auto", "gemini", "deepseek"], help="какой ИИ использовать (по умолчанию из .env)"
    )
    parser.add_argument("--no-tts", action="store_true", help="не озвучивать ответы")
    parser.add_argument("--list-mics", action="store_true", help="показать микрофоны и выйти")
    parser.add_argument("--background", action="store_true", help="запуститься в фоне (окно скрыто)")
    parser.add_argument("--tk", action="store_true", help="старое окно на Tkinter")
    parser.add_argument("--selftest", action="store_true", help="проверить загрузку модулей и выйти")
    args = parser.parse_args()
    setup_logging()

    if args.selftest:
        sys.exit(selftest())

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
        if not args.tk:
            try:
                from jarvis.ui.webui import run_webui

                run_webui(settings, background=args.background)
                return
            except ImportError as exc:  # нет pywebview — запасное окно
                logging.getLogger("jarvis").warning("Новое окно недоступно (%s), открываю простое", exc)
        from jarvis.ui.gui import run_gui

        run_gui(settings)
        return

    if not ensure_api_key_console(settings):
        sys.exit("API-ключ не задан.")
    from jarvis.ui.console import run_chat, run_voice

    (run_chat if args.mode == "chat" else run_voice)(settings)


if __name__ == "__main__":
    main()
