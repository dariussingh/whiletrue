import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from whiletrue.core import (
    AgentLimits,
    ConfigError,
    load_agent_definition,
    load_pipeline,
    parse_agent_definition,
    parse_pipeline,
)


def err(fn: Callable[[], Any]) -> ConfigError:
    with pytest.raises(ConfigError) as info:
        fn()
    return info.value


def test_parses_a_list_pipeline_into_an_implicit_sequence() -> None:
    cfg = parse_pipeline(
        """name: demo
pipeline:
  - shell: { run: "echo 1" }
  - id: second
    gate.shell: { run: "true" }
""",
        "w.yaml",
    )
    assert cfg.name == "demo"
    assert cfg.on_idle == "exit"
    assert cfg.on_fail == "park"
    assert (cfg.root.id, cfg.root.use) == ("root", "sequence")
    assert [(c.id, c.use) for c in cfg.root.children] == [
        ("root.0", "shell"),
        ("second", "gate.shell"),
    ]
    assert cfg.root.children[0].config == {"run": "echo 1"}


def test_parses_a_strategy_root_and_hoists_do_from_inside_the_config() -> None:
    cfg = parse_pipeline(
        """pipeline:
  loop:
    max: 3
    do:
      - shell: { run: "a" }
""",
        "w.yaml",
    )
    assert cfg.root.use == "loop"
    assert cfg.root.config == {"max": 3}
    assert len(cfg.root.children) == 1
    assert cfg.root.children[0].id == "root.0"


def test_accepts_a_sibling_do_and_wires() -> None:
    cfg = parse_pipeline(
        """pipeline:
  - id: a
    retry: { max: 2 }
    do:
      - shell: { run: x }
    wires: { fail: b }
  - id: b
    shell: { run: y }
""",
        "w.yaml",
    )
    a = cfg.root.children[0]
    assert len(a.children) == 1
    assert a.wires == {"fail": "b"}


def test_keeps_passthrough_keys_in_extra_and_uses_on_idle_on_fail() -> None:
    cfg = parse_pipeline(
        """uses: ["whiletrue-git"]
on_idle: watch
on_fail: drop
source: { json: { path: a.json } }
pipeline: [ { shell: { run: x } } ]
""",
        str(Path(tempfile.gettempdir()) / "proj" / "w.yaml"),
    )
    assert cfg.uses == ["whiletrue-git"]
    assert cfg.on_idle == "watch"
    assert cfg.on_fail == "drop"
    assert cfg.extra == {"source": {"json": {"path": "a.json"}}}
    assert cfg.name == "proj"


def test_yaml_scalars_are_not_coerced_beyond_json() -> None:
    cfg = parse_pipeline(
        "pipeline: [ { shell: { run: x, on: yes, when: 2024-01-01, flag: true } } ]\n", "w.yaml"
    )
    assert cfg.root.children[0].config == {
        "run": "x",
        "on": "yes",
        "when": "2024-01-01",
        "flag": True,
    }


def test_reports_yaml_syntax_errors_with_file_and_line() -> None:
    e = err(lambda: parse_pipeline("pipeline:\n  - shell: {run: [\n", "bad.yaml"))
    assert e.file == "bad.yaml"
    assert e.line >= 2


def test_requires_a_mapping_and_a_pipeline() -> None:
    assert "must be a mapping" in str(err(lambda: parse_pipeline("- a\n", "w.yaml")))
    assert "Missing required key" in str(err(lambda: parse_pipeline("name: x\n", "w.yaml")))
    assert "must be a mapping" in str(err(lambda: parse_pipeline("", "w.yaml")))


def test_reports_schema_errors_at_the_offending_line() -> None:
    e = err(
        lambda: parse_pipeline(
            "name: x\non_idle: sometimes\npipeline: [ {shell: {run: x}} ]\n", "w.yaml"
        )
    )
    assert e.line == 2
    assert "on_idle" in str(e)


