import os
import re
import stat
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from whiletrue.abort import AbortSignal
from whiletrue.cli import EXIT, Io, create_default_registry, load_plugins, main
from whiletrue.cli import registry as cli_registry


@dataclass
class Cli:
    dir: Path
    out: str = ""
    err: str = ""
    _signal: AbortSignal | None = field(default=None, repr=False)

    async def __call__(self, *args: str, signal: AbortSignal | None = None) -> int:
        def out(text: str) -> None:
            self.out += text

        def err(text: str) -> None:
            self.err += text

        return await main(list(args), Io(cwd=str(self.dir), out=out, err=err, signal=signal))

    def reset(self) -> None:
        self.out = ""
        self.err = ""


@pytest.fixture
def cli(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Cli]:
    monkeypatch.delenv("WHILETRUE_CLAUDE_BIN", raising=False)
    yield Cli(tmp_path)


def success(cost: float = 0.01) -> str:
    return (
        'echo \'{"type":"result","subtype":"success","is_error":false,"result":"ok",'
        f'"total_cost_usd":{cost},"num_turns":1,"session_id":"s"}}\''
    )


def fake_claude(directory: Path, fix_on_attempt: int) -> str:
    """A fake `claude` that "fixes the bug" (touches done.flag) on its Nth invocation."""
    p = directory / "fake-claude.sh"
    p.write_text(
        f"""#!/bin/sh
n=$(cat count 2>/dev/null || echo 0); n=$((n+1)); echo $n > count
cat > "prompt-$n.txt"
[ "$n" -ge {fix_on_attempt} ] && touch done.flag
{success()}
"""
    )
    p.chmod(p.stat().st_mode | stat.S_IEXEC)
    return str(p)


def ralph_yaml(max_iterations: int = 5) -> str:
    return f"""name: ralph
pipeline:
  loop:
    max: {max_iterations}
    do:
      - agent: {{ uses: ./agents/ralph.md }}
      - gate.shell: {{ run: "test -f done.flag || (echo 'done.flag missing'; exit 1)" }}
"""


def project(directory: Path, max_iterations: int = 5) -> None:
    (directory / "whiletrue.yaml").write_text(ralph_yaml(max_iterations))
    (directory / "agents").mkdir(exist_ok=True)
    (directory / "agents" / "ralph.md").write_text(
        "---\nagent: ralph\nlimits: { budget_usd: 1, turns: 5, wall: 1m }\n---\n"
        "Fix it. Previous: {{loop.previous}}\n"
    )


class TestUsage:
    async def test_prints_help(self, cli: Cli) -> None:
        assert await cli("--help") == EXIT.ok
        assert "Usage: whiletrue" in cli.out

    async def test_shows_usage_on_stderr_when_no_command_is_given(self, cli: Cli) -> None:
        assert await cli() == EXIT.usage
        assert "Usage: whiletrue" in cli.err

    async def test_rejects_unknown_commands_and_options(self, cli: Cli) -> None:
        assert await cli("frobnicate") == EXIT.usage
        assert 'Unknown command "frobnicate"' in cli.err
        cli.reset()
        assert await cli("run", "--nope") == EXIT.usage
        assert "nope" in cli.err

    async def test_a_dangling_file_option_is_a_usage_error(self, cli: Cli) -> None:
        assert await cli("validate", "-f") == EXIT.usage
        assert "argument missing" in cli.err

    async def test_supports_the_file_equals_form(self, cli: Cli) -> None:
        (cli.dir / "alt.yaml").write_text("pipeline: [ { shell: { run: 'true' } } ]\n")
        assert await cli("validate", "--file=alt.yaml") == EXIT.ok


