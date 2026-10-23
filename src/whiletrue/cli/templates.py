RALPH_PIPELINE = """# whiletrue pipeline: a Ralph loop.
# Run the agent with a fresh context, check with a deterministic gate, repeat until green.
name: ralph

pipeline:
  loop:
    max: 5            # every loop is bounded; use `max: unbounded` to opt out explicitly
    do:
      - agent: { uses: ./agents/ralph.md }
      - gate.shell: { run: "pytest" }
"""

RALPH_AGENT = """---
agent: ralph
runner: claude-code-headless
permission_mode: acceptEdits
tools: [Read, Edit, Write, "Bash(pytest*)", "Bash(git *)"]
limits: { budget_usd: 2, turns: 40, wall: 20m }
---
Work through the next unfinished item in TODO.md. Make the tests pass.

Failure output from the previous attempt (empty on the first try):
{{loop.previous}}
"""
