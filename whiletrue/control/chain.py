from __future__ import annotations

from typing import Any

from whiletrue.core import CompositeContext, Emit, PipelineNode, Scope, resolve_outcome
from whiletrue.core.module import PortValues

#: Wires can form cycles (a fixer wired back to its checker); this bounds them.
MAX_ROUTES = 1000


async def run_chain(
    children: list[PipelineNode],
    input: PortValues,
    ctx: CompositeContext[Any],
    scope: Scope | None = None,
) -> Emit:
    """Runs children in order, feeding each child's output into the next child's input.

    A port wired to a sibling (`wires: {fail: fix}`) routes there instead, carrying the emitted
    value, and the chain continues from that sibling. A sibling wired to from an earlier step is a
    branch: it only runs when routed to. Any other outcome that doesn't continue
    (fail, needs_human, rejected, ...) stops the chain and is returned for the parent to handle.
    """
    index_of = {child.id: i for i, child in enumerate(children)}
    #: Steps an earlier sibling's wire points forward at are branches: reached by a route, never
    #: by falling through. (A wire pointing back at an earlier step is a loop, not a branch.)
    branches = {
        target
        for i, child in enumerate(children)
        for target in child.wires.values()
        if index_of.get(target, -1) > i
    }
    current = Emit("ok", input.get("in"))
    index = 0
    routes = 0
    while index < len(children):
        if ctx.signal.aborted:
            return Emit("fail", {"error": "cancelled"})
        child = children[index]
        emit = await ctx.run(child, {} if current.value is None else {"in": current.value}, scope)
        resolution = resolve_outcome(emit.port, child.wires, ctx.policy)
        if resolution.action == "route":
            routes += 1
            if routes > MAX_ROUTES:
                return Emit("fail", {"error": f"wires routed more than {MAX_ROUTES} times"})
            target = index_of.get(resolution.target or "")
            if target is None:
                return Emit("fail", {"error": f'Wire targets unknown step "{resolution.target}"'})
            current = emit
            index = target
            continue
        if resolution.action != "continue":
            return emit
        # `skip` passes the previous value through untouched.
        if emit.port != "skip":
            current = emit
        index += 1
        while index < len(children) and children[index].id in branches:
            index += 1
    return current
