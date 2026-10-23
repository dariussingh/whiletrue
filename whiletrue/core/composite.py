"""Composite expansion: `composite: { uses: <file>, params: {...} }` becomes a `sequence` node
holding the composite's steps, with `{{params.x}}` filled in. Done at load time, so validation,
planning and hashing see the real tree."""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from whiletrue.core.config import (
    CompositeDefinition,
    ConfigError,
    PipelineNode,
    SourceLoc,
    load_composite,
)
from whiletrue.core.template import render_template

MAX_DEPTH = 10
RECIPE_PREFIX = "recipe:"
RECIPES_DIR = Path(__file__).resolve().parent.parent / "recipes"

_PARAM = re.compile(r"\{\{\s*params\.([A-Za-z_][A-Za-z0-9_-]*)\s*\}\}")


class _CompositeStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uses: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=lambda: {})


def _resolve_path(uses: str, base_dir: Path, loc: SourceLoc) -> Path:
    if uses.startswith(RECIPE_PREFIX):
        path = RECIPES_DIR / f"{uses[len(RECIPE_PREFIX) :]}.yaml"
        if not path.is_file():
            raise ConfigError(loc, f'Unknown recipe "{uses[len(RECIPE_PREFIX) :]}"')
        return path
    path = (base_dir / uses).resolve()
    if not path.is_file():
        raise ConfigError(loc, f"Composite file not found: {uses}")
    return path


def _bind_params(
    definition: CompositeDefinition, given: dict[str, Any], loc: SourceLoc
) -> dict[str, Any]:
    unknown = sorted(set(given) - set(definition.params))
    if unknown:
        known = ", ".join(sorted(definition.params)) or "none"
        raise ConfigError(
            loc, f'Unknown param "{unknown[0]}" for composite "{definition.name}" (known: {known})'
        )
    bound: dict[str, Any] = {}
    for name, spec in definition.params.items():
        if name in given:
            bound[name] = given[name]
        elif spec.required:
            raise ConfigError(loc, f'Composite "{definition.name}" needs param "{name}"')
        else:
            bound[name] = spec.default
    return bound


def _substitute(value: Any, params: dict[str, Any], loc: SourceLoc) -> Any:
    """Fills `{{params.x}}`; a string that is exactly one token keeps the param's own type."""
    if isinstance(value, str):
        whole = _PARAM.fullmatch(value.strip())
        if whole:
            return _param(whole.group(1), params, loc)
        return _PARAM.sub(
            lambda m: render_template("{{v}}", {"v": _param(m.group(1), params, loc)}), value
        )
    if isinstance(value, list):
        return [_substitute(v, params, loc) for v in value]  # pyright: ignore[reportUnknownVariableType]
    if isinstance(value, dict):
        return {k: _substitute(v, params, loc) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
    return value


def _param(name: str, params: dict[str, Any], loc: SourceLoc) -> Any:
    if name not in params:
        raise ConfigError(loc, f'Unknown param "{name}"')
    return copy.deepcopy(params[name])


def _prepare(node: PipelineNode, prefix: str, params: dict[str, Any]) -> None:
    node.config = _substitute(node.config, params, node.loc)
    node.id = f"{prefix}.{node.id}"
    node.wires = {port: f"{prefix}.{target}" for port, target in node.wires.items()}
    if node.use == "switch":  # `switch` names sibling steps in its config; keep them in sync
        cases = node.config.get("cases")
        if isinstance(cases, dict):
            node.config["cases"] = {k: f"{prefix}.{v}" for k, v in cases.items()}  # pyright: ignore[reportUnknownVariableType]
        if isinstance(node.config.get("default"), str):
            node.config["default"] = f"{prefix}.{node.config['default']}"
    for child in node.children:
        _prepare(child, prefix, params)


def expand_composites(
    node: PipelineNode, base_dir: Path, sources: list[str], stack: tuple[Path, ...] = ()
) -> PipelineNode:
    """Returns `node` with every `composite` step replaced by its expansion (recursively)."""
    if node.use != "composite":
        node.children = [expand_composites(c, base_dir, sources, stack) for c in node.children]
        return node
    try:
        step = _CompositeStep.model_validate(node.config)
    except ValidationError as error:
        raise ConfigError(
            node.loc, f"Invalid composite step: {error.errors()[0]['msg']}"
        ) from error
    path = _resolve_path(step.uses, base_dir, node.loc)
    if path in stack:
        raise ConfigError(
            node.loc, f"Composite cycle: {' -> '.join(p.name for p in (*stack, path))}"
        )
    if len(stack) >= MAX_DEPTH:
        raise ConfigError(node.loc, f"Composites nested deeper than {MAX_DEPTH} levels")
    definition = load_composite(path)
    params = _bind_params(definition, step.params, node.loc)
    inner = definition.root
    _prepare(inner, node.id, params)
    if str(path) not in sources and not str(path).startswith(str(RECIPES_DIR)):
        sources.append(str(path))
    expanded = expand_composites(inner, path.parent, sources, (*stack, path))
    return PipelineNode(
        id=node.id,
        use="sequence",
        config={},
        children=[expanded],
        wires=node.wires,
        loc=node.loc,
        ports=definition.ports or None,
    )
