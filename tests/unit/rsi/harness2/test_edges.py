import json
from dataclasses import replace
from pathlib import Path

import pytest

from tests.unit.rsi.harness2.conftest import (
    Script,
    ScriptedRunner,
    make_project,
    registry_for,
    rsi_yaml,
    run_yaml,
)
from whiletrue.core import RunItem, parse_pipeline, validate_pipeline
from whiletrue.rsi import SettingError, apply_rsi
from whiletrue.rsi.harness2 import library as lib
from whiletrue.rsi.harness2 import proposal as proposals
from whiletrue.rsi.harness2 import rollout, stagecache
from whiletrue.rsi.harness2 import tree as t
from whiletrue.rsi.harness2.ledger import BudgetError, Ledger
from whiletrue.rsi.harness2.surfaces import CLAUDE_CODE_SURFACE as CC


def applied(yaml: str) -> list[str]:
    config = apply_rsi(parse_pipeline(yaml, "w.yaml"), registry_for(ScriptedRunner(Script())))
    return [c.id for c in config.root.children]


def refused(yaml: str, message: str) -> None:
    with pytest.raises(SettingError, match=message):
        apply_rsi(parse_pipeline(yaml, "w.yaml"), registry_for(ScriptedRunner(Script())))


def test_auto_scope_takes_safe_steps_around_the_first_agent() -> None:
    base = "rsi: { budget_usd: 3, adopt: never }\npipeline:\n"
    both = base + "  - id: g\n    gate.shell: { run: 'true' }\n  - id: a\n    agent: { uses: x }\n"
    assert applied(both) == ["rsi"]
    two = (
        base
        + "  - id: a\n    agent: { uses: x }\n  - id: g\n    gate.shell: { run: 'true' }\n  - id: b\n    agent: { uses: y }\n  - id: s\n    shell: { run: z }\n"
    )
    assert applied(two) == ["rsi", "s"]
    nested = (
        base
        + "  - id: n\n    sequence:\n      do:\n        - shell: { run: x }\n        - agent: { uses: x }\n"
    )
    refused(nested, "cannot wrap step n")
    refused(base + "  - id: g\n    gate.shell: { run: 'true' }\n", "no agent step")


def test_a_named_scope_refuses_people_and_unknown_modules_are_ignored() -> None:
    base = "rsi: { budget_usd: 3, adopt: never, scope: [a, h] }\npipeline:\n"
    refused(
        base + "  - id: a\n    agent: { uses: x }\n  - id: h\n    human.approve: {}\n",
        "asks a person",
    )
    ok = "rsi: { budget_usd: 3, adopt: never, scope: [a, u] }\npipeline:\n  - id: a\n    agent: { uses: x }\n  - id: u\n    nonexistent.module: {}\n"
    assert applied(ok) == ["rsi"]


def test_a_single_strategy_root_must_be_safe_and_contain_an_agent() -> None:
    base = "rsi: { budget_usd: 3 }\npipeline:\n  retry:\n    max: 2\n    do:\n"
    refused(
        base + "      - shell: { run: x }\n      - agent: { uses: x }\n", "cannot wrap the pipeline"
    )
    refused(base + "      - gate.shell: { run: 'true' }\n", "no agent step")


