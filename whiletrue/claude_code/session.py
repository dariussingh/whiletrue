from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from whiletrue.claude_code.endpoint import ToolResult
from whiletrue.claude_code.learnings import LearningsStore
from whiletrue.claude_code.transcript import Price, Usage, read_usage
from whiletrue.core import AgentLimits, CapValidation

#: Why a session was parked rather than finished.
ParkReason = Literal["needs_human", "budget_exceeded", "turns_exceeded", "wall_exceeded"]

SUBMIT_TOOL = "whiletrue_submit"
LEARN_TOOL = "whiletrue_learn"
ASK_TOOL = "whiletrue_ask"
CONTEXT_TOOL = "whiletrue_context"

_NUDGE = (
    "You have not submitted a result yet. Call the whiletrue_submit tool now with your result "
    "(it is how this step finishes). If you are blocked, call whiletrue_ask with your question."
)


@dataclass
class SessionOutcome:
    #: `submitted`: a result arrived; `parked`: the step needs a person (see `reason`); `ended`:
    #: the session closed without a result.
    status: Literal["submitted", "parked", "ended"]
    value: Any = None
    #: The agent's last message.
    text: str = ""
    reason: ParkReason | None = None
    detail: str | None = None
    session_id: str | None = None
    steered: bool = False
    turns: int = 0
    cost_usd: float = 0.0
    steered_cost_usd: float = 0.0


@dataclass(kw_only=True)
class SessionSpec:
    #: The step key the pane is registered under.
    key: str
    item: dict[str, Any] = field(default_factory=lambda: {})
    #: Validates a submitted result against the step's output cap (None: accept anything).
    validate: Callable[[Any], CapValidation] | None = None
    result_cap: str | None = None
    result_schema: dict[str, Any] | None = None
    limits: AgentLimits | None = None
    learnings: LearningsStore | None = None
    #: Extra fields stored with every learning (run, item, step).
    provenance: dict[str, Any] = field(default_factory=lambda: {})
    #: Extra context returned by `whiletrue_context` (for example spec excerpts).
    extra_context: dict[str, Any] = field(default_factory=lambda: {})
    max_nudges: int = 3
    prices: dict[str, Price] | None = None
    clock: Callable[[], float] = time.time
    on_event: Callable[[dict[str, Any]], None] | None = None
    on_attention: Callable[[str], None] | None = None
    on_attention_cleared: Callable[[], None] | None = None
    #: Called once when a limit is hit, so the runner can end the session.
    on_limit: Callable[[ParkReason], None] | None = None


