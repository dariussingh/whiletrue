from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from whiletrue.abort import AbortSignal
from whiletrue.claude_code import PaneHost
from whiletrue.github import GitHubClient
from whiletrue.tmux import TmuxController


@dataclass
class Io:
    cwd: str
    out: Callable[[str], object]
    err: Callable[[str], object]
    signal: AbortSignal | None = None
    #: The project's tmux controller; defaults to the real one (tests inject a fake).
    tmux: TmuxController | None = None
    #: Replaces this process with the given argv (tmux attach); defaults to `os.execvp`.
    attach: Callable[[list[str]], object] | None = None
    #: Where interactive agents run; defaults to tmux panes (tests inject a plain-process host).
    pane_host: PaneHost | None = None
    #: GitHub access; defaults to the `gh` CLI (tests inject an in-memory fake).
    github: GitHubClient | None = None


class Exit:
    ok = 0
    failed = 1
    parked = 2
    usage = 64
    cancelled = 130


EXIT = Exit()
