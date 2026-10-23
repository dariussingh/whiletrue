from __future__ import annotations

import asyncio
import contextlib
import json
import re
import shlex
import sys
import uuid
from collections.abc import Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from whiletrue.claude_code.endpoint import EventEndpoint
from whiletrue.claude_code.learnings import LearningsStore
from whiletrue.claude_code.panes import Pane, PaneHost, ProcessPaneHost
from whiletrue.claude_code.session import ParkReason, SessionOutcome, SessionSpec, StepSession
from whiletrue.claude_code.transcript import Price
from whiletrue.core import AgentRequest, AgentResult, RunContext, RunnerCapabilities

#: The bundled Claude Code plugin (hooks); `--plugin-dir` points here.
PLUGIN_DIR = Path(__file__).resolve().parent / "plugin"

#: The MCP server's name, which fixes its tools' names (`mcp__whiletrue__whiletrue_submit`).
MCP_SERVER = "whiletrue"

#: Linux caps a single argument at 128 KiB; stay well under it.
_MAX_PROMPT_ARG = 100_000

_SYSTEM_NOTE = """You are running as one step of a whiletrue pipeline.
- When your work is complete, call the whiletrue_submit tool with your result. That is how the \
step finishes; do not just print the result.
- If you are blocked and need a person's decision, call whiletrue_ask with your question and stop.
- Record durable, reusable lessons about this project with whiletrue_learn.
- whiletrue_context returns this item's details and recent learnings.
"""


@dataclass
class InteractiveOptions:
    #: The claude binary. Defaults to `claude` on PATH.
    command: str = "claude"
    #: Interpreter that runs the plugin's hook and MCP scripts (they use only the stdlib).
    python: str = field(default_factory=lambda: sys.executable)
    #: Where sessions run; defaults to plain subprocesses (the CLI passes a tmux host).
    host: PaneHost | None = None
    #: Shared endpoint; defaults to one started on first use.
    endpoint: EventEndpoint | None = None
    #: How many times a `Stop` without a result is turned back before the step parks.
    max_nudges: int = 3
    prices: dict[str, Price] | None = None
    #: Seconds between checks on the pane and the wall-time limit.
    poll_interval: float = 0.5
    #: After asking claude to exit, how long to wait before killing the pane.
    exit_grace: float = 5.0
    #: After a limit is hit, how long the agent gets to stop on its own before we park it.
    limit_grace: float = 20.0
    learnings_path: str | None = None
    extra_args: list[str] = field(default_factory=lambda: [])


def build_interactive_args(
    req: AgentRequest, *, plugin_dir: str, mcp_config: str, system_prompt_file: str
) -> list[str]:
    """argv (after the binary) for an interactive session; the prompt follows `--`."""
    args = ["--plugin-dir", plugin_dir, "--mcp-config", mcp_config]
    args += ["--append-system-prompt-file", system_prompt_file]
    if req.resume:
        args += ["--resume", req.resume]
        if req.fork:
            args += ["--fork-session"]
    elif req.session_id:
        args += ["--session-id", req.session_id]
    if req.model:
        args += ["--model", req.model]
    if req.permission_mode:
        args += ["--permission-mode", req.permission_mode]
    # Always allow the whiletrue MCP tools; the rule names the server, so it covers all four.
    allowed = [*(req.allowed_tools or []), f"mcp__{MCP_SERVER}"]
    args += [f"--allowedTools={','.join(allowed)}"]
    if req.disallowed_tools:
        args += [f"--disallowedTools={','.join(req.disallowed_tools)}"]
    if req.tools:
        args += [f"--tools={','.join(req.tools)}"]
    for directory in req.add_dirs or []:
        args += ["--add-dir", directory]
    if req.bare:
        args += ["--bare"]
    return args


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)[:80]


