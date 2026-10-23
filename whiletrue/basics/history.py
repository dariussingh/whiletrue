from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from whiletrue.core import (
    ActionModule,
    Emit,
    Ports,
    PortSpec,
    StateStore,
    StepContext,
    define_action,
)
from whiletrue.core.module import PortValues


class HistoryConfig(BaseModel):
    #: `item`: this item's parks, decisions, comments and steers. `project`: learnings a person
    #: approved. `all`: both.
    scope: Literal["item", "project", "all"] = "item"


def render_history(store: StateStore, item_id: str, scope: str) -> str:
    """The human input an agent should know about, as plain text."""
    sections: list[str] = []
    if scope in ("item", "all"):
        lines: list[str] = []
        for park in store.list_parks(item_id):
            if park.decision is not None:
                note = f": {park.notes}" if park.notes else ""
                lines.append(f"- At {park.node_path} ({park.what}) a person {park.decision}{note}")
        for comment in store.list_comments(item_id):
            lines.append(f"- {comment.author} commented: {comment.body}")
            if comment.reply:
                lines.append(f"  answered: {comment.reply}")
        for steer in store.list_steers(item_id):
            lines.append(f"- A person steered {steer.step}: {steer.prompt}")
        if lines:
            sections.append("Human input on this item so far:\n" + "\n".join(lines))
    if scope in ("project", "all"):
        learned = store.list_learnings(status="approved")
        if learned:
            sections.append(
                "Lessons this project has approved:\n" + "\n".join(f"- {n.note}" for n in learned)
            )
    return "\n\n".join(sections)


async def _context(_input: PortValues, ctx: StepContext[HistoryConfig]) -> Emit:
    if ctx.store is None:
        return Emit("fail", {"error": "context.history needs a state store"})
    return Emit("ok", render_history(ctx.store, ctx.item_id, ctx.config.scope))


#: Turns the item's human input (and approved project lessons) into text for the next agent.
context_history_module: ActionModule[HistoryConfig] = define_action(
    ActionModule(
        id="context.history",
        config=HistoryConfig,
        ports=Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Text")]),
        effects="deterministic",
        cacheable=False,
        run=_context,
    )
)


class DistillConfig(BaseModel):
    #: Where proposals land until a person approves them.
    scope: Literal["project", "run"] = "project"


async def _distill(input: PortValues, ctx: StepContext[DistillConfig]) -> Emit:
    """Proposes lessons from rejections and steers. Nothing is used until a person approves it."""
    store = ctx.store
    if store is None:
        return Emit("fail", {"error": "history.distill needs a state store"})
    known = {n.note for n in store.list_learnings(scope=ctx.config.scope)}
    proposed: list[int] = []

    def propose(note: str, **provenance: object) -> None:
        if note in known:
            return
        known.add(note)
        record = store.add_learning(
            scope=ctx.config.scope,
            note=note,
            status="proposed",
            created_at=ctx.now(),
            run_id=ctx.run_id,
            provenance={"item": ctx.item_id, **provenance},
        )
        proposed.append(record.id)

    for park in store.list_parks(ctx.item_id):
        if park.decision == "rejected" and park.notes:
            propose(
                f"A reviewer rejected work with: {park.notes}", source="rejection", park=park.id
            )
    for steer in store.list_steers(ctx.item_id):
        propose(f"A person steered the agent: {steer.prompt}", source="steer", steer=steer.id)
    return Emit("ok", input.get("in") if input.get("in") is not None else {"proposed": proposed})


#: Proposes learnings (with provenance) from rejections and steers; a person approves them.
history_distill_module: ActionModule[DistillConfig] = define_action(
    ActionModule(
        id="history.distill",
        config=DistillConfig,
        ports=Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Any")]),
        effects="deterministic",
        cacheable=False,
        run=_distill,
    )
)