class TestInit:
    async def test_scaffolds_a_ralph_pipeline_that_validates(self, cli: Cli) -> None:
        assert await cli("init") == EXIT.ok
        assert (cli.dir / "whiletrue.yaml").exists()
        assert (cli.dir / "agents" / "ralph.md").exists()
        assert await cli("validate") == EXIT.ok

    async def test_refuses_to_overwrite_without_force(self, cli: Cli) -> None:
        await cli("init")
        (cli.dir / "whiletrue.yaml").write_text("custom")
        cli.reset()
        assert await cli("init") == EXIT.failed
        assert "Refusing to overwrite whiletrue.yaml" in cli.err
        assert (cli.dir / "whiletrue.yaml").read_text() == "custom"
        assert await cli("init", "--force") == EXIT.ok
        assert "name: ralph" in (cli.dir / "whiletrue.yaml").read_text()


class TestValidateAndPlan:
    async def test_validate_reports_ok(self, cli: Cli) -> None:
        project(cli.dir)
        assert await cli("validate") == EXIT.ok
        assert "whiletrue.yaml: ok" in cli.out

    async def test_validate_reports_diagnostics_and_fails(self, cli: Cli) -> None:
        project(cli.dir)
        (cli.dir / "whiletrue.yaml").write_text(
            "pipeline:\n  - nope: {}\n  - loop: { do: [ { shell: { run: x } } ] }\n"
        )
        assert await cli("validate") == EXIT.failed
        assert re.search(r'whiletrue\.yaml:2:\d+: error: Unknown module "nope"', cli.err)
        assert "requires `max`" in cli.err

    async def test_reports_config_syntax_errors_and_missing_files(self, cli: Cli) -> None:
        assert await cli("validate") == EXIT.failed
        assert "Cannot load whiletrue.yaml" in cli.err
        cli.reset()
        (cli.dir / "whiletrue.yaml").write_text("pipeline: [\n")
        assert await cli("validate") == EXIT.failed
        assert re.search(r"whiletrue\.yaml:\d+:\d+:", cli.err)

    async def test_supports_file(self, cli: Cli) -> None:
        (cli.dir / "alt.yaml").write_text("pipeline: [ { shell: { run: 'true' } } ]\n")
        assert await cli("validate", "-f", "alt.yaml") == EXIT.ok

    async def test_plan_prints_the_expanded_tree_without_running_anything(self, cli: Cli) -> None:
        project(cli.dir)
        assert await cli("plan") == EXIT.ok
        assert "ralph\n" in cli.out
        assert 'loop {"max":5}' in cli.out
        assert "├─ agent" in cli.out
        assert "└─ gate.shell" in cli.out
        assert not (cli.dir / ".whiletrue").exists()

    async def test_plan_fails_on_invalid_config(self, cli: Cli) -> None:
        (cli.dir / "whiletrue.yaml").write_text("pipeline: [ { nope: {} } ]\n")
        assert await cli("plan") == EXIT.failed


