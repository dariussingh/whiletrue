from pathlib import Path
from typing import Any

import pytest

from tests.unit.rsi.harness2.conftest import (
    CAREFUL,
    Script,
    ScriptedRunner,
    make_project,
    registry_for,
)
from whiletrue.core import (
    EventBus,
    MemoryStore,
    RunItem,
    parse_pipeline,
    run_pipeline,
    validate_pipeline,
)
from whiletrue.core.validate import has_errors
from whiletrue.rsi import SettingError, apply_rsi
from whiletrue.rsi.harness2 import proposal as proposals
from whiletrue.rsi.harness2.config import Harness2Config, parse_setting
from whiletrue.rsi.harness2.library import Library
from whiletrue.rsi.harness2.surfaces import CLAUDE_CODE_SURFACE

PIPELINE = """
rsi: {rsi}
pipeline:
  - id: prep
    shell: {{ run: "true" }}
  - id: work
    agent: {{ uses: ./agents/dev.md }}
  - id: check
    gate.shell: {{ run: "true" }}
  - id: ship
    shell: {{ run: "true" }}
"""


def load(rsi: str) -> Any:
    runner = ScriptedRunner(Script())
    registry = registry_for(runner)
    config = parse_pipeline(PIPELINE.format(rsi=rsi), "w.yaml")
    return registry, config


def ids(config: Any) -> list[str]:
    return [child.id for child in config.root.children]


def test_rsi_off_changes_nothing() -> None:
    registry, config = load("false")
    assert apply_rsi(config, registry) is config
    registry2, config2 = load("true")
    config2.extra.pop("rsi")
    assert apply_rsi(config2, registry2) is config2


def test_rsi_true_wraps_the_steps_up_to_the_last_agent() -> None:
    registry, config = load("true")
    applied = apply_rsi(config, registry)
    assert ids(applied) == [
        "prep", "rsi", "rsi_review", "rsi_adopt", "rsi_discard", "check", "ship"
    ]  # fmt: skip
    wrapper = applied.root.children[1]
    assert [c.id for c in wrapper.children] == ["work"]
    assert wrapper.config["budget_is_default"] is True
    review = applied.root.children[2]
    assert review.wires == {"rejected": "rsi_discard"}
    assert (
        review.children[0].use == "human.approve" and review.children[0].config["what"] == "harness"
    )
    assert config.root.children[0].id == "prep"  # the original is untouched


def test_the_adopt_policy_decides_which_steps_follow() -> None:
    registry, config = load("{ adopt: auto, budget_usd: 3 }")
    assert ids(apply_rsi(config, registry)) == ["prep", "rsi", "rsi_adopt", "check", "ship"]
    registry, config = load("{ adopt: never, budget_usd: 3 }")
    applied = apply_rsi(config, registry)
    assert ids(applied) == ["prep", "rsi", "check", "ship"]
    assert applied.root.children[1].config.get("budget_is_default") is None


def test_a_named_scope_is_used_and_checked() -> None:
    registry, config = load("{ scope: [work], budget_usd: 3, adopt: never }")
    applied = apply_rsi(config, registry)
    assert ids(applied) == ["prep", "rsi", "check", "ship"]
    for bad, message in (
        ("{ scope: [nope] }", "unknown top-level"),
        ("{ scope: [prep, check] }", "consecutive"),
        ("{ scope: [ship] }", "side effects"),
        ("{ scope: [check] }", "contains no agent step"),
        ("{ scope: 3 }", "list of step ids"),
        ("5", "true, false, or a mapping"),
        ("{ k: 0 }", "rsi.k"),
    ):
        registry, config = load(bad)
        with pytest.raises(SettingError, match=message):
            apply_rsi(config, registry)


def test_the_scope_may_not_be_crossed_by_wires() -> None:
    yaml = """
rsi: { budget_usd: 3, adopt: never }
pipeline:
  - id: work
    agent: { uses: ./agents/dev.md }
    wires: { fail: check }
  - id: check
    gate.shell: { run: "true" }
"""
    runner = ScriptedRunner(Script())
    config = parse_pipeline(yaml, "w.yaml")
    with pytest.raises(SettingError, match="crosses the `rsi` scope"):
        apply_rsi(config, registry_for(runner))


