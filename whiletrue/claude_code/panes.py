from __future__ import annotations

import asyncio
import contextlib
import os
from typing import Literal, Protocol

from whiletrue.basics.process import kill_process_group
from whiletrue.tmux import PaneRef, TmuxController

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

    def __init__(self, tmux: TmuxController, control_command: str | None = None) -> None:
        self._tmux = tmux
        self._control_command = control_command

    async def open(self, *, title: str, argv: list[str], cwd: str, env: dict[str, str]) -> Pane:
        await self._tmux.ensure_session(self._control_command)
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
