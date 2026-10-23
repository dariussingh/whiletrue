from __future__ import annotations

from pydantic import BaseModel

from whiletrue.core import (
    ActionModule,
    Emit,
    EmptyConfig,
    Ports,
    PortSpec,
    Scope,
    StepContext,
    define_action,
)
from whiletrue.core.module import PortValues


class SessionEndConfig(BaseModel):
    #: The shared session to close (`item` is the one `session: item` uses).
    name: str = "item"


def _sessions(scope: Scope) -> dict[str, str]:
    sessions: dict[str, str] | None = scope.get("sessions")
    return sessions if sessions is not None else {}


async def _end(input: PortValues, ctx: StepContext[SessionEndConfig]) -> Emit:
    _sessions(ctx.scope).pop(ctx.config.name, None)
    return Emit("ok", input.get("in"))


async def _reset(input: PortValues, ctx: StepContext[EmptyConfig]) -> Emit:
    _sessions(ctx.scope).clear()
    return Emit("ok", input.get("in"))


_PORTS = Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Any")])

#: Closes a shared session: the next agent that asks for it starts a new conversation.
session_end_module: ActionModule[SessionEndConfig] = define_action(
    ActionModule(
        id="session.end",
        config=SessionEndConfig,
        ports=_PORTS,
        effects="deterministic",
        cacheable=False,
        run=_end,
    )
)

#: Drops every shared session of the item, so later agents start with a clean context.
context_reset_module: ActionModule[EmptyConfig] = define_action(
    ActionModule(
        id="context.reset",
        config=EmptyConfig,
        ports=_PORTS,
        effects="deterministic",
        cacheable=False,
        run=_reset,
    )
)
