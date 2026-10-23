from __future__ import annotations

import asyncio
import contextlib
import json
import re
import shlex
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

from whiletrue.basics.process import kill_process_group
from whiletrue.claude_code.panes import PaneHost
from whiletrue.core import AgentRequest, AgentResult, RunContext, RunnerCapabilities

_LINE_LIMIT = 64 * 1024 * 1024


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


def extract_json(text: str) -> Any:
    """The last JSON object or array in a model's reply (raw, or inside a code fence)."""
    candidates = re.findall(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidates.append(text)
    for chunk in reversed(candidates):
        decoder = json.JSONDecoder()
        found: Any = None
        for start in (m.start() for m in re.finditer(r"[{\[]", chunk)):
            try:
                value, _ = decoder.raw_decode(chunk[start:])
            except ValueError:
                continue
            found = value
            break
        if found is not None:
            return found
    return None


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
        log = await self._open_log(request)
        pane = await self._open_pane(log, request) if log is not None else None
        try:
            return await self._run(request, ctx, log)
        finally:
            if log is not None:
                log.with_name(log.name + ".done").write_text("done")
            if pane is not None:
                with contextlib.suppress(Exception):
                    await pane.finish(
                        keep=request.on_done == "keep", title=f"{request.label} (done)"
                    )

    async def _open_log(self, request: AgentRequest) -> Path | None:
        if self._options.host is None:
            return None
        key = re.sub(r"[^A-Za-z0-9_.-]+", "_", request.step_key or uuid.uuid4().hex)[:80]
        directory = Path(request.cwd) / ".whiletrue" / "sessions" / f"{key}-{uuid.uuid4().hex[:6]}"
        directory.mkdir(parents=True, exist_ok=True)
        log = directory / "stream.log"
        log.write_text("")
        return log

    async def _open_pane(self, log: Path, request: AgentRequest) -> Any:
        assert self._options.host is not None
        done = shlex.quote(str(log) + ".done")
        script = (
            f"tail -n +1 -F {shlex.quote(str(log))} & p=$!; "
            f"while [ ! -e {done} ]; do sleep 0.3; done; sleep 0.5; kill $p"
        )
        try:
            return await self._options.host.open(
                title=request.label or "opencode",
                argv=["sh", "-c", script],
                cwd=request.cwd,
                env={},
            )
        except Exception:
            return None

    async def _run(self, request: AgentRequest, ctx: RunContext, log: Path | None) -> AgentResult:
        opts = self._options
        try:
            proc = await asyncio.create_subprocess_exec(
                opts.command,
                *build_opencode_args(request, opts.extra_args),
                cwd=request.cwd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                limit=_LINE_LIMIT,
            )
        except OSError as error:
            return AgentResult(ok=False, text="", error=f"Failed to start {opts.command}: {error}")

        texts: list[str] = []
        state: dict[str, Any] = {"cost": 0.0, "session": None, "turns": 0, "error": None}

        def emit(event: dict[str, Any]) -> None:
            if ctx.on_event is not None:
                ctx.on_event(event)

        def note(line: str) -> None:
            if log is not None:
                with log.open("a", encoding="utf-8") as handle:
                    handle.write(line + "\n")

        async def drive() -> None:
            assert proc.stdout is not None
            while raw := await proc.stdout.readline():
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(event, dict):
                    continue
                fields = cast(dict[str, Any], event)
                part = cast(dict[str, Any], fields.get("part") or {})
                state["session"] = fields.get("sessionID") or state["session"]
                kind = fields.get("type")
                if kind == "text":
                    text = str(part.get("text", ""))
                    texts.append(text)
                    note(text)
                    emit({"kind": "text"})
                elif kind == "step_finish":
                    state["turns"] += 1
                    state["cost"] += float(part.get("cost") or 0)
                elif kind in ("tool_use", "tool"):
                    name = str(part.get("tool") or part.get("name") or "")
                    note(f"[tool] {name}")
                    emit({"kind": "tool_use", "name": name})
                elif kind == "error":
                    state["error"] = json.dumps(fields.get("error", fields))[:400]
                    note(f"[error] {state['error']}")

        drive_task = asyncio.ensure_future(drive())
        stderr_task = asyncio.ensure_future(proc.stderr.read() if proc.stderr else asyncio.sleep(0))
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
                    text="".join(texts),
                    error="aborted" if abort_task in done else "wall-time limit exceeded",
                )
            code = await proc.wait()
            stderr = (await stderr_task or b"").decode(errors="replace").strip()  # type: ignore[union-attr]
        finally:
            abort_task.cancel()
            stderr_task.cancel()
        text = "".join(texts).strip()
        common: dict[str, Any] = {
            "text": text,
            "session_id": state["session"],
            "cost_usd": state["cost"],
            "turns": state["turns"],
        }
        if code != 0 or state["error"]:
            detail = state["error"] or stderr[-400:]
            return AgentResult(
                ok=False, error=f"{opts.command} exited with code {code}: {detail}", **common
            )
        output = extract_json(text) if request.output_schema else None
        if request.output_schema and output is None:
            return AgentResult(ok=False, error="the reply contained no JSON value", **common)
        return AgentResult(ok=True, output=output, **common)
