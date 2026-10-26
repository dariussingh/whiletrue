import json
from pathlib import Path

from tests.unit.rsi.harness2.conftest import (
    CAREFUL,
    TESTS_FIRST,
    Script,
    make_project,
    rsi_yaml,
    run_yaml,
)
from whiletrue.core import RunItem
from whiletrue.rsi.harness2 import proposal as proposals
from whiletrue.rsi.harness2.surfaces import CLAUDE_CODE_SURFACE
from whiletrue.rsi.harness2.tree import read_tree


async def test_one_parallel_step_runs_the_whole_cycle_and_applies_the_winner(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    script = Script()
    result, _, _ = await run_yaml(
        project, rsi_yaml(), script, items=[RunItem("i1", {"title": "T"})]
    )

    assert result.status == "done"
    assert (result.items[0].state, result.items[0].port) == ("done", "ok")
    # R = 4k rollouts, P = 3k sessions
    assert script.count("rollout") == 4
    assert [script.count(r) for r in ("propose-INV", "propose-DOM", "compose", "reflect")] == [
        1,
        1,
        1,
        1,
    ]
    assert script.count("select") == 0  # one final: nothing to choose
    # the chosen rollout's file changes landed in the item's own workspace
    assert (project / "out.txt").read_text() == "careful"
    # but the harness itself is only a proposal until someone adopts it
    assert (project / "CLAUDE.md").read_text() == "Base rules.\n"
    proposal = proposals.load(project, "i1")
    assert proposal is not None and proposal.status == "recorded"
    assert proposal.chosen_label == "agg1" and proposal.changed
    assert CAREFUL in proposal.chosen["CLAUDE.md"] and TESTS_FIRST in proposal.chosen["CLAUDE.md"]
    assert proposal.ledger["rollouts"] == 4 and proposal.ledger["sessions"] == 3
    assert proposal.experience["cue"] == "a config file changes"
    assert proposal.restricted == [] and "+BE CAREFUL" in proposal.diff
    assert proposal.library == str(project / ".whiletrue/rsi/library")
    # every rollout ran in its own copy, never in the item's directory
    rollout_dirs = {cwd for role, cwd in script.calls if role == "rollout"}
    assert str(project) not in rollout_dirs and len(rollout_dirs) == 4


async def test_each_rollout_sees_only_its_own_harness(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    await run_yaml(project, rsi_yaml(), Script(), items=[RunItem("i1", {"title": "T"})])
    rollouts = project / ".whiletrue/rsi/items/i1/rollouts"
    seen = {p.name: (p / "ws" / "out.txt").read_text() for p in sorted(rollouts.iterdir())}
    assert seen == {"base1": "plain", "inv1": "plain", "dom1": "careful", "agg1": "careful"}
    assert "wrote careful" in (rollouts / "dom1" / "trajectory.md").read_text()
    assert json.loads((rollouts / "base1" / "result.json").read_text())["port"] == "ok"


async def test_sequential_mode_spends_the_same_budget_in_rounds(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    script = Script()
    result, _, _ = await run_yaml(
        project, rsi_yaml(k=2, mode="sequential"), script, items=[RunItem("i1", {"title": "T"})]
    )
    assert result.items[0].state == "done"
    assert script.count("rollout") == 8 and script.count("compose") == 2
    assert script.count("propose-INV") == 2 and script.count("propose-DOM") == 2
    assert script.count("select") == 1  # two finals: a selector chooses
    proposal = proposals.load(project, "i1")
    assert proposal is not None and proposal.ledger["rollouts"] == 8
    assert proposal.ledger["sessions"] == 6 and proposal.chosen_label in {"c1", "c2"}
    assert proposal.ledger["mode"] == "sequential"


async def test_a_second_run_reuses_finished_rollouts_and_sessions(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    first = Script()
    await run_yaml(project, rsi_yaml(), first, items=[RunItem("i1", {"title": "T"})])
    (project / "out.txt").unlink()
    second = Script()
    result, _, _ = await run_yaml(
        project, rsi_yaml(), second, items=[RunItem("i1", {"title": "T"})]
    )
    assert result.items[0].state == "done"
    assert second.count("rollout") == 0 and second.count("propose-DOM") == 0
    assert second.count("compose") == 0 and second.count("reflect") == 0
    assert (project / "out.txt").read_text() == "careful"  # the cached winner is applied again


async def test_an_invalid_harness_gets_one_retry_with_the_problem(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    script = Script(invalid_first=1)
    await run_yaml(project, rsi_yaml(), script, items=[RunItem("i1", {"title": "T"})])
    assert script.count("propose-INV") + script.count("propose-DOM") == 3  # one of them retried
    retry = [r for r in script.calls if r[0].startswith("propose")]
    assert len(retry) == 3
    proposal = proposals.load(project, "i1")
    assert proposal is not None and ".claude/skills/orphan/SKILL.md" not in proposal.chosen
    assert proposal.ledger["sessions"] == 3


async def test_a_harness_that_stays_invalid_is_inherited(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    script = Script(invalid_first=99)
    result, _, _ = await run_yaml(
        project, rsi_yaml(), script, items=[RunItem("i1", {"title": "T"})]
    )
    assert result.items[0].state == "done"
    proposal = proposals.load(project, "i1")
    assert proposal is not None
    assert "orphan" not in " ".join(proposal.chosen)
    assert script.count("propose-INV") + script.count("propose-DOM") == 4  # two tries each


async def test_a_failed_session_inherits_the_current_harness(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    script = Script(fail_roles={"propose-DOM"})
    result, _, _ = await run_yaml(
        project, rsi_yaml(), script, items=[RunItem("i1", {"title": "T"})]
    )
    assert result.items[0].state == "done"
    proposal = proposals.load(project, "i1")
    assert proposal is not None and CAREFUL not in proposal.chosen.get("CLAUDE.md", "")


async def test_the_budget_parks_the_item_before_overspending(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    script = Script(cost=1.0)
    result, _, _ = await run_yaml(
        project, rsi_yaml(budget_usd=2), script, items=[RunItem("i1", {"title": "T"})]
    )
    assert result.items[0].state == "parked"
    assert "out.txt" not in [p.name for p in project.iterdir()]
    assert script.count("rollout") <= 2


async def test_when_false_runs_the_steps_plainly(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    script = Script()
    yaml = rsi_yaml(when='"false"')
    result, _, _ = await run_yaml(project, yaml, script, items=[RunItem("i1", {"title": "T"})])
    assert result.items[0].state == "done"
    assert script.calls == [("rollout", str(project))]
    assert (project / "out.txt").read_text() == "plain"
    assert proposals.load(project, "i1") is None


async def test_nothing_delivered_fails_the_step(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    script = Script(fail_roles={"rollout"})
    result, _, _ = await run_yaml(
        project, rsi_yaml(), script, items=[RunItem("i1", {"title": "T"})]
    )
    assert result.items[0].state == "parked"
    assert proposals.load(project, "i1") is None
    assert not (project / "out.txt").exists()


async def test_agent_edits_to_the_harness_inside_a_rollout_are_not_applied(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    def hook(role: str, cwd: Path) -> None:
        if role == "rollout":
            (cwd / "CLAUDE.md").write_text("the agent rewrote its own rules\n")

    await run_yaml(project, rsi_yaml(), Script(hook=hook), items=[RunItem("i1", {"title": "T"})])
    assert (project / "CLAUDE.md").read_text() == "Base rules.\n"
    assert read_tree(CLAUDE_CODE_SURFACE, project) == {"CLAUDE.md": "Base rules.\n"}
