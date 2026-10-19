from __future__ import annotations

import asyncio
import contextlib
import os
import signal as signal_module
from dataclasses import dataclass
from typing import Any

from whiletrue.abort import AbortSignal


@dataclass
class CommandResult:
    #: Exit code; None if the process never started or was killed by a signal.
    code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    aborted: bool = False


def kill_process_group(pid: int) -> None:
    """SIGKILLs the whole group so grandchildren cannot keep our pipes open."""
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, signal_module.SIGKILL)


async def run_command(
    command: str,
    args: list[str],
    *,
    cwd: str,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
    signal: AbortSignal | None = None,
    timeout_ms: float | None = None,
) -> CommandResult:
    """Runs a command to completion.

    Never raises for non-zero exits; spawn errors are reported as `code=None` with the OS message
    on stderr.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            command,
            *args,
            cwd=cwd,
            env={**os.environ, **(env or {})},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as error:
        return CommandResult(
            code=None,
            stdout="",
            stderr=str(error),
            aborted=signal.aborted if signal else False,
        )

    communicate = asyncio.ensure_future(proc.communicate((stdin or "").encode()))
    abort_wait = asyncio.ensure_future(signal.wait()) if signal else None
    waiting: set[asyncio.Future[Any]] = {communicate}
    if abort_wait is not None:
        waiting.add(abort_wait)

    timed_out = False
    aborted = False
    try:
        done, _ = await asyncio.wait(
            waiting,
            timeout=None if timeout_ms is None else timeout_ms / 1000,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if communicate not in done:
            timed_out = not done
            aborted = abort_wait is not None and abort_wait in done
            kill_process_group(proc.pid)
        stdout_b, stderr_b = await communicate
        await proc.wait()
    finally:
        if abort_wait is not None:
            abort_wait.cancel()

    code = proc.returncode
    return CommandResult(
        code=None if code is None or code < 0 else code,
        stdout=stdout_b.decode(errors="replace"),
        stderr=stderr_b.decode(errors="replace"),
        timed_out=timed_out,
        aborted=aborted or (signal.aborted if signal else False),
    )


def tail(text: str, n: int = 40) -> str:
    """Last `n` lines of text, for compact failure output."""
    return "\n".join(text.rstrip().split("\n")[-n:])
