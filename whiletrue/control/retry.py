from __future__ import annotations

import asyncio
from typing import Any, Literal

from pydantic import BaseModel, PositiveFloat, PositiveInt

from whiletrue.control.chain import run_chain
from whiletrue.control.loop import describe
from whiletrue.core import (
    CompositeContext,
    ConfigIssue,
    Emit,
    PipelineNode,
    Strategy,
    ValidateContext,
    define_strategy,
    parse_duration,
)
from whiletrue.core.module import PortValues


class RetryConfig(BaseModel):
    max: PositiveInt = 3
    #: Delay before the second attempt, e.g. "2s". Grows by `factor` each further attempt.
    backoff: str | int | float | None = None
    factor: PositiveFloat = 1.0
    #: Expose the previous failure as `{{iter.previous}}` to the next attempt.
    feedback: bool = True
    #: What happens when every attempt failed. Default: park the item for a human.
    on_exhausted: Literal["needs_human", "fail"] = "needs_human"


def _validate(config: RetryConfig, _vctx: ValidateContext) -> list[ConfigIssue]:
    if config.backoff is None:
        return []
    try:
        parse_duration(config.backoff)
    except ValueError as error:
        return [ConfigIssue("error", f"`retry.backoff`: {error}")]
    return []


async def _sleep(ctx: CompositeContext[Any], ms: float) -> None:
    if ms <= 0:
        return
    waiter = asyncio.ensure_future(ctx.signal.wait())
    try:
        await asyncio.wait({waiter}, timeout=ms / 1000)
    finally:
        waiter.cancel()


async def _run(
    children: list[PipelineNode], input: PortValues, ctx: CompositeContext[RetryConfig]
) -> Emit:
    cfg = ctx.config
    delay = parse_duration(cfg.backoff) if cfg.backoff is not None else 0
    last = Emit("ok")
    for attempt in range(1, cfg.max + 1):
        if ctx.signal.aborted:
            return Emit("fail", {"error": "cancelled"})
        previous = describe(last.value) if cfg.feedback and attempt > 1 else ""
        ctx.scope.set("iter", {"index": attempt, "previous": previous, "value": None})
        last = await run_chain(children, input, ctx)
        if last.port != "fail":
            return last
        ctx.log("warn", f"retry attempt {attempt}/{cfg.max} failed", None)
        if attempt < cfg.max:
            await _sleep(ctx, delay * cfg.factor ** (attempt - 1))
    error = {"error": f"retry gave up after {cfg.max} attempts", "last": last.value}
    return Emit(cfg.on_exhausted, error)


#: Re-runs its children when they `fail`, up to `max` attempts, with optional backoff. Other
#: outcomes pass straight through. When attempts run out, the item is parked as `needs_human`
#: (or `fail` with `on_exhausted: fail`).
retry_strategy: Strategy[Any] = define_strategy(
    Strategy(id="retry", config=RetryConfig, validate=_validate, run=_run)
)


class TryConfig(BaseModel):
    #: Outcome to fire instead of `fail`: `skip` carries on with the previous value, `ok` yields
    #: the error object as the value.
    emit: Literal["skip", "ok"] = "skip"


async def _try(
    children: list[PipelineNode], input: PortValues, ctx: CompositeContext[TryConfig]
) -> Emit:
    result = await run_chain(children, input, ctx)
    if result.port != "fail" or ctx.signal.aborted:
        return result
    ctx.log("warn", "try swallowed a failure", {"error": describe(result.value)})
    return Emit("ok", result.value) if ctx.config.emit == "ok" else Emit("skip")


#: Runs its children and turns a `fail` into `skip` (or `ok`) so optional steps never stop the item.
try_strategy: Strategy[Any] = define_strategy(Strategy(id="try", config=TryConfig, run=_try))
