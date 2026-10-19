from typing import Any

import pytest

from whiletrue.core import (
    ActionModule,
    Emit,
    EmptyConfig,
    PluginApi,
    Ports,
    Registry,
    RegistryError,
    RunContext,
    Runner,
    RunnerCapabilities,
    SimplePlugin,
    Strategy,
    define_action,
    define_strategy,
)
from whiletrue.core.module import PortValues


async def _ok(*_args: Any) -> Emit:
    return Emit("ok")


def action(module_id: str) -> ActionModule[EmptyConfig]:
    return define_action(
        ActionModule(id=module_id, config=EmptyConfig, ports=Ports(), effects="pure", run=_ok)
    )


def strategy(module_id: str) -> Strategy[EmptyConfig]:
    async def run(_c: list[Any], _i: PortValues, _ctx: Any) -> Emit:
        return Emit("ok")

    return define_strategy(Strategy(id=module_id, config=EmptyConfig, run=run))


class FakeRunner:
    capabilities = RunnerCapabilities(
        interactive=False, structured_output=True, resume=False, fork=False, budget=True
    )

    def __init__(self, runner_id: str) -> None:
        self.id = runner_id

    async def run(self, request: Any, ctx: RunContext) -> Any:
        raise NotImplementedError


def runner(runner_id: str) -> Runner:
    return FakeRunner(runner_id)


def test_starts_with_builtin_caps() -> None:
    assert Registry().caps.has("Text")


def test_registers_and_resolves_actions_strategies_and_runners() -> None:
    r = Registry()
    r.register_action(action("shell"))
    r.register_strategy(strategy("loop"))
    r.register_runner(runner("claude-code-headless"))
    resolved_action = r.resolve("shell")
    resolved_strategy = r.resolve("loop")
    assert resolved_action is not None
    assert resolved_action.kind == "action"
    assert resolved_strategy is not None
    assert resolved_strategy.kind == "strategy"
    assert r.resolve("nope") is None
    found = r.get_runner("claude-code-headless")
    assert found is not None
    assert found.id == "claude-code-headless"
    assert r.get_runner("x") is None
    assert r.ids() == ["loop", "shell"]


def test_module_and_strategy_ids_share_one_namespace() -> None:
    r = Registry()
    r.register_action(action("x"))
    with pytest.raises(RegistryError):
        r.register_strategy(strategy("x"))


def test_rejects_duplicate_registrations() -> None:
    r = Registry()
    r.register_action(action("a"))
    with pytest.raises(RegistryError, match="already registered"):
        r.register_action(action("a"))
    r.register_runner(runner("r"))
    with pytest.raises(RegistryError, match="already registered"):
        r.register_runner(runner("r"))


def test_registers_and_looks_up_providers_by_kind_and_name() -> None:
    r = Registry()
    r.register_provider("source", "json", {"any": 1})
    assert r.get_provider("source", "json") == {"any": 1}
    assert r.get_provider("source", "x") is None
    with pytest.raises(RegistryError, match="already registered"):
        r.register_provider("source", "json", {})


def test_installs_plugins_through_the_public_api() -> None:
    def register(api: PluginApi) -> None:
        api.register_action(action("demo.act"))
        api.register_cap("Thing", str)

    plugin = SimplePlugin(name="demo", register=register)
    r = Registry()
    r.use(plugin)
    assert r.resolve("demo.act") is not None
    assert r.caps.has("Thing")
    assert r.plugins() == ["demo"]
    with pytest.raises(RegistryError, match="already installed"):
        r.use(plugin)
