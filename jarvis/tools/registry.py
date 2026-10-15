"""Реестр инструментов: Python-функции -> объявления функций для Gemini и их выполнение."""

import inspect
import logging
import re
import types as pytypes
import typing
from dataclasses import dataclass
from typing import Any, Callable

from ..runtime import runtime

log = logging.getLogger("jarvis.tools")

ConfirmRule = bool | str | Callable[..., str | None]


@dataclass
class Tool:
    func: Callable[..., Any]
    confirm: ConfirmRule = False
    enabled: Callable[[], bool] = lambda: True
    # Спрашивать даже в режиме «Полный доступ» (выключение ПК и т. п. — по ошибке распознавания
    # можно потерять несохранённую работу).
    critical: bool = False

    @property
    def name(self) -> str:
        return self.func.__name__


TOOLS: dict[str, Tool] = {}


def tool(confirm: ConfirmRule = False, enabled: Callable[[], bool] | None = None, critical: bool = False):
    """Регистрирует функцию как инструмент ассистента.

    confirm: False — без подтверждения; True или строка-шаблон (str.format по аргументам) —
    всегда спрашивать; функция(**kwargs) -> текст вопроса или None — спрашивать по условию.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        TOOLS[func.__name__] = Tool(func, confirm, enabled or (lambda: True), critical)
        return func

    return decorator


# --- Построение JSON-схемы параметров из сигнатуры и docstring --------------------------

_JSON_TYPES = {str: "string", int: "integer", float: "number", bool: "boolean"}


def _schema_for(annotation: Any) -> dict[str, Any]:
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Literal:
        return {"type": _JSON_TYPES.get(type(args[0]), "string"), "enum": list(args)}
    if origin in (typing.Union, pytypes.UnionType):
        non_none = [a for a in args if a is not type(None)]
        return _schema_for(non_none[0]) if len(non_none) == 1 else {"type": "string"}
    if origin is list:
        return {"type": "array", "items": _schema_for(args[0] if args else str)}
    return {"type": _JSON_TYPES.get(annotation, "string")}


def _parse_docstring(doc: str) -> tuple[str, dict[str, str]]:
    """Разбирает docstring в стиле Google: описание + секция Args."""
    doc = inspect.cleandoc(doc or "")
    if "Args:" not in doc:
        return doc, {}
    description, args_block = doc.split("Args:", 1)
    params: dict[str, str] = {}
    current = None
    for line in args_block.splitlines():
        indent = len(line) - len(line.lstrip())
        match = re.match(r"(\w+)\s*:\s*(.*)", line.strip())
        if match and indent <= 4:
            current = match.group(1)
            params[current] = match.group(2).strip()
        elif current and line.strip():
            params[current] += " " + line.strip()
    return description.strip(), params


def build_declaration(tool_: Tool) -> dict[str, Any]:
    func = tool_.func
    description, param_docs = _parse_docstring(func.__doc__ or "")
    hints = typing.get_type_hints(func)
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, param in inspect.signature(func).parameters.items():
        schema = _schema_for(hints.get(name, str))
        text = param_docs.get(name, "")
        if param.default is inspect.Parameter.empty:
            required.append(name)
        elif param.default not in (None, ""):
            text = f"{text} (по умолчанию: {param.default})".strip()
        if text:
            schema["description"] = text
        properties[name] = schema
    declaration: dict[str, Any] = {"name": tool_.name, "description": description}
    if properties:
        declaration["parameters_json_schema"] = {
            "type": "object",
            "properties": properties,
            **({"required": required} if required else {}),
        }
    return declaration


def active_tools() -> list[Tool]:
    return [t for t in TOOLS.values() if t.enabled()]


def declarations() -> list[dict[str, Any]]:
    return [build_declaration(t) for t in active_tools()]


# --- Выполнение -----------------------------------------------------------------------------


def _coerce_args(func: Callable[..., Any], args: dict[str, Any]) -> dict[str, Any]:
    """Отбрасывает лишние аргументы и приводит числа к нужному типу (JSON отдаёт 5.0 вместо 5)."""
    hints = typing.get_type_hints(func)
    params = inspect.signature(func).parameters
    result = {}
    for key, value in args.items():
        if key not in params:
            continue
        annotation = hints.get(key)
        non_none = [a for a in typing.get_args(annotation) if a is not type(None)] or [annotation]
        if int in non_none and bool not in non_none and isinstance(value, float):
            value = int(value)
        elif float in non_none and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        result[key] = value
    return result


def _confirmation_prompt(tool_: Tool, args: dict[str, Any]) -> str | None:
    rule = tool_.confirm
    if rule is False:
        return None
    if callable(rule):
        return rule(**args)
    if isinstance(rule, str):
        try:
            return rule.format(**args)
        except (KeyError, IndexError):
            return rule
    pretty = ", ".join(f"{k}={v!r}" for k, v in args.items())
    return f"Выполнить {tool_.name}({pretty})?"


def execute(name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Выполняет инструмент и возвращает словарь для ответа модели: {"result": ...} или {"error": ...}."""
    tool_ = TOOLS.get(name)
    if tool_ is None or not tool_.enabled():
        return {"error": f"Инструмент '{name}' недоступен."}
    try:
        args = _coerce_args(tool_.func, args)
        prompt = _confirmation_prompt(tool_, args)
        settings = runtime.settings
        must_ask = (tool_.critical and settings.confirm_power) or not settings.full_access
        if prompt and settings.confirm_dangerous and must_ask and not runtime.confirm(prompt):
            return {"error": "Пользователь отменил это действие."}
        result = tool_.func(**args)
    except (LookupError, OSError, ValueError, NotImplementedError) as exc:  # ожидаемые ошибки: «не найдено» и т. п.
        log.warning("Инструмент %s(%s): %s", name, args, exc)
        return {"error": f"{type(exc).__name__}: {exc}"}
    except Exception as exc:  # инструмент не должен ронять ассистента
        log.exception("Ошибка инструмента %s(%s)", name, args)
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"result": result if result is not None else "ok"}
