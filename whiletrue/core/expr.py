from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

import cel
from cel import stdlib as cel_stdlib

#: Guards against pathological inputs; the native evaluator cannot be interrupted once started.
MAX_EXPR_LENGTH = 4096

#: CEL's standard, side-effect-free functions and operators. Anything else is rejected by `check`.
ALLOWED_FUNCTIONS = frozenset(cel_stdlib.STDLIB_FUNCTIONS) | frozenset(
    {
        "size", "has", "int", "uint", "double", "string", "bool", "bytes", "type", "dyn",
        "contains", "startsWith", "endsWith", "matches", "exists", "all", "exists_one", "map",
        "filter", "min", "max", "duration", "timestamp", "getFullYear", "getMonth", "getDate",
        "getDayOfMonth", "getDayOfWeek", "getHours", "getMinutes", "getSeconds",
        "getMilliseconds", "optional", "in",
    }
)  # fmt: skip


class ExprError(ValueError):
    """Raised for syntax errors and evaluation failures."""


def _is_operator(name: str) -> bool:
    return name.startswith("_") or name.startswith("@")


@dataclass(frozen=True)
class Expr:
    """A parsed CEL expression. `check` is static, `eval` runs against a context mapping."""

    source: str
    _program: Any

    def _refs(self) -> tuple[list[str], list[str]]:
        refs = cast(dict[str, list[str]], self._program.references())
        return refs.get("variables", []), refs.get("functions", [])

    def check(self, known_variables: set[str] | frozenset[str]) -> list[str]:
        """Returns problems found without evaluating: unknown variables, disallowed functions."""
        variables, functions = self._refs()
        problems: list[str] = []
        known = ", ".join(sorted(known_variables)) or "none"
        for name in dict.fromkeys(variables):
            if name not in known_variables:
                problems.append(f'Unknown variable "{name}" (known: {known})')
        for name in dict.fromkeys(functions):
            if not _is_operator(name) and name not in ALLOWED_FUNCTIONS:
                problems.append(f'Function "{name}" is not allowed')
        return problems

    def eval(self, context: Mapping[str, Any]) -> Any:
        try:
            ctx = cel.Context(dict(context))
            cel_stdlib.add_stdlib_to_context(ctx)  # max, split, lowerAscii, ... (all pure)
            return self._program.execute(ctx)
        except Exception as error:
            raise ExprError(f"Cannot evaluate `{self.source}`: {error}") from error

    def eval_bool(self, context: Mapping[str, Any]) -> bool:
        value = self.eval(context)
        if not isinstance(value, bool):
            raise ExprError(f"`{self.source}` must be a boolean, got {type(value).__name__}")
        return value


def parse_expr(source: str) -> Expr:
    if len(source) > MAX_EXPR_LENGTH:
        raise ExprError(f"Expression is too long ({len(source)} > {MAX_EXPR_LENGTH} characters)")
    try:
        program = cel.compile(source)
    except Exception as error:
        text = str(error)
        if "reserve" in text.lower():
            raise ExprError(
                f"`{source}` uses a CEL reserved word (such as `loop`, `if`, `for`) as a name; "
                "use `iter` for the loop scope"
            ) from error
        raise ExprError(f"Invalid expression (syntax) `{source}`: {text}") from error
    return Expr(source, program)
