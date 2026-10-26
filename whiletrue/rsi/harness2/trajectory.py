"""Render a rollout's raw runner messages as a readable trajectory.

The improver reads trajectories as files, so they are written in full: nothing is clipped to fit a
prompt. A single observation (a tool result) is capped, with an explicit marker, because one huge
output would otherwise bury the rest. Runners that give no raw messages produce a short note and
the final answer instead.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any, cast

OBS_LIMIT = 20_000


def _text(value: Any) -> str:
    """The text of a message block's content: a string, or a list of text parts."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [
            str(cast("Mapping[str, Any]", part).get("text", ""))
            for part in cast("list[Any]", value)
            if isinstance(part, dict)
        ]
        return "\n".join(p for p in parts if p)
    return "" if value is None else json.dumps(value, default=str)


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}\n[truncated by whiletrue: {omitted:,} characters omitted]"


def _blocks(message: Mapping[str, Any]) -> list[dict[str, Any]]:
    inner: Any = message.get("message")
    content: Any = (
        cast("Mapping[str, Any]", inner).get("content") if isinstance(inner, dict) else None
    )
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if not isinstance(content, list):
        return []
    return [cast("dict[str, Any]", b) for b in cast("list[Any]", content) if isinstance(b, dict)]


def render(
    messages: Iterable[Mapping[str, Any]], *, final: str = "", obs_limit: int = OBS_LIMIT
) -> str:
    """The trajectory as markdown. `final` is used when the messages carry no final result."""
    lines: list[str] = []
    step = 0
    result = ""
    for message in messages:
        kind = message.get("type")
        if kind == "system" and message.get("subtype") == "init":
            lines.append(f"# Session started (model {message.get('model', 'unknown')})\n")
        elif kind == "assistant":
            for block in _blocks(message):
                if block.get("type") == "text" and str(block.get("text", "")).strip():
                    step += 1
                    lines.append(f"## {step}. Agent\n{str(block['text']).strip()}\n")
                elif block.get("type") == "tool_use":
                    step += 1
                    args = json.dumps(block.get("input", {}), default=str, ensure_ascii=False)
                    lines.append(f"## {step}. Tool call: {block.get('name', '?')}\n{args}\n")
        elif kind == "user":
            for block in _blocks(message):
                if block.get("type") == "tool_result":
                    body = _clip(_text(block.get("content")), obs_limit)
                    error = " (error)" if block.get("is_error") else ""
                    lines.append(f"### Observation{error}\n{body}\n")
        elif kind == "result":
            result = str(message.get("result") or "")
    answer = result or final
    if not lines:
        lines.append(
            "(this runner gives no step-by-step record; only the final answer is available)\n"
        )
    if answer.strip():
        lines.append(f"## Final answer\n{answer.strip()}\n")
    return "\n".join(lines)
