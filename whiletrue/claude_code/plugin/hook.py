"""Claude Code hook -> whiletrue endpoint.

Run by the bundled plugin as `hook.py <EventName>`: reads the hook payload (JSON) on stdin, posts
it to the whiletrue process and prints the answer, which is how whiletrue blocks a `Stop` or
denies a `PreToolUse`. Standard library only. A hook must never break the session, so any failure
(endpoint gone, bad reply) is silent and exits 0.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from collections.abc import Mapping
from typing import IO, Any, cast


def post_json(url: str, path: str, body: Any, token: str, session: str) -> Any:
    request = urllib.request.Request(
        url + path,
        data=json.dumps(body).encode(),
        headers={"X-WT-Token": token, "X-WT-Session": session, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=25) as response:
        return json.loads(response.read() or b"{}")


def main(argv: list[str], stdin: IO[str], stdout: IO[str], env: Mapping[str, str]) -> int:
    url, token, session = env.get("WT_ENDPOINT"), env.get("WT_TOKEN"), env.get("WT_SESSION")
    if not (url and token and session and len(argv) > 1):
        return 0
    try:
        raw = stdin.read()
        payload = cast(dict[str, Any], json.loads(raw)) if raw.strip() else {}
        reply = post_json(url, "/hook", {"event": argv[1], "payload": payload}, token, session)
    except Exception:
        return 0
    if isinstance(reply, dict) and reply:
        stdout.write(json.dumps(reply))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv, sys.stdin, sys.stdout, __import__("os").environ))
