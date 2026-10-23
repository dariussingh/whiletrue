from __future__ import annotations

import asyncio
from typing import Any, cast

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
    #: How many elements run at once (each pass gets its own `iter`). Results keep element order.
    parallel: int = Field(default=1, ge=1)


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
    elements = cast(list[Any], values)
    if ctx.config.parallel > 1:
        return await _run_parallel(children, input, ctx, elements)
    results: list[Any] = []
    for index, value in enumerate(elements, start=1):
        if ctx.signal.aborted:
            return Emit("fail", {"error": "cancelled"})
        ctx.scope.set("iter", {"index": index, "value": value, "previous": ""})
        emit = await run_chain(children, input, ctx)
        if emit.port != "ok":
            return emit
        results.append(emit.value)
    return Emit("ok", results)


async def _run_parallel(
    children: list[PipelineNode],
    input: PortValues,
    ctx: CompositeContext[MapConfig],
    elements: list[Any],
) -> Emit:
    slots = asyncio.Semaphore(ctx.config.parallel)
    stop = asyncio.Event()

    async def one(index: int, value: Any) -> Emit | None:
        async with slots:
            if stop.is_set() or ctx.signal.aborted:
                return None
            scope = ctx.scope.child()
            scope.set("iter", {"index": index, "value": value, "previous": ""})
            emit = await run_chain(children, input, ctx, scope)
            if emit.port != "ok":
                stop.set()
            return emit

    emits = await asyncio.gather(*(one(i, v) for i, v in enumerate(elements, start=1)))
    for emit in emits:
        if emit is not None and emit.port != "ok":
            return emit
    if any(e is None for e in emits):
        return Emit("fail", {"error": "cancelled"})
    return Emit("ok", [e.value for e in emits if e is not None])


#: Runs the children once per element of `over` (one after another, or `parallel` at once) and
#: returns the list of results.
#: The first element that doesn't succeed stops the map and its outcome is returned.
map_strategy: Strategy[Any] = define_strategy(
    Strategy(id="map", config=MapConfig, validate=_validate, run=_run)
)
