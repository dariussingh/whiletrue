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
| 8   | ~~Claim npm org / GitHub org~~ **Resolved:** npm org `whiletrue-ai` (`@whiletrue` taken); packages are `@whiletrue-ai/*`, CLI binary stays `whiletrue`. No GitHub org needed.   | Done        |

## Decision: expression language (OQ-6)

**Choice:** CEL (Common Expression Language), wrapped behind an `Expr` interface in `@whiletrue-ai/core` so the engine can be swapped.

**Why:** not Turing-complete, no I/O or side effects (safe without a custom sandbox); statically typed, so expressions are checked against port caps at `validate`/`plan` time ("validate before spending"); pure and deterministic, so safe for replay and cache hashing; familiar syntax.

**Rejected:** JSONata (more than needed, weaker static checks); Jexl / expr-eval (fine fallback, no static typing); `vm` / `new Function` (not a real sandbox).

**Risk:** JS CEL libraries are less mature than Go/Java ones. A spike must confirm: type-checking against a schema, a restricted function set, and a timeout/cost limit. If none pass, fall back to Jexl plus a whiletrue-owned cap checker.
