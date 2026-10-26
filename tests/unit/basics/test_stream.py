import asyncio
from pathlib import Path

import pytest

from whiletrue.abort import AbortSignal
from whiletrue.basics.stream import run_stream


async def stream(script: str, tmp_path: Path, **kwargs: object) -> tuple[list[bytes], object]:
    lines: list[bytes] = []
    result = await run_stream(
        "sh",
        ["-c", script],
        cwd=str(tmp_path),
        on_line=lines.append,
        signal=AbortSignal(),
        **kwargs,  # type: ignore[arg-type]
    )
    return lines, result


async def test_lines_are_delivered_and_the_exit_code_reported(tmp_path: Path) -> None:
    lines, result = await stream("echo one; echo two; exit 3", tmp_path)
    assert lines == [b"one\n", b"two\n"]
    assert (result.code, result.stopped) == (3, None)  # type: ignore[attr-defined]


async def test_stdin_is_fed_and_stderr_captured(tmp_path: Path) -> None:
    lines, result = await stream("cat; echo oops >&2", tmp_path, stdin="hello")
    assert lines == [b"hello"] and result.stderr == "oops"  # type: ignore[attr-defined]


async def test_a_process_that_ignores_stdin_does_not_break_the_writer(tmp_path: Path) -> None:
    lines, result = await stream("echo done", tmp_path, stdin="x" * 1_000_000)
    assert lines == [b"done\n"] and result.code == 0  # type: ignore[attr-defined]


async def test_stderr_can_be_ignored(tmp_path: Path) -> None:
    _, result = await stream("echo oops >&2", tmp_path, capture_stderr=False)
    assert result.stderr == ""  # type: ignore[attr-defined]


async def test_the_wall_limit_kills_the_whole_process_group(tmp_path: Path) -> None:
    marker = tmp_path / "grandchild"
    script = f"(sleep 30; touch {marker}) & echo started; sleep 30"
    lines, result = await stream(script, tmp_path, wall_ms=400)
    assert lines == [b"started\n"]
    assert (result.code, result.stopped) == (None, "wall")  # type: ignore[attr-defined]
    await asyncio.sleep(0.1)
    assert not marker.exists()


async def test_an_abort_stops_it(tmp_path: Path) -> None:
    signal = AbortSignal()
    task = asyncio.ensure_future(
        run_stream(
            "sh", ["-c", "sleep 30"], cwd=str(tmp_path), on_line=lambda _l: None, signal=signal
        )
    )
    await asyncio.sleep(0.2)
    signal.abort()
    result = await task
    assert (result.code, result.stopped) == (None, "aborted")


async def test_a_missing_command_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        await run_stream(
            "/no/such/binary", [], cwd=str(tmp_path), on_line=lambda _l: None, signal=AbortSignal()
        )
