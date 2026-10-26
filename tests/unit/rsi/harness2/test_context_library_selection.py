from pathlib import Path

import pytest

from whiletrue.rsi.harness2 import context, selection
from whiletrue.rsi.harness2 import tree as t
from whiletrue.rsi.harness2.library import Experience, Library, domain_dir, slug
from whiletrue.rsi.harness2.surfaces import CLAUDE_CODE_SURFACE as CC

HARNESS: t.Tree = {
    "CLAUDE.md": "Be brief. Use the `fix` skill.\n",
    ".claude/skills/fix/SKILL.md": "---\nname: fix\ndescription: d\n---\n",
}
EXP = Experience(cue="a test fails", lesson="read the failure first", evidence="run 2, step 4")


def rollout(label: str, *, ok: bool = True) -> context.RolloutView:
    return context.RolloutView(label, ok, f"# {label}\nstep 1\n", f"{label} changed a.py")


def test_build_writes_only_what_exists_and_describes_it(tmp_path: Path) -> None:
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "INDEX.md").write_text("# lib\n")
    cand = context.PoolEntry("inv1", {**HARNESS, "CLAUDE.md": "Be very brief.\n"}, rollout("inv1"))
    built = context.build(
        tmp_path / "context",
        CC,
        item_id="42",
        task="Fix the bug.",
        rollouts=[rollout("base1"), rollout("base2", ok=False)],
        harness=HARNESS,
        library_dir=lib,
        pool=[cand, context.PoolEntry("dom1", HARNESS)],
        decisions={"c1": "kept -> x", "c2": ""},
    )
    root = built.root
    assert (root / "task.md").read_text() == "Fix the bug."
    assert (root / "rollouts/base1.md").is_file() and (root / "deliverables/base2.md").is_file()
    assert (root / "harness/CLAUDE.md").read_text() == HARNESS["CLAUDE.md"]
    assert "Be very brief" in (root / "pool/inv1/harness.diff").read_text()
    assert (root / "pool/inv1/rollout.md").is_file() and not (
        root / "pool/dom1/rollout.md"
    ).exists()
    assert (root / "library/INDEX.md").is_file()
    assert "No decision notes" in (root / "decisions/c2.md").read_text()
    assert built.failed == ["base2"] and built.pool == ["inv1", "dom1"] and built.decisions == 2
    layout = built.layout
    for part in ("task.md", "decisions/", "rollouts/", "harness/", "pool/", "library/"):
        assert part in layout
    text = context.mission(built, job="Improve it.")
    assert text.startswith("Improve it.") and layout in text


def test_layout_omits_what_is_absent_and_truncates_long_lists(tmp_path: Path) -> None:
    built = context.build(tmp_path / "c", CC, item_id="1", task="t")
    assert built.layout == "context/task.md                  the task as the agent received it"
    many = context.build(
        tmp_path / "c2", CC, item_id="1", task="t", rollouts=[rollout(f"r{i}") for i in range(8)]
    )
    assert "(+2 more)" in many.layout


def test_an_existing_destination_is_replaced(tmp_path: Path) -> None:
    dest = tmp_path / "c"
    dest.mkdir()
    (dest / "stale.md").write_text("old")
    context.build(dest, CC, item_id="1", task="t")
    assert not (dest / "stale.md").exists()


def test_a_gate_verdict_in_evidence_is_refused(tmp_path: Path) -> None:
    leaky = context.RolloutView("base1", True, "the gate.tests step failed", "")
    with pytest.raises(context.LeakError, match="gate.tests"):
        context.build(tmp_path / "c", CC, item_id="1", task="t", rollouts=[leaky])
    named = tmp_path / "n"
    (named / "gate.shell-output").mkdir(parents=True)
    (named / "gate.shell-output" / "x.txt").write_text("x")
    with pytest.raises(context.LeakError, match="matches"):
        context.assert_no_verdicts(named)
    context.assert_no_verdicts(tmp_path / "missing")
    context.assert_no_verdicts(tmp_path, markers=())
    binary = tmp_path / "b"
    binary.mkdir()
    (binary / "x.bin").write_bytes(b"gate.shell")
    context.assert_no_verdicts(binary)


