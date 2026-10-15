# Open questions

| # | Question | Decide by |
|---|---|---|
| 1 | Does a `--plugin-dir` plugin bundling hooks and an MCP server behave as designed in interactive sessions, including the `Stop` nudge loop? (spike) | Start of M2 |
| 2 | How accurate is a budget estimate from transcript usage; is a soft cap acceptable for interactive steps? | M2 |
| 3 | After a whiletrue crash: re-attach to the still-running tmux pane, or restart with `claude --resume`? | M3 |
| 4 | For `session: item` across retries, how does resume behave when the working directory has changed? | M3 |
| 5 | Convention for spec links in issue bodies (e.g. `spec: specs/m3/api.md#L40-L88`) so the knowledge provider can parse them | M3/M4 |
| 6 | Expression language for `until:` and templates: safe, sandboxed, no arbitrary JavaScript | M1 |
| 7 | License: MIT or Apache-2.0 | Before first publish |
| 8 | Claim `@whiletrue` npm org and a GitHub org | Before first publish |
