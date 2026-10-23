from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from whiletrue.abort import AbortSignal
from whiletrue.core.caps import CapValidation


@dataclass(frozen=True)
class RunnerCapabilities:
    """What a runner can do; `validate` rejects steps that ask for more."""

    interactive: bool
    structured_output: bool
    resume: bool
    fork: bool
    budget: bool


@dataclass(frozen=True)
class AgentLimits:
    #: Max spend in USD (hard where the runner supports it, an estimate otherwise).
    budget_usd: float | None = None
    turns: int | None = None
    #: Wall-clock limit in milliseconds.
    wall_ms: int | None = None


@dataclass
class AgentRequest:
    prompt: str
    cwd: str
    model: str | None = None
    tools: list[str] | None = None
    allowed_tools: list[str] | None = None
    disallowed_tools: list[str] | None = None
    permission_mode: str | None = None
    system_prompt_append: str | None = None
    #: JSON Schema the final structured output must satisfy.
    output_schema: dict[str, Any] | None = None
    limits: AgentLimits | None = None
    #: New session id to use (fresh sessions).
    session_id: str | None = None
    #: Existing session to resume.
    resume: str | None = None
    #: With `resume`: branch into a new session instead of continuing the old one.
    fork: bool = False
    add_dirs: list[str] | None = None
    append_system_prompt_file: str | None = None
    mcp_config: list[str] | None = None
    #: Reproducible mode: skip hooks, plugins, memory and auto-discovery (`claude --bare`).
    bare: bool = False
    #: Interactive runners: what happens to the pane when the step ends.
    on_done: Literal["close", "keep"] = "close"
    #: Identity of the step execution, for tying a live session back to it.
    step_key: str | None = None
    #: A short title for the pane, e.g. `#7 implement`.
    label: str | None = None
    #: The item's payload (what `whiletrue_context` returns to the agent).
    item: dict[str, Any] | None = None
    #: Name of the output cap and a validator for it (interactive runners check `whiletrue_submit`).
    output_cap: str | None = None
    validate_output: Callable[[Any], CapValidation] | None = None


@dataclass
class AgentResult:
    ok: bool
    #: Final text answer.
    text: str
    #: Structured output when the request carried an output schema.
    output: Any = None
    session_id: str | None = None
    cost_usd: float | None = None
    turns: int | None = None
    error: str | None = None
    #: Why the step needs a person instead of failing: `needs_human`, `budget_exceeded`, ...
    park: str | None = None
    #: A person typed into the session, which lifts the step's limits.
    steered: bool = False
    #: The part of `cost_usd` spent after the steer.
    steered_cost_usd: float | None = None


@dataclass
class RunContext:
    signal: AbortSignal
    #: Raw runner events (tool calls, messages) for the event log.
    on_event: Callable[[dict[str, Any]], None] | None = None


class Runner(Protocol):
    id: str
    capabilities: RunnerCapabilities

    async def run(self, request: AgentRequest, ctx: RunContext) -> AgentResult: ...
