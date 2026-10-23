from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from whiletrue.core import ActionModule, Emit, Ports, PortSpec, StepContext, define_action
from whiletrue.core.module import PortValues


class SetConfig(BaseModel):
    #: Any JSON value; `{{...}}` templates in strings are rendered before the step runs.
    value: Any = None


async def _run(_input: PortValues, ctx: StepContext[SetConfig]) -> Emit:
    return Emit("ok", ctx.config.value)


#: Emits a constant (after templating). Useful for markers in branches and for tests.
set_module: ActionModule[SetConfig] = define_action(
    ActionModule(
        id="set",
        config=SetConfig,
        ports=Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Any")]),
        effects="pure",
        run=_run,
    )
)
