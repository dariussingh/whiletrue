from __future__ import annotations

import asyncio
from typing import Any, cast

from pydantic import BaseModel, Field

from whiletrue.core import (
    CommentsContext,
    CompositeContext,
    ConfigIssue,
    Emit,
    ParkRecord,
    PipelineNode,
    Strategy,
    ValidateContext,
    WakeContext,
    WakeResult,
    WakeSpec,
    define_strategy,
    evaluate_wake,
    parse_duration,
    parse_wake,
    sync_comments,
    wake_to_dict,
)
from whiletrue.core.module import PortValues


class ApproveConfig(BaseModel):
    #: What is being decided; shown in `whiletrue status` and the control panel.
    what: str = "approval"
    #: Shown to the person deciding (templates see `input`).
    show: str | None = None
    #: What ends the wait besides `whiletrue approve|reject`: see `parse_wake`.
    wake: Any = Field(default="cli")
    #: How often a watching gate re-checks (seconds, or a duration like `5s`).
    poll: str | float = "2s"


def _spec(config: ApproveConfig) -> WakeSpec:
    return parse_wake(config.wake)


def _validate(config: ApproveConfig, vctx: ValidateContext) -> list[ConfigIssue]:
    try:
        spec = _spec(config)
    except ValueError as error:
        return [ConfigIssue("error", str(error))]
    issues = [
        ConfigIssue("error", f'Unknown wake condition "{c.name}"')
        for c in spec.conditions
        if vctx.registry.get_provider("wake", c.name) is None
    ]
    try:
        parse_duration(config.poll)
    except ValueError as error:
        issues.append(ConfigIssue("error", f"`poll`: {error}"))
    return issues


async def _sleep(seconds: float, ctx: CompositeContext[ApproveConfig]) -> None:
    waiter = asyncio.ensure_future(ctx.signal.wait())
    try:
        await asyncio.wait({waiter}, timeout=seconds)
    finally:
        waiter.cancel()


def _park_for(ctx: CompositeContext[ApproveConfig], spec: WakeSpec) -> ParkRecord:
    """The Nth run of this gate within a lineage is the Nth park: replays find their old park."""
    assert ctx.store is not None
    parks = ctx.store.list_parks(ctx.item_id, node_path=ctx.node_id, run_id=ctx.lineage)
    if ctx.attempt <= len(parks):
        return parks[ctx.attempt - 1]
    park = ctx.store.create_park(
        run_id=ctx.lineage,
        item_id=ctx.item_id,
        node_path=ctx.node_id,
        what=ctx.config.what,
        wake=wake_to_dict(spec),
        reason=f"waiting for {ctx.config.what}",
        created_at=ctx.now(),
    )
    ctx.bus.emit(
        "park.created",
        ctx.run_id,
        {"park": park.id, "node": ctx.node_id, "what": park.what},
        item_id=ctx.item_id,
    )
    return park


def _item(ctx: CompositeContext[ApproveConfig]) -> dict[str, Any]:
    item: dict[str, Any] | None = ctx.scope.get("item")
    return item or {"id": ctx.item_id}


async def _run(
    children: list[PipelineNode], input: PortValues, ctx: CompositeContext[ApproveConfig]
) -> Emit:
    if ctx.store is None:
        return Emit("fail", {"error": "human.approve needs a state store"})
    store = ctx.store
    spec = _spec(ctx.config)
    park = _park_for(ctx, spec)
    value = input.get("in")
    item = _item(ctx)
    shown = ctx.config.show  # templates in config were rendered before the gate ran
    poll = parse_duration(ctx.config.poll) / 1000
    answered_attempts: set[int] = set()

    while True:
        park = next(p for p in store.list_parks(ctx.item_id) if p.id == park.id)
        if park.decision is None:
            woke = await evaluate_wake(
                spec,
                WakeContext(ctx.cwd, ctx.now, ctx.item_id, park, ctx.registry, item, value),
            )
            if woke is not None:
                _record(ctx, park, woke)
                continue
        else:
            return _outcome(park, value)

        pending = await _unanswered(ctx, item, value)
        fresh = [c for c in pending if c.id not in answered_attempts]
        if fresh and children:
            answered_attempts.update(c.id for c in fresh)
            # New comments always get a live response, never a recorded one from an earlier cycle.
            ctx.scope.set("no_replay", True)
            cur = Emit("ok", {"comments": [_comment_dict(c) for c in fresh], "input": value})
            for child in children:
                cur = await ctx.run(child, {"in": cur.value})
                if cur.port != "ok":
                    break
            ctx.log("info", f"answered {len(fresh)} comment(s) on {ctx.item_id}", None)
            continue

        if not ctx.watch:
            return Emit(
                "needs_human",
                {"park": park.id, "what": park.what, "show": shown, "waiting_for": "approval"},
            )
        if ctx.signal.aborted:
            return Emit("fail", {"error": "cancelled"})
        await _sleep(poll, ctx)


def _comment_dict(comment: Any) -> dict[str, Any]:
    return {
        "id": comment.id,
        "author": comment.author,
        "body": comment.body,
        "thread": comment.thread,
        "url": comment.url,
        "source": comment.source,
        "kind": comment.kind,
    }


async def _unanswered(ctx: CompositeContext[ApproveConfig], item: dict[str, Any], value: Any):
    assert ctx.store is not None
    await sync_comments(
        ctx.registry, ctx.store, CommentsContext(ctx.cwd, ctx.now, ctx.item_id, item, value)
    )
    return ctx.store.list_comments(ctx.item_id, unanswered_only=True)


def _record(ctx: CompositeContext[ApproveConfig], park: ParkRecord, woke: WakeResult) -> None:
    assert ctx.store is not None
    ctx.store.decide_park(
        park.id, woke.decision, decided_by=woke.by, decided_at=ctx.now(), notes=woke.note
    )
    ctx.bus.emit(
        "park.decided",
        ctx.run_id,
        {"park": park.id, "decision": woke.decision, "by": woke.by, "note": woke.note},
        item_id=ctx.item_id,
    )


def _outcome(park: ParkRecord, value: Any) -> Emit:
    """Approval passes the input on. A rejection passes it on too, with the reviewer's note under
    `rejection`, so whatever flows through the gate (the PR, say) survives a fix-and-review loop."""
    carried: dict[str, Any] = (
        {k: v for k, v in cast(dict[str, Any], value).items() if k != "rejection"}
        if isinstance(value, dict)
        else {"value": value}
    )
    if park.decision == "rejected":
        carried["rejection"] = {"note": park.notes, "decided_by": park.decided_by}
        return Emit("rejected", carried)
    return Emit("ok", carried if isinstance(value, dict) else value)


#: Parks the item until a person approves or rejects (or a wake condition fires).
#: `do:` children run whenever new comments arrive, then the gate waits again.
approve_strategy: Strategy[ApproveConfig] = define_strategy(
    Strategy(id="human.approve", config=ApproveConfig, run=_run, validate=_validate)
)
