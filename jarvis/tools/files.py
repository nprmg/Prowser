"""Работа с файлами и папками."""

import fnmatch
import os
import shutil
import time
from pathlib import Path

from ._platform import IS_LINUX, IS_WIN, open_default, run
from .registry import tool

_FOLDERS = {
    "desktop": (["рабочий стол", "рабочем столе", "desktop"], "Desktop", "DESKTOP", "Desktop"),
    "documents": (["документы", "мои документы", "documents"], "Personal", "DOCUMENTS", "Documents"),
    "downloads": (["загрузки", "закачки", "downloads"], "{374DE290-123F-4565-9164-39C4925E467B}", "DOWNLOAD", "Downloads"),
    "pictures": (["изображения", "картинки", "фото", "pictures"], "My Pictures", "PICTURES", "Pictures"),
    "music": (["музыка", "music"], "My Music", "MUSIC", "Music"),
    "videos": (["видео", "videos"], "My Video", "VIDEOS", "Videos"),
    "home": (["домашняя папка", "папка пользователя", "home", "~"], None, None, ""),
}


def known_folder(key: str) -> Path:
    """Реальный путь к стандартной папке (учитывает перенос в OneDrive и русские имена папок)."""
    _aliases, reg_name, xdg_name, default = _FOLDERS[key]
    if IS_WIN and reg_name:
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            ) as reg_key:
                value, _ = winreg.QueryValueEx(reg_key, reg_name)
            path = Path(os.path.expandvars(value))
            if path.exists():
                return path
        except OSError:
            pass
    if IS_LINUX and xdg_name and shutil.which("xdg-user-dir"):
        try:
            value = run(["xdg-user-dir", xdg_name], timeout=5).stdout.strip()
            if value and Path(value).exists():
                return Path(value)
        except (OSError, ValueError):
            pass
    return Path.home() / default


def _folder_key(name: str) -> str | None:
    name = name.lower().replace("ё", "е").strip()
    for key, (aliases, *_rest) in _FOLDERS.items():
        if name == key or name in aliases:
            return key
    return None


def resolve_path(path: str) -> Path:
    """Понимает «рабочий стол/заметки.txt», «downloads», «~/file», «%USERPROFILE%\\...» и обычные пути."""
    raw = os.path.expandvars(os.path.expanduser(path.strip().strip("\"'")))
    first, _sep, rest = raw.replace("\\", "/").partition("/")
    key = _folder_key(first)
    if key:
        base = known_folder(key)
        return base / rest if rest else base
    candidate = Path(raw)
    return candidate if candidate.is_absolute() else Path.home() / candidate


def _human_size(size: int) -> str:
    for unit in ("Б", "КБ", "МБ", "ГБ"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} ТБ"


