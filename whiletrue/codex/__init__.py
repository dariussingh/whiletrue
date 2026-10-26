"""The OpenAI Codex CLI runner."""

from whiletrue.codex.runner import CodexOptions, CodexRunner, build_codex_args
from whiletrue.core import PluginApi, SimplePlugin


def codex_plugin(options: CodexOptions | None = None) -> SimplePlugin:
    def register(api: PluginApi) -> None:
        api.register_runner(CodexRunner(options))

    return SimplePlugin(name="whiletrue.codex", register=register)


__all__ = ["CodexOptions", "CodexRunner", "build_codex_args", "codex_plugin"]
