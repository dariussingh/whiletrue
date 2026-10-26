from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from whiletrue.basics.process import kill_process_group
from whiletrue.claude_code.panes import PaneHost, StreamMirror
from whiletrue.core import AgentRequest, AgentResult, RunContext, RunnerCapabilities
from whiletrue.opencode.runner import extract_json

_LINE_LIMIT = 64 * 1024 * 1024

#: whiletrue's permission modes (Claude Code's names) -> Codex sandbox modes.
SANDBOX_FOR_MODE = {
    "plan": "read-only",
    "default": "workspace-write",
    "acceptEdits": "workspace-write",
    "auto": "workspace-write",
    "dontAsk": "workspace-write",
    "bypassPermissions": "danger-full-access",
}


@dataclass
class CodexOptions:
    #: The codex binary. Defaults to `codex` on PATH.
    command: str = "codex"
    #: Where to show the live stream (a read-only pane); None runs without one.
    host: PaneHost | None = None
    extra_args: list[str] = field(default_factory=lambda: [])


def build_codex_args(
    req: AgentRequest, *, schema_file: str | None = None, extra_args: list[str] | None = None
) -> list[str]:
    """argv after the binary. The prompt goes over stdin (`-`).

    A fresh session uses real flags; resuming or forking uses `-c key=value` overrides, which
    every `codex exec` subcommand accepts.
    """
    sandbox = SANDBOX_FOR_MODE.get(req.permission_mode or "default", "workspace-write")
    if req.resume:
        args = [
            "exec",
            "fork" if req.fork else "resume",
            req.resume,
            "--json",
            "--skip-git-repo-check",
        ]
        args += ["-c", f'sandbox_mode="{sandbox}"']
        if req.model:
            args += ["-c", f'model="{req.model}"']
    else:
        args = ["exec", "--json", "--skip-git-repo-check", "-C", req.cwd, "-s", sandbox]
        if req.model:
            args += ["-m", req.model]
        if schema_file:
            args += ["--output-schema", schema_file]
    return [*args, *(extra_args or []), "-"]


def _short(value: Any) -> str:
    return json.dumps(value)[:300] if not isinstance(value, str) else value[:300]


