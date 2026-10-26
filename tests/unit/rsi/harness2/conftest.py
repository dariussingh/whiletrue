"""A scripted agent runner that plays every role in an rsi run, plus helpers to run one."""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from whiletrue.basics import basics_plugin
from whiletrue.control import control_plugin
from whiletrue.core import (
    AgentRequest,
    AgentResult,
    EventBus,
    MemoryStore,
    Registry,
    RunContext,
    RunnerCapabilities,
    RunResult,
    parse_pipeline,
    run_pipeline,
)
from whiletrue.rsi import rsi_plugin

CAREFUL = "BE CAREFUL with config files."
TESTS_FIRST = "Run the tests first."
EXPERIENCE = (
    "<edits>component: CLAUDE.md\nverdict: helped</edits>\n"
    "<experience>cue: a config file changes\nlesson: CLAUDE.md should warn about config files\n"
    "evidence: dom1 rollout step 2\nconfidence: medium</experience>"
)


@dataclass
class Script:
    """What the scripted agent does. Counters let tests check what ran (and what was reused)."""

    cost: float = 0.1
    fail_roles: set[str] = field(default_factory=set)
    #: Called with (role, cwd) before each run; may raise to fail a session.
    hook: Callable[[str, Path], None] | None = None
    calls: list[tuple[str, str]] = field(default_factory=lambda: [])
    #: Overrides for what a role returns as text.
    replies: dict[str, str] = field(default_factory=lambda: {})
    #: For `propose`, what to append to CLAUDE.md per dimension (INV or DOM).
    edits: dict[str, str] = field(default_factory=lambda: {"INV": TESTS_FIRST, "DOM": CAREFUL})
    #: Extra files an improver writes into context/harness (path -> content).
    extra_files: dict[str, str] = field(default_factory=lambda: {})
    #: With this many invalid attempts first, the improver writes a skill whose name is wrong.
    invalid_first: int = 0
    _invalid_left: int = 0

    def __post_init__(self) -> None:
        self._invalid_left = self.invalid_first

    def count(self, role: str) -> int:
        return sum(1 for r, _ in self.calls if r == role)


def role_of(prompt: str) -> str:
    if "YOUR DIMENSION: INV" in prompt:
        return "propose-INV"
    if "YOUR DIMENSION: DOM" in prompt:
        return "propose-DOM"
    if "composing THE final harness" in prompt or "composing checkpoint" in prompt:
        return "compose"
    if "choosing which of" in prompt:
        return "select"
    if "complete record of one improvement round" in prompt:
        return "reflect"
    return "rollout"


