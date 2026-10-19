import os

from whiletrue.abort import AbortSignal
from whiletrue.basics import run_command, tail

CWD = os.getcwd()


async def test_captures_stdout_stderr_and_exit_code() -> None:
    r = await run_command("sh", ["-c", "echo out; echo err >&2; exit 3"], cwd=CWD)
    assert (r.code, r.stdout, r.stderr, r.timed_out) == (3, "out\n", "err\n", False)


async def test_feeds_stdin_and_merges_env() -> None:
    r = await run_command("sh", ["-c", "cat; echo $FOO"], cwd=CWD, env={"FOO": "bar"}, stdin="in:")
    assert r.stdout == "in:bar\n"


async def test_kills_on_timeout() -> None:
    r = await run_command("sh", ["-c", "sleep 5"], cwd=CWD, timeout_ms=50)
    assert r.timed_out
    assert r.code != 0


async def test_does_not_time_out_when_the_command_finishes_first() -> None:
    r = await run_command("true", [], cwd=CWD, timeout_ms=5000)
    assert (r.code, r.timed_out) == (0, False)


async def test_aborts_via_signal() -> None:
    signal = AbortSignal()
    task = __import__("asyncio").ensure_future(
        run_command("sh", ["-c", "sleep 5"], cwd=CWD, signal=signal)
    )
    await __import__("asyncio").sleep(0.05)
    signal.abort()
    r = await task
    assert r.aborted
    assert not r.timed_out


async def test_kills_grandchildren_that_hold_the_pipes() -> None:
    r = await run_command("sh", ["-c", "sleep 30 & wait"], cwd=CWD, timeout_ms=100)
    assert r.timed_out


async def test_reports_spawn_failures() -> None:
    r = await run_command("definitely-not-a-command-xyz", [], cwd=CWD, timeout_ms=1000)
    assert r.code is None
    assert "No such file" in r.stderr


async def test_reports_spawn_failures_when_already_aborted() -> None:
    signal = AbortSignal()
    signal.abort()
    r = await run_command("definitely-not-a-command-xyz", [], cwd=CWD, signal=signal)
    assert r.aborted


def test_tail_keeps_the_last_n_lines() -> None:
    assert tail("a\nb\nc\n", 2) == "b\nc"
    assert tail("a", 5) == "a"
