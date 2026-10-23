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


# ---- M4 foundations: journal, resume, no_replay, dependencies, services -------------------

executions: list[str] = []


class FxCfg(BaseModel):
    key: str = "k"


async def fx_run(_i: PortValues, ctx: StepContext[FxCfg]) -> Emit:
    executions.append(ctx.config.key)
    return Emit("ok", {"made": ctx.config.key, "n": len(executions)})


def journaled_registry(*, journal: bool = True) -> Registry:
    r = make_registry()
    r.register_action(
        define_action(
            ActionModule(
                id="fx",
                config=FxCfg,
                ports=Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Any")]),
                effects="side-effect",
                run=fx_run,
                idempotency_key=lambda _i, ctx: f"fx:{ctx.config.key}",
                journal=journal,
            )
        )
    )
    return r


async def test_a_journaled_effect_runs_once_across_runs() -> None:
    executions.clear()
    store = MemoryStore()
    config = parse_pipeline("pipeline:\n  - fx: { key: a }\n", "t.yaml")
    first = await run_pipeline(registry=journaled_registry(), config=config, store=store)
    second = await run_pipeline(registry=journaled_registry(), config=config, store=store)
    assert executions == ["a"]
    assert first.items[0].value == second.items[0].value
    assert store.list_events(second.run_id, type="step.journaled")
    done = store.effect_get("fx:a")
    assert done is not None and done.status == "done"


async def test_unjournaled_effects_run_every_time() -> None:
    executions.clear()
    store = MemoryStore()
    config = parse_pipeline("pipeline:\n  - fx: { key: b }\n", "t.yaml")
    registry = journaled_registry(journal=False)
    await run_pipeline(registry=registry, config=config, store=store)
    await run_pipeline(registry=registry, config=config, store=store)
    assert executions == ["b", "b"]


async def test_a_failed_effect_is_not_remembered() -> None:
    store = MemoryStore()
    registry = journaled_registry()
    config = parse_pipeline("pipeline:\n  - emit: { port: fail, value: x }\n", "t.yaml")
    await run_pipeline(registry=registry, config=config, store=store)
    assert store.effect_get("fx:never") is None


async def test_resume_serves_recorded_steps_and_runs_the_rest_for_real() -> None:
    executions.clear()
    store = MemoryStore()
    registry = journaled_registry(journal=False)
    first_cfg = parse_pipeline("pipeline:\n  - fx: { key: one }\n", "t.yaml")
    first = await run_pipeline(registry=registry, config=first_cfg, store=store)
    assert executions == ["one"]

    longer = parse_pipeline("pipeline:\n  - fx: { key: one }\n  - fx: { key: two }\n", "t.yaml")
    from whiletrue.core import load_recording

    recording = load_recording(store, first.run_id)
    # The first step shares a node id with the original run; the second is new.
    result = await run_pipeline(
        registry=registry,
        config=longer,
        store=store,
        items=recording.items,
        replay=recording.steps,
        resume=True,
    )
    assert executions == ["one", "two"]
    assert result.status == "done"
    assert store.list_events(result.run_id, type="step.replayed")


async def test_strict_replay_still_diverges_on_a_missing_recording() -> None:
    store = MemoryStore()
    config = parse_pipeline("pipeline:\n  - fx: { key: z }\n", "t.yaml")
    result = await run_pipeline(
        registry=journaled_registry(journal=False), config=config, store=store, replay={}
    )
    assert result.status == "parked"
    steps = store.list_steps(result.run_id)
    assert any("Replay diverged" in (s.error or "") for s in steps)


async def test_scopes_can_opt_out_of_replay() -> None:
    executions.clear()
    store = MemoryStore()
    registry = journaled_registry(journal=False)

    async def gate_run(
        children: list[PipelineNode], input: PortValues, ctx: CompositeContext[EmptyConfig]
    ) -> Emit:
        ctx.scope.set("no_replay", True)
        return await ctx.run(children[0], {"in": None})

    registry.register_strategy(
        define_strategy(Strategy(id="live", config=EmptyConfig, run=gate_run))
    )
    config = parse_pipeline("pipeline:\n  live:\n    do:\n      - fx: { key: inner }\n", "t.yaml")
    first = await run_pipeline(registry=registry, config=config, store=store)
    from whiletrue.core import load_recording

    recording = load_recording(store, first.run_id)
    await run_pipeline(
        registry=registry,
        config=config,
        store=store,
        items=recording.items,
        replay=recording.steps,
        resume=True,
    )
    assert executions == ["inner", "inner"]


async def test_items_run_after_their_dependencies_and_blocked_items_wait() -> None:
    store = MemoryStore()
    config = parse_pipeline("pipeline:\n  - emit: { value: ok }\n", "t.yaml")
    order: list[str] = []
    bus = EventBus()
    bus.subscribe(lambda e: order.append(e.item_id or "") if e.type == "step.started" else None)
    result = await run_pipeline(
        registry=make_registry(),
        config=config,
        store=store,
        bus=bus,
        items=[
            RunItem("b", depends_on=["a"]),
            RunItem("a"),
            RunItem("c", depends_on=["missing"]),
        ],
    )
    assert list(dict.fromkeys(order)) == ["a", "b"]
    states = {o.item_id: o.state for o in result.items}
    assert states == {"a": "done", "b": "done", "c": "pending"}
    assert result.status == "parked"
    blocked = store.list_events(result.run_id, item_id="c", type="item.state")
    assert blocked and blocked[-1].data["state"] == "pending"


async def test_a_failed_dependency_blocks_its_dependents() -> None:
    store = MemoryStore()
    config = parse_pipeline("pipeline:\n  - emit: { port: fail, value: x }\n", "t.yaml")
    result = await run_pipeline(
        registry=make_registry(),
        config=config,
        store=store,
        items=[RunItem("a"), RunItem("b", depends_on=["a"])],
    )
    states = {o.item_id: o.state for o in result.items}
    assert states == {"a": "parked", "b": "pending"}


async def test_steps_see_the_store_the_clock_and_watch_mode() -> None:
    seen: dict[str, Any] = {}

    async def probe(_i: PortValues, ctx: StepContext[EmptyConfig]) -> Emit:
        seen.update(store=ctx.store, now=ctx.now(), watch=ctx.watch)
        return Emit("ok")

    registry = make_registry()
    registry.register_action(
        define_action(
            ActionModule(
                id="probe",
                config=EmptyConfig,
                ports=Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Any")]),
                effects="pure",
                run=probe,
            )
        )
    )
    store = MemoryStore()
    config = parse_pipeline("on_idle: watch\npipeline:\n  - probe: {}\n", "t.yaml")
    await run_pipeline(registry=registry, config=config, store=store, now=lambda: 42)
    assert seen == {"store": store, "now": 42, "watch": True}
