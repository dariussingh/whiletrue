from pathlib import Path

import pytest
from pydantic import BaseModel

from whiletrue.control import control_plugin
from whiletrue.core import (
    ActionModule,
    ConfigError,
    Emit,
    MemoryStore,
    Ports,
    PortSpec,
    Registry,
    StepContext,
    define_action,
    load_pipeline,
    run_pipeline,
    validate_pipeline,
)
from whiletrue.core.module import PortValues


class Cfg(BaseModel):
    value: object = None


def registry(in_cap: str = "Any", out_cap: str = "Any") -> Registry:
    async def run(_input: PortValues, ctx: StepContext[Cfg]) -> Emit:
        return Emit("ok", ctx.config.value)

    r = Registry()
    r.use(control_plugin)
    r.register_action(
        define_action(
            ActionModule(
                id="val",
                config=Cfg,
                ports=Ports(inputs=[PortSpec("in", in_cap)], outputs=[PortSpec("out", out_cap)]),
                effects="pure",
                cacheable=False,
                run=run,
            )
        )
    )
    return r


def write(directory: Path, name: str, text: str) -> Path:
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


PIPE = """
pipeline:
  - composite: { uses: ./c/pair.yaml, params: { v: hello, n: 3 } }
"""

PAIR = """
composite: pair
params:
  v: { required: true }
  n: { default: 1 }
ports: { in: Any, out: Any }
pipeline:
  - val: { value: "{{params.v}}" }
  - val: { value: "{{params.n}}" }
  - val: { value: "n={{params.n}} v={{params.v}}" }
"""


async def run(directory: Path, text: str = PIPE, reg: Registry | None = None) -> object:
    write(directory, "whiletrue.yaml", text)
    config = load_pipeline(directory / "whiletrue.yaml")
    result = await run_pipeline(registry=reg or registry(), config=config, store=MemoryStore())
    return result.items[0].value


async def test_expands_a_composite_and_fills_params_with_their_types(tmp_path: Path) -> None:
    write(tmp_path, "c/pair.yaml", PAIR)
    assert await run(tmp_path) == "n=3 v=hello"
    config = load_pipeline(tmp_path / "whiletrue.yaml")
    assert config.root.children[0].use == "sequence"
    assert config.root.children[0].ports == {"in": "Any", "out": "Any"}
    assert config.sources == [str((tmp_path / "c" / "pair.yaml").resolve())]
    val = config.root.children[0].children[0].children[1]
    assert val.config == {"value": 3}  # a whole-token param keeps its type
    assert val.id == "root.0.root.1"


async def test_param_defaults_apply(tmp_path: Path) -> None:
    write(tmp_path, "c/pair.yaml", PAIR)
    text = "pipeline: [ { composite: { uses: ./c/pair.yaml, params: { v: x } } } ]"
    assert await run(tmp_path, text) == "n=1 v=x"


def test_composite_errors_point_at_the_step(tmp_path: Path) -> None:
    write(tmp_path, "c/pair.yaml", PAIR)

    def load(step: str) -> None:
        write(tmp_path, "whiletrue.yaml", f"pipeline: [ {step} ]")
        load_pipeline(tmp_path / "whiletrue.yaml")

    with pytest.raises(ConfigError, match='needs param "v"'):
        load("{ composite: { uses: ./c/pair.yaml } }")
    with pytest.raises(ConfigError, match='Unknown param "zzz"'):
        load("{ composite: { uses: ./c/pair.yaml, params: { v: 1, zzz: 2 } } }")
    with pytest.raises(ConfigError, match="not found"):
        load("{ composite: { uses: ./nope.yaml } }")
    with pytest.raises(ConfigError, match="Invalid composite step"):
        load("{ composite: { params: {} } }")
    with pytest.raises(ConfigError, match='Unknown recipe "nope"'):
        load("{ composite: { uses: 'recipe:nope' } }")


def test_unknown_template_param_is_an_error(tmp_path: Path) -> None:
    write(
        tmp_path, "c/bad.yaml", "composite: bad\npipeline: [ { val: { value: '{{params.q}}' } } ]"
    )
    write(tmp_path, "whiletrue.yaml", "pipeline: [ { composite: { uses: ./c/bad.yaml } } ]")
    with pytest.raises(ConfigError, match='Unknown param "q"'):
        load_pipeline(tmp_path / "whiletrue.yaml")


