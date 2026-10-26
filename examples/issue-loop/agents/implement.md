---
agent: implement
runner: claude-code-headless
permission_mode: acceptEdits
tools: [Read, Write, Edit, "Bash(git *)"]
limits: { budget_usd: 1, turns: 15, wall: 5m }
---
You are implementing issue #{{item.number}}: {{item.title}}.
Create a file named feature-{{item.number}}.txt in the current directory whose only line is: feature {{item.number}}
Then commit it with `git add -A && git commit -m "implement #{{item.number}}"`.
Previous failure (empty on the first try): {{iter.previous}}
Reply with the single word done.
