from whiletrue.basics import basics_plugin
from whiletrue.core import Registry


def test_registers_the_builtin_modules() -> None:
    r = Registry()
    r.use(basics_plugin)
    assert r.ids() == [
        "agent",
        "comments.reply",
        "context.history",
        "context.reset",
        "exec",
        "gate.comments_answered",
        "gate.diff_size",
        "gate.shell",
        "gate.tests",
        "history.distill",
        "human.approve",
        "session.end",
        "set",
        "shell",
    ]