class StepSession:
    """The in-process half of one interactive agent session.

    The plugin's hooks and MCP tools land here (through the endpoint). It decides when the step
    is done, parks it when it needs a person or hits a limit, and records a steer when a human
    types into the pane.
    """

    def __init__(self, spec: SessionSpec) -> None:
        self._spec = spec
        self._started = spec.clock()
        self.outcome: asyncio.Future[SessionOutcome] = asyncio.get_event_loop().create_future()
        self.session_id: str | None = None
        self.transcript_path: str | None = None
        self.submitted = False
        self._value: Any = None
        self._question: str | None = None
        self._last_message = ""
        self._nudges = 0
        self._prompts = 0
        self._steered_at: float | None = None
        self.tool_calls = 0
        self.needs_attention = False
        self.limit_hit: ParkReason | None = None

    @property
    def steered(self) -> bool:
        return self._steered_at is not None

    def _event(self, kind: str, **data: Any) -> None:
        if self._spec.on_event is not None:
            self._spec.on_event({"kind": kind, **data})

    # ---- tools -----------------------------------------------------------------------------

    def tools(self) -> list[dict[str, Any]]:
        spec = self._spec
        cap = f" The result must match the {spec.result_cap} schema." if spec.result_cap else ""
        result_schema: dict[str, Any] = spec.result_schema if spec.result_schema else {}
        return [
            {
                "name": SUBMIT_TOOL,
                "description": "Finish this step by submitting your result. Call it when the "
                f"work is complete.{cap} Invalid results are returned with the errors.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"result": result_schema},
                    "required": ["result"],
                },
            },
            {
                "name": LEARN_TOOL,
                "description": "Record a short, reusable lesson about this project.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"note": {"type": "string"}},
                    "required": ["note"],
                },
            },
            {
                "name": ASK_TOOL,
                "description": "Ask the human a question and stop. The step parks until answered.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"question": {"type": "string"}},
                    "required": ["question"],
                },
            },
            {
                "name": CONTEXT_TOOL,
                "description": "Get this item's details and recent project learnings.",
                "inputSchema": {"type": "object", "properties": {}},
            },
        ]

    async def on_tool(self, name: str, args: dict[str, Any]) -> ToolResult:
        if name == SUBMIT_TOOL:
            return self._submit(args)
        if name == LEARN_TOOL:
            return self._learn(args)
        if name == ASK_TOOL:
            question = args.get("question")
            if not isinstance(question, str) or not question.strip():
                return ToolResult("`question` is required", is_error=True)
            self._question = question
            self._event("ask", question=question)
            return ToolResult("Recorded. Stop now; a person will answer and resume the work.")
        if name == CONTEXT_TOOL:
            learnings = self._spec.learnings.recent(20) if self._spec.learnings else []
            body = {"item": self._spec.item, "learnings": learnings, **self._spec.extra_context}
            return ToolResult(json.dumps(body))
        return ToolResult(f'Unknown tool "{name}"', is_error=True)

    def _submit(self, args: dict[str, Any]) -> ToolResult:
        if "result" not in args:
            return ToolResult("`result` is required", is_error=True)
        value: Any = args["result"]
        if self._spec.validate is not None:
            check = self._spec.validate(value)
            if not check.ok:
                self._event("submit_rejected", errors=check.errors)
                return ToolResult(
                    "Result rejected, fix these and call whiletrue_submit again:\n"
                    + "\n".join(f"- {e}" for e in check.errors),
                    is_error=True,
                )
            value = check.value
        self._value = value
        self.submitted = True
        self._event("submit")
        return ToolResult("Result accepted.")

    def _learn(self, args: dict[str, Any]) -> ToolResult:
        note = args.get("note")
        if not isinstance(note, str) or not note.strip():
            return ToolResult("`note` is required", is_error=True)
        if self._spec.learnings is None:
            return ToolResult("Learnings are not enabled for this run", is_error=True)
        self._spec.learnings.append(note.strip(), **self._spec.provenance)
        self._event("learn")
        return ToolResult("Noted.")

    # ---- hooks -----------------------------------------------------------------------------

    async def on_hook(self, event: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        match event:
            case "SessionStart":
                self.session_id = payload.get("session_id") or self.session_id
                self.transcript_path = payload.get("transcript_path") or self.transcript_path
                self._event("session_start", session_id=self.session_id)
            case "UserPromptSubmit":
                self._on_prompt(str(payload.get("prompt", "")))
            case "PreToolUse":
                return self._on_pre_tool(payload)
            case "PostToolUse":
                self.tool_calls += 1
                self._clear_attention()
                self._event("tool_use", name=payload.get("tool_name", ""))
            case "Notification":
                message = str(payload.get("message", ""))
                self.needs_attention = True
                self._event("attention", message=message)
                if self._spec.on_attention is not None:
                    self._spec.on_attention(message)
            case "Stop":
                return self._on_stop(payload)
            case "SessionEnd":
                self._on_end()
            case _:
                pass
        return None

    def _clear_attention(self) -> None:
        if self.needs_attention:
            self.needs_attention = False
            if self._spec.on_attention_cleared is not None:
                self._spec.on_attention_cleared()

    def _on_prompt(self, prompt: str) -> None:
        self._prompts += 1
        self._clear_attention()
        if self._prompts > 1 and self._steered_at is None:
            # The prompt we launched with is the first; anything later was typed by a person.
            self._steered_at = self._spec.clock()
        if self._prompts > 1:
            self._event("steer", prompt=prompt)

    def _usage(self) -> Usage:
        return read_usage(self.transcript_path, self._spec.prices, steered_since=self._steered_at)

    def _check_limits(self) -> ParkReason | None:
        limits = self._spec.limits
        if limits is None or self.steered:
            return None  # a person driving the session lifts every limit
        if (
            limits.wall_ms is not None
            and (self._spec.clock() - self._started) * 1000 >= limits.wall_ms
        ):
            return "wall_exceeded"
        if limits.budget_usd is None and limits.turns is None:
            return None
        usage = self._usage()
        if limits.budget_usd is not None and usage.cost_usd >= limits.budget_usd:
            return "budget_exceeded"
        if limits.turns is not None and usage.turns > limits.turns:
            return "turns_exceeded"
        return None

    def _on_pre_tool(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if self.limit_hit is None:
            hit = self._check_limits()
            if hit is None:
                return None
            self.limit_hit = hit
            self._event("limit", reason=hit)
            if self._spec.on_limit is not None:
                self._spec.on_limit(hit)
        elif self.steered:
            return None
        label = {
            "budget_exceeded": "the budget estimate was exceeded",
            "turns_exceeded": "the turn limit was reached",
            "wall_exceeded": "the wall-time limit was reached",
        }.get(self.limit_hit, "a limit was reached")
        return {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"whiletrue: {label}; this step is being parked.",
            }
        }

    def _on_stop(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        if self.outcome.done():
            return None
        self._last_message = str(payload.get("last_assistant_message", self._last_message))
        if self.limit_hit is not None and not self.steered:
            self._finish("parked", reason=self.limit_hit, detail=f"limit hit: {self.limit_hit}")
        elif self._question is not None:
            self._finish("parked", reason="needs_human", detail=self._question)
        elif self.submitted:
            self._finish("submitted")
        elif self.steered:
            return None  # a person is driving: waiting for them is not a failure
        elif self._nudges < self._spec.max_nudges:
            self._nudges += 1
            self._event("nudge", count=self._nudges)
            return {"decision": "block", "reason": _NUDGE}
        else:
            self._finish(
                "parked",
                reason="needs_human",
                detail="the agent stopped without submitting a result after "
                f"{self._nudges} reminder(s)",
            )
        return None

    def _on_end(self) -> None:
        if self.outcome.done():
            return
        if self.submitted:
            self._finish("submitted")
        else:
            self._finish("ended", detail="the session ended without submitting a result")

    def _finish(
        self,
        status: Literal["submitted", "parked", "ended"],
        *,
        reason: ParkReason | None = None,
        detail: str | None = None,
    ) -> None:
        usage = self._usage()
        self._event("done", status=status)
        self.outcome.set_result(
            SessionOutcome(
                status=status,
                value=self._value,
                text=self._last_message,
                reason=reason,
                detail=detail,
                session_id=self.session_id,
                steered=self.steered,
                turns=usage.turns,
                cost_usd=usage.cost_usd,
                steered_cost_usd=usage.steered_cost_usd,
            )
        )
