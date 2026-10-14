"""Интерфейсы ассистента: окно (gui) и консоль (console)."""

from typing import Any


def format_tool_call(name: str, args: dict[str, Any]) -> str:
    """Короткая строка вида «⚙ open_app(name=«Telegram»)» для показа в чате."""
    parts = []
    for key, value in args.items():
        shown = f"«{value}»" if isinstance(value, str) else repr(value)
        if len(shown) > 60:
            shown = shown[:57] + "…"
        parts.append(f"{key}={shown}")
    return f"⚙ {name}({', '.join(parts)})"
