"""Claude Code runners: headless (`claude -p`) and interactive (a live pane)."""

from whiletrue.claude_code.endpoint import EventEndpoint, SessionHandler, ToolResult
from whiletrue.claude_code.headless import (
    HeadlessOptions,
    HeadlessRunner,
    build_headless_args,
    create_headless_runner,
)
from whiletrue.claude_code.interactive import (
    PLUGIN_DIR,
    InteractiveOptions,
    InteractiveRunner,
    build_interactive_args,
    create_interactive_runner,
)
from whiletrue.claude_code.panes import (
    Pane,
    PaneHost,
    ProcessPaneHost,
    StreamMirror,
    TmuxPaneHost,
)
from whiletrue.core import PluginApi, SimplePlugin


def claude_code_plugin(
    options: HeadlessOptions | None = None, interactive: InteractiveOptions | None = None
) -> SimplePlugin:
    """Registers the Claude Code runners."""

    def register(api: PluginApi) -> None:
        api.register_runner(create_headless_runner(options))
        api.register_runner(create_interactive_runner(interactive))

    return SimplePlugin(name="whiletrue.claude_code", register=register)


__all__ = [
    "PLUGIN_DIR",
    "EventEndpoint",
    "HeadlessOptions",
    "HeadlessRunner",
    "InteractiveOptions",
    "InteractiveRunner",
    "Pane",
    "PaneHost",
    "ProcessPaneHost",
    "SessionHandler",
    "StreamMirror",
    "TmuxPaneHost",
    "ToolResult",
    "build_headless_args",
    "build_interactive_args",
    "claude_code_plugin",
    "create_headless_runner",
    "create_interactive_runner",
]
