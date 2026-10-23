"""tmux session, window and pane management (the TUI's foundation)."""

from whiletrue.tmux.controller import (
    ExecResult,
    PaneRef,
    TmuxController,
    TmuxError,
    session_name,
)

__all__ = ["ExecResult", "PaneRef", "TmuxController", "TmuxError", "session_name"]
