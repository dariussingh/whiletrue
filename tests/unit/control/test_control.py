from typing import Any

from pydantic import BaseModel

from whiletrue.abort import AbortSignal
from whiletrue.control import control_plugin, loop_strategy, sequence_strategy
from whiletrue.control.loop import LoopConfig
from whiletrue.core import (
    ActionModule,
    Emit,
    MemoryStore,
    Ports,
    PortSpec,
    Registry,
    RunResult,
    StepContext,
    ValidateContext,
    define_action,
    parse_pipeline,
    run_pipeline,
    validate_pipeline,
)
from whiletrue.core.module import PortValues


class StepCfg(BaseModel):
    name: str


class Harness:
    """`step` replays a scripted list of emits and records each call's input and loop scope."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, Any]] = []
        self.scripts: dict[str, list[Emit]] = {}
        self.registry = Registry()
        self.registry.use(control_plugin)

        async def run(input: PortValues, ctx: StepContext[StepCfg]) -> Emit:
            self.calls.append((input.get("in"), ctx.scope.get("loop")))
            script = self.scripts.get(ctx.config.name, [])
            return script.pop(0) if script else Emit("ok", f"{ctx.config.name}-default")

        self.registry.register_action(
            define_action(
                ActionModule(
                    id="step",
                    config=StepCfg,
                    ports=Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Any")]),
                    effects="pure",
                    run=run,
                )
            )
        )

    @property
    def inputs(self) -> list[Any]:
        return [c[0] for c in self.calls]

    async def run(self, yaml_text: str, signal: AbortSignal | None = None) -> RunResult:
        return await run_pipeline(
            registry=self.registry,
            config=parse_pipeline(yaml_text, "w.yaml"),
            store=MemoryStore(),
            signal=signal,
        )


async def test_sequence_chains_values_and_stops_at_the_first_non_continuing_port() -> None:
    t = Harness()
    t.scripts["a"] = [Emit("ok", "A")]
    t.scripts["b"] = [Emit("fail", {"error": "b broke"})]
    result = await t.run(
        "pipeline:\n  - step: { name: a }\n  - step: { name: b }\n  - step: { name: c }\n"
    )
    assert (result.items[0].port, result.items[0].value) == ("fail", {"error": "b broke"})
    assert t.inputs == [None, "A"]


async def test_sequence_skip_passes_the_previous_value_through() -> None:
    t = Harness()
    t.scripts["a"] = [Emit("ok", "A")]
    t.scripts["b"] = [Emit("skip")]
    await t.run("pipeline:\n  - step: { name: a }\n  - step: { name: b }\n  - step: { name: c }\n")
    assert t.inputs == [None, "A", "A"]


async def test_sequence_returns_the_last_value_and_ok_for_an_empty_sequence() -> None:
    t = Harness()
    assert (await t.run("pipeline: [ { step: { name: z } } ]")).items[0].value == "z-default"
    empty = (await t.run("pipeline: { sequence: {} }")).items[0]
    assert (empty.state, empty.port) == ("done", "ok")


async def test_sequence_stops_when_cancelled() -> None:
    t = Harness()
    signal = AbortSignal()
    signal.abort()
    res = await t.run("pipeline: [ { step: { name: a } }, { step: { name: b } } ]", signal)
    assert res.status == "cancelled"


async def test_sequence_stops_between_children_when_cancelled_mid_run() -> None:
    t = Harness()
    signal = AbortSignal()
    original = t.registry.resolve("step")
    assert original is not None
    inner = original.module.run

    async def abort_after_first(input: PortValues, ctx: Any) -> Emit:
        signal.abort()
        return await inner(input, ctx)

    original.module.run = abort_after_first
    res = await t.run("pipeline: [ { step: { name: a } }, { step: { name: b } } ]", signal)
    assert res.items[0].state == "cancelled"
    assert len(t.calls) == 1


def test_sequence_is_registered_as_a_chaining_strategy() -> None:
    assert sequence_strategy.chain


async def test_loop_stops_at_the_first_ok_iteration_ralph() -> None:
    t = Harness()
    t.scripts["work"] = [
        Emit("fail", {"error": "tests failed", "output": "2 failing"}),
        Emit("fail", "plain text failure"),
        Emit("fail", {"other": 1}),
        Emit("ok", "green"),
    ]
    result = await t.run("pipeline:\n  loop:\n    max: 5\n    do:\n      - step: { name: work }\n")
    assert (result.items[0].state, result.items[0].value) == ("done", "green")
    assert [c[1] for c in t.calls] == [
        {"index": 1, "previous": ""},
        {"index": 2, "previous": "tests failed\n2 failing"},
        {"index": 3, "previous": "plain text failure"},
        {"index": 4, "previous": '{"other":1}'},
    ]


async def test_loop_fails_when_max_iterations_are_exhausted() -> None:
    t = Harness()
    t.scripts["work"] = [Emit("fail", "x"), Emit("fail", "y")]
    result = await t.run("pipeline: { loop: { max: 2, do: [ { step: { name: work } } ] } }")
    assert (result.items[0].port, result.items[0].value) == (
        "fail",
        {"error": "loop gave up after 2 iterations", "last": "y"},
    )
    assert len(t.calls) == 2


async def test_loop_stop_when_never_runs_every_iteration() -> None:
    t = Harness()
    result = await t.run(
        "pipeline: { loop: { max: 3, stop_when: never, do: [ { step: { name: w } } ] } }"
    )
    assert len(t.calls) == 3
    assert result.items[0].state == "done"


async def test_loop_propagates_needs_human_and_rejected_immediately() -> None:
    t = Harness()
    t.scripts["w"] = [Emit("needs_human")]
    a = await t.run("pipeline: { loop: { max: 5, do: [ { step: { name: w } } ] } }")
    assert (a.items[0].state, a.items[0].port) == ("parked", "needs_human")
    assert len(t.calls) == 1
    t.scripts["w"] = [Emit("rejected")]
    b = await t.run("pipeline: { loop: { max: 5, do: [ { step: { name: w } } ] } }")
    assert b.items[0].port == "rejected"


async def test_loop_supports_an_explicit_unbounded_loop() -> None:
    t = Harness()
    t.scripts["w"] = [Emit("fail", "x"), Emit("ok", "fin")]
    result = await t.run("pipeline: { loop: { max: unbounded, do: [ { step: { name: w } } ] } }")
    assert result.items[0].value == "fin"


async def test_loop_stops_looping_when_cancelled() -> None:
    t = Harness()
    signal = AbortSignal()
    signal.abort()
    res = await t.run("pipeline: { loop: { max: 3, do: [ { step: { name: w } } ] } }", signal)
    assert res.items[0].state == "cancelled"


async def test_loop_stops_between_iterations_when_cancelled_mid_run() -> None:
    t = Harness()
    signal = AbortSignal()
    t.scripts["w"] = [Emit("fail", "x"), Emit("fail", "y")]
    resolved = t.registry.resolve("step")
    assert resolved is not None
    inner = resolved.module.run

    async def abort_on_first(input: PortValues, ctx: Any) -> Emit:
        signal.abort()
        return await inner(input, ctx)

    resolved.module.run = abort_on_first
    res = await t.run("pipeline: { loop: { max: 5, do: [ { step: { name: w } } ] } }", signal)
    assert res.items[0].state == "cancelled"
    assert len(t.calls) == 1


def test_loop_is_rejected_by_validate_without_max() -> None:
    t = Harness()

    def check(max_clause: str) -> list[Any]:
        text = f"pipeline: {{ loop: {{ {max_clause}do: [ {{ step: {{ name: a }} }} ] }} }}"
        return validate_pipeline(parse_pipeline(text, "w.yaml"), t.registry)

    assert "requires `max`" in check("")[0].message
    assert loop_strategy.validate is not None
    assert (
        loop_strategy.validate(
            LoopConfig(stop_when="ok", max=1), ValidateContext(cwd="", registry=t.registry)
        )
        == []
    )
    assert check("max: 2, ") == []
    assert 'Invalid config for "loop"' in check("max: 0, ")[0].message
