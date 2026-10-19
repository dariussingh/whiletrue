# Open questions

| #   | Question                                                                                                                                                                        | Decide by   |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- |
| 1   | Does a `--plugin-dir` plugin bundling hooks and an MCP server behave as designed in interactive sessions, including the `Stop` nudge loop? (spike)                              | Start of M2 |
| 2   | How accurate is a budget estimate from transcript usage; is a soft cap acceptable for interactive steps?                                                                        | M2          |
| 3   | After a whiletrue crash: re-attach to the still-running tmux pane, or restart with `claude --resume`?                                                                           | M3          |
| 4   | For `session: item` across retries, how does resume behave when the working directory has changed?                                                                              | M3          |
| 5   | Convention for spec links in issue bodies (e.g. `spec: specs/m3/api.md#L40-L88`) so the knowledge provider can parse them                                                       | M3/M4       |
| 6   | ~~Expression language for `until:` and templates~~ **Resolved: CEL** behind an `Expr` interface (`parse`, `check(schema)`, `eval(scope)`), pending a half-day spike. See below. | M1          |
| 7   | ~~License: MIT or Apache-2.0~~ **Resolved: MIT**                                                                                                                                | Done        |
| 8   | ~~Claim npm org / GitHub org~~ **Superseded by OQ-9:** no npm packages. The PyPI name `whiletrue` is free (checked 2026-10-16); CLI binary stays `whiletrue`. No GitHub org needed. | Done        |
| 9   | ~~Implementation language~~ **Resolved: Python 3.12+** (2026-10-16), not TypeScript. Nothing in the design needs Node: agents are driven as subprocesses. One `whiletrue` distribution; pydantic, stdlib `sqlite3`, asyncio, pytest, ruff, pyright. | Done        |

## Decision: expression language (OQ-6)

**Choice:** CEL (Common Expression Language), wrapped behind an `Expr` interface in `whiletrue.core` so the engine can be swapped.

**Why:** not Turing-complete, no I/O or side effects (safe without a custom sandbox); statically typed, so expressions are checked against port caps at `validate`/`plan` time ("validate before spending"); pure and deterministic, so safe for replay and cache hashing; familiar syntax.

**Rejected:** JSONata (more than needed, weaker static checks); Jexl / expr-eval (fine fallback, no static typing); Python `eval` / `exec` (not a real sandbox).

**Risk:** Python CEL libraries (`cel-python`, pure Python; `common-expression-language`, Rust-backed) are less proven than the Go/Java ones. A spike must confirm: type-checking against a schema, a restricted function set, and a timeout/cost limit. If none pass, fall back to a small whiletrue-owned expression subset plus its own cap checker.
