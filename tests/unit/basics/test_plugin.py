from whiletrue.basics import basics_plugin
from whiletrue.core import Registry


def test_registers_shell_gate_shell_exec_and_agent() -> None:
    r = Registry()
    r.use(basics_plugin)
    assert r.ids() == ["agent", "exec", "gate.shell", "shell"]
