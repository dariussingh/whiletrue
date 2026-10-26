from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from whiletrue.abort import AbortSignal
from whiletrue.core import (
    ActionModule,
    AgentDefinition,
    AgentLimits,
    AgentRequest,
    AgentResult,
    ConfigError,
    ConfigIssue,
    Emit,
    Ports,
    PortSpec,
    Registry,
    RunContext,
    Runner,
    StepContext,
    ValidateContext,
    define_action,
    load_agent_definition,
    render_template,
)
from whiletrue.core.module import PortValues


class AgentConfig(BaseModel):
    uses: str = Field(min_length=1)
    runner: str | None = None
    vars: dict[str, Any] = Field(default_factory=lambda: {})


#: Caps that carry no useful JSON Schema constraint for structured output.
_UNSTRUCTURED = {"Text", "Any"}


def _runner_issues(
    definition: AgentDefinition, runner_id: str, registry: Registry
) -> list[ConfigIssue]:
    runner = registry.get_runner(runner_id)
    if runner is None:
        return [
            ConfigIssue(
                "error",
                f'Unknown runner "{runner_id}" (e.g. claude-code-headless, opencode)',
            )
        ]
    issues: list[ConfigIssue] = []
    out_cap = next(iter(definition.outputs.values()), None)
    if out_cap and out_cap not in _UNSTRUCTURED and not runner.capabilities.structured_output:
        issues.append(
            ConfigIssue(
                "error",
                f'Agent "{definition.name}" declares structured output ({out_cap}) '
                f'but runner "{runner_id}" cannot produce it',
            )
        )
    if (
        definition.limits is not None
        and definition.limits.budget_usd is not None
        and not runner.capabilities.budget
    ):
        issues.append(
            ConfigIssue(
                "error",
                f'Agent "{definition.name}" sets a budget but runner "{runner_id}" '
                "cannot enforce one",
            )
        )
    if definition.fork and not runner.capabilities.fork:
        issues.append(
            ConfigIssue(
                "error",
                f'Agent "{definition.name}" asks to fork a session but runner "{runner_id}" '
                "cannot fork",
            )
        )
    if definition.bare and runner.capabilities.interactive:
        issues.append(
            ConfigIssue(
                "warning",
                f'Agent "{definition.name}" sets `bare`, which turns off hooks: runner '
                f'"{runner_id}" then cannot nudge, enforce limits or detect steering',
            )
        )
    if definition.session != "fresh" and not runner.capabilities.resume:
        issues.append(
            ConfigIssue(
                "error",
                f'Agent "{definition.name}" asks for session "{definition.session}" '
                f'but runner "{runner_id}" cannot resume sessions',
            )
        )
    return issues


def _validate(config: AgentConfig, vctx: ValidateContext) -> list[ConfigIssue]:
    try:
        definition = load_agent_definition(Path(vctx.cwd) / config.uses)
    except ConfigError as error:
        return [ConfigIssue("error", str(error))]
    except OSError as error:
        return [ConfigIssue("error", f"Cannot read agent file: {error}")]
    issues: list[ConfigIssue] = []
    if definition.limits is None:
        issues.append(
            ConfigIssue(
                "error",
                f'Agent "{definition.name}" needs `limits` (budget_usd, turns, wall): '
                "agent steps are bounded by default",
            )
        )
    return [*issues, *_runner_issues(definition, config.runner or definition.runner, vctx.registry)]


