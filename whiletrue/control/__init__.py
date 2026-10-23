"""Control-flow strategies: sequence, loop, map, parallel, switch, retry, try."""

from whiletrue.control.chain import run_chain
from whiletrue.control.loop import LoopConfig, loop_strategy
from whiletrue.control.map_ import map_strategy
from whiletrue.control.parallel import parallel_strategy
from whiletrue.control.retry import retry_strategy, try_strategy
from whiletrue.control.sequence import sequence_strategy
from whiletrue.control.switch import switch_strategy
from whiletrue.core import PluginApi, SimplePlugin


def _register(api: PluginApi) -> None:
    api.register_strategy(sequence_strategy)
    api.register_strategy(loop_strategy)
    api.register_strategy(map_strategy)
    api.register_strategy(parallel_strategy)
    api.register_strategy(switch_strategy)
    api.register_strategy(retry_strategy)
    api.register_strategy(try_strategy)


control_plugin = SimplePlugin(name="whiletrue.control", register=_register)

__all__ = [
    "LoopConfig",
    "control_plugin",
    "loop_strategy",
    "map_strategy",
    "parallel_strategy",
    "retry_strategy",
    "run_chain",
    "sequence_strategy",
    "switch_strategy",
    "try_strategy",
]
