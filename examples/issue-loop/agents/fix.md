---
agent: fix
runner: claude-code-headless
permission_mode: acceptEdits
tools: [Read, Write, Edit, "Bash(git *)"]
limits: { budget_usd: 1, turns: 15, wall: 5m }
---
A reviewer rejected issue #{{item.number}}. Their note: {{input}}
Append the line "reviewed" to feature-{{item.number}}.txt, then commit with `git add -A && git commit -m "address review on #{{item.number}}"`.
Reply with the single word done.
