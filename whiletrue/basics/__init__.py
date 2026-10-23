"""Built-in modules: shell, gate.*, exec, set, agent, sessions, human.approve, comments."""

from whiletrue.basics.agent import AgentConfig, agent_module
from whiletrue.basics.comments import (
    CommentReplies,
    CommentReply,
    comments_reply_module,
    gate_comments_answered_module,
)
from whiletrue.basics.diff_size import DiffSizeConfig, diff_size_module
from whiletrue.basics.exec_ import ExecConfig, exec_module
from whiletrue.basics.history import (
    DistillConfig,
    HistoryConfig,
    context_history_module,
    history_distill_module,
    render_history,
)
from whiletrue.basics.human import ApproveConfig, approve_strategy
from whiletrue.basics.json_source import JsonSource
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
from whiletrue.basics.wake import FileExistsWake, TimerWake
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
    api.register_action(comments_reply_module)
    api.register_action(context_history_module)
    api.register_action(history_distill_module)
    api.register_action(gate_comments_answered_module)
    api.register_strategy(approve_strategy)
    api.register_provider("source", "json", JsonSource())
    api.register_provider("wake", "file_exists", FileExistsWake())
    api.register_provider("wake", "timer", TimerWake())
    api.register_cap("CommentReplies", CommentReplies)


#: Registers the built-in modules through the same public plugin API third parties use.
basics_plugin = SimplePlugin(name="whiletrue.basics", register=_register)

__all__ = [
    "AgentConfig",
    "ApproveConfig",
    "CommandResult",
    "CommentReplies",
    "CommentReply",
    "DiffSizeConfig",
    "DistillConfig",
    "ExecConfig",
    "GateTestsConfig",
    "HistoryConfig",
    "JsonSource",
    "SessionEndConfig",
    "SetConfig",
    "ShellConfig",
    "agent_module",
    "approve_strategy",
    "basics_plugin",
    "comments_reply_module",
    "context_history_module",
    "context_reset_module",
    "diff_size_module",
    "exec_module",
    "gate_comments_answered_module",
    "gate_shell_module",
    "gate_tests_module",
    "history_distill_module",
    "render_history",
    "run_command",
    "session_end_module",
    "set_module",
    "shell_module",
    "tail",
]
