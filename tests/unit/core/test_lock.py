from pathlib import Path

import pytest

from whiletrue.control import control_plugin
from whiletrue.core import (
    LockError,
    MemoryStore,
    Registry,
    check_lock,
    collect_file_hashes,
    load_pipeline,
    read_lock,
    run_pipeline,
    write_lock,
)


def test_lock_round_trips_sorted(tmp_path: Path) -> None:
    path = tmp_path / "whiletrue.lock"
    assert read_lock(path) is None
    write_lock(path, {"b": "2", "a": "1"})
    assert read_lock(path) == {"a": "1", "b": "2"}
    assert path.read_text().index('"a"') < path.read_text().index('"b"')


def test_unreadable_locks_are_reported(tmp_path: Path) -> None:
    path = tmp_path / "whiletrue.lock"
    for text in (
        "not json",
        "[]",
        '{"version": 9, "plugins": {}}',
        '{"version": 1, "plugins": {"a": 1}}',
    ):
        path.write_text(text)
        with pytest.raises(LockError, match="Cannot read whiletrue.lock"):
            read_lock(path)


def test_check_lock_reports_drift() -> None:
    assert check_lock({"a": "1"}, {"a": "1"}) == []
    problems = check_lock({"a": "1", "gone": "1"}, {"a": "2", "new": "3"})
    assert problems == [
        'Plugin "a" is 2 but whiletrue.lock pins 1',
        'Plugin "new" (3) is not in whiletrue.lock',
        'whiletrue.lock pins "gone" which is no longer used',
    ]


def test_registry_records_plugin_versions() -> None:
    r = Registry()
    r.use(control_plugin, "9.9")
    assert r.plugin_versions() == {"whiletrue.control": "9.9"}
    q = Registry()
    q.use(control_plugin)
    assert q.plugin_versions() == {"whiletrue.control": "unversioned"}


def test_file_hashes_cover_the_pipeline_composites_and_prompts(tmp_path: Path) -> None:
    (tmp_path / "agents").mkdir()
    (tmp_path / "agents" / "a.md").write_text("prompt")
    (tmp_path / "c.yaml").write_text("composite: c\npipeline: { shell: { run: 'true' } }")
    (tmp_path / "whiletrue.yaml").write_text(
        "pipeline:\n  - agent: { uses: ./agents/a.md }\n  - agent: { uses: ./missing.md }\n"
        "  - agent: { uses: 'recipe:x' }\n  - composite: { uses: ./c.yaml }\n"
    )
    config = load_pipeline(tmp_path / "whiletrue.yaml")
    hashes = collect_file_hashes(config, str(tmp_path))
    assert set(hashes) == {"whiletrue.yaml", "agents/a.md", "c.yaml"}
    (tmp_path / "agents" / "a.md").write_text("changed")
    assert collect_file_hashes(config, str(tmp_path))["agents/a.md"] != hashes["agents/a.md"]


def test_files_outside_the_project_are_keyed_by_absolute_path(tmp_path: Path) -> None:
    outside = tmp_path / "out.md"
    outside.write_text("x")
    project = tmp_path / "proj"
    project.mkdir()
    (project / "whiletrue.yaml").write_text(f"pipeline: {{ agent: {{ uses: {outside} }} }}")
    config = load_pipeline(project / "whiletrue.yaml")
    assert str(outside.resolve()) in collect_file_hashes(config, str(project))


async def test_a_run_records_hashes_and_the_lockfile(tmp_path: Path) -> None:
    (tmp_path / "whiletrue.yaml").write_text("pipeline: { sequence: {} }")
    config = load_pipeline(tmp_path / "whiletrue.yaml")
    registry = Registry()
    registry.use(control_plugin)
    store = MemoryStore()
    result = await run_pipeline(
        registry=registry, config=config, store=store, cwd=str(tmp_path), lockfile="abc"
    )
    event = store.list_events(result.run_id, type="run.hashes")[0]
    assert set(event.data["files"]) == {"whiletrue.yaml"}
    assert event.data["pipeline"]
    run = store.get_run(result.run_id)
    assert run is not None
    assert run.lockfile == "abc"