@tool()
def list_directory(path: str = "desktop") -> dict:
    """Показать содержимое папки.

    Args:
        path: путь к папке или её название: «рабочий стол», «загрузки», «документы», «C:/Games»
    """
    folder = resolve_path(path)
    if not folder.is_dir():
        raise FileNotFoundError(f"Папка не найдена: {folder}")
    items = []
    for entry in sorted(folder.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if entry.name.startswith(".") or entry.name.lower() == "desktop.ini":
            continue
        try:
            item = {"name": entry.name, "type": "папка" if entry.is_dir() else "файл"}
            if entry.is_file():
                item["size"] = _human_size(entry.stat().st_size)
        except OSError:
            continue
        items.append(item)
    return {"path": str(folder), "total": len(items), "items": items[:150]}


@tool()
def open_path(path: str) -> str:
    """Открыть файл (программой по умолчанию) или папку (в проводнике).

    Args:
        path: путь к файлу или папке, можно «загрузки», «рабочий стол/отчёт.docx»
    """
    target = resolve_path(path)
    if not target.exists():
        raise FileNotFoundError(f"Не найдено: {target}")
    open_default(str(target))
    return f"Открыл {target}"


_SKIP_DIRS = {
    "appdata", "node_modules", "$recycle.bin", "windows", "program files", "program files (x86)",
    "programdata", "__pycache__", "venv", ".venv", "site-packages", "library", "system volume information",
}


@tool()
def find_files(query: str, folder: str = "home", max_results: int = 20) -> dict:
    """Найти файлы или папки по имени (поиск по подстроке или маске вида *.pdf).

    Args:
        query: часть имени файла или маска, например «отчёт», «*.mp3», «резюме*.docx»
        folder: где искать: «home» (вся папка пользователя), «загрузки», «D:/» и т. п.
        max_results: сколько результатов вернуть максимум
    """
    root = resolve_path(folder)
    if not root.is_dir():
        raise FileNotFoundError(f"Папка не найдена: {root}")
    pattern = query.lower()
    use_mask = any(ch in pattern for ch in "*?[")
    deadline = time.monotonic() + 15
    found: list[str] = []
    for current, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d.lower() not in _SKIP_DIRS]
        for name in dirs + files:
            lowered = name.lower()
            matched = fnmatch.fnmatch(lowered, pattern) if use_mask else pattern in lowered
            if matched:
                found.append(os.path.join(current, name))
                if len(found) >= max_results:
                    return {"results": found, "complete": False}
        if time.monotonic() > deadline:
            return {"results": found, "complete": False, "note": "Поиск остановлен по времени (15 с)."}
    return {"results": found, "complete": True}


@tool()
def read_text_file(path: str, max_chars: int = 6000) -> dict:
    """Прочитать текстовый файл (txt, md, код, csv, логи).

    Args:
        path: путь к файлу
        max_chars: сколько символов прочитать максимум
    """
    target = resolve_path(path)
    text = target.read_text(encoding="utf-8", errors="replace")
    return {"path": str(target), "text": text[:max_chars], "truncated": len(text) > max_chars}


def _confirm_write(path: str, content: str = "", append: bool = False) -> str | None:
    target = resolve_path(path)
    if target.exists() and not append:
        return f"Файл «{target}» уже существует. Перезаписать его?"
    return None


@tool(confirm=_confirm_write)
def write_text_file(path: str, content: str, append: bool = False) -> str:
    """Создать текстовый файл или записать/дописать в него текст (заметки, списки, код).

    Args:
        path: путь к файлу, например «рабочий стол/список покупок.txt»
        content: текст для записи
        append: дописать в конец файла вместо перезаписи
    """
    target = resolve_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "a" if append else "w", encoding="utf-8") as file:
        file.write(content)
    return f"Сохранено: {target}"


@tool()
def create_folder(path: str) -> str:
    """Создать папку.

    Args:
        path: путь к новой папке, например «рабочий стол/Проекты»
    """
    target = resolve_path(path)
    target.mkdir(parents=True, exist_ok=True)
    return f"Папка создана: {target}"


@tool(confirm=lambda path: f"Удалить «{resolve_path(path)}» в корзину?")
def delete_path(path: str) -> str:
    """Удалить файл или папку (в корзину, чтобы можно было восстановить).

    Args:
        path: путь к файлу или папке
    """
    from send2trash import send2trash

    target = resolve_path(path)
    if not target.exists():
        raise FileNotFoundError(f"Не найдено: {target}")
    send2trash(str(target))
    return f"Перемещено в корзину: {target}"


def _confirm_move(source: str, destination: str) -> str | None:
    dest = resolve_path(destination)
    if dest.exists() and not dest.is_dir():
        return f"«{dest}» уже существует. Заменить его?"
    return None


@tool(confirm=_confirm_move)
def move_path(source: str, destination: str) -> str:
    """Переместить или переименовать файл/папку.

    Args:
        source: что переместить
        destination: куда (папка) или новое имя/путь
    """
    src, dest = resolve_path(source), resolve_path(destination)
    if not src.exists():
        raise FileNotFoundError(f"Не найдено: {src}")
    result = shutil.move(str(src), str(dest))
    return f"Готово: {result}"
