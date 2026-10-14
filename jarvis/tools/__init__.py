"""Инструменты, которыми Gemini управляет компьютером.

Чтобы добавить свою команду, напишите функцию с аннотациями типов и docstring
в стиле Google (описание + секция Args) и пометьте её декоратором @tool().
"""

from . import apps, files, input_control, system, timers, web  # noqa: F401  (регистрация инструментов)
from .registry import TOOLS, active_tools, declarations, execute, tool

__all__ = ["TOOLS", "active_tools", "declarations", "execute", "tool"]
