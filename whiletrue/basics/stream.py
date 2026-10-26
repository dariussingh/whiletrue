"""Run a command and read its stdout line by line, with a wall-time limit and abort.

The agent runners (Claude Code headless, opencode, Codex) all do the same thing: start a process,
feed it a prompt, read JSON lines until it exits, and stop it if it takes too long or the run is
aborted. This is that once.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from whiletrue.abort import AbortSignal
from whiletrue.basics.process import kill_process_group

#: Stream-json lines can be large (tool results); lift asyncio's 64 KiB default.
LINE_LIMIT = 64 * 1024 * 1024


@dataclass
class StreamResult:
    """How a streamed process ended."""

    #: The exit code; None when the process was stopped before it exited.
    code: int | None
    #: Why it was stopped, if it was.
    stopped: Literal["aborted", "wall"] | None = None
    #: What it wrote to stderr (empty unless `capture_stderr`).
    stderr: str = ""


async def run_stream(
    command: str,
    args: list[str],
    *,
    cwd: str,
    on_line: Callable[[bytes], None],
    signal: AbortSignal,
    stdin: str | None = None,
    wall_ms: int | None = None,
    capture_stderr: bool = True,
) -> StreamResult:
    """Run `command`, calling `on_line` for each stdout line, until it exits or is stopped.

    Raises `OSError` when the process cannot be started. A stopped process is killed with its whole
    group, so grandchildren cannot keep the pipes open.
    """
    proc = await asyncio.create_subprocess_exec(
        command,
        *args,
        cwd=cwd,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE if capture_stderr else asyncio.subprocess.DEVNULL,
        start_new_session=True,
        limit=LINE_LIMIT,
    )

    async def drive() -> None:
        assert proc.stdout is not None
        if stdin is not None:
            assert proc.stdin is not None
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                proc.stdin.write(stdin.encode())
                await proc.stdin.drain()
                proc.stdin.close()
        while line := await proc.stdout.readline():
            on_line(line)

    stderr_task = asyncio.ensure_future(proc.stderr.read()) if proc.stderr is not None else None
    drive_task = asyncio.ensure_future(drive())
    abort_task = asyncio.ensure_future(signal.wait())
    try:
        done, _ = await asyncio.wait(
            {drive_task, abort_task},
            timeout=None if wall_ms is None else wall_ms / 1000,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if drive_task not in done:
            kill_process_group(proc.pid)
            drive_task.cancel()
            await asyncio.gather(drive_task, return_exceptions=True)
            await proc.wait()
            return StreamResult(None, "aborted" if abort_task in done else "wall")
        drive_task.result()
        code = await proc.wait()
        stderr = (await stderr_task).decode(errors="replace").strip() if stderr_task else ""
        return StreamResult(code, None, stderr)
    finally:
        abort_task.cancel()
        if stderr_task is not None:
            stderr_task.cancel()
