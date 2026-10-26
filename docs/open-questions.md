# Open questions

| #   | Question                                                                                                                                                                        | Decide by   |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- |
| 1   | ~~Do `--plugin-dir` hooks and an MCP server behave as designed in interactive sessions?~~ **Resolved (2026-10-23): yes**, including the `Stop` nudge loop. See [spike-interactive.md](spike-interactive.md). | Done        |
| 2   | How accurate is a budget estimate from transcript usage; is a soft cap acceptable for interactive steps? **Deferred past v0.1:** the estimate is built and documented as an estimate; comparing it with real billing needs a longer measurement. Headless Claude Code, opencode and Codex are unaffected (Codex and opencode have no budget). | After v0.1   |
| 3   | ~~After a crash: re-attach to the pane, or restart?~~ **Resolved (M3): restart.** `resume` re-runs the interrupted step (its pane is gone) and replays everything that finished; side effects are protected by the journal. See architecture.md. | Done        |
| 4   | ~~`session: item` resume with a changed working directory~~ **Moot for now:** a recovered step starts a fresh session (the item's session ids live in memory); worktrees are reused at the same path, so a normal resume keeps its cwd. | Done        |
| 5   | ~~Convention for spec links in issue bodies~~ **Resolved (M4):** a line `spec: path[#Lstart-Lend]` (also `specs:`, several links per line). Parsed into the item's `spec_refs`. See `whiletrue/github/specs.py`. | Done        |
| 6   | ~~Expression language for `until:` and templates~~ **Resolved: CEL** behind an `Expr` interface (`parse`, `check(schema)`, `eval(scope)`), pending a half-day spike. See below. | M1          |
| 7   | ~~License: MIT or Apache-2.0~~ **Resolved: MIT**                                                                                                                                | Done        |
| 8   | ~~Claim npm org / GitHub org~~ **Superseded by OQ-9:** no npm packages. The PyPI name `whiletrue` is free (checked 2026-10-16); CLI binary stays `whiletrue`. No GitHub org needed. | Done        |
| 9   | ~~Implementation language~~ **Resolved: Python 3.12+** (2026-10-16), not TypeScript. Nothing in the design needs Node: agents are driven as subprocesses. One `whiletrue` distribution; pydantic, stdlib `sqlite3`, asyncio, pytest, ruff, pyright. | Done        |

## Decision: expression language (OQ-6)

**Choice:** CEL (Common Expression Language), wrapped behind an `Expr` interface in `whiletrue.core` so the engine can be swapped.

**Why:** not Turing-complete, no I/O or side effects (safe without a custom sandbox); statically typed, so expressions are checked against port caps at `validate`/`plan` time ("validate before spending"); pure and deterministic, so safe for replay and cache hashing; familiar syntax.

**Rejected:** JSONata (more than needed, weaker static checks); Jexl / expr-eval (fine fallback, no static typing); Python `eval` / `exec` (not a real sandbox).

**Risk:** Python CEL libraries (`cel-python`, pure Python; `common-expression-language`, Rust-backed) are less proven than the Go/Java ones. A spike must confirm: type-checking against a schema, a restricted function set, and a timeout/cost limit. If none pass, fall back to a small whiletrue-owned expression subset plus its own cap checker.

### Spike result (2026-10-22, todo 24)

**Picked `common-expression-language` (Rust-backed, 0.10).** `whiletrue.core.expr` wraps it as `parse_expr` → `Expr` (`check`, `eval`, `eval_bool`).

| | `common-expression-language` | `cel-python` |
|---|---|---|
| Install | wheels, no native deps | needs `google-re2` (native); fails to import on NixOS without `libstdc++` on `LD_LIBRARY_PATH` |
| Speed | 1000 nested iterations in ~0.5 ms | ~120 ms |
| Static analysis | `compile().references()` lists variables and functions | none |
| Schema type-check | no | no |
| Timeout / cost limit | none (native call, not interruptible) | none |
| Reserved word `loop` | rejected | accepted |

What `Expr.check` therefore does: unknown-variable and function-allow-list checks, not full cap type-checking. Cap-level checking of expressions is deferred to the validator, using port caps for the variables. Mitigation for the missing limits: a 4096-character cap on the source, and CEL being non-Turing-complete.

**Consequence:** `loop` is a reserved CEL word, so the loop scope is named `iter` everywhere (templates and expressions): `iter.index` (1-based), `iter.previous`, and `iter.value` inside `over`/`map`. Decided OQ-10 (2026-10-23): one name, `iter`.
