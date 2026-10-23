from __future__ import annotations

import asyncio
from typing import Any

from whiletrue.core import (
    CompositeContext,
    Emit,
    EmptyConfig,
    PipelineNode,
    Strategy,
    define_strategy,
    resolve_outcome,
)
from whiletrue.core.module import PortValues


async def _run(
    children: list[PipelineNode], input: PortValues, ctx: CompositeContext[EmptyConfig]
) -> Emit:
    emits = await asyncio.gather(*(ctx.run(child, input) for child in children))
    for emit in emits:
        if resolve_outcome(emit.port, {}, ctx.policy).action != "continue":
            return emit
    return Emit("ok", [emit.value for emit in emits])


#: Runs every child at the same time on the same input and returns the list of their results in
#: order. If any child doesn't succeed, the first such outcome (in child order) is returned.
parallel_strategy: Strategy[Any] = define_strategy(
    Strategy(id="parallel", config=EmptyConfig, run=_run)
)
