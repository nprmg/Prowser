"""Где лежат данные пользователя и ресурсы программы — при запуске из исходников и из .exe."""

import os
import sys
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))  # запущено из собранного .exe (PyInstaller)

if FROZEN:
    # Ресурсы (интерфейс, иконка, пример настроек) PyInstaller распаковывает в sys._MEIPASS.
    RESOURCES = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    _exe_dir = Path(sys.executable).parent
    # Портативный режим: если рядом с .exe лежит .env — всё хранится там же.
    # Иначе — в %APPDATA%\\Jarvis, чтобы настройки и ключи переживали обновление программы.
    if (_exe_dir / ".env").exists():
        DATA = _exe_dir
    else:
        DATA = Path(os.environ.get("APPDATA") or Path.home()) / "Jarvis"
else:
    RESOURCES = Path(__file__).resolve().parent.parent
    DATA = RESOURCES

DATA.mkdir(parents=True, exist_ok=True)

WEB_DIR = RESOURCES / "jarvis" / "web"  # интерфейс окна (HTML/CSS/JS)
ENV_PATH = DATA / ".env"
ENV_EXAMPLE_PATH = RESOURCES / ".env.example"
LOG_PATH = DATA / "jarvis.log"
CACHE_DIR = DATA / "cache"
