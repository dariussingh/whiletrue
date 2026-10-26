from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, cast

from whiletrue.basics.stream import run_stream
from whiletrue.core import AgentRequest, AgentResult, RunContext, RunnerCapabilities


@dataclass
class HeadlessOptions:
    #: The claude binary. Defaults to `claude` on PATH.
    command: str = "claude"
    #: Extra args appended after the generated ones (e.g. `--bare` for reproducible runs).
    extra_args: list[str] = field(default_factory=lambda: [])


def _num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def build_headless_args(req: AgentRequest, extra_args: list[str] | None = None) -> list[str]:
    """Builds the argv for `claude -p`. The prompt itself goes over stdin."""
    args = ["-p", "--output-format", "stream-json", "--verbose", "--permission-prompts", "none"]
    if req.model:
        args += ["--model", req.model]
    if req.allowed_tools:
        args += ["--allowedTools", ",".join(req.allowed_tools)]
    if req.disallowed_tools:
        args += ["--disallowedTools", ",".join(req.disallowed_tools)]
    if req.tools:
        args += ["--tools", ",".join(req.tools)]
    if req.permission_mode:
        args += ["--permission-mode", req.permission_mode]
    if req.system_prompt_append:
        args += ["--append-system-prompt", req.system_prompt_append]
    if req.output_schema:
        args += ["--json-schema", json.dumps(req.output_schema, separators=(",", ":"))]
    if req.limits is not None and req.limits.turns is not None:
        args += ["--max-turns", str(req.limits.turns)]
    if req.limits is not None and req.limits.budget_usd is not None:
        args += ["--max-budget-usd", _num(req.limits.budget_usd)]
    if req.resume:
        args += ["--resume", req.resume]
    elif req.session_id:
        args += ["--session-id", req.session_id]
    return [*args, *(extra_args or [])]


def _to_result(msg: dict[str, Any]) -> AgentResult:
    ok = msg.get("is_error") is not True and msg.get("subtype") == "success"
    errors = msg.get("errors")
    error: str | None = None
    if not ok:
        joined = (
            "; ".join(str(e) for e in cast(list[Any], errors)) if isinstance(errors, list) else ""
        )
        error = joined or msg.get("subtype") or "claude reported an error"
    return AgentResult(
        ok=ok,
        text=msg.get("result") or "",
        output=msg.get("structured_output"),
        session_id=msg.get("session_id"),
        cost_usd=msg.get("total_cost_usd"),
        turns=msg.get("num_turns"),
        error=error,
    )


class HeadlessRunner:
    """Unattended runner: `claude -p --output-format stream-json` (CI, cheap utility steps)."""

    id = "claude-code-headless"
    capabilities = RunnerCapabilities(
        interactive=False, structured_output=True, resume=True, fork=False, budget=True
    )

    def __init__(self, options: HeadlessOptions | None = None) -> None:
        self._options = options or HeadlessOptions()

    async def run(self, request: AgentRequest, ctx: RunContext) -> AgentResult:
        command = self._options.command
        if ctx.signal.aborted:
            return AgentResult(ok=False, text="", error="aborted")
        stream = _Stream(ctx)
        try:
            outcome = await run_stream(
                command,
                build_headless_args(request, self._options.extra_args),
                cwd=request.cwd,
                on_line=stream.feed,
                signal=ctx.signal,
                stdin=request.prompt,
                wall_ms=request.limits.wall_ms if request.limits is not None else None,
            )
        except OSError as error:
            return AgentResult(ok=False, text="", error=f"Failed to start {command}: {error}")
        if outcome.stopped == "aborted":
            return AgentResult(ok=False, text="", error="aborted")
        if outcome.stopped == "wall":
            return AgentResult(ok=False, text="", error="wall-time limit exceeded")
        if stream.final is not None:
            return stream.final
        detail = f": {outcome.stderr[-500:]}" if outcome.stderr else ""
        return AgentResult(
            ok=False,
            text="",
            error=f"{command} exited with code {outcome.code} without a result{detail}",
        )


class _Stream:
    """Reads Claude Code's stream-json lines: forwards events and keeps the final result."""

    def __init__(self, ctx: RunContext) -> None:
        self._ctx = ctx
        self.final: AgentResult | None = None

    def feed(self, line: bytes) -> None:
        msg = _parse_line(line)
        if msg is None:
            return
        ctx = self._ctx
        if ctx.on_raw is not None:
            ctx.on_raw(msg)
        kind = msg.get("type")
        if kind == "result":
            self.final = _to_result(msg)
            _emit(ctx, {"kind": "result", "subtype": msg.get("subtype")})
        elif kind == "assistant":
            message: dict[str, Any] = msg.get("message") or {}
            blocks: list[dict[str, Any]] = message.get("content") or []
            for block in blocks:
                if block.get("type") == "tool_use":
                    _emit(ctx, {"kind": "tool_use", "name": block.get("name") or ""})
                elif block.get("type") == "text":
                    _emit(ctx, {"kind": "text"})


def _parse_line(line: bytes) -> dict[str, Any] | None:
    try:
        msg = json.loads(line)
    except ValueError:
        return None
    return msg if isinstance(msg, dict) else None  # pyright: ignore[reportUnknownVariableType]


def _emit(ctx: RunContext, event: dict[str, Any]) -> None:
    if ctx.on_event is not None:
        ctx.on_event(event)


def create_headless_runner(options: HeadlessOptions | None = None) -> HeadlessRunner:
    return HeadlessRunner(options)
