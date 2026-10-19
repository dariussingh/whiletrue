import re
from typing import Any

from pydantic import BaseModel

from whiletrue.core import (
    ActionModule,
    ConfigIssue,
    Emit,
    EmptyConfig,
    Ports,
    PortSpec,
    Registry,
    Strategy,
    ValidateContext,
    define_action,
    define_strategy,
    format_diagnostic,
    has_errors,
    parse_pipeline,
    validate_pipeline,
)
from whiletrue.core.module import PortValues


class Cfg(BaseModel):
    run: str | None = None


async def _ok(*_args: Any) -> Emit:
    return Emit("ok")


def action(
    module_id: str,
    *,
    inputs: list[PortSpec] | None = None,
    outputs: list[PortSpec] | None = None,
    effects: Any = "pure",
    idempotency_key: Any = None,
    validate: Any = None,
) -> ActionModule[Cfg]:
    return define_action(
        ActionModule(
            id=module_id,
            config=Cfg,
            ports=Ports(
                inputs=[PortSpec("in", "Any")] if inputs is None else inputs,
                outputs=[PortSpec("out", "Any")] if outputs is None else outputs,
            ),
            effects=effects,
            run=_ok,
            idempotency_key=idempotency_key,
            validate=validate,
        )
    )


def warn_when_empty(config: Cfg, _vctx: ValidateContext) -> list[ConfigIssue]:
    return [] if config.run else [ConfigIssue("warning", "run is empty")]


class LoopCfg(BaseModel):
    max: int | None = None


def loop_validate(config: LoopCfg, _vctx: ValidateContext) -> list[ConfigIssue]:
    return [ConfigIssue("error", "loop needs max")] if config.max is None else []


def make_registry() -> Registry:
    r = Registry()
    r.register_action(action("noop"))
    r.register_action(action("emit.text", inputs=[], outputs=[PortSpec("out", "Text")]))
    r.register_action(action("need.issue", inputs=[PortSpec("in", "Issue")], outputs=[]))
    r.register_action(action("risky", effects="side-effect"))
    r.register_action(action("safe.risky", effects="side-effect", idempotency_key=lambda *_: "k"))
    r.register_action(action("checked", validate=warn_when_empty))

    async def run(_c: list[Any], _i: PortValues, _ctx: Any) -> Emit:
        return Emit("ok")

    r.register_strategy(
        define_strategy(Strategy(id="sequence", chain=True, config=EmptyConfig, run=run))
    )
    r.register_strategy(
        define_strategy(Strategy(id="loop", config=LoopCfg, validate=loop_validate, run=run))
    )
    return r


def check(yaml_text: str) -> list[Any]:
    return validate_pipeline(parse_pipeline(yaml_text, "w.yaml"), make_registry())


def test_passes_a_valid_pipeline() -> None:
    assert check("pipeline: [ { noop: {} }, { noop: {} } ]") == []


def test_flags_unknown_modules_and_lists_known_ones() -> None:
    d = check("pipeline: [ { nope: {} } ]")
    assert len(d) == 1
    assert 'Unknown module "nope" (known: ' in d[0].message
    assert "noop" in d[0].message
    assert has_errors(d)


def test_unknown_module_without_any_registered_modules() -> None:
    d = validate_pipeline(parse_pipeline("pipeline: { nope: {} }", "w.yaml"), Registry())
    assert d[0].message == 'Unknown module "nope"'


def test_flags_config_schema_errors_with_the_path() -> None:
    d = check("pipeline: [ { noop: { run: 5 } } ]")
    assert 'Invalid config for "noop"' in d[0].message
    assert d[0].message.endswith(' at "run"')


def test_runs_module_validate_hooks_and_keeps_warnings_non_fatal() -> None:
    d = check("pipeline: [ { checked: {} } ]")
    assert len(d) == 1
    assert d[0].severity == "warning"
    assert not has_errors(d)
    assert check("pipeline: [ { checked: { run: x } } ]") == []


def test_enforces_strategy_hooks() -> None:
    assert check("pipeline: { loop: { do: [ { noop: {} } ] } }")[0].message == "loop needs max"
    assert check("pipeline: { loop: { max: 2, do: [ { noop: {} } ] } }") == []


def test_forbids_children_on_action_modules() -> None:
    d = check("pipeline: [ { noop: {}, do: [ { noop: {} } ] } ]")
    assert 'Action module "noop" cannot have child steps (`do`)' in [x.message for x in d]


def test_requires_idempotency_keys_on_side_effect_modules() -> None:
    assert "idempotencyKey" in check("pipeline: [ { risky: {} } ]")[0].message
    assert check("pipeline: [ { safe.risky: {} } ]") == []


def test_flags_gates_that_are_not_deterministic_even_if_defined_unsafely() -> None:
    r = make_registry()
    # Built without define_action, simulating a plugin that skipped it.
    r.register_action(
        ActionModule(id="gate.fake", config=Cfg, ports=Ports(), effects="agentic", run=_ok)
    )
    d = validate_pipeline(parse_pipeline("pipeline: [ { gate.fake: {} } ]", "w.yaml"), r)
    assert 'Gate "gate.fake" must be deterministic' in [x.message for x in d]


def test_type_checks_chained_wiring_between_sibling_actions() -> None:
    bad = check("pipeline: [ { emit.text: {} }, { need.issue: {} } ]")
    assert len(bad) == 1
    assert bad[0].message == 'Cannot wire "root.0".out (Text) into "root.1".in (Issue)'
    assert check("pipeline: [ { emit.text: {} }, { noop: {} } ]") == []


def test_ports_missing_on_either_side_skip_the_wiring_check() -> None:
    assert check("pipeline: [ { need.issue: {} }, { emit.text: {} } ]") == []


def test_skips_wiring_checks_around_non_action_nodes() -> None:
    assert (
        check("pipeline: [ { loop: { max: 1, do: [ { noop: {} } ] } }, { need.issue: {} } ]") == []
    )


def test_validates_nested_steps() -> None:
    d = check("pipeline: { loop: { max: 1, do: [ { nope: {} } ] } }")
    assert d[0].node_id == "root.0"


def test_formats_diagnostics() -> None:
    d = check("pipeline: [ { nope: {} } ]")
    assert re.fullmatch(
        r'w\.yaml:1:\d+: error: Unknown module "nope".*\[root\.0\]', format_diagnostic(d[0])
    )