def test_rejects_malformed_steps_with_locations() -> None:
    def parse(text: str) -> ConfigError:
        return err(lambda: parse_pipeline(text, "w.yaml"))

    assert parse("pipeline:\n  - 5\n").line == 2
    assert "needs a module key" in str(parse("pipeline:\n  - id: x\n"))
    assert "exactly one module key" in str(parse("pipeline:\n  - { shell: {}, exec: {} }\n"))
    assert "must be a mapping" in str(parse("pipeline:\n  - shell: 5\n"))
    assert "list of steps" in str(parse("pipeline:\n  - { shell: {}, do: 3 }\n"))
    assert "list of steps" in str(parse("pipeline:\n  - { shell: { do: 3 } }\n"))
    assert "non-empty" in str(parse("pipeline:\n  - { shell: {}, id: '' }\n"))
    assert "wires" in str(parse("pipeline:\n  - { shell: {}, wires: [1] }\n"))
    assert "wires" in str(parse("pipeline:\n  - { shell: {}, wires: { a: 1 } }\n"))


def test_rejects_duplicate_ids_and_unknown_wire_targets() -> None:
    dup = err(
        lambda: parse_pipeline(
            "pipeline:\n  - { id: a, shell: {} }\n  - { id: a, shell: {} }\n", "w.yaml"
        )
    )
    assert 'Duplicate step id "a" (first defined at line 2)' in str(dup)
    wire = err(
        lambda: parse_pipeline(
            "pipeline:\n  - { id: a, shell: {}, wires: { fail: zzz } }\n", "w.yaml"
        )
    )
    assert 'unknown sibling step "zzz"' in str(wire)


def test_nested_steps_get_dotted_default_ids() -> None:
    cfg = parse_pipeline(
        "pipeline:\n  loop:\n    max: 1\n    do:\n      - shell: {}\n      - shell: {}\n", "w.yaml"
    )
    assert [c.id for c in cfg.root.children] == ["root.0", "root.1"]


def test_load_pipeline_reads_a_file_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "whiletrue.yaml"
    path.write_text("pipeline: [ { shell: { run: x } } ]\n")
    assert len(load_pipeline(path).root.children) == 1


AGENT = """---
agent: implement
runner: claude-code-headless
model: opus
tools: [Read, Edit]
permission_mode: acceptEdits
inputs: { issue: Issue }
outputs: { result: Verdict }
limits: { budget_usd: 8, turns: 120, wall: 90m }
---
Implement {{issue.title}}.
"""


def test_agent_parses_frontmatter_and_prompt_body() -> None:
    d = parse_agent_definition(AGENT, "a.md")
    assert d.name == "implement"
    assert d.runner == "claude-code-headless"
    assert d.model == "opus"
    assert d.session == "fresh"
    assert d.tools == ["Read", "Edit"]
    assert d.permission_mode == "acceptEdits"
    assert d.inputs == {"issue": "Issue"}
    assert d.outputs == {"result": "Verdict"}
    assert d.limits == AgentLimits(budget_usd=8, turns=120, wall_ms=5_400_000)
    assert d.prompt == "Implement {{issue.title}}."


def test_agent_applies_defaults_for_a_minimal_agent() -> None:
    d = parse_agent_definition("---\nagent: x\n---\nhi", "a.md")
    assert (d.runner, d.session, d.prompt) == ("claude-code-headless", "fresh", "hi")
    assert d.limits is None
    assert d.model is None


def test_agent_supports_partial_limits() -> None:
    d = parse_agent_definition("---\nagent: x\nlimits: { turns: 5 }\n---\nhi", "a.md")
    assert d.limits == AgentLimits(turns=5)


def test_agent_requires_frontmatter() -> None:
    assert err(lambda: parse_agent_definition("no frontmatter", "a.md")).line == 1


def test_agent_cites_file_lines_past_the_fence_for_schema_errors() -> None:
    e = err(lambda: parse_agent_definition("---\nagent: x\nlimits: { turns: -1 }\n---\nhi", "a.md"))
    assert e.file == "a.md"
    assert e.line == 3


def test_agent_rejects_bad_wall_durations_and_non_mapping_frontmatter() -> None:
    bad_wall = err(
        lambda: parse_agent_definition("---\nagent: x\nlimits: { wall: soon }\n---\n", "a.md")
    )
    assert "Invalid duration" in str(bad_wall)
    assert "must be a mapping" in str(
        err(lambda: parse_agent_definition("---\n- a\n---\n", "a.md"))
    )


def test_agent_reports_yaml_errors_in_frontmatter_on_the_right_line() -> None:
    e = err(lambda: parse_agent_definition("---\nagent: x\nmodel: [\n---\nhi", "a.md"))
    assert e.line >= 3


def test_agent_loads_from_disk(tmp_path: Path) -> None:
    path = tmp_path / "a.md"
    path.write_text(AGENT)
    assert load_agent_definition(path).name == "implement"