def test_a_single_strategy_root_is_wrapped_whole() -> None:
    yaml = """
rsi: { budget_usd: 3, adopt: never }
pipeline:
  retry:
    max: 2
    do:
      - agent: { uses: ./agents/dev.md }
"""
    runner = ScriptedRunner(Script())
    applied = apply_rsi(parse_pipeline(yaml, "w.yaml"), registry_for(runner))
    assert ids(applied) == ["rsi"]
    assert applied.root.children[0].children[0].id == "rsi_scope"
    unsafe = parse_pipeline(
        "rsi: true\npipeline:\n  sequence:\n    do:\n      - shell: { run: x }\n", "w.yaml"
    )
    unsafe.root.children[0].use = "shell"
    with pytest.raises(SettingError, match="no agent step"):
        apply_rsi(unsafe, registry_for(runner))
    with pytest.raises(SettingError, match="reserved"):
        apply_rsi(
            parse_pipeline("rsi: true\npipeline:\n  - id: rsi\n    agent: { uses: x }\n", "w.yaml"),
            registry_for(runner),
        )


def test_settings_parse_and_default() -> None:
    assert parse_setting(None) is None and parse_setting(False) is None
    on = parse_setting(True)
    assert on is not None and on.harness2.budget_usd == 10.0 and on.default_budget
    full = parse_setting({"k": 2, "mode": "sequential", "budget_usd": 7, "scope": ["a"]})
    assert full is not None and full.harness2.k == 2 and not full.default_budget
    assert full.scope == ["a"] and full.harness2.selector_spec() is full.harness2.improver
    assert full.harness2.reflector_spec() is full.harness2.improver
    profile = Harness2Config(profile={"deliverable_word": "patch"}).profile.build()  # type: ignore[arg-type]
    assert profile.deliverable_word == "patch" and "software repository" in profile.work


