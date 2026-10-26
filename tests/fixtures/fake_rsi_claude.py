#!/usr/bin/env python3
"""A fake headless `claude` that plays every role in an rsi run (agent, improver, selector).

It reads the prompt on stdin, decides what is being asked, acts on the working directory and
prints stream-json like `claude -p --output-format stream-json`.
"""

import json
import sys
from pathlib import Path

prompt = sys.stdin.read()
cwd = Path.cwd()
CAREFUL = "BE CAREFUL with config files."


def say(text: str) -> None:
    print(json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": text}]}}))


def finish(text: str) -> None:
    print(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": text,
                "total_cost_usd": 0.05,
                "num_turns": 1,
                "session_id": "fake",
            }
        )
    )


print(json.dumps({"type": "system", "subtype": "init", "model": "fake"}))
harness = cwd / "context" / "harness" / "CLAUDE.md"
if "YOUR DIMENSION: INV" in prompt or "YOUR DIMENSION: DOM" in prompt:
    line = CAREFUL if "YOUR DIMENSION: DOM" in prompt else "Run the tests first."
    harness.write_text(harness.read_text().rstrip() + "\n" + line + "\n")
    say("editing the harness")
    finish("<notes>changed</notes>")
elif "composing THE final harness" in prompt or "composing checkpoint" in prompt:
    merged = [harness.read_text().rstrip()]
    for candidate in sorted((cwd / "context" / "pool").glob("*/harness/CLAUDE.md")):
        for line in candidate.read_text().splitlines():
            if line.strip() and line not in "\n".join(merged):
                merged.append(line)
    harness.write_text("\n".join(merged) + "\n")
    finish("<notes>kept -> dom1 -> moment</notes>")
elif "choosing which of" in prompt:
    finish("<why>cleaner</why>\n```choice\nA\n```")
elif "complete record of one improvement round" in prompt:
    finish(
        "<edits>component: CLAUDE.md\nverdict: helped</edits>\n"
        "<experience>cue: a config file changes\nlesson: CLAUDE.md should warn\n"
        "evidence: dom1 step 2\nconfidence: medium</experience>"
    )
else:
    memory = cwd / "CLAUDE.md"
    rules = memory.read_text() if memory.is_file() else ""
    (cwd / "out.txt").write_text("careful" if CAREFUL in rules else "plain")
    say("working")
    finish("done")