class _AgentCall:
    """One run of an agent step: its request, events, session bookkeeping and result."""

    def __init__(
        self,
        input: PortValues,
        ctx: StepContext[AgentConfig],
        definition: AgentDefinition,
        runner_id: str,
    ) -> None:
        self.input = input
        self.ctx = ctx
        self.definition = definition
        self.runner_id = runner_id
        self.out_cap = next(iter(definition.outputs.values()), None)
        self.structured = self.out_cap is not None and self.out_cap not in _UNSTRUCTURED
        self.sessions: dict[str, str] | None = ctx.scope.get("sessions")
        shared = definition.session
        self.shared = shared if shared != "fresh" and self.sessions is not None else None
        self.existing = (
            self.sessions.get(self.shared) if self.sessions is not None and self.shared else None
        )
        self.new_session = str(uuid.uuid4())

    def _path(self, relative: str) -> str:
        return str(Path(self.ctx.cwd) / relative)

    def request(self) -> AgentRequest:
        ctx, d, out_cap = self.ctx, self.definition, self.out_cap
        prompt = render_template(
            d.prompt, {**ctx.scope.snapshot(), "input": self.input.get("in"), **ctx.config.vars}
        )
        structured_cap = out_cap if self.structured else None
        return AgentRequest(
            prompt=prompt,
            cwd=ctx.cwd,
            model=d.model,
            allowed_tools=d.tools,
            permission_mode=d.permission_mode,
            output_schema=ctx.registry.caps.to_json_schema(structured_cap)
            if structured_cap
            else None,
            limits=AgentLimits(
                budget_usd=d.limits.budget_usd, turns=d.limits.turns, wall_ms=d.limits.wall_ms
            )
            if d.limits is not None
            else None,
            session_id=None if self.existing else self.new_session,
            resume=self.existing,
            fork=bool(self.existing) and d.fork,
            add_dirs=[self._path(x) for x in d.add_dirs] if d.add_dirs else None,
            append_system_prompt_file=self._path(d.append_system_prompt_file)
            if d.append_system_prompt_file
            else None,
            mcp_config=[self._path(x) for x in d.mcp_config] if d.mcp_config else None,
            bare=d.bare,
            on_done=d.on_done,
            step_key=ctx.step_key,
            label=f"{ctx.item_id} {d.name}",
            item=ctx.scope.get("item"),
            output_cap=out_cap,
            validate_output=(lambda v, cap=structured_cap: ctx.registry.caps.validate(cap, v))
            if structured_cap
            else None,
        )

    def on_event(self, event: dict[str, Any]) -> None:
        ctx = self.ctx
        if event.get("kind") == "steer" and ctx.store is not None:
            ctx.store.add_steer(
                run_id=ctx.run_id,
                item_id=ctx.item_id,
                step=ctx.node_id,
                prompt=str(event.get("prompt", "")),
                created_at=ctx.now(),
            )
        ctx.bus.emit("agent.event", ctx.run_id, {"node": ctx.node_id, **event}, item_id=ctx.item_id)
        #: A scope may tap every event of the agents under it (`rsi` records trajectories).
        tap = ctx.scope.get("agent_tap")
        if callable(tap):
            tap(event)

    async def execute(self, runner: Runner) -> AgentResult:
        """Run the agent (holding an agent slot if the run caps them), within its wall limit."""
        ctx, limits = self.ctx, self.definition.limits
        wall: AbortSignal | None = None
        signal = ctx.signal
        if limits is not None and limits.wall_ms is not None:
            wall = AbortSignal.timeout(limits.wall_ms)
            signal = AbortSignal.any(ctx.signal, wall)
        raw_tap: Callable[[dict[str, Any]], None] | None = ctx.scope.get("agent_raw_tap")
        run_context = RunContext(
            signal=signal, on_event=self.on_event, on_raw=raw_tap if callable(raw_tap) else None
        )
        self.on_event({"kind": "runner", "runner": self.runner_id})
        request = self.request()
        try:
            if ctx.agent_slots is not None:
                async with ctx.agent_slots:
                    return await runner.run(request, run_context)
            return await runner.run(request, run_context)
        finally:
            if wall is not None:
                wall.dispose()
                signal.dispose()

    def account(self, result: AgentResult) -> None:
        """Remember the session and record what the run cost."""
        if (
            self.shared
            and self.sessions is not None
            and not (self.existing and self.definition.fork)
        ):
            # Remember the conversation (even a failed one) so a retry or a later step continues it.
            self.sessions[self.shared] = result.session_id or self.existing or self.new_session
        if result.cost_usd:
            self.ctx.record_cost(result.cost_usd)
            self.on_event({"kind": "agent.cost", "usd": result.cost_usd})
        if result.steered_cost_usd is not None:
            self.on_event(
                {
                    "kind": "cost",
                    "agent_usd": (result.cost_usd or 0.0) - result.steered_cost_usd,
                    "steered_usd": result.steered_cost_usd,
                }
            )

    def emit(self, result: AgentResult) -> Emit:
        if result.park:
            return Emit(
                "needs_human",
                {"reason": result.park, "detail": result.error, "text": result.text},
            )
        if not result.ok:
            return Emit("fail", {"error": result.error or "agent failed", "text": result.text})
        value = result.output if result.output is not None else result.text
        if self.out_cap:
            check = self.ctx.registry.caps.validate(self.out_cap, value)
            if not check.ok:
                return Emit(
                    "fail",
                    {
                        "error": f"Agent output does not match {self.out_cap}: "
                        + "; ".join(check.errors)
                    },
                )
        return Emit("ok", value)


async def _run(input: PortValues, ctx: StepContext[AgentConfig]) -> Emit:
    definition = load_agent_definition(Path(ctx.cwd) / ctx.config.uses)
    runner_id = ctx.config.runner or definition.runner
    runner = ctx.registry.get_runner(runner_id)
    if runner is None:
        return Emit("fail", {"error": f'Unknown runner "{runner_id}"'})
    call = _AgentCall(input, ctx, definition, runner_id)
    result = await call.execute(runner)
    call.account(result)
    return call.emit(result)


#: Runs a prompt-defined agent (markdown with frontmatter) on a runner.
agent_module: ActionModule[AgentConfig] = define_action(
    ActionModule(
        id="agent",
        config=AgentConfig,
        ports=Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Any")]),
        effects="agentic",
        validate=_validate,
        run=_run,
    )
)
