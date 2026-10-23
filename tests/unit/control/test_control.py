import asyncio
import time
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
    RunItem,
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
            self.calls.append((input.get("in"), ctx.scope.get("iter")))
            script = self.scripts.get(ctx.config.name, [])
            return script.pop(0) if script else Emit("ok", f"{ctx.config.name}-default")

        self.registry.register_action(
            define_action(
                ActionModule(
                    id="step",
                    config=StepCfg,
                    ports=Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Any")]),
                    effects="pure",
                    cacheable=False,
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
        {"index": 1, "previous": "", "value": None},
        {"index": 2, "previous": "tests failed\n2 failing", "value": None},
        {"index": 3, "previous": "plain text failure", "value": None},
        {"index": 4, "previous": '{"other":1}', "value": None},
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


async def test_wires_route_a_failure_to_a_sibling_and_continue_from_there() -> None:
    t = Harness()
    t.scripts["check"] = [Emit("fail", {"error": "red"}), Emit("ok", "green")]
    t.scripts["fix"] = [Emit("ok", "patched")]
    result = await t.run(
        """
pipeline:
  - id: check
    step: { name: check }
    wires: { fail: fix, ok: done }
  - id: fix
    step: { name: fix }
    wires: { ok: check }
  - id: done
    step: { name: done }
"""
    )
    assert result.items[0].state == "done"
    assert t.inputs == [None, {"error": "red"}, "patched", "green"]


async def test_wires_that_cycle_forever_are_cut_off() -> None:
    t = Harness()
    result = await t.run(
        """
pipeline:
  - id: a
    step: { name: a }
    wires: { ok: b }
  - id: b
    step: { name: b }
    wires: { ok: a }
"""
    )
    assert result.items[0].port == "fail"
    assert "routed more than" in str(result.items[0].value)


async def test_loop_until_expression_ends_the_loop() -> None:
    t = Harness()
    t.scripts["w"] = [Emit("ok", 1), Emit("ok", 2), Emit("ok", 3)]
    result = await t.run(
        "pipeline: { loop: { max: 5, until: 'last.value >= 2', do: [ { step: { name: w } } ] } }"
    )
    assert (result.items[0].port, result.items[0].value) == ("ok", 2)
    assert len(t.calls) == 2


async def test_loop_until_keeps_going_through_failures_and_gives_up_at_max() -> None:
    t = Harness()
    t.scripts["w"] = [Emit("fail", "x"), Emit("fail", "y")]
    result = await t.run(
        "pipeline: { loop: { max: 2, until: 'last.ok', do: [ { step: { name: w } } ] } }"
    )
    assert result.items[0].port == "fail"
    assert len(t.calls) == 2


async def test_loop_over_iterates_a_list_and_collects() -> None:
    t = Harness()
    result = await t.run(
        """
pipeline:
  loop:
    over: "[1, 2, 3]"
    collect: true
    do: [ { step: { name: "n-{{iter.value}}" } } ]
"""
    )
    assert result.items[0].value == ["n-1-default", "n-2-default", "n-3-default"]
    assert [c[1]["index"] for c in t.calls] == [1, 2, 3]


async def test_loop_over_without_collect_returns_the_last_value_and_fails_fast() -> None:
    t = Harness()
    t.scripts["w"] = [Emit("ok", "a"), Emit("fail", "boom"), Emit("ok", "c")]
    result = await t.run("pipeline: { loop: { over: '[1,2,3]', do: [ { step: { name: w } } ] } }")
    assert (result.items[0].port, result.items[0].value) == ("fail", "boom")
    assert len(t.calls) == 2
    ok = await t.run("pipeline: { loop: { over: '[1,2]', do: [ { step: { name: w } } ] } }")
    assert ok.items[0].value == "w-default"
    empty = await t.run("pipeline: { loop: { over: '[]', do: [ { step: { name: w } } ] } }")
    assert (empty.items[0].port, empty.items[0].value) == ("ok", None)


async def test_loop_over_must_be_a_list() -> None:
    t = Harness()
    result = await t.run("pipeline: { loop: { over: '5', do: [ { step: { name: w } } ] } }")
    assert "must be a list" in str(result.items[0].value)


async def test_loop_over_reads_the_item_payload_and_max_caps_it() -> None:
    t = Harness()
    result = await run_pipeline(
        registry=t.registry,
        config=parse_pipeline(
            "pipeline: { loop: { over: 'item.files', max: 2, collect: true, "
            "do: [ { step: { name: s } } ] } }"
        ),
        store=MemoryStore(),
        items=[RunItem("i", {"files": ["a", "b", "c"]})],
    )
    assert len(result.items[0].value) == 2


def test_loop_validates_its_expressions() -> None:
    t = Harness()

    def check(clause: str) -> list[str]:
        text = f"pipeline: {{ loop: {{ {clause}, do: [ {{ step: {{ name: a }} }} ] }} }}"
        diags = validate_pipeline(parse_pipeline(text, "w.yaml"), t.registry)
        return [d.message for d in diags]

    assert check("over: '[1]'") == []
    assert check("max: 2, until: 'last.ok'") == []
    assert any("Unknown variable" in m for m in check("max: 2, until: 'nope.x'"))
    assert any("syntax" in m for m in check("max: 2, until: '1 +'"))
    assert any("requires `max`" in m for m in check("until: 'last.ok'"))


async def test_map_runs_children_per_element_and_returns_results() -> None:
    t = Harness()
    result = await t.run(
        "pipeline: { map: { over: '[\"a\", \"b\"]', do: [ { step: { name: 'p-{{iter.value}}' } } ] } }"
    )
    assert result.items[0].value == ["p-a-default", "p-b-default"]
    assert [c[1]["value"] for c in t.calls] == ["a", "b"]


async def test_map_stops_at_the_first_failure_and_rejects_non_lists() -> None:
    t = Harness()
    t.scripts["w"] = [Emit("ok", 1), Emit("fail", "bad")]
    result = await t.run("pipeline: { map: { over: '[1,2,3]', do: [ { step: { name: w } } ] } }")
    assert (result.items[0].port, result.items[0].value) == ("fail", "bad")
    assert len(t.calls) == 2
    bad = await t.run("pipeline: { map: { over: '1', do: [ { step: { name: w } } ] } }")
    assert "must be a list" in str(bad.items[0].value)


async def test_map_stops_when_cancelled() -> None:
    t = Harness()
    signal = AbortSignal()
    signal.abort()
    res = await t.run("pipeline: { map: { over: '[1]', do: [ { step: { name: w } } ] } }", signal)
    assert res.status == "cancelled"


async def test_parallel_runs_children_on_the_same_input_and_collects_in_order() -> None:
    t = Harness()
    t.scripts["a"] = [Emit("ok", "A")]
    t.scripts["b"] = [Emit("ok", "B")]
    result = await t.run(
        "pipeline: { parallel: { do: [ { step: { name: a } }, { step: { name: b } } ] } }"
    )
    assert result.items[0].value == ["A", "B"]


async def test_parallel_returns_the_first_non_continuing_outcome() -> None:
    t = Harness()
    t.scripts["b"] = [Emit("fail", "b broke")]
    t.scripts["c"] = [Emit("fail", "c broke")]
    result = await t.run(
        "pipeline: { parallel: { do: [ { step: { name: a } }, { step: { name: b } },"
        " { step: { name: c } } ] } }"
    )
    assert (result.items[0].port, result.items[0].value) == ("fail", "b broke")


SWITCH = """
pipeline:
  switch:
    on: "input"
    cases: { x: cx, "2": two }
    default: other
    do:
      - { id: cx, step: { name: cx } }
      - { id: two, step: { name: two } }
      - { id: other, step: { name: other } }
"""


async def test_switch_runs_the_matching_case_default_or_skips() -> None:
    t = Harness()
    for value, expected in (("x", "cx-default"), (2, "two-default"), ("zzz", "other-default")):
        result = await run_pipeline(
            registry=t.registry,
            config=parse_pipeline(SWITCH.replace("input", "item.v")),
            store=MemoryStore(),
            items=[RunItem("i", {"v": value})],
        )
        assert result.items[0].value == expected
    no_default = SWITCH.replace("    default: other\n", "").replace("item.v", "item.v")
    result = await run_pipeline(
        registry=t.registry,
        config=parse_pipeline(no_default.replace("input", "item.v")),
        store=MemoryStore(),
        items=[RunItem("i", {"v": "nope"})],
    )
    assert (result.items[0].state, result.items[0].port) == ("done", "skip")


def test_switch_validates_its_targets_and_expression() -> None:
    t = Harness()

    def check(text: str) -> list[str]:
        return [d.message for d in validate_pipeline(parse_pipeline(text, "w.yaml"), t.registry)]

    assert check(SWITCH.replace("input", "item.v")) == []
    bad = SWITCH.replace("cx", "gone", 1).replace("default: other", "default: nope")
    messages = check(bad.replace("input", "item.v"))
    assert any('unknown step "gone"' in m for m in messages)
    assert any('unknown step "nope"' in m for m in messages)
    assert any("Unknown variable" in m for m in check(SWITCH.replace("input", "zip.x")))


async def test_retry_reruns_until_success_and_feeds_back_the_failure() -> None:
    t = Harness()
    t.scripts["w"] = [Emit("fail", {"error": "e1"}), Emit("fail", "e2"), Emit("ok", "fine")]
    result = await t.run("pipeline: { retry: { max: 3, do: [ { step: { name: w } } ] } }")
    assert (result.items[0].state, result.items[0].value) == ("done", "fine")
    assert [c[1]["previous"] for c in t.calls] == ["", "e1", "e2"]


async def test_retry_parks_for_a_human_when_exhausted_by_default() -> None:
    t = Harness()
    t.scripts["w"] = [Emit("fail", "x"), Emit("fail", "y")]
    result = await t.run(
        "pipeline: { retry: { max: 2, backoff: 1ms, factor: 2, do: [ { step: { name: w } } ] } }"
    )
    assert (result.items[0].state, result.items[0].port) == ("parked", "needs_human")
    t.scripts["w"] = [Emit("fail", "x")]
    failed = await t.run(
        "pipeline: { retry: { max: 1, on_exhausted: fail, feedback: false,"
        " do: [ { step: { name: w } } ] } }"
    )
    assert failed.items[0].port == "fail"


async def test_retry_passes_other_outcomes_through_and_honours_cancellation() -> None:
    t = Harness()
    t.scripts["w"] = [Emit("needs_human", "q")]
    result = await t.run("pipeline: { retry: { max: 3, do: [ { step: { name: w } } ] } }")
    assert result.items[0].port == "needs_human"
    assert len(t.calls) == 1
    signal = AbortSignal()
    signal.abort()
    cancelled = await t.run("pipeline: { retry: { do: [ { step: { name: w } } ] } }", signal)
    assert cancelled.status == "cancelled"


async def test_retry_backoff_wait_ends_early_on_abort() -> None:
    t = Harness()
    t.scripts["w"] = [Emit("fail", "x")] * 3
    signal = AbortSignal()
    asyncio.get_running_loop().call_later(0.05, signal.abort)
    started = time.monotonic()
    await t.run(
        "pipeline: { retry: { max: 3, backoff: 30s, do: [ { step: { name: w } } ] } }", signal
    )
    assert time.monotonic() - started < 5


def test_retry_validates_backoff() -> None:
    t = Harness()
    text = "pipeline: { retry: { backoff: soon, do: [ { step: { name: a } } ] } }"
    diags = validate_pipeline(parse_pipeline(text, "w.yaml"), t.registry)
    assert any("Invalid duration" in d.message for d in diags)
    ok = "pipeline: { retry: { backoff: 2s, do: [ { step: { name: a } } ] } }"
    assert validate_pipeline(parse_pipeline(ok, "w.yaml"), t.registry) == []


async def test_try_swallows_failures() -> None:
    t = Harness()
    t.scripts["a"] = [Emit("ok", "A")]
    t.scripts["w"] = [Emit("fail", "x"), Emit("fail", "y")]
    seq = await t.run(
        "pipeline:\n  - step: { name: a }\n  - try: { do: [ { step: { name: w } } ] }\n"
        "  - step: { name: c }\n"
    )
    assert seq.items[0].state == "done"
    assert t.inputs[-1] == "A"
    as_ok = await t.run("pipeline: { try: { emit: ok, do: [ { step: { name: w } } ] } }")
    assert (as_ok.items[0].port, as_ok.items[0].value) == ("ok", "y")


async def test_try_keeps_success_and_does_not_hide_cancellation() -> None:
    t = Harness()
    ok = await t.run("pipeline: { try: { do: [ { step: { name: z } } ] } }")
    assert ok.items[0].value == "z-default"
    signal = AbortSignal()
    signal.abort()
    res = await t.run("pipeline: { try: { do: [ { step: { name: z } } ] } }", signal)
    assert res.status == "cancelled"


def test_map_validates_its_expression() -> None:
    t = Harness()

    def check(over: str) -> list[str]:
        text = f"pipeline: {{ map: {{ over: '{over}', do: [ {{ step: {{ name: a }} }} ] }} }}"
        return [d.message for d in validate_pipeline(parse_pipeline(text, "w.yaml"), t.registry)]

    assert check("[1]") == []
    assert any("Unknown variable" in m for m in check("zip.x"))
    assert any("syntax" in m for m in check("1 +"))
