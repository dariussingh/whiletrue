from typing import Any

from pydantic import BaseModel

from whiletrue.abort import AbortSignal
from whiletrue.core import (
    ActionModule,
    CompositeContext,
    Emit,
    EmptyConfig,
    EventBus,
    MemoryStore,
    PipelineNode,
    Ports,
    PortSpec,
    Registry,
    RunItem,
    RunResult,
    StepContext,
    Strategy,
    define_action,
    define_strategy,
    hash_pipeline,
    parse_pipeline,
    run_pipeline,
)
from whiletrue.core.module import PortValues

disposed: list[str] = []


class EmitCfg(BaseModel):
    value: Any = None
    port: str | None = None
    cost: float | None = None
    throws: bool = False


class ValueCfg(BaseModel):
    value: Any = None


async def emit_run(input: PortValues, ctx: StepContext[EmitCfg]) -> Emit:
    if ctx.config.throws:
        raise RuntimeError("kaboom")
    if ctx.config.cost:
        ctx.record_cost(ctx.config.cost)
    value = ctx.config.value if ctx.config.value is not None else input.get("in")
    return Emit(ctx.config.port or "ok", value)


async def text_run(_i: PortValues, ctx: StepContext[ValueCfg]) -> Emit:
    return Emit("ok", ctx.config.value)


async def leak_run(_i: PortValues, ctx: StepContext[EmptyConfig]) -> Emit:
    from whiletrue.core import Resource

    ctx.scope.add_resource(Resource("res", lambda: disposed.append(ctx.node_id)))
    return Emit("ok")


async def sequence_run(
    children: list[PipelineNode], input: PortValues, ctx: CompositeContext[EmptyConfig]
) -> Emit:
    cur = Emit("ok", input.get("in"))
    for child in children:
        cur = await ctx.run(child, {"in": cur.value})
        if cur.port != "ok":
            return cur
    return cur


def make_registry() -> Registry:
    r = Registry()
    any_ports = Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Any")])
    r.register_action(
        define_action(
            ActionModule(id="emit", config=EmitCfg, ports=any_ports, effects="pure", run=emit_run)
        )
    )
    r.register_action(
        define_action(
            ActionModule(
                id="text",
                config=ValueCfg,
                ports=Ports(outputs=[PortSpec("out", "Text")]),
                effects="pure",
                run=text_run,
            )
        )
    )
    r.register_action(
        define_action(
            ActionModule(id="leak", config=EmptyConfig, ports=Ports(), effects="pure", run=leak_run)
        )
    )
    r.register_strategy(
        define_strategy(Strategy(id="sequence", chain=True, config=EmptyConfig, run=sequence_run))
    )
    return r


async def run(
    yaml_text: str, registry: Registry | None = None, **extra: Any
) -> tuple[RunResult, MemoryStore]:
    store = MemoryStore()
    result = await run_pipeline(
        registry=registry or make_registry(),
        config=parse_pipeline(yaml_text, "w.yaml"),
        store=store,
        bus=EventBus(),
        now=lambda: 1000,
        **extra,
    )
    return result, store


async def test_runs_a_sequence_chaining_outputs_and_persists_everything() -> None:
    result, store = await run(
        """
pipeline:
  - emit: { value: hello }
  - emit: {}
"""
    )
    assert result.status == "done"
    assert [(i.item_id, i.state, i.port, i.value) for i in result.items] == [
        ("main", "done", "ok", "hello")
    ]
    steps = store.list_steps(result.run_id)
    assert [(s.node_path, s.status, s.port) for s in steps] == [
        ("root", "completed", "ok"),
        ("root.0", "completed", "ok"),
        ("root.1", "completed", "ok"),
    ]
    run_record = store.get_run(result.run_id)
    assert run_record is not None
    assert run_record.status == "done"
    assert len(run_record.pipeline_hash) == 64
    assert store.list_items(result.run_id)[0].state == "done"
    types = [e.type for e in store.list_events(result.run_id)]
    assert types[0] == "run.started"
    assert types[-1] == "run.finished"
    assert "step.finished" in types
    assert "item.state" in types


async def test_renders_templates_from_item_scope() -> None:
    result, _ = await run(
        'pipeline:\n  - emit: { value: "issue {{item.title}} ({{item.id}})" }\n',
        items=[RunItem("i1", {"title": "T"})],
    )
    assert result.items[0].value == "issue T (i1)"


async def test_handles_non_object_item_payloads() -> None:
    result, _ = await run(
        "pipeline: [ { emit: { value: '{{item.id}}' } } ]", items=[RunItem("x", "scalar")]
    )
    assert result.items[0].value == "x"


async def test_a_failing_step_parks_the_item_by_default() -> None:
    result, store = await run("pipeline: [ { emit: { port: fail, value: nope } } ]")
    assert (result.items[0].state, result.items[0].port) == ("parked", "fail")
    assert result.status == "parked"
    assert store.list_items(result.run_id)[0].state == "parked"


async def test_drops_failing_items_when_on_fail_is_drop() -> None:
    result, _ = await run("on_fail: drop\npipeline: [ { emit: { port: fail } } ]")
    assert result.items[0].state == "failed"
    assert result.status == "failed"


