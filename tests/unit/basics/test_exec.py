import stat
from pathlib import Path
from typing import Any

from whiletrue.basics import ExecConfig, exec_module
from whiletrue.core import Emit, create_step_context


def script(directory: Path, name: str, body: str) -> str:
    p = directory / name
    p.write_text(f"#!/bin/sh\n{body}\n")
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


async def run(directory: Path, command: str, input_value: Any = "in") -> Emit:
    ctx = create_step_context(
        ExecConfig(command=command), cwd=str(directory), run_id="r", item_id="i", node_id="n"
    )
    return await exec_module.run({"in": input_value}, ctx)


async def test_round_trips_json_over_stdio(tmp_path: Path) -> None:
    p = script(tmp_path, "echo.sh", 'cat > in.json; echo \'{"port":"ok","value":{"got":true}}\'')
    assert await run(tmp_path, p) == Emit("ok", {"got": True})


async def test_passes_input_config_item_and_step_on_stdin(tmp_path: Path) -> None:
    p = script(tmp_path, "dump.sh", 'v=$(cat); printf \'{"value":%s}\' "$v"')
    r = await run(tmp_path, p, {"n": 1})
    assert r.port == "ok"
    assert r.value["input"] == {"n": 1}
    assert r.value["step"] == {"id": "n", "key": "r:i:n", "attempt": 1}


async def test_defaults_the_port_to_ok_and_lets_the_program_choose_another(tmp_path: Path) -> None:
    assert await run(tmp_path, script(tmp_path, "a.sh", "echo '{}'")) == Emit("ok")
    r = await run(tmp_path, script(tmp_path, "b.sh", 'echo \'{"port":"needs_human"}\''))
    assert r.port == "needs_human"


async def test_fails_on_non_zero_exit_bad_json_and_bad_shape(tmp_path: Path) -> None:
    bad = await run(tmp_path, script(tmp_path, "c.sh", "echo oops; exit 4"))
    assert bad.port == "fail"
    assert "code 4" in bad.value["error"]
    assert bad.value["output"] == "oops"
    r = await run(tmp_path, script(tmp_path, "d.sh", "echo not-json"))
    assert "valid JSON" in r.value["error"]
    r = await run(tmp_path, script(tmp_path, "e.sh", "echo '[1]'"))
    assert "reply must be" in r.value["error"]


async def test_reports_spawn_failures_as_unknown_exit_code(tmp_path: Path) -> None:
    r = await run(tmp_path, "./missing-command")
    assert "unknown" in r.value["error"]


async def test_applies_cwd_env_and_timeout(tmp_path: Path) -> None:
    p = script(tmp_path, "env.sh", 'cat >/dev/null; printf \'{"value":"%s"}\' "$MY"')
    cfg = ExecConfig(command=p, env={"MY": "x"}, cwd=".", timeout="5s")
    r = await exec_module.run({}, create_step_context(cfg, cwd=str(tmp_path)))
    assert r.value == "x"


def test_has_an_idempotency_key() -> None:
    assert exec_module.idempotency_key is not None
    ctx = create_step_context(ExecConfig(command="x"), run_id="r", item_id="i", node_id="n")
    assert exec_module.idempotency_key({}, ctx) == "exec:r:i:n"