async def test_composites_nest_and_pass_params_down(tmp_path: Path) -> None:
    write(
        tmp_path,
        "c/outer.yaml",
        "composite: outer\nparams: { x: { required: true } }\npipeline:\n"
        "  - composite: { uses: ./inner.yaml, params: { y: '{{params.x}}' } }\n",
    )
    write(
        tmp_path,
        "c/inner.yaml",
        "composite: inner\nparams: { y: {} }\npipeline: { val: { value: 'got {{params.y}}' } }\n",
    )
    text = "pipeline: [ { composite: { uses: ./c/outer.yaml, params: { x: deep } } } ]"
    assert await run(tmp_path, text) == "got deep"
    assert len(load_pipeline(tmp_path / "whiletrue.yaml").sources) == 2


def test_composite_cycles_and_excess_depth_are_rejected(tmp_path: Path) -> None:
    write(tmp_path, "c/a.yaml", "composite: a\npipeline: [ { composite: { uses: ./b.yaml } } ]")
    write(tmp_path, "c/b.yaml", "composite: b\npipeline: [ { composite: { uses: ./a.yaml } } ]")
    write(tmp_path, "whiletrue.yaml", "pipeline: [ { composite: { uses: ./c/a.yaml } } ]")
    with pytest.raises(ConfigError, match="cycle"):
        load_pipeline(tmp_path / "whiletrue.yaml")
    for i in range(12):
        nxt = f"composite: {{ uses: ./d{i + 1}.yaml }}" if i < 11 else "val: {}"
        write(tmp_path, f"c/d{i}.yaml", f"composite: d{i}\npipeline: [ {{ {nxt} }} ]")
    write(tmp_path, "whiletrue.yaml", "pipeline: [ { composite: { uses: ./c/d0.yaml } } ]")
    with pytest.raises(ConfigError, match="deeper than"):
        load_pipeline(tmp_path / "whiletrue.yaml")


def test_bad_composite_files_are_rejected(tmp_path: Path) -> None:
    for text, message in (
        ("- 1", "must be a mapping"),
        ("composite: x", "Missing required key"),
        ("composite: x\nports: { sideways: Any }\npipeline: []", "named"),
        ("composite: x\npipeline: [ { val: {} } ]\nbogus: 1", "bogus"),
    ):
        write(tmp_path, "c/x.yaml", text)
        write(tmp_path, "whiletrue.yaml", "pipeline: [ { composite: { uses: ./c/x.yaml } } ]")
        with pytest.raises(ConfigError, match=message):
            load_pipeline(tmp_path / "whiletrue.yaml")


def test_declared_ports_are_type_checked_against_neighbours(tmp_path: Path) -> None:
    write(
        tmp_path,
        "c/diff.yaml",
        "composite: d\nports: { in: Diff, out: Verdict }\npipeline: [ { val: {} } ]",
    )
    text = "pipeline: [ { val: {} }, { composite: { uses: ./c/diff.yaml } }, { val: {} } ]"
    write(tmp_path, "whiletrue.yaml", text)
    config = load_pipeline(tmp_path / "whiletrue.yaml")
    ok = validate_pipeline(config, registry("Any", "Any"))
    assert ok == []
    bad = [d.message for d in validate_pipeline(config, registry("Text", "Issue"))]
    assert any('into "root.1".in (Diff)' in m for m in bad)
    assert any("(Verdict) into" in m for m in bad)


def test_declared_ports_must_name_known_caps(tmp_path: Path) -> None:
    write(tmp_path, "c/x.yaml", "composite: x\nports: { in: Nope }\npipeline: [ { val: {} } ]")
    write(tmp_path, "whiletrue.yaml", "pipeline: [ { composite: { uses: ./c/x.yaml } } ]")
    diags = validate_pipeline(load_pipeline(tmp_path / "whiletrue.yaml"), registry())
    assert any('unknown cap "Nope"' in d.message for d in diags)


def test_parse_without_a_base_dir_leaves_composites_alone() -> None:
    from whiletrue.core import parse_pipeline

    config = parse_pipeline("pipeline: { composite: { uses: ./x.yaml } }")
    assert config.root.use == "composite"