class ScriptedRunner:
    id = "claude-code-headless"
    capabilities = RunnerCapabilities(
        interactive=False, structured_output=True, resume=False, fork=False, budget=True
    )

    def __init__(self, script: Script) -> None:
        self.script = script
        self.requests: list[AgentRequest] = []

    async def run(self, request: AgentRequest, ctx: RunContext) -> AgentResult:
        script, cwd = self.script, Path(request.cwd)
        role = role_of(request.prompt)
        script.calls.append((role, request.cwd))
        self.requests.append(request)
        if script.hook is not None:
            script.hook(role, cwd)
        if role in script.fail_roles:
            return AgentResult(ok=False, text="", error=f"{role} exploded", cost_usd=script.cost)
        text = script.replies.get(role)
        if role == "rollout":
            memory = cwd / "CLAUDE.md"
            rules = memory.read_text() if memory.is_file() else ""
            result = "careful" if CAREFUL in rules else "plain"
            (cwd / "out.txt").write_text(result)
            if ctx.on_raw:
                ctx.on_raw(
                    {
                        "type": "assistant",
                        "message": {"content": [{"type": "text", "text": f"wrote {result}"}]},
                    }
                )
            text = text or f"done ({result})"
        elif role.startswith("propose"):
            self._edit(cwd, script.edits[role.split("-")[1]])
            text = text or "<notes>changed</notes>"
        elif role == "compose":
            self._compose(cwd)
            text = text or "<notes>kept -> dom1 -> moment</notes>"
        elif role == "select":
            text = text or "<why>cleaner</why>\n```choice\nA\n```"
        else:
            text = text or EXPERIENCE
        if ctx.on_event:
            ctx.on_event({"kind": "tool_use", "name": role})
        return AgentResult(ok=True, text=text, cost_usd=script.cost)

    def _edit(self, cwd: Path, line: str) -> None:
        memory = cwd / "context" / "harness" / "CLAUDE.md"
        memory.write_text(memory.read_text().rstrip() + "\n" + line + "\n")
        for rel, content in self.script.extra_files.items():
            target = cwd / "context" / "harness" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)
        if self.script._invalid_left > 0:
            self.script._invalid_left -= 1
            skill = cwd / "context" / "harness" / ".claude" / "skills" / "orphan" / "SKILL.md"
            skill.parent.mkdir(parents=True, exist_ok=True)
            skill.write_text("---\nname: wrong\ndescription: d\n---\n")
        else:
            orphan = cwd / "context" / "harness" / ".claude" / "skills" / "orphan"
            if orphan.exists():
                import shutil

                shutil.rmtree(orphan)

    def _compose(self, cwd: Path) -> None:
        memory = cwd / "context" / "harness" / "CLAUDE.md"
        merged: list[str] = [memory.read_text().rstrip()]
        for candidate in sorted((cwd / "context" / "pool").glob("*/harness/CLAUDE.md")):
            for line in candidate.read_text().splitlines():
                if line.strip() and line not in "\n".join(merged):
                    merged.append(line)
        memory.write_text("\n".join(merged) + "\n")
        for rel, content in self.script.extra_files.items():
            target = cwd / "context" / "harness" / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)


def make_project(root: Path) -> Path:
    project = root / "proj"
    (project / "agents").mkdir(parents=True)
    (project / "src").mkdir()
    (project / "src" / "app.py").write_text("x = 1\n")
    (project / "CLAUDE.md").write_text("Base rules.\n")
    (project / "agents" / "dev.md").write_text(
        "---\nagent: dev\nrunner: claude-code-headless\npermission_mode: acceptEdits\n"
        "limits: { budget_usd: 1, turns: 10, wall: 5m }\n---\nImplement the task.\n"
    )
    return project


def registry_for(runner: ScriptedRunner) -> Registry:
    registry = Registry()
    registry.use(control_plugin)
    registry.use(basics_plugin)
    registry.use(rsi_plugin)
    registry.register_runner(runner)
    return registry


async def run_yaml(
    project: Path,
    yaml_text: str,
    script: Script | None = None,
    *,
    registry: Registry | None = None,
    store: MemoryStore | None = None,
    runner: ScriptedRunner | None = None,
    items: list[Any] | None = None,
    **extra: Any,
) -> tuple[RunResult, MemoryStore, ScriptedRunner]:
    runner = runner or ScriptedRunner(script or Script())
    registry = registry or registry_for(runner)
    store = store or MemoryStore()
    result = await run_pipeline(
        registry=registry,
        config=parse_pipeline(yaml_text, str(project / "whiletrue.yaml")),
        store=store,
        bus=EventBus(),
        cwd=str(project),
        items=items,
        now=lambda: 1000,
        **extra,
    )
    return result, store, runner


def rsi_yaml(**settings: Any) -> str:
    body = "\n".join(
        f"      {k}: {v}"
        for k, v in {"k": 1, "budget_usd": 5, "adopt": "never", **settings}.items()
    )
    return (
        "pipeline:\n"
        "  - id: work\n"
        "    rsi.harness2:\n"
        f"{body}\n"
        "    do:\n"
        "      - agent: { uses: ./agents/dev.md }\n"
    )


def titled(text: str) -> re.Pattern[str]:
    return re.compile(text)
