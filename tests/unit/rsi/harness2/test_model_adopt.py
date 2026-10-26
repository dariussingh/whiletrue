from pathlib import Path
from typing import Any

from tests.unit.rsi.harness2.conftest import (
    CAREFUL,
    Script,
    ScriptedRunner,
    make_project,
    registry_for,
)
from whiletrue.core import EventBus, MemoryStore, RunItem, parse_pipeline, run_pipeline
from whiletrue.rsi import apply_rsi
from whiletrue.rsi.harness2 import proposal as proposals
from whiletrue.rsi.harness2.library import Library
from whiletrue.rsi.harness2.surfaces import CLAUDE_CODE_SURFACE


async def run_model(project: Path, script: Script, extra: str = "") -> Any:
    runner = ScriptedRunner(script)
    registry = registry_for(runner)
    config = apply_rsi(
        parse_pipeline(
            f"rsi: {{ k: 1, budget_usd: 5, adopt: model{extra} }}\n"
            "pipeline:\n  - agent: { uses: ./agents/dev.md }\n",
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


async def test_a_model_approval_adopts_without_asking_a_person(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    script = Script()
    result = await run_model(project, script)
    assert result.items[0].state == "done"  # never parked at a human gate
    assert script.count("review") == 1
    assert CAREFUL in (project / "CLAUDE.md").read_text()
    proposal = proposals.load(project, "i1")
    assert proposal is not None and proposal.status == "adopted"
    assert proposal.model_decision == "approve" and "supported" in proposal.model_why
    library = Library(project / ".whiletrue/rsi/library", project.name, CLAUDE_CODE_SURFACE)
    assert library.entries() == ["i1"]


async def test_a_model_rejection_keeps_the_current_harness(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    script = Script(replies={"review": "<why>speculative</why>\n<decision>reject</decision>"})
    result = await run_model(project, script)
    assert result.items[0].state == "done"
    assert (project / "CLAUDE.md").read_text() == "Base rules.\n"
    proposal = proposals.load(project, "i1")
    assert proposal is not None and proposal.status == "recorded"
    assert (proposal.model_decision, proposal.model_why) == ("reject", "speculative")
    assert (project / "out.txt").read_text() == "careful"  # the work itself is still applied


async def test_anything_but_an_exact_approve_rejects(tmp_path: Path) -> None:
    for name, script in (
        ("rambled", Script(replies={"review": "Looks fine to me, approve it."})),
        ("failed", Script(fail_roles={"review"})),
    ):
        project = make_project(tmp_path / name)
        await run_model(project, script)
        proposal = proposals.load(project, "i1")
        assert proposal is not None and proposal.model_decision == "reject", name
        assert (project / "CLAUDE.md").read_text() == "Base rules.\n", name
    assert "reviewer failed" in proposals.load(tmp_path / "failed" / "proj", "i1").model_why  # type: ignore[union-attr]


async def test_a_model_cannot_approve_code_files_on_its_own(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    script = Script(extra_files={".claude/scripts/check.py": "print(1)\n"})
    result = await run_model(project, script)
    assert result.items[0].state == "parked"
    assert not (project / ".claude/scripts/check.py").exists()
    proposal = proposals.load(project, "i1")
    assert proposal is not None and proposal.model_decision == "approve"
    assert proposal.restricted == [".claude/scripts/check.py"]


async def test_nothing_is_reviewed_when_the_harness_did_not_change(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    script = Script(edits={"INV": "", "DOM": ""})

    def keep(role: str, cwd: Path) -> None:
        if role == "compose":
            (cwd / "context" / "harness" / "CLAUDE.md").write_text("Base rules.\n")

    script.hook = keep
    result = await run_model(project, script)
    assert result.items[0].state == "done" and script.count("review") == 0


async def test_the_reviewer_can_be_a_different_session(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    runner = ScriptedRunner(Script())
    registry = registry_for(runner)
    config = apply_rsi(
        parse_pipeline(
            "rsi: { k: 1, budget_usd: 5, adopt: model, reviewer: { model: opus, "
            "limits: { turns: 5, wall: 2m } } }\npipeline:\n  - agent: { uses: ./agents/dev.md }\n",
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
        items=[RunItem("i1", {"title": "T"})],
        now=lambda: 1000,
    )
    review = next(r for r in runner.requests if "reviewing ONE proposed change" in r.prompt)
    assert review.model == "opus" and review.limits is not None and review.limits.turns == 5
    assert review.allowed_tools == ["Read", "Glob", "Grep"]
