"""Control-flow strategies: sequence and loop (more arrive in M1)."""

from whiletrue.control.chain import run_chain
from whiletrue.control.loop import LoopConfig, loop_strategy
from whiletrue.control.sequence import sequence_strategy
from whiletrue.core import PluginApi, SimplePlugin


def _register(api: PluginApi) -> None:
    api.register_strategy(sequence_strategy)
    api.register_strategy(loop_strategy)


control_plugin = SimplePlugin(name="whiletrue.control", register=_register)

__all__ = ["LoopConfig", "control_plugin", "loop_strategy", "run_chain", "sequence_strategy"]
