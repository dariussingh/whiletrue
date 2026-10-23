#!/usr/bin/env python3
"""A stand-in for interactive `claude`: does what the real plugin would do, per FAKE_MODE."""

import importlib.util
import json
import os
import sys
import time

args = sys.argv[1:]
out = os.environ["FAKE_OUT"]
open(out + ".args", "w").write(json.dumps(args))
open(out + ".env", "w").write(
    json.dumps({k: v for k, v in os.environ.items() if k.startswith("WT_")})
)
plugin = args[args.index("--plugin-dir") + 1]
spec = importlib.util.spec_from_file_location("hook", plugin + "/hook.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)
env = os.environ
mode = env.get("FAKE_MODE", "submit")


def post(path, body):
    return hook.post_json(env["WT_ENDPOINT"], path, body, env["WT_TOKEN"], env["WT_SESSION"])


def fire(event, payload=None):
    return post("/hook", {"event": event, "payload": payload or {}})


def tool(name, arguments):
    return post("/tools/call", {"name": name, "arguments": arguments})


def exit_on_slash_exit():
    for line in sys.stdin:
        if line.strip() == "/exit":
            break
    fire("SessionEnd", {})
    sys.exit(0)


transcript = env.get("FAKE_TRANSCRIPT")
fire("SessionStart", {"session_id": "claude-sid", "transcript_path": transcript})
fire("UserPromptSubmit", {"prompt": args[-1]})
open(out + ".prompt", "w").write(args[-1])

if mode == "die":
    sys.exit(3)
if mode == "hang":
    exit_on_slash_exit()
if mode == "bad_then_ok":
    reply = tool("whiletrue_submit", {"result": {"pass": "nope"}})
    open(out + ".first", "w").write(json.dumps(reply))
    tool("whiletrue_submit", {"result": {"pass": True}})
elif mode == "ask":
    tool("whiletrue_ask", {"question": "which database?"})
elif mode == "nudge":
    reply = fire("Stop", {"last_assistant_message": "thinking"})
    open(out + ".nudge", "w").write(json.dumps(reply))
    tool("whiletrue_submit", {"result": "late"})
elif mode == "attention":
    fire("Notification", {"message": "needs permission"})
    time.sleep(0.2)
    fire("PostToolUse", {"tool_name": "Bash"})
    tool("whiletrue_submit", {"result": "ok"})
elif mode == "overbudget":
    reply = fire("PreToolUse", {"tool_name": "Bash"})
    open(out + ".pretool", "w").write(json.dumps(reply))
    exit_on_slash_exit()
elif mode == "silent":
    pass
elif mode == "steer":
    fire("UserPromptSubmit", {"prompt": "do it differently"})
    tool("whiletrue_submit", {"result": "steered"})
elif mode == "tools":
    reply = fire("PreToolUse", {"tool_name": "Bash"})
    open(out + ".pretool", "w").write(json.dumps(reply))
    fire("PostToolUse", {"tool_name": "Bash"})
    tool("whiletrue_submit", {"result": "tooled"})
else:
    if env.get("FAKE_TOUCH"):
        open(env["FAKE_TOUCH"], "w").write("done")
    tool("whiletrue_submit", {"result": "42"})
fire("Stop", {"last_assistant_message": "all done"})
if mode == "stubborn":
    time.sleep(60)
exit_on_slash_exit()
