"""tmux session, window and pane management (the TUI's foundation) and where agents run."""

from whiletrue.tmux.controller import (
    ExecResult,
    PaneRef,
    TmuxController,
    TmuxError,
    session_name,
)
from whiletrue.tmux.panes import Pane, PaneHost, ProcessPaneHost, StreamMirror, TmuxPaneHost

__all__ = [
    "ExecResult",
    "Pane",
    "PaneHost",
    "PaneRef",
    "ProcessPaneHost",
    "StreamMirror",
    "TmuxController",
    "TmuxError",
    "TmuxPaneHost",
    "session_name",
]
