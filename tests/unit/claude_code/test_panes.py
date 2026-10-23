import asyncio
from pathlib import Path

from whiletrue.claude_code.panes import ProcessPaneHost, TmuxPaneHost
from whiletrue.tmux import ExecResult, TmuxController


class ScriptedTmux:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.alive = "0"

    async def __call__(self, argv: list[str]) -> ExecResult:
        self.calls.append(argv)
        match argv[0]:
            case "has-session":
                return ExecResult(0, "", "")
            case "new-window":
                return ExecResult(0, "%1 @1\n", "")
            case "list-panes":
                return ExecResult(0, f"%1 {self.alive}\n", "")
            case _:
                return ExecResult(0, "", "")


async def test_tmux_host_opens_a_titled_pane_and_drives_it() -> None:
    fake = ScriptedTmux()
    host = TmuxPaneHost(TmuxController("wt-x", exec=fake), control_command="whiletrue status")
    pane = await host.open(title="#1 impl", argv=["sh", "-c", "claude"], cwd="/w", env={"A": "1"})
    assert await pane.state() == "running"
    fake.alive = "1"
    assert await pane.state() == "dead"
    await pane.send_text("/exit")
    await pane.set_title("! #1 impl needs you")
    assert ["select-pane", "-t", "%1", "-T", "! #1 impl needs you"] in fake.calls
    await pane.finish(keep=True, title="#1 impl (done)")
    assert any("remain-on-exit" in c for c in fake.calls)
    await pane.finish(keep=False, title="x")
    await pane.kill()
    assert fake.calls[-1] == ["kill-pane", "-t", "%1"]


async def test_process_host_runs_a_command_and_reports_its_state(tmp_path: Path) -> None:
    host = ProcessPaneHost()
    pane = await host.open(title="t", argv=["sleep", "30"], cwd=str(tmp_path), env={})
    assert await pane.state() == "running"
    await pane.set_title("renamed")
    await pane.finish(keep=False, title="done")
    assert await pane.state() == "dead"
    await pane.send_text("ignored after exit")
    await pane.kill()


async def test_process_host_delivers_text_on_stdin(tmp_path: Path) -> None:
    out = tmp_path / "got"
    host = ProcessPaneHost()
    pane = await host.open(
        title="t", argv=["sh", "-c", f"read line; echo $line > {out}"], cwd=str(tmp_path), env={}
    )
    await pane.send_text("hello")
    for _ in range(100):
        if await pane.state() == "dead":
            break
        await asyncio.sleep(0.02)
    assert out.read_text().strip() == "hello"