class InteractiveRunner:
    """Runs a step as a live, interactive Claude Code session in a pane.

    The agent can be watched and steered from the pane. It finishes by calling `whiletrue_submit`
    (validated against the step's output cap); hooks give whiletrue turns, tool calls, steering and
    the stop signal, which is how limits are enforced without headless flags.
    """

    id = "claude-code"
    capabilities = RunnerCapabilities(
        interactive=True, structured_output=True, resume=True, fork=True, budget=True
    )

    def __init__(self, options: InteractiveOptions | None = None) -> None:
        self._options = options or InteractiveOptions()
        self._endpoint = self._options.endpoint
        self._host = self._options.host or ProcessPaneHost()
        self._starting = asyncio.Lock()

    async def _ensure_endpoint(self) -> EventEndpoint:
        async with self._starting:
            if self._endpoint is None:
                self._endpoint = EventEndpoint()
            if not self._endpoint.started:
                await self._endpoint.start()
        return self._endpoint

    async def run(self, request: AgentRequest, ctx: RunContext) -> AgentResult:
        if ctx.signal.aborted:
            return AgentResult(ok=False, text="", error="aborted")
        opts = self._options
        endpoint = await self._ensure_endpoint()
        key = request.step_key or str(uuid.uuid4())
        label = request.label or key
        workdir = (
            Path(request.cwd) / ".whiletrue" / "sessions" / f"{_safe(key)}-{uuid.uuid4().hex[:6]}"
        )
        try:
            argv = self._prepare(request, endpoint, key, workdir)
        except OSError as error:
            return AgentResult(ok=False, text="", error=f"Cannot prepare the session: {error}")

        pane_holder: list[Pane] = []
        loop = asyncio.get_running_loop()
        background: set[asyncio.Task[None]] = set()

        def spawn(coro: Coroutine[Any, Any, None]) -> None:
            task = asyncio.ensure_future(coro)
            background.add(task)
            task.add_done_callback(background.discard)

        async def retitle(title: str) -> None:
            if pane_holder:
                with contextlib.suppress(Exception):
                    await pane_holder[0].set_title(title)

        def emit(event: dict[str, object]) -> None:
            if ctx.on_event is not None:
                ctx.on_event({str(k): v for k, v in event.items()})

        session_holder: list[StepSession] = []

        def on_limit(reason: ParkReason) -> None:
            # The agent normally stops once its tools are denied; if it doesn't, park anyway.
            def force() -> None:
                if session_holder:
                    session_holder[0].park(reason, f"limit hit: {reason}")

            loop.call_later(opts.limit_grace, force)

        session = StepSession(
            SessionSpec(
                key=key,
                item=request.item or {},
                validate=request.validate_output,
                result_cap=request.output_cap,
                result_schema=request.output_schema,
                limits=request.limits,
                learnings=LearningsStore(
                    opts.learnings_path or Path(request.cwd) / ".whiletrue" / "learnings.jsonl"
                ),
                provenance={"step": key},
                max_nudges=opts.max_nudges,
                prices=opts.prices,
                on_event=emit,
                on_attention=lambda message: spawn(retitle(f"! {label} needs you")),
                on_attention_cleared=lambda: spawn(retitle(label)),
                on_limit=on_limit,
            )
        )
        session_holder.append(session)
        unregister = endpoint.register(key, session)
        try:
            env = {**endpoint.env_for(key), "WT_PYTHON": opts.python}
            try:
                pane = await self._host.open(title=label, argv=argv, cwd=request.cwd, env=env)
            except Exception as error:
                return AgentResult(ok=False, text="", error=f"Failed to start a pane: {error}")
            pane_holder.append(pane)
            outcome = await self._wait(session, pane, request, ctx)
            await self._wind_down(pane, session, request, label, aborted=outcome is None)
        finally:
            unregister()
            for task in list(background):
                task.cancel()
        if outcome is None:
            return AgentResult(ok=False, text="", error="aborted")
        return self._result(outcome, request)

    def _prepare(
        self, request: AgentRequest, endpoint: EventEndpoint, key: str, workdir: Path
    ) -> list[str]:
        """Writes the per-session files and builds the shell command the pane runs."""
        opts = self._options
        workdir.mkdir(parents=True, exist_ok=True)
        prompt_file = workdir / "prompt.md"
        prompt_file.write_text(request.prompt, encoding="utf-8")
        note = _SYSTEM_NOTE
        if request.append_system_prompt_file:
            note += "\n" + Path(request.append_system_prompt_file).read_text(encoding="utf-8")
        system_file = workdir / "system.md"
        system_file.write_text(note, encoding="utf-8")
        mcp_file = workdir / "mcp.json"
        mcp_file.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        MCP_SERVER: {
                            "command": opts.python,
                            "args": [str(PLUGIN_DIR / "mcp_server.py")],
                            "env": endpoint.env_for(key),
                        }
                    }
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        flags = build_interactive_args(
            request,
            plugin_dir=str(PLUGIN_DIR),
            mcp_config=str(mcp_file),
            system_prompt_file=str(system_file),
        )
        for extra in request.mcp_config or []:
            flags += ["--mcp-config", extra]
        flags += opts.extra_args
        # The prompt is read from a file so the pane's command stays short. One argument is capped
        # (128 KiB on Linux), so a very large prompt is handed over as a pointer to its file.
        if len(request.prompt.encode()) > _MAX_PROMPT_ARG:
            pointer = (
                f"Your task is written in `{prompt_file}`. Read that file completely, "
                "then carry it out."
            )
            prompt_file.with_name("pointer.md").write_text(pointer, encoding="utf-8")
            argument_file = prompt_file.with_name("pointer.md")
        else:
            argument_file = prompt_file
        script = (
            f"exec {shlex.quote(opts.command)} {shlex.join(flags)} -- "
            f'"$(cat {shlex.quote(str(argument_file))})"'
        )
        return ["sh", "-c", script]

    async def _wait(
        self, session: StepSession, pane: Pane, request: AgentRequest, ctx: RunContext
    ) -> SessionOutcome | None:
        """Waits for the step to finish. None means the run was aborted."""
        opts = self._options
        loop = asyncio.get_running_loop()
        wall = request.limits.wall_ms if request.limits is not None else None
        deadline = None if wall is None else loop.time() + wall / 1000
        abort = asyncio.ensure_future(ctx.signal.wait())
        try:
            while not session.outcome.done():
                if ctx.signal.aborted:
                    return None
                await asyncio.wait(
                    {session.outcome, abort},
                    timeout=opts.poll_interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if session.outcome.done():
                    break
                if await pane.state() != "running":
                    # The process is gone; give its last hook a moment to land first.
                    await asyncio.sleep(min(opts.poll_interval, 0.2))
                    if not session.outcome.done():
                        await session.on_hook("SessionEnd", {})
                elif deadline is not None and loop.time() >= deadline and not session.steered:
                    session.park("wall_exceeded", "the wall-time limit was reached")
            return session.outcome.result()
        finally:
            abort.cancel()

    async def _wind_down(
        self, pane: Pane, session: StepSession, request: AgentRequest, label: str, *, aborted: bool
    ) -> None:
        opts = self._options
        with contextlib.suppress(Exception):
            if aborted:
                await pane.kill()
                return
            if await pane.state() == "running":
                await pane.send_text("/exit")
                loop = asyncio.get_running_loop()
                end = loop.time() + opts.exit_grace
                while loop.time() < end and await pane.state() == "running":  # noqa: ASYNC110
                    await asyncio.sleep(0.05)
                if await pane.state() == "running":
                    await pane.kill()
                    return
            await pane.finish(keep=request.on_done == "keep", title=f"{label} (done)")

    def _result(self, outcome: SessionOutcome, request: AgentRequest) -> AgentResult:
        common = {
            "text": outcome.text,
            "session_id": outcome.session_id or request.session_id or request.resume,
            "cost_usd": outcome.cost_usd,
            "turns": outcome.turns,
            "steered": outcome.steered,
            "steered_cost_usd": outcome.steered_cost_usd if outcome.steered else None,
        }
        if outcome.status == "submitted":
            return AgentResult(ok=True, output=outcome.value, **common)  # pyright: ignore[reportArgumentType]
        error = outcome.detail or "the session ended without a result"
        return AgentResult(
            ok=False,
            error=error,
            park=outcome.reason if outcome.status == "parked" else None,
            **common,  # pyright: ignore[reportArgumentType]
        )


def create_interactive_runner(options: InteractiveOptions | None = None) -> InteractiveRunner:
    return InteractiveRunner(options)
