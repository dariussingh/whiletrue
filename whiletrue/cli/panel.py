"""The tmux control panel (window 0): every item, where it is, and who it is waiting for."""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from whiletrue.cli.common import Io
from whiletrue.core import StateStore
from whiletrue.tmux import TmuxController

KEYS = "j/k move   a approve   r reject   f focus pane   q quit   (rerun/cancel: not built yet)"


@dataclass
class Row:
    item: str
    state: str
    step: str
    waiting: str
    cost: float
    age_s: int


def rows(store: StateStore, now_ms: int) -> list[Row]:
    run = store.latest_run()
    if run is None:
        return []
    parks = store.list_parks(undecided_only=True)
    out: list[Row] = []
    for item in store.list_items(run.id):
        steps = store.list_steps(run.id, item.id)
        working = [s for s in steps if s.status == "running" and s.node_path != "root"]
        last = working[-1] if working else (steps[-1] if steps else None)
        mine = [p for p in parks if p.item_id == item.id]
        waiting = f"{mine[-1].what} at {mine[-1].node_path}" if mine else ""
        started = min((s.started_at for s in steps), default=run.started_at)
        out.append(
            Row(
                item=item.id,
                state=item.state,
                step=last.node_path if last else "",
                waiting=waiting,
                cost=sum(s.cost_usd for s in steps),
                age_s=max(0, (now_ms - started) // 1000),
            )
        )
    return out


def _age(seconds: int) -> str:
    if seconds >= 3600:
        return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"
    return f"{seconds // 60}m{seconds % 60:02d}s"


def render(table: list[Row], selected: int, message: str = "") -> str:
    lines = [f"  {'ITEM':<10}{'STATE':<10}{'STEP':<18}{'WAITING FOR':<24}{'COST':>8}  AGE"]
    for index, row in enumerate(table):
        mark = ">" if index == selected else " "
        lines.append(
            f"{mark} {row.item:<10}{row.state:<10}{row.step:<18}{row.waiting:<24}"
            f"{'$' + format(row.cost, '.2f'):>8}  {_age(row.age_s)}"
        )
    if not table:
        lines.append("  (no items yet)")
    lines += ["", KEYS]
    if message:
        lines.append(message)
    return "\n".join(lines)


class Panel:
    """Keys act on the selected item. Reads and writes go through the store, like the CLI."""

    def __init__(
        self,
        store: StateStore,
        tmux: TmuxController | None = None,
        *,
        now: Callable[[], int] = lambda: int(time.time() * 1000),
        ask: Callable[[str], Awaitable[str]] | None = None,
        who: str = "panel",
    ) -> None:
        self._store = store
        self._tmux = tmux
        self._now = now
        self._ask = ask
        self._who = who
        self.selected = 0
        self.message = ""

    def view(self) -> str:
        table = rows(self._store, self._now())
        self.selected = min(self.selected, max(0, len(table) - 1))
        return render(table, self.selected, self.message)

    def _current(self) -> Row | None:
        table = rows(self._store, self._now())
        return table[self.selected] if table else None

    async def key(self, key: str) -> bool:
        """Handles one key; False means quit."""
        self.message = ""
        if key == "q":
            return False
        if key in ("j", "k"):
            size = len(rows(self._store, self._now()))
            self.selected = max(0, min(size - 1, self.selected + (1 if key == "j" else -1)))
            return True
        row = self._current()
        if row is None:
            return True
        if key in ("a", "r"):
            await self._decide(row, approve=key == "a")
        elif key == "f":
            await self._focus(row)
        elif key in ("R", "c"):
            self.message = (
                "rerun and cancel need the scheduler (milestone 3); use Ctrl-C on the run"
            )
        return True

    async def _decide(self, row: Row, *, approve: bool) -> None:
        waiting = self._store.list_parks(row.item, undecided_only=True)
        if not waiting:
            self.message = f"{row.item} is not waiting for a decision"
            return
        note: str | None = None
        if not approve:
            note = (await self._ask("rejection note: ")).strip() if self._ask else ""
            if not note:
                self.message = "a rejection needs a note"
                return
        self._store.decide_park(
            waiting[-1].id,
            "approved" if approve else "rejected",
            decided_by=self._who,
            decided_at=self._now(),
            notes=note,
        )
        self.message = f"{row.item}: {'approved' if approve else 'rejected'}"

    async def _focus(self, row: Row) -> None:
        if self._tmux is None:
            self.message = "no tmux session to focus"
            return
        pane = await self._tmux.find_pane(row.item)
        if pane is None:
            self.message = f"no live pane for {row.item}"
            return
        await self._tmux.focus(pane)


async def run_panel(
    io: Io, store: StateStore, tmux: TmuxController | None
) -> int:  # pragma: no cover
    """The interactive loop: redraw every second, act on single key presses."""
    import getpass  # noqa: PLC0415
    import select  # noqa: PLC0415
    import termios  # noqa: PLC0415
    import tty  # noqa: PLC0415

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)

    async def ask(prompt: str) -> str:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
        try:
            return await asyncio.to_thread(input, prompt)
        finally:
            tty.setcbreak(fd)

    panel = Panel(store, tmux, ask=ask, who=getpass.getuser())
    tty.setcbreak(fd)
    try:
        while True:
            io.out("\x1b[2J\x1b[H" + panel.view() + "\n")
            ready, _, _ = await asyncio.to_thread(select.select, [sys.stdin], [], [], 1.0)
            if ready and not await panel.key(sys.stdin.read(1)):
                return 0
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
