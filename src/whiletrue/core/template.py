from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, cast


class TemplateError(Exception):
    pass


_TOKEN = re.compile(r"\{\{\s*([^{}]*?)\s*\}\}")
_PATH = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)*$")


def _lookup(scope: Any, path: str) -> Any:
    current: Any = scope
    for key in path.split("."):
        if isinstance(current, Mapping) and key in current:
            current = cast(Mapping[str, Any], current)[key]
        elif (
            isinstance(current, list) and key.isdigit() and int(key) < len(cast(list[Any], current))
        ):
            current = cast(list[Any], current)[int(key)]
        else:
            raise TemplateError(f'Template variable "{path}" is not defined')
    return current


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def render_template(template: str, scope: Any) -> str:
    """Replaces `{{dotted.path}}` with values from `scope`. Strict: a missing variable raises.

    Objects are JSON-encoded. (Full expressions arrive with the expression-language item.)
    """

    def substitute(match: re.Match[str]) -> str:
        expr = match.group(1)
        if not _PATH.match(expr):
            raise TemplateError(f'Unsupported template expression "{expr}"')
        return _stringify(_lookup(scope, expr))

    return _TOKEN.sub(substitute, template)


def render_deep(value: Any, scope: Any) -> Any:
    """Renders every string leaf of a JSON-like value."""
    if isinstance(value, str):
        return render_template(value, scope)
    if isinstance(value, list):
        return [render_deep(v, scope) for v in value]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, dict):
        return {k: render_deep(v, scope) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    return value
