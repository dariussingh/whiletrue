import pytest
from pydantic import BaseModel

from whiletrue.control import control_plugin
from whiletrue.core import (
    ActionModule,
    Emit,
    MemoryStore,
    Ports,
    PortSpec,
    Registry,
    ReplayError,
    StepContext,
    define_action,
    load_recording,
    parse_pipeline,
    run_pipeline,
)
from whiletrue.core.module import PortValues


class Cfg(BaseModel):
    n: int = 0


def build(calls: list[str]) -> Registry:
    def action(module_id: str, effects: str):
        async def run(_input: PortValues, ctx: StepContext[Cfg]) -> Emit:
            calls.append(module_id)
            return Emit("ok", ctx.config.n)

        return define_action(
            ActionModule(
                id=module_id,
                config=Cfg,
                ports=Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Any")]),
                effects=effects,  # type: ignore[arg-type]
                cacheable=False,
                run=run,
            )
        )

    r = Registry()
    r.use(control_plugin)
    r.register_action(action("calc", "pure"))
    r.register_action(action("agentish", "agentic"))
    return r


PIPE = "pipeline: [ { calc: { n: 1 } }, { agentish: { n: 2 } } ]"


async def test_replay_serves_recordings_but_reruns_pure_modules() -> None:
    calls: list[str] = []
    store = MemoryStore()
    first = await run_pipeline(registry=build(calls), config=parse_pipeline(PIPE), store=store)
    assert calls == ["calc", "agentish"]
    recording = load_recording(store, first.run_id)
    assert recording.status == "done"
    assert recording.item_states == {"main": "done"}
    again = await run_pipeline(
        registry=build(calls),
        config=parse_pipeline(PIPE),
        store=store,
        items=recording.items,
        replay=recording.steps,
    )
    assert calls == ["calc", "agentish", "calc"]  # only the pure module ran again
    assert again.items[0].value == 2
    assert len(store.list_events(again.run_id, type="step.replayed")) == 1


async def test_replay_fails_the_step_when_the_recording_has_no_entry() -> None:
    calls: list[str] = []
    store = MemoryStore()
    await run_pipeline(registry=build(calls), config=parse_pipeline(PIPE), store=store)
    result = await run_pipeline(
        registry=build(calls), config=parse_pipeline(PIPE), store=store, replay={}
    )
    assert result.items[0].port == "fail"
    assert "Replay diverged" in str(result.items[0].value)


def test_loading_an_unknown_run_is_an_error() -> None:
    with pytest.raises(ReplayError, match='No run "x"'):
        load_recording(MemoryStore(), "x")
