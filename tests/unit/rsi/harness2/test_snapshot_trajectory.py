from pathlib import Path

import pytest

from whiletrue.rsi.harness2 import snapshot as sn
from whiletrue.rsi.harness2 import trajectory as tj


def project(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("a = 1\n")
    (root / "README.md").write_text("hi\n")
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref")
    (root / ".whiletrue").mkdir()
    (root / ".whiletrue" / "state.db").write_text("x")
    (root / "node_modules" / "pkg").mkdir(parents=True)
    (root / "node_modules" / "pkg" / "i.js").write_text("x")
    (root / "src" / "__pycache__").mkdir()
    (root / "src" / "__pycache__" / "a.pyc").write_bytes(b"\0")
    return root


def test_a_snapshot_copies_the_working_state_without_noise(tmp_path: Path) -> None:
    root = project(tmp_path)
    snap = sn.take(root, tmp_path / "snap")
    assert sorted(snap.manifest) == ["README.md", "src/a.py"]
    assert (snap.path / "src/a.py").read_text() == "a = 1\n"
    assert not (snap.path / ".git").exists()
    again = sn.take(root, snap.path)
    assert sorted(again.manifest) == ["README.md", "src/a.py"]
    with_git = sn.take(root, tmp_path / "g", copy_git=True)
    assert ".git/HEAD" in with_git.manifest
    with pytest.raises(sn.SnapshotError, match="not a directory"):
        sn.take(tmp_path / "missing", tmp_path / "x")


def test_changes_report_added_modified_and_deleted_files_with_a_diff(tmp_path: Path) -> None:
    root = project(tmp_path)
    snap = sn.take(root, tmp_path / "snap")
    assert not sn.changes(snap) and sn.changes(snap).paths == []
    (snap.path / "src/a.py").write_text("a = 2\n")
    (snap.path / "new.txt").write_text("n\n")
    (snap.path / "README.md").unlink()
    (snap.path / "blob.bin").write_bytes(b"\xff\xfe\x00")
    found = sn.changes(snap)
    assert found.paths == ["README.md", "blob.bin", "new.txt", "src/a.py"]
    assert found.files["README.md"] is None and found.files["new.txt"] == b"n\n"
    assert "-a = 1" in found.diff and "+a = 2" in found.diff
    assert "Binary file blob.bin changed" in found.diff and "+n" in found.diff
    assert "--- a/README.md" in found.diff


def test_an_empty_new_file_is_still_a_change(tmp_path: Path) -> None:
    root = project(tmp_path)
    snap = sn.take(root, tmp_path / "snap")
    (snap.path / "empty.txt").write_text("")
    found = sn.changes(snap)
    assert found.paths == ["empty.txt"] and "empty-file change" in found.diff


def test_a_long_diff_is_truncated_with_a_marker(tmp_path: Path) -> None:
    root = project(tmp_path)
    snap = sn.take(root, tmp_path / "snap")
    (snap.path / "big.txt").write_text("line\n" * 20_000)
    assert "[diff truncated:" in sn.changes(snap).diff


def test_applying_a_change_set_writes_deletes_and_refuses_escapes(tmp_path: Path) -> None:
    root = project(tmp_path)
    snap = sn.take(root, tmp_path / "snap")
    (snap.path / "src/a.py").write_text("a = 2\n")
    (snap.path / "deep/er").mkdir(parents=True)
    (snap.path / "deep/er/n.txt").write_text("n")
    (snap.path / "README.md").unlink()
    touched = sn.apply(sn.changes(snap), root)
    assert touched == ["README.md", "deep/er/n.txt", "src/a.py"]
    assert (root / "src/a.py").read_text() == "a = 2\n" and not (root / "README.md").exists()
    assert (root / "deep/er/n.txt").read_text() == "n"
    with pytest.raises(sn.SnapshotError, match="escapes"):
        sn.apply(sn.Changes({"../evil": b"x"}, ""), root)


def test_describe_lists_what_the_selector_reads(tmp_path: Path) -> None:
    root = project(tmp_path)
    snap = sn.take(root, tmp_path / "snap")
    assert "(none)" in sn.describe(sn.changes(snap), "")
    (snap.path / "README.md").write_text("changed\n")
    text = sn.describe(sn.changes(snap), "Done.")
    assert "Done." in text and "- README.md" in text and "```diff" in text


def messages() -> list[dict[str, object]]:
    return [
        {"type": "system", "subtype": "init", "model": "sonnet"},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "Looking."}]}},
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": "Read", "input": {"p": "a.py"}}]},
        },
        {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "content": "a = 1", "is_error": False}]
            },
        },
        {
            "type": "user",
            "message": {
                "content": [
                    {
                        "type": "tool_result",
                        "content": [{"type": "text", "text": "boom"}],
                        "is_error": True,
                    }
                ]
            },
        },
        {"type": "user", "message": {"content": "plain"}},
        {"type": "result", "result": "All done."},
    ]


def test_a_trajectory_reads_in_order_with_every_event() -> None:
    text = tj.render(messages())
    for fragment in (
        "Session started (model sonnet)",
        "## 1. Agent\nLooking.",
        "## 2. Tool call: Read",
        '{"p": "a.py"}',
        "### Observation\na = 1",
        "### Observation (error)\nboom",
        "## Final answer\nAll done.",
    ):
        assert fragment in text


def test_a_long_observation_is_capped_with_an_explicit_marker() -> None:
    big = [{"type": "user", "message": {"content": [{"type": "tool_result", "content": "x" * 50}]}}]
    text = tj.render(big, obs_limit=10)
    assert "[truncated by whiletrue: 40 characters omitted]" in text


def test_a_runner_without_raw_messages_still_produces_a_note_and_the_answer() -> None:
    text = tj.render([], final="Answer.")
    assert "no step-by-step record" in text and "## Final answer\nAnswer." in text
    assert tj.render([{"type": "assistant", "message": {"content": 5}}]).startswith("(this runner")
    assert tj._text(None) == "" and tj._text({"a": 1}) == '{"a": 1}'


def test_a_change_set_round_trips_through_disk(tmp_path: Path) -> None:
    root = project(tmp_path)
    snap = sn.take(root, tmp_path / "snap")
    (snap.path / "src/a.py").write_text("a = 2\n")
    (snap.path / "x/y").mkdir(parents=True)
    (snap.path / "x/y/z.txt").write_text("z")
    (snap.path / "README.md").unlink()
    original = sn.changes(snap)
    sn.save(original, tmp_path / "saved")
    sn.save(original, tmp_path / "saved")
    loaded = sn.load(tmp_path / "saved")
    assert loaded.files == original.files and loaded.diff == original.diff


def test_changes_can_skip_the_harness_and_refresh_the_baseline(tmp_path: Path) -> None:
    root = project(tmp_path)
    snap = sn.take(root, tmp_path / "snap")
    (snap.path / "CLAUDE.md").write_text("harness\n")
    sn.refresh(snap)
    assert not sn.changes(snap)
    (snap.path / "CLAUDE.md").write_text("agent edited the harness\n")
    (snap.path / "src/a.py").write_text("a = 3\n")
    only_code = sn.changes(snap, skip=lambda rel: rel == "CLAUDE.md")
    assert only_code.paths == ["src/a.py"]
    (snap.path / "CLAUDE.md").unlink()
    assert sn.changes(snap, skip=lambda rel: rel == "CLAUDE.md").paths == ["src/a.py"]
