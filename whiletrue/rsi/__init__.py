"""Recursive self-improvement: optional, off by default. One subpackage per method.

`whiletrue.rsi.harness2` is the first method (Harness², arXiv:2610.14224). The plugin registers
its strategy and adoption steps; the top-level `rsi:` setting is applied by `apply_rsi`.
"""

from whiletrue.core import PluginApi, SimplePlugin
from whiletrue.rsi.harness2.adopt import rsi_adopt_module, rsi_discard_module, rsi_review_strategy
from whiletrue.rsi.harness2.apply import apply_rsi
from whiletrue.rsi.harness2.config import SettingError
from whiletrue.rsi.harness2.strategy import rsi_harness2_strategy


def _register(api: PluginApi) -> None:
    api.register_strategy(rsi_harness2_strategy)
    api.register_strategy(rsi_review_strategy)
    api.register_action(rsi_adopt_module)
    api.register_action(rsi_discard_module)


rsi_plugin = SimplePlugin(name="whiletrue.rsi", register=_register)
#: Lets a pipeline name it: `uses: [whiletrue.rsi]`.
plugin = rsi_plugin

__all__ = ["SettingError", "apply_rsi", "plugin", "rsi_plugin"]
