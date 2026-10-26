from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, cast

from whiletrue.basics.json_extract import extract_json
from whiletrue.basics.stream import run_stream
from whiletrue.core import AgentRequest, AgentResult, RunContext, RunnerCapabilities
from whiletrue.tmux import PaneHost, StreamMirror


@dataclass
class OpencodeOptions:
    #: The opencode binary. Defaults to `opencode` on PATH.
    command: str = "opencode"
    #: Where to show the live stream (a read-only pane); None runs without one.
    host: PaneHost | None = None
    extra_args: list[str] = field(default_factory=lambda: [])


_JSON_INSTRUCTION = (
    "\n\nWhen you are finished, reply with ONLY a JSON value that matches this JSON Schema, "
    "with no commentary and no code fence:\n{schema}"
)


def build_opencode_args(req: AgentRequest, extra_args: list[str] | None = None) -> list[str]:
    """argv after the binary. The prompt is the last positional, after `--`."""
    args = ["run", "--format", "json", "--dir", req.cwd]
    if req.model:
        args += ["--model", req.model]
    if req.resume:
        args += ["--session", req.resume]
        if req.fork:
            args += ["--fork"]
    prompt = req.prompt
    if req.output_schema:
        prompt += _JSON_INSTRUCTION.format(schema=json.dumps(req.output_schema))
    return [*args, *(extra_args or []), "--", prompt]


class OpencodeRunner:
    """Runs a step with `opencode run --format json` (review passes, cheap second opinions).

    There is no interactive mode: with a pane host, the step's live stream is shown in a
    read-only pane. Structured output is requested in the prompt and checked by the agent module.
    """

    id = "opencode"
    capabilities = RunnerCapabilities(
        interactive=False, structured_output=True, resume=True, fork=True, budget=False
    )

    def __init__(self, options: OpencodeOptions | None = None) -> None:
        self._options = options or OpencodeOptions()

    async def run(self, request: AgentRequest, ctx: RunContext) -> AgentResult:
        if ctx.signal.aborted:
            return AgentResult(ok=False, text="", error="aborted")
        mirror = StreamMirror(self._options.host)
        await mirror.open(request.cwd, request.step_key, request.label or "opencode")
        try:
            return await self._run(request, ctx, mirror)
        finally:
            await mirror.close(keep=request.on_done == "keep", title=f"{request.label} (done)")

    async def _run(
        self, request: AgentRequest, ctx: RunContext, mirror: StreamMirror
    ) -> AgentResult:
        command = self._options.command
        stream = _Stream(ctx, mirror)
        try:
            outcome = await run_stream(
                command,
                build_opencode_args(request, self._options.extra_args),
                cwd=request.cwd,
                on_line=stream.feed,
                signal=ctx.signal,
                wall_ms=request.limits.wall_ms if request.limits is not None else None,
            )
        except OSError as error:
            return AgentResult(ok=False, text="", error=f"Failed to start {command}: {error}")
        if outcome.stopped is not None:
            return AgentResult(
                ok=False,
                text="".join(stream.texts),
                error="aborted" if outcome.stopped == "aborted" else "wall-time limit exceeded",
            )
        text = "".join(stream.texts).strip()
        common: dict[str, Any] = {
            "text": text,
            "session_id": stream.session,
            "cost_usd": stream.cost,
            "turns": stream.turns,
        }
        if outcome.code != 0 or stream.error:
            detail = stream.error or outcome.stderr[-400:]
            return AgentResult(
                ok=False, error=f"{command} exited with code {outcome.code}: {detail}", **common
            )
        output = extract_json(text) if request.output_schema else None
        if request.output_schema and output is None:
            return AgentResult(ok=False, error="the reply contained no JSON value", **common)
        return AgentResult(ok=True, output=output, **common)


class _Stream:
    """Reads `opencode run --format json` lines: shows them, forwards events, keeps totals."""

    def __init__(self, ctx: RunContext, mirror: StreamMirror) -> None:
        self._ctx = ctx
        self._mirror = mirror
        self.texts: list[str] = []
        self.cost = 0.0
        self.session: str | None = None
        self.turns = 0
        self.error: str | None = None

    def _emit(self, event: dict[str, Any]) -> None:
        if self._ctx.on_event is not None:
            self._ctx.on_event(event)

    def feed(self, raw: bytes) -> None:
        try:
            event = json.loads(raw)
        except ValueError:
            return
        if not isinstance(event, dict):
            return
        fields = cast(dict[str, Any], event)
        part = cast(dict[str, Any], fields.get("part") or {})
        self.session = fields.get("sessionID") or self.session
        kind = fields.get("type")
        if kind == "text":
            text = str(part.get("text", ""))
            self.texts.append(text)
            self._mirror.note(text)
            self._emit({"kind": "text"})
        elif kind == "step_finish":
            self.turns += 1
            self.cost += float(part.get("cost") or 0)
        elif kind in ("tool_use", "tool"):
            name = str(part.get("tool") or part.get("name") or "")
            self._mirror.note(f"[tool] {name}")
            self._emit({"kind": "tool_use", "name": name})
        elif kind == "error":
            self.error = json.dumps(fields.get("error", fields))[:400]
            self._mirror.note(f"[error] {self.error}")
