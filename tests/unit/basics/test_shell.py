import asyncio
from pathlib import Path

from whiletrue.abort import AbortSignal
from whiletrue.basics import ShellConfig, gate_shell_module, shell_module
from whiletrue.core import Registry, ValidateContext, create_step_context


def ctx(directory: Path, **config: object):
    cfg = ShellConfig.model_validate(config)
    return create_step_context(cfg, cwd=str(directory))


async def test_fires_ok_with_stdout(tmp_path: Path) -> None:
    r = await shell_module.run({}, ctx(tmp_path, run="echo hi"))
    assert (r.port, r.value) == ("ok", "hi\n")


async def test_uses_cwd_and_env(tmp_path: Path) -> None:
    (tmp_path / "marker.txt").write_text("x")
    r = await shell_module.run({}, ctx(tmp_path, run="ls; echo $V", cwd=".", env={"V": "1"}))
    assert r.value == "marker.txt\n1\n"


async def test_fires_fail_with_the_exit_code_and_output_tail(tmp_path: Path) -> None:
    r = await shell_module.run({}, ctx(tmp_path, run="echo boom; exit 2"))
    assert r.port == "fail"
    assert r.value["exit_code"] == 2
    assert r.value["output"] == "boom"
    assert "code 2" in r.value["error"]


async def test_reports_timeouts(tmp_path: Path) -> None:
    r = await shell_module.run({}, ctx(tmp_path, run="sleep 5", timeout="50ms"))
    assert "timed out" in r.value["error"]


async def test_reports_aborts(tmp_path: Path) -> None:
    signal = AbortSignal()
    context = create_step_context(ShellConfig(run="sleep 5"), cwd=str(tmp_path), signal=signal)
    task = asyncio.ensure_future(shell_module.run({}, context))
    await asyncio.sleep(0.05)
    signal.abort()
    assert "aborted" in (await task).value["error"]


async def test_reports_unknown_exit_codes_on_spawn_failure() -> None:
    r = await shell_module.run(
        {}, create_step_context(ShellConfig(run="x"), cwd="/definitely/not/here")
    )
    assert "unknown" in r.value["error"]


def test_has_an_idempotency_key_and_validates_timeouts(tmp_path: Path) -> None:
    assert shell_module.idempotency_key is not None
    key = shell_module.idempotency_key(
        {}, create_step_context(ShellConfig(run="x"), run_id="r", item_id="i", node_id="n")
    )
    assert key == "shell:r:i:n"
    vctx = ValidateContext(cwd=str(tmp_path), registry=Registry())
    assert shell_module.validate is not None
    assert shell_module.validate(ShellConfig(run="x", timeout="1s"), vctx) == []
    assert shell_module.validate(ShellConfig(run="x"), vctx) == []
    bad = shell_module.validate(ShellConfig(run="x", timeout="soon"), vctx)
    assert "Invalid duration" in bad[0].message


async def test_gate_shell_is_a_deterministic_gate_by_exit_code(tmp_path: Path) -> None:
    assert gate_shell_module.effects == "deterministic"
    assert gate_shell_module.idempotency_key is None
    assert (await gate_shell_module.run({}, ctx(tmp_path, run="true"))).port == "ok"
    assert (await gate_shell_module.run({}, ctx(tmp_path, run="false"))).port == "fail"
