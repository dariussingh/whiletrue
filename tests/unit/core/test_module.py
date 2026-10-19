from dataclasses import replace
from typing import Any, cast

import pytest
from pydantic import BaseModel

from whiletrue.core import (
    EFFECTS,
    ActionModule,
    Emit,
    EmptyConfig,
    ModuleDefinitionError,
    Ports,
    PortSpec,
    StepContext,
    Strategy,
    create_step_context,
    define_action,
    define_strategy,
    is_effect,
)
from whiletrue.core.module import PortValues


class ShellCfg(BaseModel):
    run: str


async def _run(_input: PortValues, ctx: StepContext[ShellCfg]) -> Emit:
    return Emit("ok", ctx.config.run)


def shell_module() -> ActionModule[ShellCfg]:
    return ActionModule(
        id="shell",
        config=ShellCfg,
        ports=Ports(inputs=[PortSpec("in", "Any")], outputs=[PortSpec("out", "Text")]),
        effects="side-effect",
        run=_run,
    )


def test_lists_the_four_effect_kinds() -> None:
    assert EFFECTS == ("pure", "deterministic", "agentic", "side-effect")


def test_is_effect_accepts_only_known_effects() -> None:
    assert is_effect("pure")
    assert is_effect("agentic")
    assert not is_effect("magic")
    assert not is_effect(None)


def test_define_action_returns_a_valid_module_unchanged() -> None:
    mod = shell_module()
    assert define_action(mod) is mod


def test_define_action_accepts_dotted_ids() -> None:
    assert define_action(replace(shell_module(), id="git.commit")).id == "git.commit"


@pytest.mark.parametrize("bad", ["", "Shell", "gate.", ".gate", "gate..x", "1gate", "has space"])
def test_define_action_rejects_invalid_ids(bad: str) -> None:
    with pytest.raises(ModuleDefinitionError):
        define_action(replace(shell_module(), id=bad))


def test_define_action_rejects_a_non_action_kind() -> None:
    bad = replace(shell_module(), kind=cast(Any, "composite"))
    with pytest.raises(ModuleDefinitionError, match="kind"):
        define_action(bad)


def test_define_action_rejects_an_unknown_effect() -> None:
    bad = replace(shell_module(), effects=cast(Any, "magic"))
    with pytest.raises(ModuleDefinitionError, match="effect"):
        define_action(bad)


def test_gates_must_be_deterministic() -> None:
    with pytest.raises(ModuleDefinitionError, match="gates must be deterministic"):
        define_action(replace(shell_module(), id="gate.x", effects="agentic"))
    define_action(replace(shell_module(), id="gate.x", effects="deterministic"))


def test_rejects_duplicate_port_names_within_a_direction() -> None:
    bad = replace(
        shell_module(),
        ports=Ports(inputs=[PortSpec("in", "Any"), PortSpec("in", "Text")], outputs=[]),
    )
    with pytest.raises(ModuleDefinitionError, match="(?i)duplicate"):
        define_action(bad)


def test_allows_the_same_port_name_on_in_and_out() -> None:
    ok = replace(
        shell_module(),
        ports=Ports(inputs=[PortSpec("x", "Any")], outputs=[PortSpec("x", "Any")]),
    )
    define_action(ok)


def test_names_the_offending_module_in_the_error() -> None:
    with pytest.raises(ModuleDefinitionError) as info:
        define_action(replace(shell_module(), id="Bad"))
    assert "Bad" in str(info.value)


async def _noop(_c: list[Any], _i: PortValues, _ctx: Any) -> Emit:
    return Emit("ok")


def loop() -> Strategy[EmptyConfig]:
    return Strategy(id="loop", config=EmptyConfig, run=_noop)


def test_define_strategy_returns_a_valid_strategy_unchanged() -> None:
    s = loop()
    assert define_strategy(s) is s


def test_define_strategy_rejects_an_invalid_id() -> None:
    with pytest.raises(ModuleDefinitionError):
        define_strategy(replace(loop(), id="Loop!"))


async def test_an_action_emits_a_port_and_value() -> None:
    mod = define_action(shell_module())
    emit = await mod.run({}, create_step_context(ShellCfg(run="echo hi")))
    assert emit == Emit("ok", "echo hi")
