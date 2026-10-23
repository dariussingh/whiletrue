from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from whiletrue.control.map_ import check_expr
from whiletrue.core import (
    CompositeContext,
    ConfigIssue,
    Emit,
    PipelineNode,
    Strategy,
    ValidateContext,
    define_strategy,
    parse_expr,
)
from whiletrue.core.module import PortValues


class SwitchConfig(BaseModel):
    #: CEL expression; its value (strings as-is, anything else as JSON) selects a case.
    on: str = Field(min_length=1)
    #: Case value -> id of a child step.
    cases: dict[str, str] = Field(default_factory=lambda: {})
    #: Child step id used when no case matches. Without it, no match fires `skip`.
    default: str | None = None


def _validate(config: SwitchConfig, _vctx: ValidateContext) -> list[ConfigIssue]:
    return check_expr("switch.on", config.on)


def _validate_node(
    config: SwitchConfig, node: PipelineNode, _vctx: ValidateContext
) -> list[ConfigIssue]:
    ids = {c.id for c in node.children}
    targets = [*config.cases.items(), *([("default", config.default)] if config.default else [])]
    return [
        ConfigIssue("error", f'`switch` case "{case}" targets unknown step "{target}"')
        for case, target in targets
        if target not in ids
    ]


def _key(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, separators=(",", ":"))


async def _run(
    children: list[PipelineNode], input: PortValues, ctx: CompositeContext[SwitchConfig]
) -> Emit:
    value = parse_expr(ctx.config.on).eval({**ctx.scope.snapshot(), "input": input.get("in")})
    target = ctx.config.cases.get(_key(value), ctx.config.default)
    child = next((c for c in children if c.id == target), None)
    if child is None:
        return Emit("skip")
    return await ctx.run(child, input)


#: Runs exactly one child, chosen by evaluating `on` and looking it up in `cases`.
switch_strategy: Strategy[Any] = define_strategy(
    Strategy(
        id="switch", config=SwitchConfig, validate=_validate, validate_node=_validate_node, run=_run
    )
)
