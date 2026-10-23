from typing import Any

from pydantic import BaseModel

from whiletrue.control import control_plugin
from whiletrue.core import (
    ActionModule,
    Emit,
    MemoryStore,
    Ports,
    PortSpec,
    Registry,
    StepContext,
    define_action,
    parse_pipeline,
    run_pipeline,
)
from whiletrue.core.module import PortValues
from whiletrue.store_sqlite import SqliteStore


class Cfg(BaseModel):
    n: int = 0
    fail: bool = False


def registry(calls: list[int], effects: Any = "pure", cacheable: bool = True) -> Registry:
    async def run(_input: PortValues, ctx: StepContext[Cfg]) -> Emit:
        calls.append(ctx.config.n)
        return Emit("fail", "x") if ctx.config.fail else Emit("ok", ctx.config.n * 2)

    r = Registry()
    r.use(control_plugin)
    r.register_action(
        define_action(
            ActionModule(
                id="double",
                config=Cfg,
                ports=Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Any")]),
                effects=effects,
                cacheable=cacheable,
                run=run,
            )
        )
    )
    return r


TWICE = "pipeline: [ { double: { n: 4 } }, { double: { n: 4 } } ]"


async def test_pure_results_are_cached_by_config_and_input() -> None:
    calls: list[int] = []
    store = MemoryStore()
    await run_pipeline(registry=registry(calls), config=parse_pipeline(TWICE), store=store)
    # The second step has a different input (the first one's output), so both run.
    assert calls == [4, 4]
    again = await run_pipeline(registry=registry(calls), config=parse_pipeline(TWICE), store=store)
    assert calls == [4, 4]  # served from the cache on the second run
    assert again.items[0].value == 8
    events = store.list_events(again.run_id, type="step.cached")
    assert len(events) == 2


async def test_the_cache_survives_a_new_store_connection(tmp_path: Any) -> None:
    calls: list[int] = []
    path = tmp_path / "state.db"
    first = SqliteStore(path)
    await run_pipeline(registry=registry(calls), config=parse_pipeline(TWICE), store=first)
    first.close()
    second = SqliteStore(path)
    await run_pipeline(registry=registry(calls), config=parse_pipeline(TWICE), store=second)
    second.close()
    assert calls == [4, 4]


async def test_different_config_misses_the_cache() -> None:
    calls: list[int] = []
    store = MemoryStore()
    r = registry(calls)
    await run_pipeline(
        registry=r, config=parse_pipeline("pipeline: { double: { n: 1 } }"), store=store
    )
    await run_pipeline(
        registry=r, config=parse_pipeline("pipeline: { double: { n: 2 } }"), store=store
    )
    assert calls == [1, 2]


async def test_agentic_side_effect_and_uncacheable_modules_are_never_cached() -> None:
    for effects, cacheable in (("agentic", True), ("side-effect", True), ("pure", False)):
        calls: list[int] = []
        store = MemoryStore()
        r = registry(calls, effects, cacheable)
        # side-effect modules need an idempotency key to be defined; skip validation by running
        text = "pipeline: { double: { n: 1 } }"
        await run_pipeline(registry=r, config=parse_pipeline(text), store=store)
        await run_pipeline(registry=r, config=parse_pipeline(text), store=store)
        assert calls == [1, 1], effects


async def test_failures_are_not_cached_and_cache_can_be_disabled() -> None:
    calls: list[int] = []
    store = MemoryStore()
    r = registry(calls)
    bad = "pipeline: { double: { n: 1, fail: true } }"
    await run_pipeline(registry=r, config=parse_pipeline(bad), store=store)
    await run_pipeline(registry=r, config=parse_pipeline(bad), store=store)
    assert calls == [1, 1]
    off = "cache: false\npipeline: { double: { n: 5 } }"
    await run_pipeline(registry=r, config=parse_pipeline(off), store=store)
    await run_pipeline(registry=r, config=parse_pipeline(off), store=store)
    assert calls == [1, 1, 5, 5]
