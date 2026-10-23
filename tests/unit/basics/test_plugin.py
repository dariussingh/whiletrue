from whiletrue.basics import basics_plugin
from whiletrue.core import Registry


def test_registers_the_builtin_modules() -> None:
    r = Registry()
    r.use(basics_plugin)
    assert r.ids() == [
        "agent",
        "exec",
        "gate.diff_size",
        "gate.shell",
        "gate.tests",
        "set",
        "shell",
    ]
