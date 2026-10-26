"""Result caching and the side-effect journal around an action's run."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from whiletrue.core.context import StepContext
from whiletrue.core.events import EventBus
from whiletrue.core.module import Emit, PortValues


def cache_key(module: Any, parsed: Any, input: PortValues) -> str | None:
    """Cache key for pure/deterministic modules that don't read the workspace; else None."""
    if module.effects not in ("pure", "deterministic") or not module.cacheable:
        return None
    payload = json.dumps(
        [module.id, parsed.model_dump(mode="json"), input], sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode()).hexdigest()


async def run_journaled(
    module: Any, input: PortValues, ctx: StepContext[Any], bus: EventBus, step_id: int
) -> Emit:
    """Runs an action; journaled effects are looked up first and recorded after they succeed."""
    if not (module.journal and module.idempotency_key is not None) or ctx.store is None:
        return await module.run(input, ctx)
    store, run_id, now = ctx.store, ctx.run_id, ctx.now
    key = module.idempotency_key(input, ctx)
    known = store.effect_get(key)
    if known is not None and known.status == "done":
        bus.emit(
            "step.journaled",
            run_id,
            {"node": ctx.node_id, "key": key},
            item_id=ctx.item_id,
            step_id=step_id,
        )
        recorded: dict[str, Any] = known.ref
        return Emit(recorded["port"], recorded["value"])
    store.effect_put(key, run_id=run_id, status="started", ref=None, created_at=now())
    emit = await module.run(input, ctx)
    if emit.port == "ok":
        store.effect_put(
            key,
            run_id=run_id,
            status="done",
            ref={"port": emit.port, "value": emit.value},
            created_at=now(),
        )
    return emit