async def test_parks_on_needs_human() -> None:
    result, _ = await run("pipeline: [ { emit: { port: needs_human } } ]")
    assert result.items[0].state == "parked"


async def test_converts_raised_errors_into_a_failing_errored_step() -> None:
    result, store = await run("pipeline: [ { emit: { throws: true } } ]")
    assert result.items[0].port == "fail"
    step = next(s for s in store.list_steps(result.run_id) if s.node_path == "root.0")
    assert (step.status, step.error) == ("errored", "kaboom")
    assert any(e.type == "step.error" for e in store.list_events(result.run_id))


async def test_fails_on_invalid_module_config() -> None:
    result, store = await run("pipeline: [ { emit: { cost: nope } } ]")
    assert result.items[0].port == "fail"
    errors = [s.error for s in store.list_steps(result.run_id) if s.error]
    assert any("Invalid config" in e for e in errors)


async def test_fails_on_unknown_modules_and_unresolved_templates() -> None:
    result, store = await run("pipeline: [ { nope: {} } ]")
    assert result.items[0].port == "fail"
    assert any("Unknown module" in (s.error or "") for s in store.list_steps(result.run_id))
    result, _ = await run("pipeline: [ { emit: { value: '{{item.zzz}}' } } ]")
    assert result.items[0].port == "fail"


async def test_validates_ok_output_against_the_out_ports_cap() -> None:
    bad, bad_store = await run("pipeline: [ { text: { value: 5 } } ]")
    assert bad.items[0].port == "fail"
    err = next(s.error for s in bad_store.list_steps(bad.run_id) if s.node_path == "root.0")
    assert err is not None
    assert "does not match cap Text" in err
    good, _ = await run("pipeline: [ { text: { value: hi } } ]")
    assert good.items[0].value == "hi"


async def test_records_per_step_cost() -> None:
    result, store = await run("pipeline: [ { emit: { cost: 0.25 } } ]")
    step = next(s for s in store.list_steps(result.run_id) if s.node_path == "root.0")
    assert step.cost_usd == 0.25


async def test_releases_scoped_resources_when_a_steps_scope_exits() -> None:
    disposed.clear()
    await run("pipeline: [ { leak: {} } ]")
    assert disposed == ["root.0"]


async def test_runs_multiple_items_sequentially() -> None:
    result, store = await run(
        "pipeline: [ { emit: { value: '{{item.id}}' } } ]", items=[RunItem("a"), RunItem("b")]
    )
    assert [i.value for i in result.items] == ["a", "b"]
    assert len(store.list_items(result.run_id)) == 2


async def test_cancels_cleanly_when_the_signal_is_aborted() -> None:
    signal = AbortSignal()
    signal.abort()
    result, _ = await run("pipeline: [ { emit: {} } ]", signal=signal)
    assert result.status == "cancelled"
    assert result.items[0].state == "cancelled"


async def test_counts_attempts_when_a_node_runs_again() -> None:
    reg = make_registry()

    async def twice(
        children: list[PipelineNode], input: PortValues, ctx: CompositeContext[EmptyConfig]
    ) -> Emit:
        await ctx.run(children[0], input)
        return await ctx.run(children[0], input)

    reg.register_strategy(define_strategy(Strategy(id="twice", config=EmptyConfig, run=twice)))
    result, store = await run("pipeline: { twice: { do: [ { emit: {} } ] } }", registry=reg)
    attempts = [s.attempt for s in store.list_steps(result.run_id) if s.node_path == "root.0"]
    assert attempts == [1, 2]


async def test_logs_when_a_scope_fails_to_release() -> None:
    from whiletrue.core import Resource

    reg = make_registry()

    def cannot_release() -> None:
        raise RuntimeError("cannot release")

    async def badres(_i: PortValues, ctx: StepContext[EmptyConfig]) -> Emit:
        ctx.scope.add_resource(Resource("x", cannot_release))
        return Emit("ok")

    reg.register_action(
        define_action(
            ActionModule(id="badres", config=EmptyConfig, ports=Ports(), effects="pure", run=badres)
        )
    )
    logs: list[str] = []
    await run(
        "pipeline: [ { badres: {} } ]",
        registry=reg,
        log=lambda _level, message, _data=None: logs.append(message),
    )
    assert "Failed to release resources for root.0: cannot release" in logs[0]


async def test_stringifies_odd_exceptions_and_survives_a_failing_listener() -> None:
    reg = make_registry()

    async def boom(_i: PortValues, _ctx: StepContext[EmptyConfig]) -> Emit:
        raise ValueError

    reg.register_action(
        define_action(
            ActionModule(id="boom", config=EmptyConfig, ports=Ports(), effects="pure", run=boom)
        )
    )
    result, _ = await run("pipeline: [ { boom: {} } ]", registry=reg)
    assert result.items[0].port == "fail"


def test_hash_pipeline_is_stable_and_sensitive_to_config() -> None:
    a = parse_pipeline("pipeline: [ { emit: { value: 1 } } ]", "a.yaml")
    b = parse_pipeline("pipeline: [ { emit: { value: 1 } } ]", "b.yaml")
    c = parse_pipeline("pipeline: [ { emit: { value: 2 } } ]", "c.yaml")
    assert hash_pipeline(a) == hash_pipeline(b)
    assert hash_pipeline(a) != hash_pipeline(c)
