"""The opencode runner (second opinions and review passes)."""

from whiletrue.core import PluginApi, SimplePlugin
from whiletrue.opencode.runner import (
    OpencodeOptions,
    OpencodeRunner,
    build_opencode_args,
)


def opencode_plugin(options: OpencodeOptions | None = None) -> SimplePlugin:
    def register(api: PluginApi) -> None:
        api.register_runner(OpencodeRunner(options))

    return SimplePlugin(name="whiletrue.opencode", register=register)


__all__ = [
    "OpencodeOptions",
    "OpencodeRunner",
    "build_opencode_args",
    "opencode_plugin",
]
