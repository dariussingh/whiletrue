from __future__ import annotations

from typing import Any

from whiletrue.control.chain import run_chain
from whiletrue.core import (
    CompositeContext,
    Emit,
    EmptyConfig,
    PipelineNode,
    Strategy,
    define_strategy,
)
from whiletrue.core.module import PortValues


async def _run(
    children: list[PipelineNode], input: PortValues, ctx: CompositeContext[EmptyConfig]
) -> Emit:
    return await run_chain(children, input, ctx)


#: Runs children one after another; each `out` feeds the next `in`.
sequence_strategy: Strategy[Any] = define_strategy(
    Strategy(id="sequence", chain=True, config=EmptyConfig, run=_run)
)
