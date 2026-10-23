from __future__ import annotations

import asyncio
import re
import shlex
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


def session_name(project_dir: str) -> str:
    """`wt-<directory name>`, with characters tmux dislikes replaced."""
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(project_dir).name).strip("-")
    return f"wt-{name or 'project'}"


class TmuxError(Exception):
    """A tmux command failed."""


@dataclass(frozen=True)
class ExecResult:
    code: int
    out: str
    err: str


@dataclass(frozen=True)
class PaneRef:
    #: tmux pane id, e.g. `%12`.
    id: str
    #: tmux window id, e.g. `@3`.
    window: str


#: Runs `tmux <argv>`; injected so the controller can be tested without a tmux server.
Exec = Callable[[list[str]], Awaitable[ExecResult]]

PaneState = Literal["running", "dead", "gone"]


class TmuxController:
    """One tmux session: window 0 is the control panel, every agent session gets a pane.

    Agent panes are laid out in a tiled grid, `panes_per_window` to a window; a new window opens
    when the last one is full. Slots free up as panes close.
    """

    def __init__(
        self,
        session: str,
        *,
        exec: Exec | None = None,
        socket: str | None = None,
        binary: str = "tmux",
        panes_per_window: int = 4,
    ) -> None:
        self.session = session
        self._socket = socket
        self._binary = binary
        self._per_window = panes_per_window
        self._exec = exec if exec is not None else self._run_binary
        self._windows: list[str] = []

    async def _run_binary(self, argv: list[str]) -> ExecResult:
        prefix = ["-L", self._socket] if self._socket else []
        proc = await asyncio.create_subprocess_exec(
            self._binary,
            *prefix,
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await proc.communicate()
        return ExecResult(proc.returncode or 0, out.decode(), err.decode())

    async def _ok(self, argv: list[str]) -> str:
        result = await self._exec(argv)
        if result.code != 0:
            raise TmuxError(f"tmux {argv[0]} failed: {result.err.strip() or result.out.strip()}")
        return result.out

    async def has_session(self) -> bool:
        return (await self._exec(["has-session", "-t", self.session])).code == 0

    async def ensure_session(self, control_command: str | None = None) -> None:
        """Creates the session (detached) with its control window unless it already exists."""
        if await self.has_session():
            return
        argv = ["new-session", "-d", "-s", self.session, "-n", "control"]
        await self._ok([*argv, control_command] if control_command else argv)
        for option, value in (
            ("pane-border-status", "top"),
            ("pane-border-format", " #{pane_title} "),
        ):
            await self._ok(["set-option", "-t", self.session, option, value])

    async def _live_panes(self, window: str) -> list[str] | None:
        result = await self._exec(["list-panes", "-t", window, "-F", "#{pane_id} #{pane_dead}"])
        if result.code != 0:
            return None
        return [line.split()[0] for line in result.out.splitlines() if line.strip()]

    async def _window_with_room(self) -> str | None:
        for window in list(self._windows):
            panes = await self._live_panes(window)
            if panes is None:
                self._windows.remove(window)
            elif len(panes) < self._per_window:
                return window
        return None

    async def open_pane(
        self, *, title: str, argv: list[str], cwd: str, env: dict[str, str]
    ) -> PaneRef:
        """Starts `argv` in a new pane (a new window when the grid is full) and titles it."""
        flags = ["-d", "-P", "-F", "#{pane_id} #{window_id}", "-c", cwd]
        for key, value in env.items():
            flags += ["-e", f"{key}={value}"]
        command = shlex.join(argv)
        window = await self._window_with_room()
        if window is None:
            out = await self._ok(
                ["new-window", *flags, "-t", f"{self.session}:", "-n", "agents", command]
            )
        else:
            out = await self._ok(["split-window", *flags, "-t", window, command])
        pane_id, window_id = out.split()
        if window is None:
            self._windows.append(window_id)
        else:
            await self._ok(["select-layout", "-t", window_id, "tiled"])
        pane = PaneRef(pane_id, window_id)
        await self.set_title(pane, title)
        return pane

    async def set_title(self, pane: PaneRef, title: str) -> None:
        await self._ok(["select-pane", "-t", pane.id, "-T", title])

    async def send_text(self, pane: PaneRef, text: str, *, enter: bool = True) -> None:
        await self._ok(["send-keys", "-t", pane.id, "-l", text])
        if enter:
            await self._ok(["send-keys", "-t", pane.id, "Enter"])

    async def send_keys(self, pane: PaneRef, *keys: str) -> None:
        await self._ok(["send-keys", "-t", pane.id, *keys])

    async def pane_state(self, pane: PaneRef) -> PaneState:
        result = await self._exec(
            ["list-panes", "-t", pane.window, "-F", "#{pane_id} #{pane_dead}"]
        )
        if result.code != 0:
            return "gone"
        for line in result.out.splitlines():
            pane_id, _, dead = line.partition(" ")
            if pane_id == pane.id:
                return "dead" if dead.strip() == "1" else "running"
        return "gone"

    async def keep_pane(self, pane: PaneRef, title: str) -> None:
        """`on_done: keep`: the pane stays on screen (with its output) after its process exits."""
        await self._ok(["set-option", "-p", "-t", pane.id, "remain-on-exit", "on"])
        await self.set_title(pane, title)

    async def close_pane(self, pane: PaneRef) -> None:
        """`on_done: close`. Closing a pane that is already gone is fine."""
        await self._exec(["kill-pane", "-t", pane.id])

    async def kill_server(self) -> None:
        await self._exec(["kill-server"])

    def attach_argv(self) -> list[str]:
        prefix = ["-L", self._socket] if self._socket else []
        return [self._binary, *prefix, "attach-session", "-t", self.session]
