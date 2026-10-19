"""Built-in modules: shell, gate.shell, exec, agent."""

from whiletrue.basics.agent import AgentConfig, agent_module
from whiletrue.basics.exec_ import ExecConfig, exec_module
from whiletrue.basics.process import CommandResult, run_command, tail
from whiletrue.basics.shell import ShellConfig, gate_shell_module, shell_module
from whiletrue.core import PluginApi, SimplePlugin


def _register(api: PluginApi) -> None:
    api.register_action(shell_module)
    api.register_action(gate_shell_module)
    api.register_action(exec_module)
    api.register_action(agent_module)


#: Registers the built-in modules through the same public plugin API third parties use.
basics_plugin = SimplePlugin(name="whiletrue.basics", register=_register)

__all__ = [
    "AgentConfig",
    "CommandResult",
    "ExecConfig",
    "ShellConfig",
    "agent_module",
    "basics_plugin",
    "exec_module",
    "gate_shell_module",
    "run_command",
    "shell_module",
    "tail",
]
