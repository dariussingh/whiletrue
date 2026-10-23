from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from whiletrue.control.chain import run_chain
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
from whiletrue.core.expr import ExprError
from whiletrue.core.module import PortValues

#: Variables a `map`/`switch` expression may use.
EXPR_VARIABLES = frozenset({"run", "item", "iter", "input"})


class MapConfig(BaseModel):
    #: CEL expression yielding a list. Each element is `iter.value` for one pass over the children.
    over: str = Field(min_length=1)


def check_expr(name: str, source: str) -> list[ConfigIssue]:
    try:
        problems = parse_expr(source).check(EXPR_VARIABLES)
    except ExprError as error:
        problems = [str(error)]
    return [ConfigIssue("error", f"`{name}`: {p}") for p in problems]


def _validate(config: MapConfig, _vctx: ValidateContext) -> list[ConfigIssue]:
    return check_expr("map.over", config.over)


async def _run(
    children: list[PipelineNode], input: PortValues, ctx: CompositeContext[MapConfig]
) -> Emit:
    scope = {**ctx.scope.snapshot(), "input": input.get("in")}
    values = parse_expr(ctx.config.over).eval(scope)
    if not isinstance(values, list):
        return Emit("fail", {"error": f"`map.over` must be a list, got {type(values).__name__}"})
    results: list[Any] = []
    for index, value in enumerate(values, start=1):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        if ctx.signal.aborted:
            return Emit("fail", {"error": "cancelled"})
        ctx.scope.set("iter", {"index": index, "value": value, "previous": ""})
        emit = await run_chain(children, input, ctx)
        if emit.port != "ok":
            return emit
        results.append(emit.value)
    return Emit("ok", results)


#: Runs the children once per element of `over`, one after another, and returns the list of results.
#: The first element that doesn't succeed stops the map and its outcome is returned.
map_strategy: Strategy[Any] = define_strategy(
    Strategy(id="map", config=MapConfig, validate=_validate, run=_run)
)
