from pathlib import Path

import pytest

from tests.unit.rsi.harness2.conftest import (
    EXPERIENCE,
    Script,
    ScriptedRunner,
    make_project,
    rsi_yaml,
    run_yaml,
)
from whiletrue.core import RunItem
from whiletrue.rsi.harness2 import proposal as proposals
from whiletrue.rsi.harness2 import sessions
from whiletrue.rsi.harness2.library import Experience


def test_experience_blocks_are_parsed_field_by_field() -> None:
    exp = sessions.parse_experience(EXPERIENCE)
    assert (exp.cue, exp.confidence) == ("a config file changes", "medium")
    assert exp.lesson == "CLAUDE.md should warn about config files" and exp.complete
    assert "component: CLAUDE.md" in exp.edits
    wrapped = "<experience>cue: when X\nlesson: do Y\n  and then Z\nevidence: e</experience>"
    assert sessions.parse_experience(wrapped).lesson == "do Y and then Z"
    declined = "<edits>row</edits>\nNO_ENTRY"
    assert (
        not sessions.parse_experience(declined).complete
        and sessions.parse_experience(declined).edits == "row"
    )
    assert not sessions.parse_experience("nothing here").complete
    assert not sessions.parse_experience("<experience>cue: only</experience>").complete


async def test_a_selector_chooses_among_several_finals_blind(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    script = Script(replies={"select": "<why>closest to the request</why>\n```choice\nB\n```"})
    await run_yaml(project, rsi_yaml(k=2), script, items=[RunItem("i1", {"title": "T"})])
    proposal = proposals.load(project, "i1")
    assert script.count("select") == 1 and proposal is not None
    assert proposal.why == "closest to the request"
    candidates = list(
        (project / ".whiletrue/rsi/items/i1/stages/select/context/candidates").iterdir()
    )
    assert sorted(p.name for p in candidates) == ["A.md", "B.md"]
    assert not any("agg" in p.read_text().split("\n")[0] for p in candidates)


async def test_a_selector_that_fails_or_rambles_falls_back_to_a_base_output(tmp_path: Path) -> None:
    for name, script in (
        ("failed", Script(fail_roles={"select"})),
        ("rambled", Script(replies={"select": "I like them both"})),
    ):
        project = make_project(tmp_path / name)
        await run_yaml(project, rsi_yaml(k=2), script, items=[RunItem("i1", {"title": "T"})])
        proposal = proposals.load(project, "i1")
        assert proposal is not None, name
        assert proposal.chosen_label == "base1" and not proposal.changed, name
        assert (project / "out.txt").read_text() == "plain", name


async def test_a_reflection_that_fails_still_finishes_the_item(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    result, _, _ = await run_yaml(
        project, rsi_yaml(), Script(fail_roles={"reflect"}), items=[RunItem("i1", {"title": "T"})]
    )
    proposal = proposals.load(project, "i1")
    assert result.items[0].state == "done" and proposal is not None and proposal.experience == {}


async def test_an_unknown_improver_runner_fails_the_session_not_the_item(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    yaml = rsi_yaml(improver="{ runner: nope }")
    result, _, _ = await run_yaml(project, yaml, Script(), items=[RunItem("i1", {"title": "T"})])
    assert result.items[0].state == "done"
    proposal = proposals.load(project, "i1")
    assert proposal is not None and not proposal.changed


async def test_a_session_limit_set_in_config_reaches_the_runner(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runner = ScriptedRunner(Script())
    yaml = rsi_yaml(improver="{ limits: { budget_usd: 0.5, turns: 7, wall: 2m } }")
    await run_yaml(project, yaml, runner=runner, items=[RunItem("i1", {"title": "T"})])
    asked = [r for r in runner.requests if "MISSION" in r.prompt or "YOUR DIMENSION" in r.prompt]
    limits = asked[0].limits
    assert limits is not None and limits.turns == 7 and limits.wall_ms == 120_000
    assert limits.budget_usd == pytest.approx(0.5)
    assert asked[0].allowed_tools == ["Read", "Edit", "Write", "Glob", "Grep"]


def test_proposals_round_trip_and_list(tmp_path: Path) -> None:
    project = tmp_path
    assert proposals.load(project, "x") is None and proposals.all_items(project) == []
    made = proposals.make(
        item_id="org/repo#1",
        domain="d",
        surface_name="claude-code",
        base={"CLAUDE.md": "a\n"},
        chosen={"CLAUDE.md": "b\n"},
        chosen_label="agg1",
        restricted=[],
        experience=Experience(cue="c", lesson="l", evidence="e"),
        why="w",
        adopt="approve",
        ledger={"rollouts": 4},
        run_id="r",
    )
    assert made.status == "pending" and made.changed and made.lesson().complete
    proposals.save(project, made)
    again = proposals.load(project, "org/repo#1")
    assert again == made
    assert [p.item_id for p in proposals.all_items(project)] == ["org/repo#1"]
    proposals.set_status(project, made, "discarded")
    assert proposals.load(project, "org/repo#1").status == "discarded"  # type: ignore[union-attr]
    path = proposals.path_of(project, "org/repo#1")
    path.write_text("{ broken")
    assert proposals.load(project, "org/repo#1") is None
    never = proposals.make(
        item_id="i",
        domain="d",
        surface_name="s",
        base={},
        chosen={},
        chosen_label="base1",
        restricted=[],
        experience=Experience(),
        why="",
        adopt="never",
        ledger={},
        run_id="r",
    )
    assert never.status == "recorded" and not never.changed
