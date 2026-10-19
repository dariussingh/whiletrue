from __future__ import annotations

import json
import math
from typing import Any, Literal

from pydantic import BaseModel, PositiveInt

from whiletrue.control.chain import run_chain
from whiletrue.core import (
    CompositeContext,
    ConfigIssue,
    Emit,
    PipelineNode,
    Strategy,
    ValidateContext,
    define_strategy,
)
from whiletrue.core.module import PortValues


class LoopConfig(BaseModel):
    max: PositiveInt | Literal["unbounded"] | None = None
    #: `ok`: stop as soon as an iteration succeeds (Ralph). `never`: always run all iterations.
    stop_when: Literal["ok", "never"] = "ok"


def _describe(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        parts = [v for k in ("error", "output") if isinstance(v := value.get(k), str)]  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
        if parts:
            return "\n".join(parts)
    if value is None:
        return ""
    return json.dumps(value, separators=(",", ":"), default=str)


def _validate(config: LoopConfig, _vctx: ValidateContext) -> list[ConfigIssue]:
    if config.max is None:
        return [
            ConfigIssue(
                "error",
                "`loop` requires `max` (or an explicit `max: unbounded`): "
                "loops are bounded by default",
            )
        ]
    return []


async def _run(
    children: list[PipelineNode], input: PortValues, ctx: CompositeContext[LoopConfig]
) -> Emit:
    configured = ctx.config.max
    limit = math.inf if configured is None or configured == "unbounded" else configured
    last = Emit("ok")
    index = 0
    while index < limit:
        index += 1
        if ctx.signal.aborted:
            return Emit("fail", {"error": "cancelled"})
        ctx.scope.set(
            "loop",
            {"index": index, "previous": _describe("" if last.port == "ok" else last.value)},
        )
        ctx.log("info", f"loop iteration {index}{'' if limit == math.inf else f'/{limit}'}", None)
        last = await run_chain(children, input, ctx)
        if last.port in ("needs_human", "rejected"):
            return last
        if last.port == "ok" and ctx.config.stop_when == "ok":
            return last
    if ctx.config.stop_when == "never":
        return last
    return Emit("fail", {"error": f"loop gave up after {limit} iterations", "last": last.value})


#: Repeats its children, bounded by `max` (or an explicit `max: unbounded`). Each iteration sees
#: `{{loop.index}}` (1-based) and `{{loop.previous}}`, the previous failed iteration's output.
#: M0 semantics; `until` expressions, `over` and `collect` arrive with the expression language.
loop_strategy: Strategy[Any] = define_strategy(
    Strategy(id="loop", config=LoopConfig, validate=_validate, run=_run)
)