def test_validation_catches_empty_scopes_bad_expressions_and_unreadable_agents(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    registry = registry_for(ScriptedRunner(Script()))

    def messages(yaml: str) -> str:
        config = parse_pipeline(yaml, str(project / "w.yaml"))
        return "\n".join(d.message for d in validate_pipeline(config, registry, cwd=str(project)))

    empty = "pipeline:\n  - id: w\n    rsi.harness2: { budget_usd: 3 }\n    do:\n      - gate.shell: { run: 'true' }\n"
    assert "wraps no agent step" in messages(empty)
    bad_when = "pipeline:\n  - id: w\n    rsi.harness2: { budget_usd: 3, when: '(((' }\n    do:\n      - agent: { uses: ./agents/dev.md }\n"
    assert "`rsi.when`" in messages(bad_when)
    missing = "pipeline:\n  - id: w\n    rsi.harness2: { budget_usd: 3 }\n    do:\n      - agent: { uses: ./agents/missing.md }\n"
    assert "Cannot" in messages(missing) or "cannot" in messages(missing)
    other = "pipeline:\n  - id: w\n    rsi.harness2: { budget_usd: 3, surface: codex }\n    do:\n      - agent: { uses: ./agents/dev.md }\n"
    assert 'but `rsi.surface` is "codex"' in messages(other)


async def test_unreadable_files_and_slots_and_task_text(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    yaml = "max_agents: 2\n" + rsi_yaml()
    script = Script()
    result, _, _ = await run_yaml(
        project, yaml, script, items=[RunItem("i1", {"body": "Fix the thing", "other": 1})]
    )
    assert result.items[0].state == "done"
    task = (project / ".whiletrue/rsi/items/i1/stages/inv1/context/task.md").read_text()
    assert task == "Fix the thing"
    result2, _, _ = await run_yaml(project, yaml, Script(), items=[RunItem("i2", {"other": 1})])
    assert (
        "other: 1" in (project / ".whiletrue/rsi/items/i2/stages/inv1/context/task.md").read_text()
    )
    assert result2.items[0].state == "done"


async def test_string_input_reaches_the_improver(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    yaml = (
        "pipeline:\n  - set: { value: 'first do this' }\n  - id: w\n    rsi.harness2: { budget_usd: 5, adopt: never }\n"
        "    do:\n      - agent: { uses: ./agents/dev.md }\n"
    )
    await run_yaml(project, yaml, Script(), items=[RunItem("i1", {"title": "T"})])
    task = (project / ".whiletrue/rsi/items/i1/stages/inv1/context/task.md").read_text()
    assert "Input to the steps\nfirst do this" in task


async def test_a_second_k2_run_reuses_the_selection(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    await run_yaml(project, rsi_yaml(k=2), Script(), items=[RunItem("i1", {"title": "T"})])
    again = Script()
    await run_yaml(project, rsi_yaml(k=2), again, items=[RunItem("i1", {"title": "T"})])
    assert again.count("select") == 0 and again.count("rollout") == 0


async def test_a_broken_budget_identity_is_logged_not_fatal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(self: Ledger) -> None:
        raise BudgetError("off by one")

    monkeypatch.setattr(Ledger, "assert_identity", broken)
    project = make_project(tmp_path)
    result, _, _ = await run_yaml(
        project, rsi_yaml(), Script(), items=[RunItem("i1", {"title": "T"})]
    )
    assert result.items[0].state == "done"


async def test_a_snapshot_failure_fails_the_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(rollout.sn, "take", boom)
    project = make_project(tmp_path)
    result, _, _ = await run_yaml(
        project, rsi_yaml(), Script(), items=[RunItem("i1", {"title": "T"})]
    )
    assert result.items[0].state == "parked"


def test_rollout_helpers_cope_with_odd_values_and_broken_files(tmp_path: Path) -> None:
    assert rollout._text(None) == "" and rollout._text({"a": 1}) == '{"a": 1}'
    assert "<object" in rollout._json_safe(object())
    assert rollout._json_safe({"a": 1}) == {"a": 1}
    from whiletrue.rsi.harness2 import runmark

    runmark.mark_complete(tmp_path, run_id="r", harness=None)
    (tmp_path / "result.json").write_text("{ broken")
    assert rollout._reload(tmp_path, "base1", None) is None
    (tmp_path / "result.json").write_text(json.dumps({"port": "ok", "value": 1, "final": "x"}))
    assert rollout._reload(tmp_path, "base1", None) is None  # no change set saved


def test_session_cache_ignores_foreign_files(tmp_path: Path) -> None:
    (tmp_path / "result.json").write_text("[1, 2]")
    assert stagecache.load_cached(tmp_path, "k") is None
    (tmp_path / "result.json").write_text('{"key": "other"}')
    assert stagecache.load_cached(tmp_path, "k") is None
    assert stagecache.load_cached(tmp_path / "nowhere", "k") is None


def test_surface_stubs_and_subagent_modes() -> None:
    stubbed = replace(
        CC, stubs=((".claude/scripts/README.md", "notes\n"),), subagent_requires_mode=True
    )
    assert t.is_allowed(stubbed, ".claude/scripts/a.py")
    tree = {"CLAUDE.md": "x\n", ".claude/agents/r.md": "---\ndescription: d\n---\n"}
    with pytest.raises(t.TreeError, match="mode: subagent"):
        t.validate(stubbed, tree)


def test_stubs_are_written_and_hidden(tmp_path: Path) -> None:
    stubbed = replace(CC, stubs=((".claude/scripts/README.md", "notes\n"),))
    t.write_tree(stubbed, {"CLAUDE.md": "x\n"}, tmp_path)
    assert (tmp_path / ".claude/scripts/README.md").read_text() == "notes\n"
    t.write_tree(stubbed, {"CLAUDE.md": "x\n"}, tmp_path)
    assert t.read_tree(stubbed, tmp_path) == {"CLAUDE.md": "x\n"}


def test_library_field_reader_copes_with_missing_data(tmp_path: Path) -> None:
    assert lib._field(tmp_path / "none.md", "cue") == ""
    (tmp_path / "e.md").write_text("- **lesson**: l\n")
    assert (
        lib._field(tmp_path / "e.md", "cue") == ""
        and lib._field(tmp_path / "e.md", "lesson") == "l"
    )


def test_proposal_for_an_unchanged_harness_prints_a_short_note(tmp_path: Path) -> None:
    same = proposals.make(
        item_id="i",
        domain="d",
        surface_name="claude-code",
        base={"CLAUDE.md": "a"},
        chosen={"CLAUDE.md": "a"},
        chosen_label="base1",
        restricted=[],
        experience=lib.Experience(),
        why="",
        adopt="approve",
        ledger={},
        run_id="r",
    )
    assert not same.changed
