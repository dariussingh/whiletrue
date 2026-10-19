import dataclasses
import re
from pathlib import Path
from typing import Any

from whiletrue.abort import AbortSignal
from whiletrue.basics import AgentConfig, agent_module
from whiletrue.core import (
    AgentRequest,
    AgentResult,
    Registry,
    RunContext,
    RunnerCapabilities,
    Scope,
    ValidateContext,
    WtEvent,
    create_step_context,
)

LIMITS = "limits: { budget_usd: 1, turns: 5, wall: 1m }"


class FakeRunner:
    id = "claude-code-headless"

    def __init__(self, reply: dict[str, Any] | None = None, **caps: bool) -> None:
        self.reply = reply or {}
        self.requests: list[AgentRequest] = []
        self.signals: list[AbortSignal] = []
        self.capabilities = RunnerCapabilities(
            **{
                "interactive": False,
                "structured_output": True,
                "resume": False,
                "fork": False,
                "budget": True,
                **caps,
            }
        )

    async def run(self, request: AgentRequest, ctx: RunContext) -> AgentResult:
        self.requests.append(request)
        self.signals.append(ctx.signal)
        if ctx.on_event:
            ctx.on_event({"kind": "tool", "name": "Read"})
        return dataclasses.replace(AgentResult(ok=True, text="done"), **self.reply)


def agent(
    directory: Path,
    name: str,
    frontmatter: str,
    body: str = "Do {{input}} for {{item.id}} {{extra}}",
) -> str:
    (directory / f"{name}.md").write_text(f"---\nagent: {name}\n{frontmatter}\n---\n{body}\n")
    return f"./{name}.md"


def setup(directory: Path, runner: FakeRunner, uses: str):
    registry = Registry()
    registry.register_runner(runner)
    registry.register_action(agent_module)
    scope = Scope()
    scope.set("item", {"id": "i1"})
    ctx = create_step_context(
        AgentConfig(uses=uses, vars={"extra": "!"}),
        cwd=str(directory),
        registry=registry,
        scope=scope,
    )
    events: list[WtEvent] = []
    ctx.bus.subscribe(events.append)
    return registry, ctx, events


async def test_renders_the_prompt_calls_the_runner_and_returns_text(tmp_path: Path) -> None:
    runner = FakeRunner({"text": "all good", "cost_usd": 0.3})
    uses = agent(tmp_path, "a1", f"{LIMITS}\nmodel: opus\ntools: [Read]\npermission_mode: plan")
    _, ctx, events = setup(tmp_path, runner, uses)
    cost = 0.0

    def record(c: float) -> None:
        nonlocal cost
        cost += c

    result = await agent_module.run({"in": "work"}, dataclasses.replace(ctx, record_cost=record))
    assert (result.port, result.value) == ("ok", "all good")
    assert cost == 0.3
    req = runner.requests[0]
    assert req.prompt == "Do work for i1 !"
    assert (req.cwd, req.model, req.allowed_tools, req.permission_mode) == (
        str(tmp_path),
        "opus",
        ["Read"],
        "plan",
    )
    assert req.limits is not None
    assert (req.limits.budget_usd, req.limits.turns, req.limits.wall_ms) == (1, 5, 60_000)
    assert req.session_id is not None
    assert re.fullmatch(r"[0-9a-f-]{36}", req.session_id)
    assert len(events) == 1
    assert events[0].type == "agent.event"
    assert events[0].data == {"node": "node", "kind": "tool", "name": "Read"}


async def test_omits_optional_request_fields_when_not_configured(tmp_path: Path) -> None:
    runner = FakeRunner()
    _, ctx, _ = setup(tmp_path, runner, agent(tmp_path, "a2", ""))
    await agent_module.run({"in": "x"}, ctx)
    req = runner.requests[0]
    assert (req.limits, req.model, req.output_schema) == (None, None, None)


async def test_requests_and_validates_structured_output(tmp_path: Path) -> None:
    runner = FakeRunner({"output": {"pass": True}})
    uses = agent(tmp_path, "a3", f"{LIMITS}\noutputs: {{ result: Verdict }}")
    _, ctx, _ = setup(tmp_path, runner, uses)
    r = await agent_module.run({"in": "x"}, ctx)
    assert (r.port, r.value) == ("ok", {"pass": True})
    assert runner.requests[0].output_schema is not None
    assert runner.requests[0].output_schema["type"] == "object"


