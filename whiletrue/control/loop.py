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
    parse_expr,
)
from whiletrue.core.expr import ExprError
from whiletrue.core.module import PortValues

#: Variables a loop expression may use.
LOOP_VARIABLES = frozenset({"run", "item", "iter", "last", "input"})


class LoopConfig(BaseModel):
    max: PositiveInt | Literal["unbounded"] | None = None
    #: CEL, evaluated after each iteration; the loop ends successfully when it is true.
    until: str | None = None
    #: CEL list to iterate; each iteration sees `iter.value`. Bounded by the list length.
    over: str | None = None
    #: Return the list of every successful iteration's output instead of just the last.
    collect: bool = False
    #: `ok`: stop as soon as an iteration succeeds (Ralph). `never`: always run all iterations.
    #: Ignored when `until` or `over` is set.
    stop_when: Literal["ok", "never"] = "ok"


def describe(value: Any) -> str:
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
    issues: list[ConfigIssue] = []
    if config.max is None and config.over is None:
        issues.append(
            ConfigIssue(
                "error",
                "`loop` requires `max` (or an explicit `max: unbounded`) or an `over` list: "
                "loops are bounded by default",
            )
        )
    for name, source in (("until", config.until), ("over", config.over)):
        if source is None:
            continue
        try:
            problems = parse_expr(source).check(LOOP_VARIABLES)
        except ExprError as error:
            problems = [str(error)]
        issues.extend(ConfigIssue("error", f"`loop.{name}`: {p}") for p in problems)
    return issues


def _context(ctx: CompositeContext[Any], input: PortValues, last: Emit | None) -> dict[str, Any]:
    values = {**ctx.scope.snapshot(), "input": input.get("in")}
    values["last"] = (
        {"port": last.port, "ok": last.port == "ok", "value": last.value} if last else None
    )
    return values


async def _run(
    children: list[PipelineNode], input: PortValues, ctx: CompositeContext[LoopConfig]
) -> Emit:
    cfg = ctx.config
    until = parse_expr(cfg.until) if cfg.until else None
    items: list[Any] | None = None
    if cfg.over is not None:
        value = parse_expr(cfg.over).eval(_context(ctx, input, None))
        if not isinstance(value, list):
            return Emit(
                "fail", {"error": f"`loop.over` must be a list, got {type(value).__name__}"}
            )
        items = list(value)  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]
    limit = math.inf if cfg.max is None or cfg.max == "unbounded" else cfg.max
    if items is not None:
        limit = min(limit, len(items))

    last = Emit("ok")
    collected: list[Any] = []
    index = 0
    while index < limit:
        index += 1
        if ctx.signal.aborted:
            return Emit("fail", {"error": "cancelled"})
        ctx.scope.set(
            "iter",
            {
                "index": index,
                "previous": describe("" if last.port == "ok" else last.value),
                "value": items[index - 1] if items is not None else None,
            },
        )
        ctx.log("info", f"loop iteration {index}{'' if limit == math.inf else f'/{limit}'}", None)
        last = await run_chain(children, input, ctx)
        if last.port in ("needs_human", "rejected"):
            return last
        if last.port == "ok" and cfg.collect:
            collected.append(last.value)
        result = Emit("ok", collected if cfg.collect else last.value)
        if until is not None:
            if until.eval_bool(_context(ctx, input, last)):
                return result
        elif items is not None:
            if last.port != "ok":
                return last
        elif cfg.stop_when == "ok" and last.port == "ok":
            return result
    if until is None and (items is not None or cfg.stop_when == "never"):
        return Emit("ok", collected if cfg.collect else last.value)
    return Emit("fail", {"error": f"loop gave up after {limit} iterations", "last": last.value})


#: Repeats its children, bounded by `max` (or an explicit `max: unbounded`) or by an `over` list.
#: Each iteration sees `{{iter.index}}` (1-based), `{{iter.previous}}` (the previous failed
#: iteration's output) and `{{iter.value}}` (the current `over` element). `until` is a CEL
#: condition over `last` (`last.ok`, `last.value`), `iter`, `input`, `item`, `run`.
loop_strategy: Strategy[Any] = define_strategy(
    Strategy(id="loop", config=LoopConfig, validate=_validate, run=_run)
)
