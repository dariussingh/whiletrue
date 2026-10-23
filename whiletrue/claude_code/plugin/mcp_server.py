"""Stdio MCP server exposing whiletrue's agent tools.

Passed to Claude Code with `--mcp-config`. It holds no logic of its own: the tool list and every
call are forwarded to the whiletrue process, which validates results against the step's output
caps. Standard library only.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from collections.abc import Mapping
from typing import IO, Any

PROTOCOL_FALLBACK = "2024-11-05"


def post_json(url: str, path: str, body: Any, token: str, session: str) -> Any:
    request = urllib.request.Request(
        url + path,
        data=json.dumps(body).encode(),
        headers={"X-WT-Token": token, "X-WT-Session": session, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.loads(response.read() or b"{}")


def _reply(message_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def handle(message: dict[str, Any], env: Mapping[str, str]) -> dict[str, Any] | None:
    """Answers one JSON-RPC message (None for notifications)."""
    method, message_id = message.get("method"), message.get("id")
    if message_id is None:
        return None
    url, token, session = (
        env.get("WT_ENDPOINT", ""),
        env.get("WT_TOKEN", ""),
        env.get("WT_SESSION", ""),
    )
    params: dict[str, Any] = message.get("params") or {}
    if method == "initialize":
        return _reply(
            message_id,
            {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_FALLBACK),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "whiletrue", "version": "0.1.0"},
            },
        )
    if method == "ping":
        return _reply(message_id, {})
    if method == "tools/list":
        try:
            tools = post_json(url, "/tools/list", {}, token, session).get("tools", [])
        except Exception:
            tools = []
        return _reply(message_id, {"tools": tools})
    if method == "tools/call":
        try:
            answer = post_json(
                url,
                "/tools/call",
                {"name": params.get("name"), "arguments": params.get("arguments") or {}},
                token,
                session,
            )
            text, is_error = str(answer.get("text", "")), bool(answer.get("is_error"))
        except Exception as error:
            text, is_error = f"whiletrue is unreachable: {error}", True
        return _reply(
            message_id, {"content": [{"type": "text", "text": text}], "isError": is_error}
        )
    return {
        "jsonrpc": "2.0",
        "id": message_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def serve(stdin: IO[str], stdout: IO[str], env: Mapping[str, str]) -> None:
    for line in stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        response = handle(message, env) if isinstance(message, dict) else None  # pyright: ignore[reportUnknownArgumentType]
        if response is not None:
            stdout.write(json.dumps(response) + "\n")
            stdout.flush()


if __name__ == "__main__":  # pragma: no cover
    serve(sys.stdin, sys.stdout, __import__("os").environ)
