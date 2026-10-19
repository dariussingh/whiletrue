"""Claude Code runners."""

from whiletrue.claude_code.headless import (
    HeadlessOptions,
    HeadlessRunner,
    build_headless_args,
    create_headless_runner,
)
from whiletrue.core import PluginApi, SimplePlugin


def claude_code_plugin(options: HeadlessOptions | None = None) -> SimplePlugin:
    """Registers the Claude Code runners."""

    def register(api: PluginApi) -> None:
        api.register_runner(create_headless_runner(options))

    return SimplePlugin(name="whiletrue.claude_code", register=register)


__all__ = [
    "HeadlessOptions",
    "HeadlessRunner",
    "build_headless_args",
    "claude_code_plugin",
    "create_headless_runner",
]