async def test_fails_when_structured_output_does_not_match_its_cap(tmp_path: Path) -> None:
    runner = FakeRunner({"output": {"pass": "nope"}})
    _, ctx, _ = setup(tmp_path, runner, agent(tmp_path, "a4", "outputs: { result: Verdict }"))
    r = await agent_module.run({"in": "x"}, ctx)
    assert r.port == "fail"
    assert "does not match Verdict" in r.value["error"]


async def test_does_not_request_a_schema_for_text_outputs(tmp_path: Path) -> None:
    runner = FakeRunner({"text": "t"})
    _, ctx, _ = setup(tmp_path, runner, agent(tmp_path, "a5", "outputs: { result: Text }"))
    assert (await agent_module.run({"in": "x"}, ctx)).port == "ok"
    assert runner.requests[0].output_schema is None


async def test_fails_when_the_runner_reports_failure(tmp_path: Path) -> None:
    runner = FakeRunner({"ok": False, "text": "partial", "error": "budget exceeded"})
    _, ctx, _ = setup(tmp_path, runner, agent(tmp_path, "a6", ""))
    r = await agent_module.run({"in": "x"}, ctx)
    assert (r.port, r.value) == ("fail", {"error": "budget exceeded", "text": "partial"})


async def test_defaults_the_failure_message(tmp_path: Path) -> None:
    runner = FakeRunner({"ok": False, "text": ""})
    _, ctx, _ = setup(tmp_path, runner, agent(tmp_path, "a7", ""))
    assert (await agent_module.run({"in": "x"}, ctx)).value["error"] == "agent failed"


async def test_fails_on_an_unknown_runner_override(tmp_path: Path) -> None:
    runner = FakeRunner()
    _, ctx, _ = setup(tmp_path, runner, agent(tmp_path, "a8", ""))
    override = dataclasses.replace(ctx, config=AgentConfig(uses=ctx.config.uses, runner="nope"))
    r = await agent_module.run({"in": "x"}, override)
    assert r.value == {"error": 'Unknown runner "nope"'}


async def test_passes_a_wall_time_abort_signal_to_the_runner(tmp_path: Path) -> None:
    runner = FakeRunner()
    _, ctx, _ = setup(tmp_path, runner, agent(tmp_path, "a9", "limits: { wall: 1h }"))
    await agent_module.run({"in": "x"}, ctx)
    assert not runner.signals[0].aborted
    assert runner.signals[0] is not ctx.signal


def validate(directory: Path, runner: FakeRunner, uses: str):
    registry, _, _ = setup(directory, runner, uses)
    assert agent_module.validate is not None
    return agent_module.validate(
        AgentConfig(uses=uses), ValidateContext(cwd=str(directory), registry=registry)
    )


def test_validate_passes_a_well_formed_agent(tmp_path: Path) -> None:
    assert validate(tmp_path, FakeRunner(), agent(tmp_path, "v1", LIMITS)) == []


def test_validate_requires_limits(tmp_path: Path) -> None:
    assert (
        "needs `limits`" in validate(tmp_path, FakeRunner(), agent(tmp_path, "v2", ""))[0].message
    )


def test_validate_reports_unreadable_and_malformed_agent_files(tmp_path: Path) -> None:
    assert "Cannot read agent file" in validate(tmp_path, FakeRunner(), "./missing.md")[0].message
    (tmp_path / "bad.md").write_text("no frontmatter")
    assert "frontmatter" in validate(tmp_path, FakeRunner(), "./bad.md")[0].message


def test_validate_reports_unknown_runners(tmp_path: Path) -> None:
    uses = agent(tmp_path, "v3", f"{LIMITS}\nrunner: gpt")
    assert 'Unknown runner "gpt"' in validate(tmp_path, FakeRunner(), uses)[0].message


def test_validate_rejects_asks_the_runner_cannot_satisfy(tmp_path: Path) -> None:
    uses = agent(tmp_path, "v4", f"{LIMITS}\noutputs: {{ r: Verdict }}\nsession: item")
    runner = FakeRunner(structured_output=False, budget=False, resume=False)
    messages = [i.message for i in validate(tmp_path, runner, uses)]
    assert len(messages) == 3
    assert "structured output" in messages[0]
    assert "budget" in messages[1]
    assert 'session "item"' in messages[2]
