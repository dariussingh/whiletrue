"""Built-in modules: shell, gate.shell, gate.tests, gate.diff_size, exec, set, agent."""

from whiletrue.basics.agent import AgentConfig, agent_module
from whiletrue.basics.diff_size import DiffSizeConfig, diff_size_module
from whiletrue.basics.exec_ import ExecConfig, exec_module
from whiletrue.basics.process import CommandResult, run_command, tail
from whiletrue.basics.set_ import SetConfig, set_module
from whiletrue.basics.shell import (
    GateTestsConfig,
    ShellConfig,
    gate_shell_module,
    gate_tests_module,
    shell_module,
)
from whiletrue.core import PluginApi, SimplePlugin


def _register(api: PluginApi) -> None:
    api.register_action(shell_module)
    api.register_action(gate_shell_module)
    api.register_action(gate_tests_module)
    api.register_action(diff_size_module)
    api.register_action(exec_module)
    api.register_action(set_module)
    api.register_action(agent_module)


#: Registers the built-in modules through the same public plugin API third parties use.
basics_plugin = SimplePlugin(name="whiletrue.basics", register=_register)

__all__ = [
    "AgentConfig",
    "CommandResult",
    "DiffSizeConfig",
    "ExecConfig",
    "GateTestsConfig",
    "SetConfig",
    "ShellConfig",
    "agent_module",
    "basics_plugin",
    "diff_size_module",
    "exec_module",
    "gate_shell_module",
    "gate_tests_module",
    "run_command",
    "set_module",
    "shell_module",
    "tail",
]
