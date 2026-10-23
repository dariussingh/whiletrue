import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from whiletrue.claude_code.endpoint import EventEndpoint, ToolResult


class Recorder:
    """A SessionHandler that records what it is sent."""

    def __init__(self) -> None:
        self.hooks: list[tuple[str, dict[str, Any]]] = []
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.hook_reply: dict[str, Any] | None = {"decision": "block", "reason": "nudge"}

    async def on_hook(self, event: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        self.hooks.append((event, payload))
        return self.hook_reply

    def tools(self) -> list[dict[str, Any]]:
        return [{"name": "whiletrue_submit", "description": "d", "inputSchema": {"type": "object"}}]

    async def on_tool(self, name: str, args: dict[str, Any]) -> ToolResult:
        self.calls.append((name, args))
        return ToolResult("accepted") if name == "ok" else ToolResult("bad input", is_error=True)


def post(url: str, path: str, body: Any, *, token: str, session: str) -> tuple[int, Any]:
    request = urllib.request.Request(
        url + path,
        data=json.dumps(body).encode(),
        headers={"X-WT-Token": token, "X-WT-Session": session, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


async def call(endpoint: EventEndpoint, path: str, body: Any, **over: str) -> tuple[int, Any]:
    headers = {"token": endpoint.token, "session": "s1", **over}
    return await asyncio.to_thread(post, endpoint.url, path, body, **headers)


@pytest.fixture
async def endpoint():
    ep = EventEndpoint()
    await ep.start()
    yield ep
    await ep.stop()


async def test_routes_a_hook_to_the_registered_session(endpoint: EventEndpoint) -> None:
    handler = Recorder()
    endpoint.register("s1", handler)
    status, body = await call(endpoint, "/hook", {"event": "Stop", "payload": {"a": 1}})
    assert (status, body) == (200, {"decision": "block", "reason": "nudge"})
    assert handler.hooks == [("Stop", {"a": 1})]


async def test_a_hook_with_nothing_to_say_gets_an_empty_object(endpoint: EventEndpoint) -> None:
    handler = Recorder()
    handler.hook_reply = None
    endpoint.register("s1", handler)
    assert await call(endpoint, "/hook", {"event": "SessionStart", "payload": {}}) == (200, {})


async def test_lists_and_calls_tools(endpoint: EventEndpoint) -> None:
    handler = Recorder()
    endpoint.register("s1", handler)
    status, body = await call(endpoint, "/tools/list", {})
    assert status == 200 and body["tools"][0]["name"] == "whiletrue_submit"
    assert await call(endpoint, "/tools/call", {"name": "ok", "arguments": {"x": 1}}) == (
        200,
        {"text": "accepted", "is_error": False},
    )
    assert await call(endpoint, "/tools/call", {"name": "no", "arguments": {}}) == (
        200,
        {"text": "bad input", "is_error": True},
    )
    assert handler.calls == [("ok", {"x": 1}), ("no", {})]


async def test_rejects_a_wrong_token_and_an_unknown_session(endpoint: EventEndpoint) -> None:
    endpoint.register("s1", Recorder())
    assert (await call(endpoint, "/hook", {"event": "Stop"}, token="nope"))[0] == 401
    assert (await call(endpoint, "/hook", {"event": "Stop"}, session="other"))[0] == 404


async def test_unregistering_stops_delivery(endpoint: EventEndpoint) -> None:
    handler = Recorder()
    unregister = endpoint.register("s1", handler)
    unregister()
    assert (await call(endpoint, "/hook", {"event": "Stop", "payload": {}}))[0] == 404
    assert handler.hooks == []


async def test_a_registration_key_cannot_be_reused_while_live(endpoint: EventEndpoint) -> None:
    endpoint.register("s1", Recorder())
    with pytest.raises(ValueError, match="already registered"):
        endpoint.register("s1", Recorder())


async def test_bad_requests_get_400_and_errors_get_500(endpoint: EventEndpoint) -> None:
    class Exploding(Recorder):
        async def on_hook(self, event: str, payload: dict[str, Any]) -> dict[str, Any] | None:
            raise RuntimeError("boom")

    endpoint.register("s1", Exploding())
    assert (await call(endpoint, "/hook", ["not", "an", "object"]))[0] == 400
    assert (await call(endpoint, "/hook", {"event": "Stop", "payload": {}}))[0] == 500
    assert (await call(endpoint, "/nowhere", {}))[0] == 404


async def test_malformed_http_is_dropped_without_killing_the_server(
    endpoint: EventEndpoint,
) -> None:
    reader, writer = await asyncio.open_connection("127.0.0.1", endpoint.port)
    writer.write(b"GARBAGE\r\n\r\n")
    await writer.drain()
    await reader.read()
    writer.close()
    endpoint.register("s1", Recorder())
    assert (await call(endpoint, "/hook", {"event": "Stop", "payload": {}}))[0] == 200


async def test_environment_for_a_session_points_the_plugin_at_the_endpoint(
    endpoint: EventEndpoint,
) -> None:
    env = endpoint.env_for("s1")
    assert env == {
        "WT_ENDPOINT": endpoint.url,
        "WT_TOKEN": endpoint.token,
        "WT_SESSION": "s1",
    }
