from __future__ import annotations

from typing import Any

from whiletrue.core import CompositeContext, Emit, PipelineNode, resolve_outcome
from whiletrue.core.module import PortValues


async def run_chain(
    children: list[PipelineNode], input: PortValues, ctx: CompositeContext[Any]
) -> Emit:
    """Runs children in order, feeding each child's output into the next child's input.

    Stops at the first outcome that doesn't continue (fail, needs_human, rejected, ...)
    and returns it unchanged for the parent to handle.
    """
    current = Emit("ok", input.get("in"))
    for child in children:
        if ctx.signal.aborted:
            return Emit("fail", {"error": "cancelled"})
        emit = await ctx.run(child, {} if current.value is None else {"in": current.value})
        if resolve_outcome(emit.port, child.wires, ctx.policy).action != "continue":
            return emit
        # `skip` passes the previous value through untouched.
        if emit.port != "skip":
            current = emit
    return current
