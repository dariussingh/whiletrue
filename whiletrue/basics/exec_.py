from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from whiletrue.basics.process import run_command, tail
from whiletrue.core import (
    ActionModule,
    Emit,
    Ports,
    PortSpec,
    StepContext,
    define_action,
    parse_duration,
)
from whiletrue.core.module import PortValues


class ExecConfig(BaseModel):
    command: str = Field(min_length=1)
    args: list[str] = Field(default_factory=lambda: [])
    cwd: str | None = None
    env: dict[str, str] | None = None
    timeout: str | int | float | None = None


class _Reply(BaseModel):
    port: str | None = None
    value: Any = None


async def _run(input: PortValues, ctx: StepContext[ExecConfig]) -> Emit:
    cfg = ctx.config
    result = await run_command(
        cfg.command,
        cfg.args,
        cwd=str((Path(ctx.cwd) / (cfg.cwd or ".")).resolve()),
        env=cfg.env,
        signal=ctx.signal,
        timeout_ms=None if cfg.timeout is None else parse_duration(cfg.timeout),
        stdin=json.dumps(
            {
                "input": input.get("in"),
                "config": ctx.config.model_dump(),
                "item": ctx.scope.get("item"),
                "step": {"id": ctx.node_id, "key": ctx.step_key, "attempt": ctx.attempt},
            }
        ),
    )
    if result.code != 0:
        code = "unknown" if result.code is None else result.code
        return Emit(
            "fail",
            {
                "error": f"`{cfg.command}` exited with code {code}",
                "output": tail(result.stdout + result.stderr),
            },
        )
    try:
        parsed = json.loads(result.stdout)
    except ValueError:
        return Emit("fail", {"error": f"`{cfg.command}` did not print valid JSON on stdout"})
    try:
        reply = _Reply.model_validate(parsed)
    except ValidationError:
        return Emit("fail", {"error": f"`{cfg.command}` reply must be {{ port?, value? }}"})
    return Emit(reply.port or "ok", reply.value)


#: Escape hatch: runs any executable that speaks JSON over stdio, so a module can be written in
#: any language. Receives `{ input, config, item, step }` on stdin; replies `{ port?, value? }`.
exec_module = define_action(
    ActionModule(
        id="exec",
        config=ExecConfig,
        ports=Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Any")]),
        effects="side-effect",
        idempotency_key=lambda _input, ctx: f"exec:{ctx.step_key}",
        run=_run,
    )
)
