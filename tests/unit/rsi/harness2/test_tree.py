from pathlib import Path

import pytest

from whiletrue.rsi.harness2 import tree as t
from whiletrue.rsi.harness2.surfaces import CLAUDE_CODE_SURFACE as CC
from whiletrue.rsi.harness2.surfaces import CODEX_SURFACE as CX

SKILL = "---\nname: fix\ndescription: when a test fails\n---\nbody\n"


def good_tree() -> t.Tree:
    return {
        "CLAUDE.md": "Be brief. When a test fails, use the `fix` skill.\n",
        ".claude/skills/fix/SKILL.md": SKILL,
    }


def test_normalize_rejects_escapes_and_tidies() -> None:
    assert t.normalize_rel("./a/b/") == "a/b"
    assert t.normalize_rel("a\\b") == "a/b"
    for bad in ("/etc/passwd", "../x", "a/../b"):
        with pytest.raises(t.TreeError):
            t.normalize_rel(bad)


def test_is_allowed_knows_the_claude_code_surface() -> None:
    for rel in (
        "CLAUDE.md",
        ".claude/rules/guardrails.md",
        ".claude/skills/fix/SKILL.md",
        ".claude/skills/fix/scripts/run.sh",
        ".claude/agents/reviewer.md",
        ".claude/scripts/check.py",
    ):
        assert t.is_allowed(CC, rel), rel
    for rel in (
        "src/app.py",
        ".claude/settings.json",
        "../CLAUDE.md",
        ".claude/skills/Bad/SKILL.md",
    ):
        assert not t.is_allowed(CC, rel), rel


def test_codex_surface_has_hooks_and_no_subagents() -> None:
    assert t.is_allowed(CX, ".codex/hooks.json")
    assert not t.is_allowed(CX, "sub_agents/x/AGENT.md")
    assert t.subagent_names(CX, {"sub_agents/x/AGENT.md": ""}) == []