def test_a_harness_tree_with_a_verdict_cannot_be_stored() -> None:
    context.assert_tree_has_no_verdicts(HARNESS)
    with pytest.raises(context.LeakError, match="name"):
        context.assert_tree_has_no_verdicts({"gate.tests.md": "x"})
    with pytest.raises(context.LeakError, match="contains"):
        context.assert_tree_has_no_verdicts({"CLAUDE.md": "see gate.shell"})
    context.assert_tree_has_no_verdicts({"data.bin": "gate.shell"})


def test_the_library_stores_and_indexes_adopted_harnesses(tmp_path: Path) -> None:
    library = Library(tmp_path, "backend / api", CC)
    assert not library.exists() and library.context_dir() is None and library.entries() == []
    assert library.add("1", HARNESS, Experience(cue="x")) is None
    folder = library.add("PR/7", HARNESS, EXP, why="best", label="agg1", run_id="r9")
    assert folder is not None and library.entries() == ["PR__7"]
    assert library.tree_of("PR/7") == HARNESS
    text = (folder / "EXPERIENCE.md").read_text()
    assert "adopted: `agg1` (run `r9`)" in text and "**lesson**: read the failure first" in text
    index = library.index.read_text()
    assert "backend / api" in index and "cue:     a test fails" in index and "1 skills" in index
    assert library.context_dir() == library.dir
    assert library.dir.name == "backend__api"
    library.add("2", {"CLAUDE.md": "x\n"}, EXP)
    assert "always-on edits only" in library.index.read_text()
    library.forget(["PR/7", "missing"])
    assert library.entries() == ["2"]
    library.forget(["nope"])
    library.forget(["2"])
    assert "empty" in library.index.read_text()


def test_the_library_refuses_a_harness_with_a_verdict(tmp_path: Path) -> None:
    library = Library(tmp_path, "d", CC)
    with pytest.raises(context.LeakError):
        library.add("1", {"CLAUDE.md": "gate.tests"}, EXP)
    with pytest.raises(context.LeakError):
        library.add("1", HARNESS, Experience(cue="c", lesson="l", evidence="gate.shell"))


def test_domain_and_slug_helpers() -> None:
    assert domain_dir(" a / b ") == "a__b" and domain_dir("") == "unknown"
    assert slug("a/b") == "a__b"


def test_anonymous_ids_are_stable_and_unbiased_by_label_order() -> None:
    a = selection.assign_ids(["agg2", "agg1"], item_id="7", k=2, mode="parallel")
    b = selection.assign_ids(["agg1", "agg2"], item_id="7", k=2, mode="parallel")
    assert a == b and set(a) == {"A", "B"} and set(a.values()) == {"agg1", "agg2"}
    assert [selection.anon_id(i) for i in (0, 25, 26, 27, 701, 702)] == [
        "A",
        "Z",
        "AA",
        "AB",
        "ZZ",
        "AAA",
    ]


def test_the_pool_is_exactly_the_finals() -> None:
    assert selection.final_labels("parallel", 2) == ["agg1", "agg2"]
    assert selection.final_labels("sequential", 3) == ["c1", "c2", "c3"]
    rolls = {"base1": "b", "agg1": "x", "agg2": "y", "inv1": "z"}
    assert selection.selection_pool("parallel", 2, rolls) == {"agg1": "x", "agg2": "y"}
    assert selection.selection_pool("sequential", 2, rolls) == {}
    assert selection.is_base("base1") and not selection.is_base("agg1")


def test_choices_and_blocks_are_parsed_from_the_last_occurrence() -> None:
    reply = "<why>first</why>\n```choice\nA\n```\nmore\n<why>second</why> ```choice B```"
    assert selection.parse_choice(reply, ["A", "B"]) == "B"
    assert selection.parse_choice(reply, ["A"]) is None
    assert selection.parse_choice("no block", ["A"]) is None
    assert selection.parse_block(reply, "why") == "second"
    assert selection.parse_block(reply, "edits") == ""