async def test_the_whole_pipeline_runs_with_approval_adoption(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runner = ScriptedRunner(Script())
    registry = registry_for(runner)
    config = apply_rsi(
        parse_pipeline(
            "rsi: { k: 1, budget_usd: 5 }\npipeline:\n  - agent: { uses: ./agents/dev.md }\n",
            str(project / "w.yaml"),
        ),
        registry,
    )
    store = MemoryStore()
    result = await run_pipeline(
        registry=registry,
        config=config,
        store=store,
        bus=EventBus(),
        cwd=str(project),
        items=[RunItem("i1", {"title": "T"})],
        now=lambda: 1000,
    )
    # the review gate parks the item until a person decides
    assert result.items[0].state == "parked"
    proposal = proposals.load(project, "i1")
    assert proposal is not None and proposal.status == "pending"
    assert (project / "CLAUDE.md").read_text() == "Base rules.\n"
    assert (project / "out.txt").read_text() == "careful"


async def run_with_policy(project: Path, policy: str) -> Any:
    runner = ScriptedRunner(Script(edits={"INV": "Run the tests first.", "DOM": CAREFUL}))
    registry = registry_for(runner)
    config = apply_rsi(
        parse_pipeline(
            f"rsi: {{ k: 1, budget_usd: 5, adopt: {policy} }}\npipeline:\n  - agent: {{ uses: ./agents/dev.md }}\n",
            str(project / "w.yaml"),
        ),
        registry,
    )
    return await run_pipeline(
        registry=registry,
        config=config,
        store=MemoryStore(),
        bus=EventBus(),
        cwd=str(project),
        items=[RunItem("i1", {"title": "T"})],
        now=lambda: 1000,
    )


async def test_auto_adoption_writes_the_harness_and_stores_the_lesson(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    result = await run_with_policy(project, "auto")
    assert result.items[0].state == "done"
    assert CAREFUL in (project / "CLAUDE.md").read_text()
    proposal = proposals.load(project, "i1")
    assert proposal is not None and proposal.status == "adopted"
    library = Library(project / ".whiletrue/rsi/library", project.name, CLAUDE_CODE_SURFACE)
    assert library.entries() == ["i1"]
    assert "CLAUDE.md should warn" in (library.entry_dir("i1") / "EXPERIENCE.md").read_text()


async def test_auto_adoption_refuses_paths_that_execute_code(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runner = ScriptedRunner(Script(extra_files={".claude/scripts/check.py": "print(1)\n"}))
    registry = registry_for(runner)
    config = apply_rsi(
        parse_pipeline(
            "rsi: { k: 1, budget_usd: 5, adopt: auto }\npipeline:\n  - agent: { uses: ./agents/dev.md }\n",
            str(project / "w.yaml"),
        ),
        registry,
    )
    result = await run_pipeline(
        registry=registry,
        config=config,
        store=MemoryStore(),
        bus=EventBus(),
        cwd=str(project),
        items=[RunItem("i1", {"title": "T"})],
        now=lambda: 1000,
    )
    assert result.items[0].state == "parked"
    assert not (project / ".claude/scripts/check.py").exists()
    proposal = proposals.load(project, "i1")
    assert proposal is not None and proposal.restricted == [".claude/scripts/check.py"]
    assert proposal.status == "pending"


async def test_never_only_records_the_proposal(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    result = await run_with_policy(project, "never")
    assert result.items[0].state == "done"
    assert (project / "CLAUDE.md").read_text() == "Base rules.\n"
    proposal = proposals.load(project, "i1")
    assert proposal is not None and proposal.status == "recorded"


async def test_a_second_item_reads_the_first_items_lesson(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    await run_with_policy(project, "auto")
    seen: list[bool] = []

    def hook(role: str, cwd: Path) -> None:
        if role.startswith("propose"):
            seen.append((cwd / "context" / "library" / "INDEX.md").is_file())

    runner = ScriptedRunner(Script(hook=hook))
    registry = registry_for(runner)
    config = apply_rsi(
        parse_pipeline(
            "rsi: { k: 1, budget_usd: 5, adopt: never }\npipeline:\n  - agent: { uses: ./agents/dev.md }\n",
            str(project / "w.yaml"),
        ),
        registry,
    )
    await run_pipeline(
        registry=registry,
        config=config,
        store=MemoryStore(),
        bus=EventBus(),
        cwd=str(project),
        items=[RunItem("i2", {"title": "T2"})],
        now=lambda: 1000,
    )
    assert seen == [True, True]


def test_validation_explains_what_it_costs_and_refuses_unsafe_scopes(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runner = ScriptedRunner(Script())
    registry = registry_for(runner)

    def diagnostics(yaml: str) -> list[str]:
        config = apply_rsi(parse_pipeline(yaml, str(project / "w.yaml")), registry)
        return [
            f"{d.severity}: {d.message}"
            for d in validate_pipeline(config, registry, cwd=str(project))
        ]

    good = diagnostics("rsi: true\npipeline:\n  - agent: { uses: ./agents/dev.md }\n")
    assert any("7x one agent run" in line and "$10.00" in line for line in good)
    assert any("sets no budget" in line for line in good)
    assert not any(line.startswith("error") for line in good)

    explicit = diagnostics(
        "rsi: { budget_usd: 2, k: 2 }\npipeline:\n  - agent: { uses: ./agents/dev.md }\n"
    )
    assert any("14x one agent run" in line and "$2.00" in line for line in explicit)
    assert not any("sets no budget" in line for line in explicit)

    direct = """
pipeline:
  - id: w
    rsi.harness2: { budget_usd: 3, improver: { runner: nope }, when: "bogus > 1" }
    do:
      - shell: { run: x }
      - github.merge: { repo: a/b, method: squash }
      - human.approve: {}
      - agent: { uses: ./agents/dev.md, runner: opencode }
"""
    config = parse_pipeline(direct, str(project / "w.yaml"))
    messages = [d.message for d in validate_pipeline(config, registry, cwd=str(project))]
    joined = "\n".join(messages)
    assert has_errors(validate_pipeline(config, registry, cwd=str(project)))
    for fragment in ('unknown runner "nope"', "`rsi.when`", "cannot run inside `rsi.harness2`"):
        assert fragment in joined


def test_an_agent_on_another_framework_warns(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    (project / "agents" / "codex.md").write_text(
        "---\nagent: cx\nrunner: codex\nlimits: { turns: 5, wall: 1m }\n---\nx\n"
    )
    runner = ScriptedRunner(Script())
    registry = registry_for(runner)
    yaml = "pipeline:\n  - id: w\n    rsi.harness2: { budget_usd: 3 }\n    do:\n      - agent: { uses: ./agents/codex.md }\n"
    config = parse_pipeline(yaml, str(project / "w.yaml"))
    text = "\n".join(
        f"{d.severity}: {d.message}" for d in validate_pipeline(config, registry, cwd=str(project))
    )
    assert "does not exist" in text or "Unknown runner" in text or "`rsi.surface`" in text