def test_read_and_write_round_trip(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x = 1\n")
    t.write_tree(CC, good_tree(), tmp_path)
    rejected: list[str] = []
    assert t.read_tree(CC, tmp_path, rejected=rejected) == good_tree()
    assert rejected == ["src/app.py"]
    assert (tmp_path / "src" / "app.py").read_text() == "x = 1\n"
    assert t.is_tree(CC, tmp_path)
    assert not t.is_tree(CC, tmp_path / "missing")
    assert t.read_tree(CC, tmp_path / "missing") == {}


def test_write_replaces_editable_files_and_creates_empty_always_on(tmp_path: Path) -> None:
    t.write_tree(CC, good_tree(), tmp_path)
    t.write_tree(CC, {"CLAUDE.md": "new\n"}, tmp_path)
    assert not (tmp_path / ".claude/skills/fix/SKILL.md").exists()
    assert not (tmp_path / ".claude/rules/guardrails.md").exists()
    assert t.read_tree(CC, tmp_path) == {"CLAUDE.md": "new\n"}
    t.write_tree(CC, {"CLAUDE.md": "new\n"}, tmp_path, create_always_on=True)
    assert (tmp_path / ".claude/rules/guardrails.md").read_text() == ""


def test_validate_accepts_a_good_tree() -> None:
    census = t.validate(CC, good_tree())
    assert (census.skills, census.sub_agents, census.scripts) == (1, 0, 0)
    assert census.n_files == 2


def test_validate_lists_every_problem() -> None:
    bad = {
        "CLAUDE.md": "x" * 30_000,
        ".claude/skills/fix/SKILL.md": "no frontmatter",
        ".claude/agents/r.md": "no description",
        ".claude/scripts/a.py": "def broken(:\n",
    }
    with pytest.raises(t.TreeError) as info:
        t.validate(CC, bad)
    message = str(info.value)
    for fragment in (
        "over the 24000 cap",
        "needs a `description:`",
        "matching its directory",
        "never named in the always-on text",
        "does not compile",
    ):
        assert fragment in message


def test_validate_flags_scripts_that_are_named_but_missing() -> None:
    tree = {"CLAUDE.md": "Run `python3 .claude/scripts/lint.py` first.\n"}
    with pytest.raises(t.TreeError, match="not in the harness"):
        t.validate(CC, tree)
    tree[".claude/scripts/lint.py"] = "print(1)\n"
    assert t.validate(CC, tree).scripts == 1
    reserved = {"CLAUDE.md": "Run `python3 .claude/scripts/pack.py` first.\n"}
    assert t.validate(CC, reserved).scripts == 0


def test_validate_subagents_can_be_disallowed() -> None:
    tree = {"CLAUDE.md": "x\n", ".claude/agents/r.md": "---\ndescription: d\n---\n"}
    assert t.validate(CC, tree).sub_agents == 1
    with pytest.raises(t.TreeError, match="not available"):
        t.validate(CC, tree, allow_subagents=False)


def test_codex_skill_name_override_and_empty_always_on() -> None:
    skill = "---\nname: other\ndescription: d\n---\n"
    with pytest.raises(t.TreeError, match="OVERRIDES the directory name"):
        t.validate(CX, {"AGENTS.md": "use fix", ".agents/skills/fix/SKILL.md": skill})
    with pytest.raises(t.TreeError, match="is empty"):
        t.validate(CX, {"AGENTS.md": "  \n"})


def test_codex_hooks_are_checked() -> None:
    def errors(source: str) -> list[str]:
        found: list[str] = []
        t.check_hooks(CX, source, found)
        return found

    assert errors("not json")[0].endswith(
        "is not valid JSON: Expecting value: line 1 column 1 (char 0)"
    )
    assert "needs a top-level" in errors("{}")[0]
    assert "unknown event" in errors('{"hooks": {"Nope": []}}')[0]
    assert "LIST" in errors('{"hooks": {"Stop": {}}}')[0]
    assert "needs a `hooks` list" in errors('{"hooks": {"Stop": [1]}}')[0]
    assert 'must be "command"' in errors('{"hooks": {"Stop": [{"hooks": [{"type": "x"}]}]}}')[0]
    assert "empty `command`" in errors('{"hooks": {"Stop": [{"hooks": [{"type": "command"}]}]}}')[0]
    ok = '{"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "true"}]}]}}'
    assert errors(ok) == []
    census = t.validate(CX, {"AGENTS.md": "x", ".codex/hooks.json": ok})
    assert census.hooks == 1


def test_autotrigger_names_unmentioned_skills_within_the_cap() -> None:
    tree = {"CLAUDE.md": "Be brief.", ".claude/skills/fix/SKILL.md": SKILL}
    assert t.autotrigger(CC, tree) == ["fix"]
    assert "use the `fix` skill" in tree["CLAUDE.md"]
    assert t.autotrigger(CC, tree) == []
    tree2 = {"CLAUDE.md": "x" * 100, ".claude/skills/fix/SKILL.md": "---\nname: fix\n---\n"}
    assert t.autotrigger(CC, tree2, always_on_max=100) == []
    tree3 = {".claude/skills/fix/SKILL.md": "---\nname: fix\n---\n"}
    assert t.autotrigger(CC, tree3) == ["fix"]
    assert "the fix workflow applies" in tree3["CLAUDE.md"]


def test_diff_and_restricted_changes() -> None:
    before = good_tree()
    after = {**good_tree(), "CLAUDE.md": "changed " * 3, ".claude/scripts/x.py": "print(1)\n"}
    del after[".claude/skills/fix/SKILL.md"]
    d = t.diff_trees(CC, before, after)
    assert d.added == [".claude/scripts/x.py"]
    assert d.removed == [".claude/skills/fix/SKILL.md"]
    assert d.modified == ["CLAUDE.md"]
    assert d.paths == sorted(d.added + d.removed + d.modified)
    assert not d.empty and t.diff_trees(CC, before, before).empty
    assert t.restricted_changes(CC, before, after) == [".claude/scripts/x.py"]


def test_diversity_shows_collapse() -> None:
    same = {"a": {"f": "x\ny\n"}, "b": {"f": "x\ny\n"}}
    assert t.diversity(same) == (1, 1.0)
    differ = {"a": {"f": "x\ny\n"}, "b": {"f": "x\nz\n"}}
    distinct, similarity = t.diversity(differ)
    assert distinct == 2 and 0 < similarity < 1
    assert t.diversity({"a": {"f": "x"}}) == (1, 1.0)
    assert t.diversity({"a": {}, "b": {}}) == (1, 1.0)