class TestRun:
    async def test_m0_exit_a_bounded_ralph_loop_runs_until_the_gate_passes(
        self, cli: Cli, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project(cli.dir)
        monkeypatch.setenv("WHILETRUE_CLAUDE_BIN", fake_claude(cli.dir, 3))
        assert await cli("run") == EXIT.ok
        assert re.search(r"run [0-9a-f-]{36}: done", cli.out)
        assert "main: done (ok)" in cli.out
        assert "loop iteration 3/5" in cli.out
        # Fresh session per attempt, with the previous failure fed back in.
        assert (cli.dir / "prompt-1.txt").read_text() == "Fix it. Previous: "
        assert "done.flag missing" in (cli.dir / "prompt-2.txt").read_text()
        assert (cli.dir / "count").read_text().strip() == "3"

    async def test_status_shows_the_run_steps_and_cost(
        self, cli: Cli, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project(cli.dir)
        monkeypatch.setenv("WHILETRUE_CLAUDE_BIN", fake_claude(cli.dir, 2))
        await cli("run")
        cli.reset()
        assert await cli("status") == EXIT.ok
        assert re.search(r"run [0-9a-f-]{36}  done", cli.out)
        assert "main  done  steps=" in cli.out
        assert "cost=$0.0200" in cli.out
        assert re.search(r"root\.1#1  completed  fail", cli.out)
        match = re.search(r"run ([0-9a-f-]{36})", cli.out)
        assert match is not None
        run_id = match.group(1)
        cli.reset()
        assert await cli("status", run_id) == EXIT.ok
        assert run_id in cli.out

    async def test_parks_the_item_exit_2_when_the_loop_is_exhausted(
        self, cli: Cli, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        project(cli.dir, 2)
        monkeypatch.setenv("WHILETRUE_CLAUDE_BIN", fake_claude(cli.dir, 99))
        assert await cli("run") == EXIT.parked
        assert "main: parked (fail)" in cli.out

    async def test_fails_fast_when_validation_fails_without_creating_state(self, cli: Cli) -> None:
        (cli.dir / "whiletrue.yaml").write_text("pipeline: [ { nope: {} } ]\n")
        assert await cli("run") == EXIT.failed
        assert not (cli.dir / ".whiletrue").exists()

    async def test_returns_exit_1_when_the_run_fails_outright(self, cli: Cli) -> None:
        (cli.dir / "whiletrue.yaml").write_text(
            "on_fail: drop\npipeline: [ { shell: { run: 'exit 3' } } ]\n"
        )
        assert await cli("run") == EXIT.failed
        assert "main: failed (fail)" in cli.out

    async def test_returns_130_when_cancelled(self, cli: Cli) -> None:
        project(cli.dir)
        signal = AbortSignal()
        signal.abort()
        assert await cli("run", signal=signal) == EXIT.cancelled

    async def test_forwards_non_debug_logs_to_the_output(self, cli: Cli) -> None:
        (cli.dir / "whiletrue.yaml").write_text(
            "pipeline: { loop: { max: 1, do: [ { shell: { run: 'true' } } ] } }\n"
        )
        await cli("run")
        assert "[info] loop iteration 1/1" in cli.out


class TestStatusWithoutRuns:
    async def test_reports_a_missing_database_and_unknown_runs(self, cli: Cli) -> None:
        assert await cli("status") == EXIT.failed
        assert "No runs yet" in cli.err
        (cli.dir / "whiletrue.yaml").write_text("pipeline: [ { shell: { run: 'true' } } ]\n")
        await cli("run")
        cli.reset()
        assert await cli("status", "nope") == EXIT.failed
        assert 'No run "nope"' in cli.err

    async def test_reports_an_empty_database(self, cli: Cli) -> None:
        from whiletrue.store_sqlite import SqliteStore

        SqliteStore(cli.dir / ".whiletrue" / "state.db").close()
        assert await cli("status") == EXIT.failed
        assert "No runs yet" in cli.err


class TestPlugins:
    @pytest.fixture(autouse=True)
    def _clean_modules(self) -> Iterator[None]:
        before = set(sys.modules)
        yield
        for name in set(sys.modules) - before:
            sys.modules.pop(name, None)

    def write_plugin(self, cli: Cli, name: str, source: str) -> None:
        (cli.dir / f"{name}.py").write_text(source)

    async def test_loads_project_plugins_named_in_uses(self, cli: Cli) -> None:
        self.write_plugin(
            cli,
            "demo_plugin",
            """
from pydantic import BaseModel
from whiletrue.core import ActionModule, Emit, Ports, SimplePlugin


class Cfg(BaseModel):
    pass


async def run(_input, _ctx):
    return Emit("ok")


def register(api):
    api.register_action(
        ActionModule(id="demo.hi", config=Cfg, ports=Ports(), effects="pure", run=run)
    )


plugin = SimplePlugin(name="demo_plugin", register=register)
""",
        )
        (cli.dir / "whiletrue.yaml").write_text(
            'uses: ["demo_plugin"]\npipeline: [ { demo.hi: {} } ]\n'
        )
        assert await cli("validate") == EXIT.ok
        assert not any(p == str(cli.dir) for p in sys.path)

    async def test_reports_missing_plugins_and_bad_exports(self, cli: Cli) -> None:
        (cli.dir / "whiletrue.yaml").write_text(
            'uses: ["not_installed_xyz"]\npipeline: [ { shell: { run: x } } ]\n'
        )
        assert await cli("validate") == EXIT.failed
        assert 'Cannot find plugin "not_installed_xyz"' in cli.err
        cli.reset()
        self.write_plugin(cli, "bad_plugin", "plugin = 42\n")
        (cli.dir / "whiletrue.yaml").write_text(
            'uses: ["bad_plugin"]\npipeline: [ { shell: { run: x } } ]\n'
        )
        assert await cli("validate") == EXIT.failed
        assert "must export a Plugin" in cli.err

    async def test_a_plugin_that_fails_to_import_is_reported_as_missing_dependency(
        self, cli: Cli
    ) -> None:
        self.write_plugin(cli, "broken_plugin", "import definitely_not_a_module_xyz\n")
        (cli.dir / "whiletrue.yaml").write_text(
            'uses: ["broken_plugin"]\npipeline: [ { shell: { run: x } } ]\n'
        )
        assert await cli("validate") == EXIT.failed
        assert 'Cannot find plugin "broken_plugin"' in cli.err

    def test_accepts_a_factory_and_skips_builtin_names(self, cli: Cli) -> None:
        self.write_plugin(
            cli,
            "factory_plugin",
            """
from whiletrue.core import SimplePlugin


def plugin():
    return SimplePlugin(name="factory_plugin", register=lambda api: None)
""",
        )
        registry = create_default_registry(cli_registry.RegistryOptions(claude_command="x"))
        load_plugins(registry, ["whiletrue.basics", "factory_plugin"], str(cli.dir))
        assert "factory_plugin" in registry.plugins()

    def test_loads_plugins_from_entry_points(
        self, cli: Cli, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from importlib.metadata import EntryPoint

        self.write_plugin(
            cli,
            "ep_plugin",
            "from whiletrue.core import SimplePlugin\n"
            "plugin = SimplePlugin(name='ep', register=lambda api: None)\n",
        )
        sys.path.insert(0, str(cli.dir))
        monkeypatch.setattr(
            cli_registry,
            "entry_points",
            lambda group: [EntryPoint("ep", "ep_plugin:plugin", group)],
        )
        registry = create_default_registry()
        load_plugins(registry, ["ep"], str(cli.dir))
        assert "ep" in registry.plugins()
        sys.path.remove(str(cli.dir))

    def test_an_entry_point_that_is_not_a_plugin_is_an_error(
        self, cli: Cli, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from importlib.metadata import EntryPoint

        self.write_plugin(cli, "ep_bad", "plugin = 3\n")
        sys.path.insert(0, str(cli.dir))
        monkeypatch.setattr(
            cli_registry,
            "entry_points",
            lambda group: [EntryPoint("epbad", "ep_bad:plugin", group)],
        )
        with pytest.raises(TypeError, match="must export a Plugin"):
            load_plugins(create_default_registry(), ["epbad"], str(cli.dir))
        sys.path.remove(str(cli.dir))


def test_default_registry_reads_the_claude_binary_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("WHILETRUE_CLAUDE_BIN", "/custom/claude")
    runner = create_default_registry().get_runner("claude-code-headless")
    assert runner is not None
    assert runner._options.command == "/custom/claude"  # type: ignore[attr-defined]
    assert os.environ["WHILETRUE_CLAUDE_BIN"] == "/custom/claude"


async def test_amain_wires_signals_and_real_streams(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from whiletrue.cli import app

    assert await app._amain(["--help"]) == EXIT.ok
    assert "Usage: whiletrue" in capsys.readouterr().out


def test_entrypoint_exits_with_the_command_status(monkeypatch: pytest.MonkeyPatch) -> None:
    from whiletrue.cli import app

    monkeypatch.setattr(sys, "argv", ["whiletrue", "--help"])
    with pytest.raises(SystemExit) as info:
        app.entrypoint()
    assert info.value.code == EXIT.ok


def test_module_execution_works_as_a_subprocess() -> None:
    import subprocess

    done = subprocess.run(
        [sys.executable, "-m", "whiletrue", "--help"], capture_output=True, text=True, check=False
    )
    assert done.returncode == 0
    assert "Usage: whiletrue" in done.stdout
