from __future__ import annotations

import asyncio
import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol, cast


@dataclass
class ToolResult:
    text: str
    is_error: bool = False


class SessionHandler(Protocol):
    """What the endpoint needs from one live agent session."""

    async def on_hook(self, event: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Handles a Claude Code hook. The returned object is printed back to the hook."""
        ...

    def tools(self) -> list[dict[str, Any]]:
        """MCP tool descriptors (`name`, `description`, `inputSchema`) for this session."""
        ...

    async def on_tool(self, name: str, args: dict[str, Any]) -> ToolResult: ...


_MAX_BODY = 8 * 1024 * 1024
_REASONS = {200: "OK", 400: "Bad Request", 401: "Unauthorized", 404: "Not Found", 500: "Error"}


class EventEndpoint:
    """A loopback HTTP server inside the whiletrue process.

    The bundled plugin's hook script and MCP server post here. Requests carry a shared secret
    (`X-WT-Token`) and the id of the session they belong to (`X-WT-Session`); the endpoint routes
    them to whatever handler registered under that id.
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._host = host
        self._port = port
        self.token = secrets.token_hex(16)
        self._handlers: dict[str, SessionHandler] = {}
        self._server: asyncio.Server | None = None

    @property
    def port(self) -> int:
        assert self._server is not None, "endpoint not started"
        return int(self._server.sockets[0].getsockname()[1])

    @property
    def started(self) -> bool:
        return self._server is not None

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self.port}"

    async def start(self) -> None:
        self._server = await asyncio.start_server(self._serve, self._host, self._port)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    def register(self, key: str, handler: SessionHandler) -> Callable[[], None]:
        if key in self._handlers:
            raise ValueError(f'Session "{key}" is already registered')
        self._handlers[key] = handler

        def unregister() -> None:
            self._handlers.pop(key, None)

        return unregister

    def env_for(self, key: str) -> dict[str, str]:
        """Environment that points the plugin's scripts at this endpoint for one session."""
        return {"WT_ENDPOINT": self.url, "WT_TOKEN": self.token, "WT_SESSION": key}

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            status, body = await self._handle(reader)
        except (ValueError, asyncio.IncompleteReadError, asyncio.LimitOverrunError):
            status, body = 400, {"error": "bad request"}
        except Exception as error:
            status, body = 500, {"error": str(error)}
        payload = json.dumps(body).encode()
        head = (
            f"HTTP/1.1 {status} {_REASONS.get(status, 'Error')}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
        )
        try:
            writer.write(head.encode() + payload)
            await writer.drain()
        except ConnectionError:  # pragma: no cover (the client went away)
            pass
        finally:
            writer.close()

    async def _handle(self, reader: asyncio.StreamReader) -> tuple[int, Any]:
        request_line = (await reader.readline()).decode("latin-1").split()
        if len(request_line) != 3:
            raise ValueError("malformed request line")
        method, path = request_line[0], request_line[1]
        headers: dict[str, str] = {}
        while (line := (await reader.readline()).decode("latin-1").strip()) != "":
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        length = int(headers.get("content-length", "0"))
        if length > _MAX_BODY:
            raise ValueError("body too large")
        raw = await reader.readexactly(length) if length else b""
        if method != "POST":
            return 404, {"error": "not found"}
        if not secrets.compare_digest(headers.get("x-wt-token", ""), self.token):
            return 401, {"error": "bad token"}
        handler = self._handlers.get(headers.get("x-wt-session", ""))
        if handler is None:
            return 404, {"error": "unknown session"}
        parsed: Any = json.loads(raw) if raw else {}
        if not isinstance(parsed, dict):
            raise ValueError("body must be an object")
        request = cast(dict[str, Any], parsed)
        if path == "/hook":
            reply = await handler.on_hook(
                str(request.get("event", "")), request.get("payload") or {}
            )
            return 200, reply or {}
        if path == "/tools/list":
            return 200, {"tools": handler.tools()}
        if path == "/tools/call":
            result = await handler.on_tool(
                str(request.get("name", "")), request.get("arguments") or {}
            )
            return 200, {"text": result.text, "is_error": result.is_error}
        return 404, {"error": "not found"}
