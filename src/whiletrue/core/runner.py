from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from whiletrue.abort import AbortSignal


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


@dataclass
class RunContext:
    signal: AbortSignal
    #: Raw runner events (tool calls, messages) for the event log.
    on_event: Callable[[dict[str, Any]], None] | None = None


class Runner(Protocol):
    id: str
    capabilities: RunnerCapabilities

    async def run(self, request: AgentRequest, ctx: RunContext) -> AgentResult: ...
