from __future__ import annotations

import asyncio
import contextlib
import os
import re
import shlex
import uuid
from pathlib import Path
from typing import Literal, Protocol

from whiletrue.basics.process import kill_process_group
from whiletrue.tmux.controller import PaneRef, TmuxController

PaneState = Literal["running", "dead", "gone"]


class Pane(Protocol):
    """One live agent session on screen (or, in tests, in a plain process)."""

    async def state(self) -> PaneState: ...

    async def send_text(self, text: str) -> None: ...

    async def set_title(self, title: str) -> None: ...

    async def finish(self, *, keep: bool, title: str) -> None:
        """The step is over. Close the pane, or `keep` it (and its output) on screen."""
        ...

    async def kill(self) -> None: ...


class PaneHost(Protocol):
    async def open(self, *, title: str, argv: list[str], cwd: str, env: dict[str, str]) -> Pane: ...


class TmuxPaneHost:
    """Opens each agent session as a pane in the project's tmux session."""

    def __init__(
        self, tmux: TmuxController, control_command: str | None = None, cwd: str | None = None
    ) -> None:
        self._tmux = tmux
        self._control_command = control_command
        self._cwd = cwd

    async def open(self, *, title: str, argv: list[str], cwd: str, env: dict[str, str]) -> Pane:
        await self._tmux.ensure_session(self._control_command, self._cwd)
        pane = await self._tmux.open_pane(title=title, argv=argv, cwd=cwd, env=env)
        return _TmuxPane(self._tmux, pane)


class _TmuxPane:
    def __init__(self, tmux: TmuxController, pane: PaneRef) -> None:
        self._tmux = tmux
        self._pane = pane

    async def state(self) -> PaneState:
        return await self._tmux.pane_state(self._pane)

    async def send_text(self, text: str) -> None:
        await self._tmux.send_text(self._pane, text)

    async def set_title(self, title: str) -> None:
        await self._tmux.set_title(self._pane, title)

    async def finish(self, *, keep: bool, title: str) -> None:
        if keep:
            await self._tmux.keep_pane(self._pane, title)
        else:
            await self._tmux.close_pane(self._pane)

    async def kill(self) -> None:
        await self._tmux.close_pane(self._pane)


class ProcessPaneHost:
    """A "pane" that is a plain subprocess: for tests and machines without tmux."""

    async def open(self, *, title: str, argv: list[str], cwd: str, env: dict[str, str]) -> Pane:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env={**os.environ, **env},
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
        )
        return _ProcessPane(proc, title)


class _ProcessPane:
    def __init__(self, proc: asyncio.subprocess.Process, title: str) -> None:
        self._proc = proc
        self.title = title

    async def state(self) -> PaneState:
        return "running" if self._proc.returncode is None else "dead"

    async def send_text(self, text: str) -> None:
        if self._proc.stdin is not None and self._proc.returncode is None:
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self._proc.stdin.write(text.encode() + b"\n")
                await self._proc.stdin.drain()

    async def set_title(self, title: str) -> None:
        self.title = title

    async def finish(self, *, keep: bool, title: str) -> None:
        self.title = title
        await self.kill()

    async def kill(self) -> None:
        if self._proc.returncode is None:
            kill_process_group(self._proc.pid)
        await self._proc.wait()


class StreamMirror:
    """Shows a headless step's live output in a read-only pane (`tail -F` of a log file).

    Without a pane host it does nothing, so runners can call it unconditionally.
    """

    def __init__(self, host: PaneHost | None) -> None:
        self._host = host
        self._log: Path | None = None
        self._pane: Pane | None = None

    async def open(self, cwd: str, step_key: str | None, label: str | None) -> Path | None:
        if self._host is None:
            return None
        key = re.sub(r"[^A-Za-z0-9_.-]+", "_", step_key or uuid.uuid4().hex)[:80]
        directory = Path(cwd) / ".whiletrue" / "sessions" / f"{key}-{uuid.uuid4().hex[:6]}"
        directory.mkdir(parents=True, exist_ok=True)
        self._log = directory / "stream.log"
        self._log.write_text("")
        done = shlex.quote(str(self._log) + ".done")
        script = (
            f"tail -n +1 -F {shlex.quote(str(self._log))} & p=$!; "
            f"while [ ! -e {done} ]; do sleep 0.3; done; sleep 0.5; kill $p"
        )
        try:
            self._pane = await self._host.open(
                title=label or "agent", argv=["sh", "-c", script], cwd=cwd, env={}
            )
        except Exception:
            self._pane = None
        return self._log

    def note(self, line: str) -> None:
        if self._log is not None:
            with self._log.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")

    async def close(self, *, keep: bool, title: str) -> None:
        if self._log is None:
            return
        self._log.with_name(self._log.name + ".done").write_text("done")
        if self._pane is not None:
            with contextlib.suppress(Exception):
                await self._pane.finish(keep=keep, title=title)
