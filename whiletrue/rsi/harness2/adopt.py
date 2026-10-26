"""Adopting (or discarding) the harness a recursion chose.

`rsi.review` asks a person when the harness changed; `rsi.adopt` writes the harness into the
item's workspace (so it rides along in the item's commit and pull request) and stores the lesson in
the experience library; `rsi.discard` leaves the harness as it was. Whatever the policy, a change
to a path that executes code is never adopted without a person's approval.
"""

from __future__ import annotations

from pathlib import Path

from whiletrue.core import (
    ActionModule,
    CompositeContext,
    Emit,
    EmptyConfig,
    PipelineNode,
    Ports,
    PortSpec,
    StepContext,
    Strategy,
    define_action,
    define_strategy,
)
from whiletrue.core.module import PortValues
from whiletrue.rsi.harness2 import proposal as proposals
from whiletrue.rsi.harness2 import tree as harness_tree
from whiletrue.rsi.harness2.library import Library
from whiletrue.rsi.harness2.surfaces import SURFACES

_PASS = Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Any")])


def _project(ctx: StepContext[EmptyConfig] | CompositeContext[EmptyConfig]) -> Path:
    return Path(str(ctx.scope.get("project_dir") or ctx.cwd))


async def _review(
    children: list[PipelineNode], input: PortValues, ctx: CompositeContext[EmptyConfig]
) -> Emit:
    """Ask a person to approve the proposed harness, but only when there is something to approve."""
    proposal = proposals.load(_project(ctx), ctx.item_id)
    if proposal is None or proposal.status != "pending" or not proposal.changed:
        return Emit("ok", input.get("in"))
    return await ctx.run(children[0], input)


async def _adopt(input: PortValues, ctx: StepContext[EmptyConfig]) -> Emit:
    project = _project(ctx)
    proposal = proposals.load(project, ctx.item_id)
    if proposal is None or proposal.status != "pending":
        return Emit("ok", input.get("in"))
    if proposal.adopt == "auto" and proposal.restricted:
        return Emit(
            "needs_human",
            {
                "reason": "rsi_restricted",
                "detail": "the harness changes paths that execute code; approve it with "
                f"`whiletrue rsi adopt {ctx.item_id}`",
                "paths": proposal.restricted,
            },
        )
    if proposal.adopt == "model":
        if proposal.restricted and proposal.model_decision == "approve":
            return Emit(
                "needs_human",
                {
                    "reason": "rsi_restricted",
                    "detail": "a model approved a harness that changes paths that execute code; "
                    f"a person must adopt it with `whiletrue rsi adopt {ctx.item_id} --yes`",
                    "paths": proposal.restricted,
                },
            )
        if proposal.model_decision != "approve":
            proposals.set_status(project, proposal, "recorded")
            return Emit("ok", input.get("in"))
    surface = SURFACES[proposal.surface]
    if proposal.changed:
        harness_tree.write_tree(surface, proposal.chosen, Path(ctx.cwd), stubs=False)
    lesson = proposal.lesson()
    if lesson.complete and proposal.library:
        library = Library(Path(proposal.library), proposal.domain, surface)
        async with ctx.locks.hold(f"rsi:{proposal.domain}"):
            library.add(
                ctx.item_id,
                proposal.chosen,
                lesson,
                why=proposal.why,
                label=proposal.chosen_label,
                run_id=proposal.run_id,
            )
    proposals.set_status(project, proposal, "adopted")
    return Emit("ok", input.get("in"))


async def _discard(input: PortValues, ctx: StepContext[EmptyConfig]) -> Emit:
    project = _project(ctx)
    proposal = proposals.load(project, ctx.item_id)
    if proposal is not None and proposal.status == "pending":
        proposals.set_status(project, proposal, "discarded")
    return Emit("ok", input.get("in"))


rsi_review_strategy: Strategy[EmptyConfig] = define_strategy(
    Strategy(id="rsi.review", config=EmptyConfig, run=_review)
)
rsi_adopt_module: ActionModule[EmptyConfig] = define_action(
    ActionModule(
        id="rsi.adopt",
        config=EmptyConfig,
        ports=_PASS,
        effects="deterministic",
        cacheable=False,
        run=_adopt,
    )
)
rsi_discard_module: ActionModule[EmptyConfig] = define_action(
    ActionModule(
        id="rsi.discard",
        config=EmptyConfig,
        ports=_PASS,
        effects="deterministic",
        cacheable=False,
        run=_discard,
    )
)