class CodexRunner:
    """Runs a step with `codex exec --json` (OpenAI Codex CLI).

    Structured output uses Codex's own `--output-schema`. Codex reports tokens, not dollars, so
    no cost is recorded and budgets cannot be enforced; wall time can.
    """

    id = "codex"
    capabilities = RunnerCapabilities(
        interactive=False, structured_output=True, resume=True, fork=True, budget=False
    )

    def __init__(self, options: CodexOptions | None = None) -> None:
        self._options = options or CodexOptions()

    async def run(self, request: AgentRequest, ctx: RunContext) -> AgentResult:
        if ctx.signal.aborted:
            return AgentResult(ok=False, text="", error="aborted")
        mirror = StreamMirror(self._options.host)
        await mirror.open(request.cwd, request.step_key, request.label or "codex")
        try:
            return await self._run(request, ctx, mirror)
        finally:
            await mirror.close(keep=request.on_done == "keep", title=f"{request.label} (done)")

    async def _run(
        self, request: AgentRequest, ctx: RunContext, mirror: StreamMirror
    ) -> AgentResult:
        opts = self._options
        schema_file: str | None = None
        if request.output_schema and not request.resume:
            directory = Path(request.cwd) / ".whiletrue" / "sessions"
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / f"codex-schema-{uuid.uuid4().hex[:8]}.json"
            path.write_text(json.dumps(_strict(request.output_schema)), encoding="utf-8")
            schema_file = str(path)
        try:
            proc = await asyncio.create_subprocess_exec(
                opts.command,
                *build_codex_args(request, schema_file=schema_file, extra_args=opts.extra_args),
                cwd=request.cwd,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,
                limit=_LINE_LIMIT,
            )
        except OSError as error:
            return AgentResult(ok=False, text="", error=f"Failed to start {opts.command}: {error}")

        messages: list[str] = []
        state: dict[str, Any] = {"thread": None, "turns": 0, "error": None, "failed": False}

        def emit(event: dict[str, Any]) -> None:
            if ctx.on_event is not None:
                ctx.on_event(event)

        async def drive() -> None:
            assert proc.stdin is not None
            assert proc.stdout is not None
            proc.stdin.write(request.prompt.encode())
            await proc.stdin.drain()
            proc.stdin.close()
            while raw := await proc.stdout.readline():
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                fields = cast(dict[str, Any], event)
                kind = fields.get("type")
                if kind == "thread.started":
                    state["thread"] = fields.get("thread_id")
                elif kind == "turn.completed":
                    state["turns"] += 1
                elif kind == "turn.failed":
                    state["failed"] = True
                    err = cast(dict[str, Any], fields.get("error") or {})
                    state["error"] = str(err.get("message", "turn failed"))
                    mirror.note(f"[error] {state['error']}")
                elif kind == "error":
                    # Codex retries on its own and reports each attempt; the last one counts.
                    state["error"] = str(fields.get("message", ""))
                elif kind in ("item.completed", "item.started"):
                    item = cast(dict[str, Any], fields.get("item") or {})
                    if kind == "item.completed":
                        self_note(item, messages, mirror, emit)

        drive_task = asyncio.ensure_future(drive())
        abort_task = asyncio.ensure_future(ctx.signal.wait())
        wall_ms = request.limits.wall_ms if request.limits is not None else None
        try:
            done, _ = await asyncio.wait(
                {drive_task, abort_task},
                timeout=None if wall_ms is None else wall_ms / 1000,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if drive_task not in done:
                kill_process_group(proc.pid)
                drive_task.cancel()
                await asyncio.gather(drive_task, return_exceptions=True)
                await proc.wait()
                return AgentResult(
                    ok=False,
                    text="\n".join(messages),
                    error="aborted" if abort_task in done else "wall-time limit exceeded",
                )
            code = await proc.wait()
        finally:
            abort_task.cancel()
        text = messages[-1].strip() if messages else ""
        common: dict[str, Any] = {
            "text": text,
            "session_id": state["thread"],
            "turns": state["turns"] or None,
        }
        if code != 0 or state["failed"]:
            detail = state["error"] or "no details"
            return AgentResult(ok=False, error=f"codex failed: {detail}", **common)
        if request.output_schema:
            output = extract_json(text)
            if output is None:
                return AgentResult(ok=False, error="the reply contained no JSON value", **common)
            return AgentResult(ok=True, output=output, **common)
        return AgentResult(ok=True, **common)


def self_note(
    item: dict[str, Any],
    messages: list[str],
    mirror: StreamMirror,
    emit: Any,
) -> None:
    """Records what an `item.completed` event says: a message, a command, a file change, a tool."""
    kind = item.get("type")
    if kind == "agent_message":
        text = str(item.get("text", ""))
        messages.append(text)
        mirror.note(text)
        emit({"kind": "text"})
    elif kind == "command_execution":
        command = _short(item.get("command", ""))
        mirror.note(f"$ {command}")
        emit({"kind": "tool_use", "name": "shell", "command": command})
    elif kind == "file_change":
        mirror.note(f"[file change] {_short(item.get('changes', ''))}")
        emit({"kind": "tool_use", "name": "apply_patch"})
    elif kind in ("mcp_tool_call", "web_search"):
        name = str(item.get("tool") or item.get("query") or kind)
        mirror.note(f"[{kind}] {name}")
        emit({"kind": "tool_use", "name": name})


def _strict(schema: dict[str, Any]) -> dict[str, Any]:
    """Codex's structured output wants closed objects (`additionalProperties: false`)."""
    out = json.loads(json.dumps(schema))

    def close(node: Any) -> None:
        if isinstance(node, dict):
            fields = cast(dict[str, Any], node)
            if fields.get("type") == "object":
                fields.setdefault("additionalProperties", False)
                props = fields.get("properties")
                if isinstance(props, dict):
                    fields["required"] = list(cast(dict[str, Any], props))
            for value in fields.values():
                close(value)
        elif isinstance(node, list):
            for value in cast(list[Any], node):
                close(value)

    close(out)
    return cast(dict[str, Any], out)
