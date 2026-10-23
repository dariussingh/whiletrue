import subprocess
from pathlib import Path

from whiletrue.basics import (
    DiffSizeConfig,
    GateTestsConfig,
    diff_size_module,
    gate_tests_module,
)
from whiletrue.core import Registry, ValidateContext, create_step_context


def git(directory: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=directory,
        check=True,
        capture_output=True,
    )


def repo(tmp_path: Path) -> Path:
    git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text("1\n2\n3\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def size_ctx(directory: Path, **config: object):
    return create_step_context(DiffSizeConfig.model_validate(config), cwd=str(directory))


async def test_gate_tests_defaults_to_pytest_and_is_deterministic() -> None:
    assert GateTestsConfig().run == "pytest"
    assert gate_tests_module.effects == "deterministic"
    assert gate_tests_module.cacheable is False


async def test_gate_tests_passes_and_fails_on_the_exit_code(tmp_path: Path) -> None:
    ok = await gate_tests_module.run(
        {}, create_step_context(GateTestsConfig(run="true"), cwd=str(tmp_path))
    )
    bad = await gate_tests_module.run(
        {}, create_step_context(GateTestsConfig(run="echo red; exit 1"), cwd=str(tmp_path))
    )
    assert ok.port == "ok"
    assert (bad.port, bad.value["output"]) == ("fail", "red")


async def test_diff_size_passes_within_limits(tmp_path: Path) -> None:
    d = repo(tmp_path)
    (d / "a.txt").write_text("1\n2\n3\n4\n")
    (d / "new.txt").write_text("x")
    r = await diff_size_module.run({}, size_ctx(d, max_lines=10, max_files=5))
    assert (r.port, r.value) == ("ok", {"lines": 1, "files": 2})


async def test_diff_size_fails_over_the_limits_and_reports_both(tmp_path: Path) -> None:
    d = repo(tmp_path)
    (d / "a.txt").write_text("a\nb\nc\nd\n")
    (d / "new.txt").write_text("x")
    r = await diff_size_module.run({}, size_ctx(d, max_lines=2, max_files=1))
    assert r.port == "fail"
    assert "exceed the limit of 2" in r.value["error"]
    assert "exceed the limit of 1" in r.value["error"]
    assert r.value["lines"] == 7


async def test_diff_size_counts_binary_files_as_zero_lines(tmp_path: Path) -> None:
    d = repo(tmp_path)
    (d / "b.bin").write_bytes(b"\x00\x01\x02")
    git(d, "add", "b.bin")
    r = await diff_size_module.run({}, size_ctx(d, max_lines=1))
    assert (r.port, r.value) == ("ok", {"lines": 0, "files": 1})


async def test_diff_size_works_before_the_first_commit(tmp_path: Path) -> None:
    git(tmp_path, "init", "-q")
    (tmp_path / "a.txt").write_text("1\n")
    git(tmp_path, "add", ".")
    r = await diff_size_module.run({}, size_ctx(tmp_path, max_files=3))
    assert (r.port, r.value) == ("ok", {"lines": 1, "files": 1})


async def test_diff_size_fails_outside_a_repository(tmp_path: Path) -> None:
    r = await diff_size_module.run({}, size_ctx(tmp_path, max_lines=5, base="main"))
    assert (r.port, r.value["error"]) == ("fail", "git diff failed")


async def test_diff_size_fails_when_listing_untracked_files_fails(tmp_path: Path) -> None:
    d = repo(tmp_path)
    r = await diff_size_module.run({}, size_ctx(d / "does-not-exist", max_lines=5))
    assert r.port == "fail"


def test_diff_size_needs_a_limit() -> None:
    vctx = ValidateContext(cwd="", registry=Registry())
    assert diff_size_module.validate is not None
    assert diff_size_module.validate(DiffSizeConfig(), vctx)[0].severity == "error"
    assert diff_size_module.validate(DiffSizeConfig(max_lines=1), vctx) == []
