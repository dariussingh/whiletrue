import asyncio
import shutil
import uuid

import pytest

from whiletrue.tmux import ExecResult, PaneRef, TmuxController, TmuxError, session_name


class FakeTmux:
    """Records every tmux invocation and answers from a script keyed by subcommand."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.answers: dict[str, list[ExecResult]] = {}
        self.pane_counter = 0
        self.window_counter = 0
        self.panes: dict[str, list[str]] = {}

    def reply(self, subcommand: str, *results: ExecResult) -> None:
        self.answers.setdefault(subcommand, []).extend(results)

    async def __call__(self, argv: list[str]) -> ExecResult:
        self.calls.append(argv)
        sub = argv[0]
        queued = self.answers.get(sub)
        if queued:
            return queued.pop(0)
        if sub == "new-window":
            self.pane_counter += 1
            self.window_counter += 1
            window = f"@{self.window_counter}"
            pane = f"%{self.pane_counter}"
            self.panes[window] = [pane]
            return ExecResult(0, f"{pane} {window}\n", "")
        if sub == "split-window":
            self.pane_counter += 1
            window = argv[argv.index("-t") + 1]
            pane = f"%{self.pane_counter}"
            self.panes[window].append(pane)
            return ExecResult(0, f"{pane} {window}\n", "")
        if sub == "list-panes":
            window = argv[argv.index("-t") + 1]
            if window not in self.panes:
                return ExecResult(1, "", "can't find window")
            return ExecResult(0, "".join(f"{p} 0\n" for p in self.panes[window]), "")
        return ExecResult(0, "", "")

    def subcommands(self) -> list[str]:
        return [c[0] for c in self.calls]


def controller(fake: FakeTmux, panes_per_window: int = 4) -> TmuxController:
    return TmuxController("wt-demo", exec=fake, panes_per_window=panes_per_window)


async def test_ensure_session_creates_it_once_with_a_control_window() -> None:
    fake = FakeTmux()
    fake.reply("has-session", ExecResult(1, "", "no session"))
    tmux = controller(fake)
    await tmux.ensure_session(control_command="watch whiletrue status", cwd="/proj")
    new = next(c for c in fake.calls if c[0] == "new-session")
    assert new[:6] == ["new-session", "-d", "-s", "wt-demo", "-n", "control"]
    assert new[new.index("-c") + 1] == "/proj"
    assert new[-1] == "watch whiletrue status"
    # Pane titles are shown on a border so a pane reads "#142 implement".
    assert any(c[0] == "set-option" and "pane-border-status" in c for c in fake.calls)

    fake.calls.clear()
    fake.reply("has-session", ExecResult(0, "", ""))
    await tmux.ensure_session()
    assert "new-session" not in fake.subcommands()


async def test_first_pane_opens_an_agent_window_with_title_env_and_cwd() -> None:
    fake = FakeTmux()
    tmux = controller(fake)
    pane = await tmux.open_pane(
        title="#1 implement", argv=["claude", "do it"], cwd="/work", env={"WT_STEP": "s1"}
    )
    assert pane == PaneRef("%1", "@1")
    new = next(c for c in fake.calls if c[0] == "new-window")
    assert "-d" in new and new[new.index("-c") + 1] == "/work"
    assert new[new.index("-e") + 1] == "WT_STEP=s1"
    assert new[-1] == "claude 'do it'"
    title = next(c for c in fake.calls if c[0] == "select-pane")
    assert title == ["select-pane", "-t", "%1", "-T", "#1 implement"]


async def test_panes_fill_a_window_then_open_a_new_one() -> None:
    fake = FakeTmux()
    tmux = controller(fake, panes_per_window=2)
    panes = [await tmux.open_pane(title=f"p{i}", argv=["x"], cwd=".", env={}) for i in range(3)]
    assert [p.window for p in panes] == ["@1", "@1", "@2"]
    assert fake.subcommands().count("split-window") == 1
    assert fake.subcommands().count("new-window") == 2
    # The window is re-tiled after a split so every pane stays usable.
    assert any(c[0] == "select-layout" and c[-1] == "tiled" for c in fake.calls)


async def test_a_closed_pane_frees_its_slot() -> None:
    fake = FakeTmux()
    tmux = controller(fake, panes_per_window=2)
    a = await tmux.open_pane(title="a", argv=["x"], cwd=".", env={})
    await tmux.open_pane(title="b", argv=["x"], cwd=".", env={})
    fake.panes[a.window].remove(a.id)
    c = await tmux.open_pane(title="c", argv=["x"], cwd=".", env={})
    assert c.window == "@1"


async def test_a_vanished_window_is_forgotten() -> None:
    fake = FakeTmux()
    tmux = controller(fake)
    await tmux.open_pane(title="a", argv=["x"], cwd=".", env={})
    del fake.panes["@1"]
    pane = await tmux.open_pane(title="b", argv=["x"], cwd=".", env={})
    assert pane.window == "@2"


async def test_failures_raise_tmux_error() -> None:
    fake = FakeTmux()
    fake.reply("new-window", ExecResult(1, "", "boom"))
    with pytest.raises(TmuxError, match="boom"):
        await controller(fake).open_pane(title="a", argv=["x"], cwd=".", env={})


async def test_send_text_and_keys() -> None:
    fake = FakeTmux()
    tmux = controller(fake)
    pane = PaneRef("%3", "@1")
    await tmux.send_text(pane, "/exit")
    assert fake.calls[-2:] == [
        ["send-keys", "-t", "%3", "-l", "/exit"],
        ["send-keys", "-t", "%3", "Enter"],
    ]
    await tmux.send_keys(pane, "Escape")
    assert fake.calls[-1] == ["send-keys", "-t", "%3", "Escape"]


async def test_pane_state_and_closing() -> None:
    fake = FakeTmux()
    tmux = controller(fake)
    pane = await tmux.open_pane(title="a", argv=["x"], cwd=".", env={})
    assert await tmux.pane_state(pane) == "running"
    fake.reply("list-panes", ExecResult(0, "%1 1\n", ""))
    assert await tmux.pane_state(pane) == "dead"
    fake.reply("list-panes", ExecResult(1, "", "gone"))
    assert await tmux.pane_state(pane) == "gone"
    await tmux.keep_pane(pane, "#1 implement (done)")
    assert any(c[0] == "set-option" and "remain-on-exit" in c for c in fake.calls)
    await tmux.close_pane(pane)
    assert fake.calls[-1] == ["kill-pane", "-t", "%1"]


async def test_closing_a_missing_pane_is_not_an_error() -> None:
    fake = FakeTmux()
    fake.reply("kill-pane", ExecResult(1, "", "can't find pane"))
    await controller(fake).close_pane(PaneRef("%9", "@9"))


def test_attach_command_targets_the_session_on_the_right_socket() -> None:
    assert controller(FakeTmux()).attach_argv() == ["tmux", "attach-session", "-t", "wt-demo"]
    tmux = TmuxController("s", exec=FakeTmux(), socket="sock", binary="/bin/tmux")
    assert tmux.attach_argv() == ["/bin/tmux", "-L", "sock", "attach-session", "-t", "s"]


@pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux not installed")
async def test_against_a_real_tmux_server() -> None:
    socket = f"wt-test-{uuid.uuid4().hex[:8]}"
    tmux = TmuxController("wt-real", socket=socket)
    try:
        await tmux.ensure_session()
        pane = await tmux.open_pane(
            title="#1 demo", argv=["sleep", "30"], cwd="/tmp", env={"WT_X": "1"}
        )
        assert await tmux.pane_state(pane) == "running"
        await tmux.close_pane(pane)
        assert await tmux.pane_state(pane) == "gone"
    finally:
        await tmux.kill_server()


def test_session_names_are_derived_from_the_project_directory() -> None:
    assert session_name("/home/me/dev/my.project:x") == "wt-my-project-x"
    assert session_name("/") == "wt-project"


class SlowTmux(FakeTmux):
    """Yields to the event loop on every call, like a real subprocess, and tracks the session."""

    def __init__(self) -> None:
        super().__init__()
        self.session_exists = False

    async def __call__(self, argv: list[str]) -> ExecResult:
        await asyncio.sleep(0)
        if argv[0] == "has-session":
            self.calls.append(argv)
            return ExecResult(0 if self.session_exists else 1, "", "")
        if argv[0] == "new-session":
            self.calls.append(argv)
            if self.session_exists:
                return ExecResult(1, "", "duplicate session: wt-demo")
            self.session_exists = True
            return ExecResult(0, "", "")
        return await super().__call__(argv)


async def test_parallel_items_share_one_session_and_one_window() -> None:
    fake = SlowTmux()
    tmux = controller(fake)

    async def start(name: str) -> PaneRef:
        await tmux.ensure_session("watch", "/proj")
        return await tmux.open_pane(title=name, argv=["claude"], cwd="/proj", env={})

    panes = await asyncio.gather(start("a"), start("b"), start("c"))

    assert fake.subcommands().count("new-session") == 1
    assert fake.subcommands().count("new-window") == 1
    assert len({pane.window for pane in panes}) == 1
