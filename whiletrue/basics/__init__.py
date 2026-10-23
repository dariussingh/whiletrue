"""Built-in modules: shell, gate.*, exec, set, agent, session.end, context.reset."""

from whiletrue.basics.agent import AgentConfig, agent_module
from whiletrue.basics.diff_size import DiffSizeConfig, diff_size_module
from whiletrue.basics.exec_ import ExecConfig, exec_module
from whiletrue.basics.process import CommandResult, run_command, tail
from whiletrue.basics.sessions import (
    SessionEndConfig,
    context_reset_module,
    session_end_module,
)
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
    api.register_action(session_end_module)
    api.register_action(context_reset_module)


#: Registers the built-in modules through the same public plugin API third parties use.
basics_plugin = SimplePlugin(name="whiletrue.basics", register=_register)

__all__ = [
    "AgentConfig",
    "CommandResult",
    "DiffSizeConfig",
    "ExecConfig",
    "GateTestsConfig",
    "SessionEndConfig",
    "SetConfig",
    "ShellConfig",
    "agent_module",
    "basics_plugin",
    "context_reset_module",
    "diff_size_module",
    "exec_module",
    "gate_shell_module",
    "gate_tests_module",
    "run_command",
    "session_end_module",
    "set_module",
    "shell_module",
    "tail",
]
