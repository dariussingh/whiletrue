from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from whiletrue.basics.process import run_command, tail
from whiletrue.core import (
    ActionModule,
    ConfigIssue,
    Emit,
    Ports,
    PortSpec,
    StepContext,
    define_action,
    parse_duration,
)
from whiletrue.core.module import PortValues


class ShellConfig(BaseModel):
    run: str = Field(min_length=1)
    cwd: str | None = None
    env: dict[str, str] | None = None
    timeout: str | int | float | None = None


class GateTestsConfig(ShellConfig):
    run: str = Field(default="pytest", min_length=1)


def _make_shell[Cfg: ShellConfig](
    module_id: str, effects: Literal["side-effect", "deterministic"], config_cls: type[Cfg]
) -> ActionModule[Cfg]:
    def validate(config: Cfg, _vctx: object) -> list[ConfigIssue]:
        if config.timeout is None:
            return []
        try:
            parse_duration(config.timeout)
        except ValueError as error:
            return [ConfigIssue("error", str(error))]
        return []

    async def run(_input: PortValues, ctx: StepContext[Cfg]) -> Emit:
        cfg = ctx.config
        result = await run_command(
            "sh",
            ["-c", cfg.run],
            cwd=str((Path(ctx.cwd) / (cfg.cwd or ".")).resolve()),
            env=cfg.env,
            signal=ctx.signal,
            timeout_ms=None if cfg.timeout is None else parse_duration(cfg.timeout),
        )
        if result.code == 0:
            return Emit("ok", result.stdout)
        if result.timed_out:
            reason = "timed out"
        elif result.aborted:
            reason = "aborted"
        else:
            reason = f"exited with code {'unknown' if result.code is None else result.code}"
        return Emit(
            "fail",
            {
                "error": f"`{cfg.run}` {reason}",
                "exit_code": result.code,
                "output": tail(result.stdout + result.stderr),
            },
        )

    return define_action(
        ActionModule(
            id=module_id,
            config=config_cls,
            ports=Ports(
                inputs=[PortSpec("in", "Any")],
                outputs=[PortSpec("out", "Text")],
            ),
            effects=effects,
            validate=validate,  # pyright: ignore[reportArgumentType]
            cacheable=False,
            idempotency_key=(lambda _i, ctx: f"shell:{ctx.step_key}")
            if effects == "side-effect"
            else None,
            run=run,
        )
    )


#: Runs a shell command. Fires `ok` on exit 0, otherwise `fail` with the output tail.
shell_module = _make_shell("shell", "side-effect", ShellConfig)
#: A deterministic gate: pass/fail by exit code only.
gate_shell_module = _make_shell("gate.shell", "deterministic", ShellConfig)
#: A deterministic gate for the test suite: `run` defaults to `pytest`.
gate_tests_module = _make_shell("gate.tests", "deterministic", GateTestsConfig)
